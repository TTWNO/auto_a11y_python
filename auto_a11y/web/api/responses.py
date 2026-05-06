"""Response builders for the bare-body REST shape.

The new API returns the resource directly as the JSON body — no
``{"success": true, ...}`` envelope. These helpers exist mainly to keep
status-code conventions and the ``Location`` header consistent across
handlers.
"""
from __future__ import annotations

from http import HTTPStatus
from typing import Any

from flask import Response, jsonify


def json_response(body: Any, *, status: int = HTTPStatus.OK) -> tuple[Response, int]:
    """Return ``body`` serialized as JSON with the given status code.

    For 2xx responses, ``body`` should be the resource representation
    itself (a dict, list, or primitive). The caller is responsible for
    serialization shape — there is no envelope.
    """
    return jsonify(body), status


def created(body: Any, *, location: str) -> tuple[Response, int]:
    """201 Created with a ``Location`` header.

    ``location`` MUST be the canonical URL of the newly-created resource
    (typically built with :func:`flask.url_for`).
    """
    response = jsonify(body)
    response.headers["Location"] = location
    return response, HTTPStatus.CREATED


def no_content() -> tuple[Response, int]:
    """204 No Content. Use for successful ``DELETE`` and side-effect-only ``POST``."""
    response = Response(status=HTTPStatus.NO_CONTENT)
    return response, HTTPStatus.NO_CONTENT
