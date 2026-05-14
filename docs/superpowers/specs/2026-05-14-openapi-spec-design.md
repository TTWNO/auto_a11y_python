# OpenAPI 3.1 spec for /api/v1/* — design

**Author:** Claude (drafted with Tait Hoyem)
**Date:** 2026-05-14
**Status:** Approved — ready for plan
**Related issues:** follows #27 (REST API rollout), prepares #21 (frontend migration)

---

## 1. Problem and goal

Issue #27 shipped a complete REST surface at `/api/v1/*` (177 routes across the §5.1–§5.16 sections of `docs/REST_API_ROADMAP.md`). The frontend migration tracked under #21 needs machine-readable type contracts to generate a typed HTTP client without hand-copying every payload shape. There is currently no OpenAPI, Swagger, or JSON Schema artifact for the API; the roadmap markdown is the only source of truth.

**Goal:** Author an OpenAPI 3.1 specification that documents every `/api/v1/*` endpoint with request and response schemas precise enough that `openapi-typescript` (or any standard generator) produces a usable client. The spec must stay in sync with the live API — drift between the document and the running code is treated as a CI failure, not a documentation lag.

**Non-goals:**
- Not migrating the frontend to consume the new spec — that is #21.
- Not redesigning the existing REST surface; this work documents what already ships.
- Not introducing new endpoints or changing status codes the existing handlers return.
- Not writing JS or Python client libraries; downstream tooling consumes the spec.

---

## 2. Decisions (locked)

| Decision | Choice | Rationale |
|---|---|---|
| Spec depth | Full spec, code-first | 177 endpoints make hand-authoring impractical and drift-prone. Code-first means Pydantic models are the source of truth. |
| Pydantic role | Validates **both requests and responses** | Strongest contract guarantee. Drift between spec and code becomes a 500 in tests, not silent. |
| Phasing | One huge branch | User-chosen; reviewer takes the hit once. |
| Validation status code | Keep existing 400 for validation failures | Matches current handler behaviour; avoids changing the debugging surface. |
| Spec consumption | Both dynamic endpoint and static file | Dynamic `/api/v1/openapi.json` can never drift; static `docs/api/openapi.yaml` works with file-based tooling. |
| CI integration | Extend existing `typecheck` workflow | Same posture as mypy/pyright/ty; no new bypass paths. |
| Pydantic version | v2 (2.12.5 already in `requirements.txt`) | No new top-level dep. |

---

## 3. Architecture

### 3.1 Schema package — `auto_a11y/web/api/schemas/`

One Pydantic v2 module per roadmap §5.x section, plus `common.py` for cross-cutting types:

```
auto_a11y/web/api/schemas/
├── __init__.py        # re-exports for ergonomic imports
├── common.py          # Problem (RFC 7807), PaginationMeta, ListEnvelope[T], Empty
├── projects.py        # §5.1 — ProjectIn / ProjectPatch / ProjectOut / ProjectList
├── websites.py        # §5.2
├── pages.py           # §5.3
├── test_runs.py       # §5.4 — TestRunOut / TestResultOut / TestSummaryOut
├── reports.py         # §5.5
├── recordings.py      # §5.6
├── schedules.py       # §5.7
├── scripts.py         # §5.8
├── pdfs.py            # §5.9
├── share_tokens.py    # §5.10
├── members.py         # §5.11
├── drupal.py          # §5.12
├── auth.py            # §5.13 — LoginIn / TokenOut / AppUserOut / AppUserPatch
├── admin.py           # §5.14
├── health.py          # §5.15
└── fixtures.py        # §5.16
```

Conventions enforced on every model:

- `model_config = ConfigDict(extra='forbid', populate_by_name=True)` — unknown input fields raise `ValidationError`; aliases (snake_case JSON ↔ camelCase Python or vice versa) are supported but the wire form is canonical.
- All fields are concretely typed. **No `Any`, no `dict[str, Any]`, no `object` placeholders** in schema field types.
- Three families of models per resource:
  - `<Resource>In` — request body for `POST`. Server-controlled fields (`id`, `created_at`, `updated_at`) excluded.
  - `<Resource>Patch` — request body for `PATCH`. All fields `Optional[...]`; `model_fields_set` distinguishes "field omitted" from "field explicitly set to null". `*Patch` models never appear in responses — `PATCH /resource/<id>` returns the full `<Resource>Out` after applying the patch.
  - `<Resource>Out` — full output shape, including server-controlled fields. Has a `@classmethod from_db(cls, model: <DBModel>) -> <Resource>Out` adapter — the **one** place DB-to-API conversion is permitted.
