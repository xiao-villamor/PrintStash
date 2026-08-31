"""Container startup tolerates transient registry failures without hiding real errors.

Cold CI runners pull every pinned provider image. A registry rate-limit response
must not discard an otherwise valid 7,000-test run, while configuration and
readiness failures must still surface immediately.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from tests import containers


class _Container:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.started = False

    def start(self) -> None:
        if self.error is not None:
            raise self.error
        self.started = True

    def stop(self) -> None:
        return None


def _factory(
    errors: list[Exception | None],
) -> tuple[Callable[[], _Container], list[_Container]]:
    created: list[_Container] = []

    def create() -> _Container:
        container = _Container(errors[len(created)])
        created.append(container)
        return container

    return create, created


class TestStartContainer:
    def test_returns_a_container_that_starts_immediately(self) -> None:
        factory, created = _factory([None])

        result = containers._start_container(factory)

        assert result is created[0]
        assert result.started is True

    def test_retries_a_transient_registry_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        factory, created = _factory(
            [RuntimeError("toomanyrequests: retry-after: 1s"), None]
        )
        monkeypatch.setattr(containers.time, "sleep", lambda _delay: None)

        result = containers._start_container(factory)

        assert result is created[1]
        assert result.started is True
        assert len(created) == 2

    def test_does_not_retry_a_permanent_start_failure(self) -> None:
        error = RuntimeError("container readiness check failed")
        factory, created = _factory([error])

        with pytest.raises(RuntimeError, match="readiness check failed"):
            containers._start_container(factory)

        assert len(created) == 1

    def test_raises_after_transient_retries_are_exhausted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        errors = [
            RuntimeError("toomanyrequests: registry throttled")
            for _ in range(containers.CONTAINER_START_ATTEMPTS)
        ]
        factory, created = _factory(errors)
        monkeypatch.setattr(containers.time, "sleep", lambda _delay: None)

        with pytest.raises(RuntimeError, match="registry throttled"):
            containers._start_container(factory)

        assert len(created) == containers.CONTAINER_START_ATTEMPTS
