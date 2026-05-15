"""Translation from pydantic.ValidationError to RFC 7807 Problem.

This is the single place where Pydantic validation failures become HTTP
responses. Every @document-wrapped handler that takes a request model
funnels its ValidationError through here.
"""
from __future__ import annotations

from flask import Response, jsonify
from pydantic import ValidationError


def validation_error_to_problem(exc: ValidationError) -> tuple[Response, int]:
    """Return a (400 Problem, 400) tuple suitable for direct return.

    The Problem body conforms to RFC 7807, with an additional ``errors``
    array carrying the per-field validation failures Pydantic produced. The
    Content-Type is set to ``application/problem+json``.
    """
    errors: list[dict[str, object]] = []
    for err in exc.errors():
        errors.append({
            "loc": list(err.get("loc", [])),
            "msg": err.get("msg", ""),
            "type": err.get("type", ""),
        })
    body = jsonify({
        "type": "about:blank",
        "title": "Bad Request",
        "status": 400,
        "detail": "request body failed validation",
        "errors": errors,
    })
    body.mimetype = "application/problem+json"
    return body, 400