- Listing endpoints return `ListEnvelope[<Resource>Out]` from `common.py`, which carries `data: list[T]`, `pagination: PaginationMeta`. No bare arrays.

### 3.2 Decorator + runtime wrapper — `auto_a11y/web/api/openapi/document.py`

```python
@document(
    request=ProjectIn,                # validates request.get_json()
    response_200=ProjectOut,           # validates the success return
    response_201=ProjectOut,
    errors=[400, 401, 403, 404, 409], # documents Problem responses
    tags=['Projects'],
    summary='Create a project',
    description='Create a new project owned by the authenticated user.',
    security='bearer+session',          # both schemes accepted
)
def create_project(body: ProjectIn) -> tuple[ProjectOut, int]:
    project = get_db().create_project(name=body.name, ...)
    return ProjectOut.from_db(project), 201
```

Runtime behaviour:

1. **Request side.** When `request=Model` is set, the wrapper reads `request.get_json(silent=True)`, calls `Model.model_validate(payload or {})`, and injects the resulting model as the `body` keyword argument to the handler. On `pydantic.ValidationError`, control flow jumps to the central error mapper (§3.3) which returns a **400 Problem** — matching what the current hand-rolled validation returns today.
2. **Response side.** If the handler returns a `BaseModel`, the wrapper calls `.model_dump(mode='json', by_alias=True, exclude_none=True)` and `jsonify()`s. If it returns `(BaseModel, int)`, the int becomes the HTTP status. If it returns a Flask `Response` or `(Response, int)`, the wrapper passes it through unchanged (escape hatch for file downloads, NDJSON, redirects).
3. **Documentation side.** The decorator stores an `EndpointDoc(view_function, request_model, response_models_by_status, errors, tags, summary, description, security)` record in a module-level registry keyed by `(method, rule)`. The runtime path never reads the registry; only the spec builder (§3.4) does.

The decorator's overload set covers exactly four return-type shapes from the wrapped view, all expressed without `Any`:

- `BaseModel` — serialized with HTTP 200.
- `tuple[BaseModel, int]` — serialized with the supplied status.
- `flask.Response` — passed through.
- `tuple[flask.Response, int]` — passed through with the supplied status.

Any other return type is a type-check error at handler definition time.

The decorator is type-generic over the request and response model classes; type checkers see real Pydantic types on both sides. The only escape hatch is the `Response` passthrough, which is encoded as a `Union[BaseModel, tuple[BaseModel, int], Response, tuple[Response, int]]` return type on the wrapped view — no `Any`, no `cast(Any, ...)`.

### 3.3 Error mapper — `auto_a11y/web/api/openapi/errors.py`

Single function `validation_error_to_problem(exc: pydantic.ValidationError) -> tuple[Response, int]` returning RFC 7807 Problem with shape:

```json
{
  "type": "about:blank",
  "title": "Bad Request",
  "status": 400,
  "detail": "request body failed validation",
  "errors": [
    {"loc": ["name"], "msg": "field required", "type": "missing"},
    {"loc": ["email"], "msg": "value is not a valid email address", "type": "value_error"}
  ]
}
```

Returns **400** (not 422). This is the only place `ValidationError` is translated; no handler catches it directly. All other 4xx and 5xx the handlers already return are preserved byte-for-byte.

### 3.4 Spec builder — `auto_a11y/web/api/openapi/builder.py`

```python
def build_spec(app: Flask) -> dict[str, object]: ...
```

Walks `app.url_map.iter_rules()` joined with the `EndpointDoc` registry. For each documented rule:

