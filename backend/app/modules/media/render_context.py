"""Scheduling context for visual derivatives, independent of compute transport."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_background_render: ContextVar[bool] = ContextVar("background_render", default=False)


@contextmanager
def background_render_scope() -> Iterator[None]:
    """Allow derivative captures to coalesce without changing Job priority."""
    token = _background_render.set(True)
    try:
        yield
    finally:
        _background_render.reset(token)


def background_rendering() -> bool:
    return _background_render.get()
