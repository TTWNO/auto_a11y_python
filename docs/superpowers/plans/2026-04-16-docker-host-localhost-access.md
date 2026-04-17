# Docker Host Localhost Access Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow the containerised `app` service to reach HTTP servers running on the host (e.g., `localhost:8000`, `:8080`, `:80`) so users can run accessibility tests against their local dev sites.

**Architecture:** Add the standard Docker `host-gateway` alias (`host.docker.internal`) to the `app` service via `extra_hosts` in `docker-compose.yml`. Document the workflow in `README.md` and `README.fr.md` under a new `## Docker` top-level section placed between `## Installation` and `## Configuration`. No application code changes.

**Tech Stack:** Docker Compose / Podman Compose (YAML config); plain Markdown docs.

**Spec:** `docs/superpowers/specs/2026-04-16-docker-host-localhost-access-design.md`

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `docker-compose.yml` | Modify | Add `extra_hosts` to `app` service (with inline comment) so the container can resolve `host.docker.internal` |
| `docker-compose.override.yml` | Inspect only | Confirm it does not redefine `app` networking and therefore inherits `extra_hosts`; no edit needed |
| `README.md` | Modify | New `## Docker` section documenting `docker compose up` and the host-local server workflow |
| `README.fr.md` | Modify | Matching French `## Docker` section, placed at the same line position |

No new files. No code changes. No tests (manual verification only — this is a runtime-networking config).

---

## Task 1: Add `extra_hosts` to the app service

**Files:**
- Modify: `docker-compose.yml` (under `services.app`, alongside existing keys)

- [ ] **Step 1: Inspect current compose to locate the edit site**

Run: `cat docker-compose.yml` (or open it in your editor).

Find the `app` service block. Today it has keys: `build`, `container_name`, `command`, `ports`, `env_file`, `environment`, `volumes`, `depends_on`, `deploy`, `restart`. You will add `extra_hosts` as a new key in that block. Suggested placement: immediately after `environment:` and before `volumes:` (keeps networking-ish keys grouped). Any order is valid YAML; that's just for readability.

- [ ] **Step 2: Confirm the override does not shadow networking**

Run: `cat docker-compose.override.yml`

Verify the `app` block in the override defines only `command` and `volumes` — NOT `extra_hosts`, `networks`, or `network_mode`. Compose merges `extra_hosts` as a list-append across files, but there's nothing to merge; the base value will apply as-is. If you find a `network_mode`, `networks`, or `extra_hosts` entry in the override, STOP and ask — that changes the design.

- [ ] **Step 3: Add the `extra_hosts` key**

Edit `docker-compose.yml`. Under `services.app`, after the `environment:` block (the list ending with `- BROWSER_HEADLESS=True`) and before `volumes:`, insert:

```yaml
    # Allow the container to reach HTTP servers running on the host
    # (e.g. http://host.docker.internal:8080). Requires Docker Engine 20.10+
    # or Podman 4.1+. No effect on Docker Desktop, which provides this alias
    # natively.
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

Indentation matters: `extra_hosts` must be indented the same as the sibling keys (`environment`, `volumes`, etc.) — four spaces under the `app:` service.

- [ ] **Step 4: Validate the compose file**

Run: `docker compose config > /dev/null`
Expected: exit 0, no output. (If `docker` is unavailable, `podman-compose config > /dev/null` is an acceptable substitute.)

If the command errors with a YAML parse error, re-check indentation. If it errors with "unknown field" on `extra_hosts`, the Compose version is too old — stop and surface to human.

- [ ] **Step 5: Verify the alias resolves at runtime**

Run (in sequence):

```bash
# Start a trivial HTTP server on the host in another terminal:
python3 -m http.server 8765 &
HOST_SERVER_PID=$!

# Bring up the stack (rebuild not required — compose config-only change):
docker compose up -d

# From inside the app container, confirm the hostname resolves and the
# host server responds:
docker compose exec app getent hosts host.docker.internal
docker compose exec app curl -sS -o /dev/null -w '%{http_code}\n' \
  http://host.docker.internal:8765/

