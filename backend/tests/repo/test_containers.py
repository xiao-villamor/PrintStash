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

    def test_retries_when_a_pulled_digest_is_not_immediately_visible(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        factory, created = _factory(
            [RuntimeError("No such image: provider@sha256:abc"), None]
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


class TestOpenSshImage:
    @pytest.mark.parametrize("machine", ["x86_64", "amd64"], ids=["linux", "docker"])
    def test_selects_the_pinned_amd64_manifest(
        self, monkeypatch: pytest.MonkeyPatch, machine: str
    ) -> None:
        monkeypatch.setattr(containers.platform, "machine", lambda: machine)

        assert containers._openssh_image() == (
            "lscr.io/linuxserver/openssh-server"
            "@sha256:85fa42da0475a71e1f51426439ebad63bf7ba3daaa7961430482759ea9ba562b"
        )

    @pytest.mark.parametrize("machine", ["aarch64", "arm64"], ids=["linux", "docker"])
    def test_selects_the_pinned_arm64_manifest(
        self, monkeypatch: pytest.MonkeyPatch, machine: str
    ) -> None:
        monkeypatch.setattr(containers.platform, "machine", lambda: machine)

        assert containers._openssh_image() == (
            "lscr.io/linuxserver/openssh-server"
            "@sha256:bdf6c42b8d9a7e2250685ef7e6f9cfb07dd7b956cfb431590c49c7b312cbaf8a"
        )

    def test_rejects_an_unsupported_architecture(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(containers.platform, "machine", lambda: "riscv64")

        with pytest.raises(
            RuntimeError, match="unsupported OpenSSH test architecture: riscv64"
        ):
            containers._openssh_image()
