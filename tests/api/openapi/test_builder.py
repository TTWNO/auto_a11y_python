"""Tests for build_spec()."""
from __future__ import annotations

from typing import cast

import pytest
from flask import Flask
from pydantic import Field

from auto_a11y.web.api.openapi.builder import build_spec
from auto_a11y.web.api.openapi.document import document, register_documented_views
from auto_a11y.web.api.openapi.registry import reset_registry_for_tests
from auto_a11y.web.api.schemas.common import StrictModel


class _ProjectIn(StrictModel):
    name: str = Field(min_length=1, max_length=200)


class _ProjectOut(StrictModel):
    id: str
    name: str


@pytest.fixture
def app() -> Flask:
    reset_registry_for_tests()
    flask_app = Flask(__name__)

    @document(response_200=_ProjectOut, errors=[404], tags=["Projects"], summary="Get a project")
    def get_project(project_id: str) -> _ProjectOut:
        return _ProjectOut(id=project_id, name="x")

    @document(request=_ProjectIn, response_201=_ProjectOut, errors=[400, 401],
              tags=["Projects"], summary="Create a project")
    def create_project(body: _ProjectIn) -> tuple[_ProjectOut, int]:
        return _ProjectOut(id="1", name=body.name), 201

    flask_app.add_url_rule("/projects/<project_id>", view_func=get_project, methods=["GET"])
    flask_app.add_url_rule("/projects", view_func=create_project, methods=["POST"])
    register_documented_views(flask_app, prefix="")
    return flask_app


def _as_dict(obj: object) -> dict[str, object]:
    """Narrow object -> dict[str, object] for test assertions."""
    assert isinstance(obj, dict)
    return cast(dict[str, object], obj)


def test_spec_has_openapi_3_1_version(app: Flask) -> None:
    spec = build_spec(app)
    assert spec["openapi"] == "3.1.0"


def test_spec_lists_both_paths(app: Flask) -> None:
    spec = build_spec(app)
    paths = _as_dict(spec["paths"])
    assert "/projects" in paths
    assert "/projects/{project_id}" in paths


def test_spec_get_has_path_parameter(app: Flask) -> None:
    spec = build_spec(app)
    paths = _as_dict(spec["paths"])
    path_obj = _as_dict(paths["/projects/{project_id}"])
    get_op = _as_dict(path_obj["get"])
    params_raw = get_op["parameters"]
    assert isinstance(params_raw, list)
    params = cast(list[object], params_raw)
    pp_dict = next(
        cast(dict[str, object], p) for p in params
        if isinstance(p, dict) and cast(dict[str, object], p).get("name") == "project_id"
    )
    assert pp_dict["in"] == "path"
    assert pp_dict["required"] is True
    schema = _as_dict(pp_dict["schema"])
    assert schema["type"] == "string"


def test_spec_post_has_request_body_ref(app: Flask) -> None:
    spec = build_spec(app)
    paths = _as_dict(spec["paths"])
    path_obj = _as_dict(paths["/projects"])
    post_op = _as_dict(path_obj["post"])
    rb_outer = _as_dict(post_op["requestBody"])
    content = _as_dict(rb_outer["content"])
    aj = _as_dict(content["application/json"])
    assert aj["schema"] == {"$ref": "#/components/schemas/_ProjectIn"}


def test_spec_post_documents_201_response(app: Flask) -> None:
    spec = build_spec(app)
    paths = _as_dict(spec["paths"])
    post_op = _as_dict(_as_dict(paths["/projects"])["post"])
    responses = _as_dict(post_op["responses"])
    r201 = _as_dict(responses["201"])
    content = _as_dict(r201["content"])
    aj = _as_dict(content["application/json"])
    assert aj["schema"] == {"$ref": "#/components/schemas/_ProjectOut"}


def test_spec_documents_error_responses(app: Flask) -> None:
    spec = build_spec(app)
    paths = _as_dict(spec["paths"])
    post_op = _as_dict(_as_dict(paths["/projects"])["post"])
    responses = _as_dict(post_op["responses"])
    assert "400" in responses
    assert "401" in responses
    r400 = _as_dict(responses["400"])
    content = _as_dict(r400["content"])
    pj = _as_dict(content["application/problem+json"])
    assert pj["schema"] == {"$ref": "#/components/schemas/Problem"}


def test_spec_components_schemas_populated(app: Flask) -> None:
    spec = build_spec(app)
    components = _as_dict(spec["components"])
    schemas = _as_dict(components["schemas"])
    assert "_ProjectIn" in schemas
    assert "_ProjectOut" in schemas
    assert "Problem" in schemas
    proj_in = _as_dict(schemas["_ProjectIn"])
    props = _as_dict(proj_in["properties"])
    name_prop = _as_dict(props["name"])
    assert name_prop["minLength"] == 1


def test_spec_security_schemes(app: Flask) -> None:
    spec = build_spec(app)
    components = _as_dict(spec["components"])
    schemes = _as_dict(components["securitySchemes"])
    assert "bearerAuth" in schemes
    assert "sessionAuth" in schemes


def test_spec_validates_via_openapi_spec_validator(app: Flask) -> None:
    from openapi_spec_validator import validate_spec
    spec = build_spec(app)
    validate_spec(spec)  # raises on invalid spec


def test_spec_emits_multipart_for_request_form(app: Flask) -> None:
    """Verify build_spec produces multipart/form-data for @document(request_form=...).

    The recordings refactor (§5.6) needed the builder to emit
    ``multipart/form-data`` content with binary-string file properties
    for any operation whose ``EndpointDoc`` carries ``request_form_model``
    and ``request_files``. This test isolates that behaviour on a tiny
    synthetic upload endpoint so a regression won't be hidden by the
    recordings handlers' weight.
    """
    from auto_a11y.web.api.openapi.document import (
        document,
        register_documented_views,
    )
    from auto_a11y.web.api.openapi.registry import reset_registry_for_tests

    reset_registry_for_tests()
    new_app = Flask(__name__)

    class _UploadMeta(StrictModel):
        title: str

    class _Out(StrictModel):
        id: str

    @document(
        request_form=_UploadMeta,
        request_files=["recording"],
        response_201=_Out,
        tags=["X"],
        summary="upload",
    )
    def upload(
        form: _UploadMeta, recording: object = None,
    ) -> tuple[_Out, int]:
        del form, recording
        return _Out(id="1"), 201

    new_app.add_url_rule("/upload", view_func=upload, methods=["POST"])
    register_documented_views(new_app, prefix="")

    spec = build_spec(new_app)
    paths = _as_dict(spec["paths"])
    post_op = _as_dict(_as_dict(paths["/upload"])["post"])
    rb = _as_dict(post_op["requestBody"])
    content = _as_dict(rb["content"])
    assert "multipart/form-data" in content
    mp_schema = _as_dict(_as_dict(content["multipart/form-data"])["schema"])
    assert mp_schema["type"] == "object"
    props = _as_dict(mp_schema["properties"])
    # File field appears as binary string.
    rec_prop = _as_dict(props["recording"])
    assert rec_prop["type"] == "string"
    assert rec_prop["format"] == "binary"
    # Form field also appears.
    assert "title" in props
    # Required includes both the form's required keys and the file fields.
    required_raw = mp_schema["required"]
    assert isinstance(required_raw, list)
    required = cast(list[object], required_raw)
    assert "title" in required
    assert "recording" in required