- Translates Flask path params `<project_id>`, `<int:page_id>`, `<uuid:token>` to OpenAPI `{project_id}`, `{page_id}`, `{token}` plus an inline path-parameter schema. The Flask converter type drives the OpenAPI schema: `<int:...>` → `{type: integer}`, `<float:...>` → `{type: number}`, `<uuid:...>` → `{type: string, format: uuid}`, the default (string) and `<path:...>` → `{type: string}`. Custom converters fall back to `{type: string}` with a TODO log emitted at build time so they're not silently dropped.
- Emits `summary`, `description`, `tags`, `security` from the decorator.
- For each `(status, model)` in the response map, emits `responses.<status>.content.application/json.schema` as a `$ref` into `components.schemas`.
- For each error status in `errors=`, emits a Problem reference (`{"$ref": "#/components/schemas/Problem"}`).
- For the request body (if any), emits `requestBody.content.application/json.schema` as a `$ref`.

`components.schemas` is populated by calling `model_json_schema(ref_template='#/components/schemas/{model}')` on every registered Pydantic model, then merging the `$defs` blocks of each into a single flat namespace. Nested models become `$ref` entries; circular references are supported via Pydantic's native handling.

Security schemes emitted to `components.securitySchemes`:
- `bearerAuth` — HTTP, scheme `bearer`, bearerFormat `Token`.
- `sessionAuth` — apiKey, in `cookie`, name `session`.

Per-operation security: `security: [{bearerAuth: []}, {sessionAuth: []}]` (logical OR) for `bearer+session`; `[{bearerAuth: []}]` for `bearer`; `[]` (empty array, meaning **no auth required**) for public routes.

### 3.5 Dynamic endpoint — `GET /api/v1/openapi.json` + `GET /api/v1/openapi.yaml`

Implemented as a small new module `auto_a11y/web/routes/v1_openapi.py` that defines two view functions (`openapi_json` and `openapi_yaml`) and attaches them to the existing `api_bp` via `api_bp.add_url_rule(...)` at module load time. **No new blueprint** — keeping the `/api/v1` URL prefix consistent and avoiding a second registration site. Both views call `build_spec(current_app)` per request — never cached, so they cannot drift. Public (no auth required); the spec describes only the existence and shape of endpoints, not data. Content negotiation is via path suffix.

### 3.6 Static file — `docs/api/openapi.yaml`

Single YAML file (~6–10k lines). Generated by:

```bash
python scripts/generate_openapi.py            # writes the file
python scripts/generate_openapi.py --check    # exits non-zero if on-disk is stale
```

The check flag rebuilds the spec in memory, normalises whitespace and key ordering deterministically, and diffs against the on-disk file. The pre-commit hook and CI both run `--check`. To regenerate, run without `--check`.

Single file (not split per resource) because OpenAPI consumers (`openapi-typescript`, `oapi-codegen`, Stoplight) expect a single document; splitting and reassembling adds tooling cost with no payoff.

### 3.7 Validation tooling

`openapi-spec-validator` (Python, ~20 KB on PyPI, no Node) added to `requirements.txt` as a dev dependency. Used by:

- `scripts/generate_openapi.py` — validates immediately after generation, fails fast on decorator misuse.
- CI — re-runs `openapi-spec-validator` against the checked-in YAML.

No Spectral, no Stoplight, no Node toolchain — keeps the dev environment Python-only.

### 3.8 Contract tests — `tests/api/test_openapi_contract.py`

Pytest module that programmatically iterates every entry in the `EndpointDoc` registry. For each entry with a documented happy-path response:

1. Builds a minimal valid request using the existing `logged_in_client` or `bearer_client` fixtures.
2. Sends the request via Flask's test client.
3. Validates the response body against the documented Pydantic model.
4. On failure: emits `POST /api/v1/projects returned 201 but body failed ProjectOut.model_validate: ...`.

Endpoints requiring elaborate setup (file uploads, multi-step state) are marked `@pytest.mark.skip("contract test requires …")` with the reason captured in the skip message — surfaced in the test report so we can't lose track. A counter in the test suite tracks `documented_endpoints / contract_tested_endpoints / skipped_endpoints`.

**Coverage denominator clarification.** The 80% gate is computed as `contract_tested_endpoints / contract_eligible_endpoints`, where `contract_eligible_endpoints` is the count of registry entries that have **at least one `BaseModel`-typed response model** (i.e., the handler returns a documented model rather than a raw `Response`). The drift gate is separate and harsher: every entry in `api_bp.url_map` lacking a registry entry fails the suite, regardless of response type. The two gates together mean (a) every v1 route is documented, and (b) at least 80% of model-returning routes have a passing contract test against real wire bytes.

