"""Command admission preserves API capacity and never abandons owned writes."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from threading import Barrier, Event

import pytest
from anyio import CancelScope
from pydantic import ValidationError

from app.api.command_execution import run_command
from app.core.config import Settings, _overlay


class TestCommandExecution:
    @pytest.mark.asyncio
    async def test_limits_simultaneous_commands(self, monkeypatch):
        monkeypatch.setitem(_overlay, "api_command_concurrency", 2)
        entered = Barrier(3)
        release = Event()
        third_started = Event()

        def held(value):
            entered.wait(timeout=2)
            assert release.wait(timeout=2)
            return value

        def third():
            third_started.set()
            return 3

        first = asyncio.create_task(run_command(held, 1))
        second = asyncio.create_task(run_command(held, 2))
        await asyncio.to_thread(entered.wait, 2)
        last = asyncio.create_task(run_command(third))
        try:
            await asyncio.sleep(0)
            assert not third_started.is_set()
        finally:
            release.set()
        assert await asyncio.gather(first, second, last) == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_cancelled_waiter_never_executes(self, monkeypatch):
        monkeypatch.setitem(_overlay, "api_command_concurrency", 1)
        started = Event()
        release = Event()
        cancelled_started = Event()

        def held():
            started.set()
            assert release.wait(timeout=2)
            return 1

        first = asyncio.create_task(run_command(held))
        assert await asyncio.to_thread(started.wait, 2)
        waiting = asyncio.create_task(run_command(cancelled_started.set))
        try:
            await asyncio.sleep(0)
            waiting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiting
            assert not cancelled_started.is_set()
        finally:
            release.set()
        assert await first == 1
        assert await run_command(lambda: 2) == 2

    @pytest.mark.asyncio
    async def test_started_command_finishes_before_scope_cancellation_returns(self):
        completed = Event()
        release = Event()
        loop = asyncio.get_running_loop()
        scope = CancelScope()

        def cancel_then_release():
            scope.cancel()
            release.set()

        def owned_write():
            loop.call_soon_threadsafe(cancel_then_release)
            assert release.wait(timeout=2)
            completed.set()
            return "committed"

        with scope:
            result = await run_command(owned_write)
            assert result == "committed"
        assert completed.is_set()

    @pytest.mark.asyncio
    async def test_native_cancellation_waits_for_owned_write(self, monkeypatch):
        monkeypatch.setitem(_overlay, "api_command_concurrency", 1)
        started = Event()
        release = Event()
        completed = Event()
        successor_started = Event()

        def owned_write():
            started.set()
            assert release.wait(timeout=2)
            completed.set()
            return "committed"

        def successor():
            successor_started.set()
            return completed.is_set()

        first = asyncio.create_task(run_command(owned_write))
        assert await asyncio.to_thread(started.wait, 2)
        first.cancel()
        second = asyncio.create_task(run_command(successor))
        try:
            await asyncio.sleep(0)
            assert not first.done(), (
                "native cancellation returned before the write finished"
            )
            assert not completed.is_set()
            assert not successor_started.is_set(), (
                "cancelled write released its capacity early"
            )
        finally:
            release.set()
            outcomes = await asyncio.gather(first, second, return_exceptions=True)

        assert isinstance(outcomes[0], asyncio.CancelledError)
        assert completed.is_set()
        assert outcomes[1] is True

    @pytest.mark.asyncio
    async def test_native_cancellation_removes_queued_command(self, monkeypatch):
        monkeypatch.setitem(_overlay, "api_command_concurrency", 1)
        started = Event()
        release = Event()
        cancelled_started = Event()

        def owned_write():
            started.set()
            assert release.wait(timeout=2)
            return "committed"

        first = asyncio.create_task(run_command(owned_write))
        assert await asyncio.to_thread(started.wait, 2)
        queued = asyncio.create_task(run_command(cancelled_started.set))
        try:
            await asyncio.sleep(0)
            queued.cancel()
            with pytest.raises(asyncio.CancelledError):
                await queued
            assert not cancelled_started.is_set()
        finally:
            release.set()
            await first

        assert not cancelled_started.is_set()
        assert await run_command(lambda: "successor") == "successor"

    @pytest.mark.asyncio
    async def test_preserves_command_arguments_with_context(self):
        identity = ContextVar("command-test-identity")
        token = identity.set("caller")
        try:

            def result(number, *, suffix):
                return f"{identity.get()}:{number}:{suffix}"

            assert await run_command(result, 7, suffix="owned") == "caller:7:owned"
        finally:
            identity.reset(token)

    @pytest.mark.asyncio
    async def test_exceptions_release_command_capacity(self, monkeypatch):
        monkeypatch.setitem(_overlay, "api_command_concurrency", 1)

        def failed():
            raise ValueError("write failed")

        with pytest.raises(ValueError, match="write failed"):
            await run_command(failed)
        assert await run_command(lambda: "successor") == "successor"

    @pytest.mark.asyncio
    async def test_updated_capacity_admits_waiting_work(self, monkeypatch):
        monkeypatch.setitem(_overlay, "api_command_concurrency", 1)
        started = Event()
        release = Event()

        def held():
            started.set()
            assert release.wait(timeout=2)
            return 1

        first = asyncio.create_task(run_command(held))
        assert await asyncio.to_thread(started.wait, 2)
        try:
            monkeypatch.setitem(_overlay, "api_command_concurrency", 2)
            assert await asyncio.wait_for(run_command(lambda: 2), timeout=1) == 2
        finally:
            release.set()
        assert await first == 1

    @pytest.mark.parametrize("limit", [0, 129])
    def test_rejects_capacity_outside_the_supported_limits(self, limit):
        with pytest.raises(ValidationError, match="api_command_concurrency"):
            Settings(api_command_concurrency=limit)
