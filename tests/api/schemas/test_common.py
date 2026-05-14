"""Tests for shared OpenAPI schema types."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from auto_a11y.web.api.schemas.common import (
    Empty,
    ListEnvelope,
    PaginationMeta,
    Problem,
)
from auto_a11y.web.api.schemas.projects import ProjectOut  # forward-ref OK


def test_problem_minimal_fields() -> None:
    p = Problem(type="about:blank", title="Bad Request", status=400)
    assert p.status == 400
    assert p.detail is None


def test_problem_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        Problem.model_validate({"type": "about:blank", "title": "x", "status": 400, "extra": "no"})


def test_pagination_meta_round_trip() -> None:
    m = PaginationMeta(total=42, page=1, per_page=20, total_pages=3)
    assert m.model_dump() == {"total": 42, "page": 1, "per_page": 20, "total_pages": 3}


def test_list_envelope_is_generic() -> None:
    env = ListEnvelope[ProjectOut](
        data=[],
        pagination=PaginationMeta(total=0, page=1, per_page=20, total_pages=0),
    )
    assert env.data == []
    assert env.pagination.total == 0


def test_empty_has_no_fields() -> None:
    e = Empty()
    assert e.model_dump() == {}