### 3.9 CI integration

**Pre-commit hook** — `.githooks/pre-commit`:
- Existing mypy/pyright/ty steps unchanged.
- New step: `python scripts/generate_openapi.py --check`.
- Same no-bypass policy (`--no-verify` already prohibited by CLAUDE.md).

**GitHub Actions** — extend the existing `typecheck` workflow:
- New job `openapi_check` mirroring the Python 3.11 / 3.12 matrix.
- Runs in this order: `python scripts/generate_openapi.py --check`, `openapi-spec-validator docs/api/openapi.yaml`, `pytest tests/api/test_openapi_contract.py`.
- Must be added to required status checks on `main` by the repo owner.

### 3.10 Type-checker scope

`pyproject.toml` updates, applied consistently to mypy, pyright, and ty `files`/`include` lists per the existing project rule:

- Add `auto_a11y/web/api/schemas/**/*.py`.
- Add `auto_a11y/web/api/openapi/**/*.py`.
- Add `scripts/generate_openapi.py`.
- `tests/api/test_openapi_contract.py` is already covered by `tests/**/*.py`.

---

## 4. Handler refactor pattern

Every one of the 177 `/api/v1/*` handlers becomes:

```python
@api_bp.route('/projects', methods=['POST'])
@require_authenticated
@document(request=ProjectIn, response_201=ProjectOut,
          errors=[400, 401, 403], tags=['Projects'],
          summary='Create a project')
def create_project(body: ProjectIn) -> tuple[ProjectOut, int]:
    project = get_db().create_project(name=body.name, owner=current_user_id())
    return ProjectOut.from_db(project), 201
```

Rules:

1. Handlers take a `body: <Model>In` keyword parameter when the operation has a JSON request body. Path parameters keep their existing positional shape.
2. Handlers return either a Pydantic output model or `(model, status)`. The `@document` wrapper performs serialization.
3. The `Response` passthrough (returning a raw Flask `Response`) is reserved for non-JSON endpoints (file downloads, NDJSON streaming, redirects). The decorator detects this and skips serialization.
4. All `if not data.get('foo'): return jsonify({...}), 400` branches are deleted. Pydantic raises; the central mapper translates.
5. `<Resource>Out.from_db(db_model)` is the one and only DB-to-API translation site. Handlers must not hand-build response dicts.

### 4.1 Expected scope

- 177 handler conversions (file-by-file across `auto_a11y/web/routes/api.py` plus the few endpoints in `recordings.py`, `pdf.py`, `share_tokens.py`, etc. that live on `api_v1` paths).
- ~50 input models (`*In` and `*Patch`).
- ~80 output models (`*Out`, `*List`, and aggregate result shapes).
- ~177 `@document(...)` annotations.
- Light test fixture/assertion adjustments where renames surface (e.g., `created_at` vs `createdAt`).
- Realistic diff size: **~8,000–12,000 lines**. One reviewer takes the hit; reviewer must allocate dedicated time.

---

## 5. File-by-file change plan

| Path | Change |
|---|---|
| `requirements.txt` | Add `openapi-spec-validator>=0.7`. |
| `pyproject.toml` | Add new schema and openapi packages to mypy/pyright/ty `files`/`include`. |
| `auto_a11y/web/api/schemas/` (new) | 17 Pydantic modules (one per §5.x + `common.py`). |
| `auto_a11y/web/api/openapi/` (new) | `document.py`, `errors.py`, `builder.py`, `registry.py`, `__init__.py`. |
| `auto_a11y/web/routes/api.py` | All 177 handlers converted to the new pattern. |
| `auto_a11y/web/routes/recordings.py` etc. | Any v1-mounted handlers converted likewise. |
| `auto_a11y/web/routes/v1_openapi.py` (new) | View functions for dynamic `GET /api/v1/openapi.json` and `.yaml`; attaches to existing `api_bp`. |
| `scripts/generate_openapi.py` (new) | CLI wrapper around `build_spec()` with `--check`. |
| `docs/api/openapi.yaml` (new) | Initial generated artifact, committed. |
| `.githooks/pre-commit` | Add `scripts/generate_openapi.py --check` step. |
| `.github/workflows/typecheck.yml` (or wherever the CI workflow lives) | Add `openapi_check` job. |
| `tests/api/test_openapi_contract.py` (new) | Auto-iterating contract test suite. |

