"""Tests for the EndpointDoc registry."""
from __future__ import annotations

from auto_a11y.web.api.openapi.registry import (
    EndpointDoc,
    REGISTRY,
    register,
    registry_snapshot,
    reset_registry_for_tests,
)
from auto_a11y.web.api.schemas.common import Empty


def _view() -> Empty:
    return Empty()


def _empty_doc() -> EndpointDoc:
    return EndpointDoc(
        view=_view,
        request_model=None,
        request_form_model=None,
        request_files=(),
        responses={200: Empty},
        errors=[400],
        tags=["Test"],
        summary="Test endpoint",
        description=None,
        security="bearer+session",
    )


def test_register_inserts_entry() -> None:
    reset_registry_for_tests()
    doc = _empty_doc()
    register("GET", "/test", doc)
    snap = registry_snapshot()
    assert ("GET", "/test") in snap
    assert snap[("GET", "/test")] is doc


def test_register_rejects_different_doc_at_same_key() -> None:
    """Re-registering a different EndpointDoc at the same key raises.

    Note: register_documented_views() is idempotent for identical metas —
    the strict check is on the registry's ``register()`` primitive, which
    raises only when two distinct EndpointDoc objects collide.
    """
    reset_registry_for_tests()
    doc1 = _empty_doc()
    doc2 = _empty_doc()  # distinct object, same shape
    register("GET", "/dup", doc1)
    try:
        register("GET", "/dup", doc2)
    except RuntimeError as exc:
        assert "/dup" in str(exc)
    else:
        raise AssertionError("expected RuntimeError on distinct duplicate")


def test_global_registry_is_dict() -> None:
    # Sanity: the module-level REGISTRY is a real dict, not a property.
    assert isinstance(REGISTRY, dict)