# Cleanup:
docker compose down
kill $HOST_SERVER_PID
```

Expected:
- `getent hosts` prints a line like `172.17.0.1 host.docker.internal` (IP varies).
- `curl` prints `200`.

If `getent` returns nothing, the `host-gateway` keyword isn't supported — surface to human.
If `curl` fails with connection refused, the host server isn't bound to an interface the gateway can reach; try `python3 -m http.server 8765 --bind 0.0.0.0` and retry.

- [ ] **Step 6: Commit**

```bash
git add docker-compose.yml
git commit -m "$(cat <<'EOF'
docker: allow container to reach host-local servers

Adds host.docker.internal:host-gateway to the app service's extra_hosts
so users can run accessibility tests against HTTP servers running on
their host machine (e.g. a local dev server on port 8080). Works on
Docker Desktop, Docker Engine 20.10+, and Podman 4.1+.

Spec: docs/superpowers/specs/2026-04-16-docker-host-localhost-access-design.md
EOF
)"
```

(If GPG signing times out, the user has authorised `-c commit.gpgsign=false` for this branch.)

---

## Task 2: Document in `README.md`

**Files:**
- Modify: `README.md` (insert a new `## Docker` section between `## Installation` (currently ends at line 74) and `## Configuration` (currently starts at line 76))

- [ ] **Step 1: Read the exact surrounding lines**

Run (or use your editor) to see context:

```bash
sed -n '70,80p' README.md
```

You should see the tail of the Installation section followed by `## Configuration`. Line numbers may have drifted if other changes landed; always search for the literal `## Configuration` marker rather than trusting the line number.

- [ ] **Step 2: Insert the new section**

Insert the following block immediately before the `## Configuration` heading. Keep one blank line above and below the new section.

```markdown
## Docker

Auto A11y ships with a `docker-compose.yml` that runs the app and
MongoDB together. With Docker (20.10+) or Podman (4.1+) installed:

```bash
docker compose up
# or: podman-compose up
```

The UI is then available at http://localhost:5001.

### Testing host-local servers

To run accessibility tests against an HTTP server running on **your
host machine** (for example a dev server on port `8080`), use the
hostname `host.docker.internal` instead of `localhost`:

| Running on host as | Enter in Auto A11y as |
|---|---|
| `http://localhost:80` | `http://host.docker.internal` |
| `http://localhost:8080` | `http://host.docker.internal:8080` |
| `http://localhost:8000` | `http://host.docker.internal:8000` |

This is enabled by the `extra_hosts` entry in `docker-compose.yml`. No
extra configuration is required. If the host server is bound only to
`127.0.0.1`, rebind it to `0.0.0.0` so the container can reach it.
```

Note the code fence: this is a Markdown document with an embedded
fenced code block. When pasting, make sure the outer code fence you
are using in your editor (if any) doesn't collide with the inner
```bash fence. The final rendered README should contain the inner
fence verbatim.

- [ ] **Step 3: Verify the section renders correctly**

Run: `sed -n '/^## Docker$/,/^## Configuration$/p' README.md`

Expected: you see the full `## Docker` section followed by `## Configuration`. No stray backticks, no broken table.

Optional: render with a Markdown previewer (GitHub's preview, `glow`, VSCode preview) and confirm the table displays as a table.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "$(cat <<'EOF'
docs: document Docker setup and host-local server testing (README.md)

Adds a new ## Docker section covering docker compose up and the
host.docker.internal hostname swap for testing host-local servers.
EOF
)"
```

---

## Task 3: Mirror the documentation in `README.fr.md`

**Files:**
- Modify: `README.fr.md` (insert a French `## Docker` section in the same position as the English one — between `## Installation` and `## Configuration`)

Per `CLAUDE.md`: "Any change to `README.md` MUST be reflected in `README.fr.md` — they must stay in sync."

- [ ] **Step 1: Locate the insert point**

Run: `sed -n '/^## Configuration$/=' README.fr.md`