---

## 6. Type-checker considerations

Three known frictions, all resolvable without suppressions:

1. **`@document` decorator generics.** The decorator's signature has to express "the wrapped callable's return type is one of `BaseModel | tuple[BaseModel, int] | Response | tuple[Response, int]`" without using `Any`. Resolved with a `TypeVar` over the response model plus an overload set covering the four return shapes. Reference: Pydantic's own decorators do this.
2. **`model_json_schema()` returns `dict[str, Any]`.** The Pydantic stubs declare the return type loosely. Inside the builder, we accept it at the boundary and assign to `dict[str, object]`; per PEP 484, `Any` is assignable to `object`. Mypy and pyright both accept this; if ty objects, we'll know on first run.
3. **Per-request validation on path params.** Flask hands integers through the URL converter (`<int:page_id>`) as native `int`; UUIDs (`<uuid:...>`) as `uuid.UUID`. The decorator does not touch path params — type-checking is the responsibility of the handler signature, which already works today.

The project's "no `# type: ignore`" rule applies to all new code. Any objection from ty that no refactor or stubs patch can resolve falls under the existing `[tool.auto_a11y_typecheck] ty_enabled` escape hatch (per CLAUDE.md), not a per-line suppression.

---

## 7. i18n and colour constraints

Out of scope for this work — the OpenAPI spec is machine-readable, not user-visible, so neither the bilingual Fluent rule nor the custom-colour-class rule apply. The spec's `description` and `summary` fields are written in English; if frontend tooling renders them in a UI later (Swagger UI, Redoc), translation is part of #21 not #27.

---

## 8. Risks and mitigations

| Risk | Mitigation |
|---|---|
| 8–12k line diff overwhelms the reviewer | User-chosen phasing; flagged twice during brainstorming. Reviewer can request per-section split if needed. |
| Pydantic v2 model conversion subtleties (e.g., `Optional` vs `\| None` vs explicit-null PATCH semantics) | `<Resource>Patch` models use a documented Pydantic v2 pattern with `model_fields_set` to distinguish unset from explicit-null. Worked example in the first converted resource. |
| Spec drift if a developer adds a route but forgets `@document` | The contract test suite walks `api_bp.url_map`, not the registry — it fails on any v1 route lacking documentation. |
| Frontend (#21) consumers want camelCase wire format | The schema modules can opt into `populate_by_name` and snake-to-camel aliases. The default in this spec is **snake_case wire format** matching what the existing handlers already return; we don't change the wire shape. |
| `openapi-spec-validator` produces a false positive on a Pydantic-generated construct | Pin the version. If a real upstream bug surfaces, the existing `[tool.auto_a11y_typecheck]`-style downgrade pattern in `pyproject.toml` handles it (separate flag, repo-wide, review-visible). |

---

## 9. Out of scope (deferred)

- Frontend migration to consume the generated client — tracked under #21.
- OpenAPI tags for "deprecated" routes — the `Deprecation` and `Sunset` HTTP headers are already in place per the §27 work; OpenAPI's `deprecated: true` field can be added later if needed.
- Swagger UI / Redoc rendered HTML page — anyone who wants a UI can point `swagger-ui` at `/api/v1/openapi.json`.
- Client SDK generation (Python, TS, etc.) — the spec enables this; producing it is a separate task.

---

## 10. Definition of done

- All 177 handlers refactored to the `@document(...)` + Pydantic-model pattern.
- `auto_a11y/web/api/schemas/` and `auto_a11y/web/api/openapi/` packages complete, all members type-clean under mypy + pyright + ty strict.
- `docs/api/openapi.yaml` committed and passes `openapi-spec-validator`.
- `GET /api/v1/openapi.json` and `GET /api/v1/openapi.yaml` return the same spec the file contains.
- `tests/api/test_openapi_contract.py` achieves ≥80% contract coverage of documented endpoints; the remainder are explicitly skipped with reasons.
- Pre-commit hook and CI both reject drift between the live spec and the on-disk file.
- All existing test suites still pass — no behavioural regression.
- Roadmap doc `docs/REST_API_ROADMAP.md` gets a §10 update pointing to the spec and noting that the OpenAPI document is now the canonical contract.
