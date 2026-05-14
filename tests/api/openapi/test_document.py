"""Tests for the @document decorator (validation + serialization + registry)."""
from __future__ import annotations

from typing import Optional

import pytest
from flask import Flask, Response
from werkzeug.datastructures import FileStorage

from auto_a11y.web.api.openapi.document import (
    DocumentedView,
    document,
    register_documented_views,
)
from auto_a11y.web.api.openapi.registry import (
    REGISTRY,
    reset_registry_for_tests,
)
from auto_a11y.web.api.schemas.common import StrictModel


class _BodyIn(StrictModel):
    name: str
    count: Optional[int] = None


class _BodyOut(StrictModel):
    id: str
    name: str


@pytest.fixture
def app() -> Flask:
    """Fresh Flask app + cleared registry per test.

    `register_documented_views` is called explicitly inside each test
    AFTER add_url_rule, so the registry assertions in the test body see the
    populated state.
    """
    reset_registry_for_tests()
    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    return flask_app


def _register(app: Flask, rule: str, view: DocumentedView, methods: list[str]) -> None:
    """Helper: add_url_rule + register_documented_views in one step.

    ``prefix=""`` because tests use a vanilla Flask app with no /api/v1
    mount; rules stay as-is for registry-key purposes.
    """
    app.add_url_rule(rule, view_func=view, methods=methods)
    register_documented_views(app, prefix="")


def test_request_model_validates_json(app: Flask) -> None:
    @document(request=_BodyIn, response_200=_BodyOut, tags=["X"], summary="x")
    def view(body: _BodyIn) -> _BodyOut:
        return _BodyOut(id="1", name=body.name)

    _register(app, "/v", view, ["POST"])
    client = app.test_client()
    r = client.post("/v", json={"name": "alice"})
    assert r.status_code == 200
    assert r.get_json() == {"id": "1", "name": "alice"}


def test_request_validation_failure_returns_400_problem(app: Flask) -> None:
    @document(request=_BodyIn, response_200=_BodyOut, tags=["X"], summary="x")
    def view(body: _BodyIn) -> _BodyOut:
        return _BodyOut(id="1", name=body.name)

    _register(app, "/v", view, ["POST"])
    client = app.test_client()
    r = client.post("/v", json={"count": 1})  # missing name
    assert r.status_code == 400
    assert r.mimetype == "application/problem+json"
    body = r.get_json()
    assert body["title"] == "Bad Request"
    assert any(e["loc"] == ["name"] for e in body["errors"])


def test_response_201_status_via_tuple(app: Flask) -> None:
    @document(request=_BodyIn, response_201=_BodyOut, tags=["X"], summary="x")
    def view(body: _BodyIn) -> tuple[_BodyOut, int]:
        return _BodyOut(id="2", name=body.name), 201

    _register(app, "/v", view, ["POST"])
    client = app.test_client()
    r = client.post("/v", json={"name": "bob"})
    assert r.status_code == 201
    assert r.get_json() == {"id": "2", "name": "bob"}


def test_response_pass_through_for_raw_response(app: Flask) -> None:
    @document(tags=["X"], summary="x")
    def view() -> Response:
        return Response(b"raw bytes", status=418, mimetype="application/octet-stream")

    _register(app, "/v", view, ["GET"])
    client = app.test_client()
    r = client.get("/v")
    assert r.status_code == 418
    assert r.data == b"raw bytes"


def test_no_request_body_when_request_unset(app: Flask) -> None:
    @document(response_200=_BodyOut, tags=["X"], summary="x")
    def view() -> _BodyOut:
        return _BodyOut(id="3", name="anon")

    _register(app, "/v", view, ["GET"])
    client = app.test_client()
    r = client.get("/v")
    assert r.status_code == 200
    assert r.get_json() == {"id": "3", "name": "anon"}


def test_registry_populated_at_decoration(app: Flask) -> None:
    @document(request=_BodyIn, response_201=_BodyOut, tags=["X"], summary="create x")
    def view(body: _BodyIn) -> tuple[_BodyOut, int]:
        return _BodyOut(id="1", name=body.name), 201

    _register(app, "/things", view, ["POST"])
    # Keys are blueprint-relative — see register_documented_views in Step 3.
    assert ("POST", "/things") in REGISTRY
    doc = REGISTRY[("POST", "/things")]
    assert doc.request_model is _BodyIn
    assert doc.responses == {201: _BodyOut}
    assert doc.tags == ["X"]
    assert doc.summary == "create x"


def test_register_documented_views_is_idempotent(app: Flask) -> None:
    """Calling register_documented_views twice on the same app is a no-op."""
    @document(response_200=_BodyOut, tags=["X"], summary="x")
    def view() -> _BodyOut:
        return _BodyOut(id="1", name="a")

    _register(app, "/v", view, ["GET"])
    # Second call: must not raise duplicate-registration error.
    register_documented_views(app, prefix="")
    # Registry still has exactly one entry for ("GET", "/v").
    assert ("GET", "/v") in REGISTRY


def test_multipart_form_validates_form_fields(app: Flask) -> None:
    class _UploadIn(StrictModel):
        title: str

    @document(request_form=_UploadIn, request_files=["file"],
              response_201=_BodyOut, tags=["X"], summary="upload")
    def view(form: _UploadIn, file: Optional[FileStorage] = None) -> tuple[_BodyOut, int]:
        # The decorator injects ``file`` as a kwarg per request_files=["file"].
        assert file is not None
        return _BodyOut(id="up1", name=form.title), 201

    _register(app, "/upload", view, ["POST"])
    client = app.test_client()
    # Send multipart with both a form field and a file.
    import io
    r = client.post(
        "/upload",
        data={"title": "doc.pdf", "file": (io.BytesIO(b"\x25PDF-1.4"), "doc.pdf")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 201
    assert r.get_json() == {"id": "up1", "name": "doc.pdf"}
