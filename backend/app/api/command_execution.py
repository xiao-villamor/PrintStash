"""Bound synchronous request commands independently of ordinary API workers.

A slow storage call or engine nudge occupies command capacity without exhausting
FastAPI's general thread limiter. The complete callable, including its SQL
Session lifetime, executes on one worker; cancellation never abandons its writes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial, wraps
from inspect import signature
from typing import ParamSpec, TypeVar

from anyio import CapacityLimiter, create_task_group, to_thread
from anyio.lowlevel import RunVar

from app.core.config import settings

P = ParamSpec("P")
T = TypeVar("T")
_limiter = RunVar[CapacityLimiter]("request_command_limiter")


def _command_limiter() -> CapacityLimiter:
    limiter = _limiter.get(None)
    limit = settings.api_command_concurrency
    if limiter is None:
        limiter = CapacityLimiter(limit)
        _limiter.set(limiter)
    elif limiter.total_tokens != limit:
        limiter.total_tokens = limit
    return limiter


@dataclass(frozen=True, slots=True)
class _CommandSuccess[T]:
    value: T


@dataclass(frozen=True, slots=True)
class _CommandFailure:
    error: BaseException


async def run_command(command: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    outcome: _CommandSuccess[T] | _CommandFailure | None = None
    limiter = _command_limiter()

    async def execute_owned() -> None:
        nonlocal outcome
        try:
            result = await to_thread.run_sync(
                partial(command, *args, **kwargs), limiter=limiter
            )
        except BaseException as exc:
            outcome = _CommandFailure(exc)
        else:
            outcome = _CommandSuccess(result)

    # The group joins the owned child even when native Task.cancel() interrupts
    # the caller. A started worker retains its capacity until its writes finish.
    # Store callable failures so task-group exit cannot wrap HTTP exceptions.
    async with create_task_group() as group:
        group.start_soon(execute_owned)

    if isinstance(outcome, _CommandFailure):
        raise outcome.error
    if outcome is None:
        raise RuntimeError("owned command exited without an outcome")
    return outcome.value


def bounded_command(command: Callable[P, T]) -> Callable[P, Awaitable[T]]:
    """Expose a synchronous HTTP command through bounded awaited execution."""

    @wraps(command)
    async def execute(*args: P.args, **kwargs: P.kwargs) -> T:
        return await run_command(command, *args, **kwargs)

    # Resolve the original module's postponed annotations before FastAPI reads
    # the wrapper; route schemas retain their original types and documentation.
    execute.__dict__["__signature__"] = signature(command, eval_str=True)
    return execute