Expected: one line number (the French `## Configuration` heading). Insert the new section immediately before it.

- [ ] **Step 2: Insert the French section**

Insert the following block immediately before the `## Configuration` heading:

```markdown
## Docker

Auto A11y inclut un fichier `docker-compose.yml` qui démarre
l'application et MongoDB ensemble. Avec Docker (20.10+) ou Podman
(4.1+) installé :

```bash
docker compose up
# ou : podman-compose up
```

L'interface est ensuite accessible à l'adresse http://localhost:5001.

### Tester des serveurs locaux à l'hôte

Pour exécuter des tests d'accessibilité sur un serveur HTTP qui tourne
sur **votre machine hôte** (par exemple un serveur de développement
sur le port `8080`), utilisez le nom d'hôte `host.docker.internal` à
la place de `localhost` :

| Sur l'hôte | À saisir dans Auto A11y |
|---|---|
| `http://localhost:80` | `http://host.docker.internal` |
| `http://localhost:8080` | `http://host.docker.internal:8080` |
| `http://localhost:8000` | `http://host.docker.internal:8000` |

Ce comportement est activé par l'entrée `extra_hosts` du fichier
`docker-compose.yml`. Aucune configuration supplémentaire n'est
requise. Si le serveur de l'hôte écoute uniquement sur `127.0.0.1`,
reconfigurez-le pour écouter sur `0.0.0.0` afin que le conteneur
puisse l'atteindre.
```

- [ ] **Step 3: Verify French/English parity**

Run:

```bash
diff <(grep -c '^## ' README.md) <(grep -c '^## ' README.fr.md)
diff <(grep -c '^### ' README.md) <(grep -c '^### ' README.fr.md)
```

Expected: both `diff` commands print nothing (same heading counts in both files).

Also run: `sed -n '/^## Docker$/,/^## Configuration$/p' README.fr.md` and visually confirm the French section looks reasonable (tables align, code fences balanced).

- [ ] **Step 4: Commit**

```bash
git add README.fr.md
git commit -m "$(cat <<'EOF'
docs: document Docker setup and host-local server testing (README.fr.md)

Mirrors the ## Docker section added to README.md so the French and
English documentation stay in sync per CLAUDE.md.
EOF
)"
```

---

## Task 4: Final end-to-end manual verification

No automated test covers this; a single manual pass confirms the feature works for the actual user workflow.

- [ ] **Step 1: Start a host server**

In a separate terminal on your host machine:

```bash
python3 -m http.server 8765 --bind 0.0.0.0
```

Leave it running.

- [ ] **Step 2: Start the auto-a11y stack (fresh)**

```bash
docker compose down    # ensure clean state
docker compose up -d
```

Wait ~20 seconds for MongoDB to be healthy, then confirm the app is up:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' http://localhost:5001/
```

Expected: `200` (or `302` if it redirects to a login page).

- [ ] **Step 3: Register a test page against `host.docker.internal`**

In the auto-a11y web UI at http://localhost:5001:

1. Create or open a project and website.
2. Add a page with URL `http://host.docker.internal:8765/`.
3. Trigger an accessibility test.
4. Confirm the test completes successfully and produces results (the page is a directory listing, so a few accessibility findings are expected — what matters is that the page was reachable).

Expected: test runs to completion (no "failed to load page" / timeout error).

- [ ] **Step 4: Cleanup**

```bash
docker compose down
# Kill the host python http.server in its terminal (Ctrl+C).
```

- [ ] **Step 5: No commit — this task is verification only.**

---

## Rollout / Rollback

- **Rollout:** Normal merge. On next `docker compose up` (no rebuild required), the `extra_hosts` entry takes effect.
- **Rollback:** `git revert` the three commits from Tasks 1–3. No data migration, no schema change, no config flag.

## Out of Scope

- Auto-rewriting user-entered `localhost` URLs.
- Granting the `mongo` service host access.
- Changing `network_mode`.
- Documenting the rest of the Docker setup (env files, volumes, etc.) beyond what's necessary for this feature.
