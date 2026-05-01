"""Async compatibility helpers for nest_asyncio on Python 3.14+.

``nest_asyncio.apply()`` swaps :class:`asyncio.Task` for the pure-Python
``_PyTask`` so the event loop can be re-entered. In Python 3.14 the
``asyncio.tasks`` module re-binds ``current_task`` (and friends) to the
C implementation at module load. The C ``current_task()`` reads C-level
state that ``_PyTask.__step`` never updates, so it returns ``None``
once nest_asyncio is in effect.

That breaks anything that relies on a current task — most notably
:func:`asyncio.wait_for`, which in 3.14 wraps the awaitable in
``async with asyncio.timeout(...):`` and raises
``RuntimeError("Timeout should be used inside a task")`` when
``current_task()`` is ``None``.

This module provides drop-in replacements that avoid ``current_task()``.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable
from typing import TypeVar

T = TypeVar("T")


async def wait_for(awaitable: Awaitable[T], timeout: float | None) -> T:
    """Drop-in replacement for :func:`asyncio.wait_for`.

    Behaves like ``asyncio.wait_for``: on timeout, the underlying task
    is cancelled and :class:`asyncio.TimeoutError` is raised. If the
    surrounding task is cancelled, cancellation propagates to the
    inner task before re-raising.
    """
    if timeout is None:
        return await awaitable

    task = asyncio.ensure_future(awaitable)
    try:
        await asyncio.wait({task}, timeout=timeout)
    except BaseException:
        if not task.done():
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
        raise

    if not task.done():
        task.cancel()
        with contextlib.suppress(BaseException):
            await task
        raise TimeoutError(f"Operation timed out after {timeout}s")

    return task.result()
