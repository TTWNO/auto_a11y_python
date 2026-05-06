"""``@api_endpoint`` — the decorator every REST handler wears.

What it does:

- Catches :class:`auto_a11y.web.api.errors.ApiError` and returns the
  RFC 7807 Problem Details response.
- Catches unexpected exceptions, logs them with the request context, and
  returns a generic 500 Problem Details body — never a stack-trace or a
  Flask debug page.
- Sets ``instance`` on the problem to ``request.path`` so clients can
  correlate errors with the URL that produced them.

Handlers wrapped with ``@api_endpoint`` should return either:

- A Flask :class:`flask.Response` (most often built with the helpers in
  :mod:`auto_a11y.web.api.responses`), or
- A ``(Response, status_code)`` tuple, matching Flask's native return
  shape.
"""
from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from typing import ParamSpec

from flask import Response, request

from auto_a11y.web.api.errors import ApiError, ProblemDetails, problem_response

logger = logging.getLogger(__name__)


P = ParamSpec("P")
HandlerReturn = Response | tuple[Response, int]


def api_endpoint(handler: Callable[P, HandlerReturn]) -> Callable[P, HandlerReturn]:
    """Wrap a Flask view function with API error translation.

    The decorated function MUST return either a :class:`Response` or a
    ``(Response, int)`` tuple — the same shapes Flask itself accepts.
    """

    @functools.wraps(handler)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> HandlerReturn:
        try:
            return handler(*args, **kwargs)
        except ApiError as err:
            problem = err.problem()
            if problem.instance is None:
                problem = ProblemDetails(
                    type=problem.type,
                    title=problem.title,
                    status=problem.status,
                    detail=problem.detail,
                    instance=request.path,
                    errors=problem.errors,
                )
            return problem_response(problem)
        except Exception:
            # Log with stack trace, but do not leak the exception details
            # to the response. Production clients see a generic 500 with a
            # consistent shape; developers see the traceback in the logs.
            logger.exception(
                "Unhandled exception in API handler %s",
                getattr(handler, "__name__", "<anonymous>"),
            )
            problem = ProblemDetails(
                type="https://auto-a11y/errors/internal",
                title="Internal server error",
                status=500,
                detail=None,
                instance=request.path,
            )
            return problem_response(problem)

    return wrapper


__all__ = ["api_endpoint"]
