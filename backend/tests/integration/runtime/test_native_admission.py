"""Resource credits coordinate real processes and outlive a killed launcher."""

from __future__ import annotations

import ctypes
import json
import os
import selectors
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.modules.media.worker_bootstrap import reap_descendants
from app.runtime.native_admission import (
    AdmissionCorrupted,
    AdmissionTooLarge,
    LocalResourcePool,
    NativePermit,
    Resources,
)
from tests.paths import BACKEND_DIR


@pytest.fixture
def waiter():
    """Observe a second failed admission poll, without timing a negative result."""
    polled = threading.Event()
    calls = 0

    def checkpoint():
        nonlocal calls
        calls += 1
        if calls >= 3:
            polled.set()

    return polled, checkpoint


@pytest.fixture
def orphan(tmp_path, request):
    """Reap this probe's descendants even if readiness or an assertion fails."""
    libc = ctypes.CDLL(None, use_errno=True)
    prior = ctypes.c_int()
    assert libc.prctl(37, ctypes.byref(prior), 0, 0, 0) == 0
    assert libc.prctl(36, 1, 0, 0, 0) == 0
    parent = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "tests.fakes.native_admission_process",
            getattr(request, "param", "orphan"),
            str(tmp_path),
        ],
        cwd=BACKEND_DIR,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    child = None
    try:
        assert parent.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(parent.stdout, selectors.EVENT_READ)
            assert selector.select(10), "orphan probe did not become ready"
        child = json.loads(parent.stdout.readline())["child"]
        yield parent, child
    finally:
        try:
            os.killpg(parent.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        parent.wait(timeout=5)
        reap_descendants(parent.pid)
        if (
            getattr(request, "param", "orphan") == "guarded-orphan"
            and child is not None
        ):
            try:
                os.killpg(child, signal.SIGKILL)
            except ProcessLookupError:
                pass
            reap_descendants(child)
        assert libc.prctl(36, prior.value, 0, 0, 0) == 0
        if parent.stdout is not None:
            parent.stdout.close()
        if parent.stdin is not None:
            parent.stdin.close()


class TestLocalResourcePool:
    def test_admits_simultaneous_claims_within_capacity(self, tmp_path):
        pool = LocalResourcePool(tmp_path)
        with pool.reserve(Resources(1, 40), Resources(2, 100), checkpoint=lambda: None):
            with LocalResourcePool(tmp_path).reserve(
                Resources(1, 40), Resources(2, 100), checkpoint=lambda: None
            ) as other:
                assert other.resources == Resources(1, 40)

    def test_large_claim_waits_for_memory(self, tmp_path, waiter):
        pool = LocalResourcePool(tmp_path)
        polled, checkpoint = waiter
        released = threading.Event()

        def claim():
            with pool.reserve(
                Resources(1, 80), Resources(2, 100), checkpoint=checkpoint
            ):
                return released.is_set()

        with ThreadPoolExecutor(1) as executor:
            with pool.reserve(
                Resources(1, 40), Resources(2, 100), checkpoint=lambda: None
            ):
                future = executor.submit(claim)
                assert polled.wait(5)
                assert not future.done()
                released.set()
            assert future.result(timeout=5) is True

    def test_fails_closed_when_its_queued_ticket_disappears(self, tmp_path):
        pool = LocalResourcePool(tmp_path)
        calls = 0

        def remove_queued():
            nonlocal calls
            calls += 1
            if calls == 3:
                ticket = next(
                    path
                    for path in tmp_path.glob("*.ticket")
                    if json.loads(path.read_text())["state"] == "queued"
                )
                ticket.unlink()
            if calls == 4:
                raise TimeoutError("lost ticket kept waiting")

        with pool.reserve(
            Resources(1, 100), Resources(1, 100), checkpoint=lambda: None
        ):
            with pytest.raises(AdmissionCorrupted, match="disappeared"):
                with pool.reserve(
                    Resources(1, 100), Resources(1, 100), checkpoint=remove_queued
                ):
                    pytest.fail("missing ticket was admitted")

    @pytest.mark.parametrize(
        "field,value",
        [
            ("state", "active"),
            ("request", [1, 50]),
            ("capacity", [2, 200]),
            ("order", 0),
        ],
        ids=["state", "request", "capacity", "order"],
    )
    def test_refuses_mutation_of_its_registered_claim(self, tmp_path, field, value):
        pool = LocalResourcePool(tmp_path)
        calls = 0

        def mutate():
            nonlocal calls
            calls += 1
            if calls == 2:
                path = next(tmp_path.glob("*.ticket"))
                ticket = json.loads(path.read_text())
                ticket[field] = value
                path.write_text(json.dumps(ticket))
            elif calls > 2:
                raise TimeoutError("changed claim kept waiting")

        with pytest.raises(AdmissionCorrupted, match="changed"):
            with pool.reserve(Resources(1, 100), Resources(1, 100), checkpoint=mutate):
                pytest.fail("modified claim was admitted")

    def test_fifo_starts_when_the_claim_is_registered(self, tmp_path):
        pool = LocalResourcePool(tmp_path)
        delayed = threading.Event()
        resume = threading.Event()
        first_queued = threading.Event()
        delayed_queued = threading.Event()
        observed = []

        def claim(name, queued, pause=False):
            calls = 0

            def check():
                nonlocal calls
                calls += 1
                if pause and calls == 1:
                    delayed.set()
                    assert resume.wait(5)
                if calls >= 3:
                    queued.set()

            with pool.reserve(Resources(1, 100), Resources(1, 100), checkpoint=check):
                observed.append(name)

        with ThreadPoolExecutor(2) as executor:
            try:
                with pool.reserve(
                    Resources(1, 100), Resources(1, 100), checkpoint=lambda: None
                ):
                    late = executor.submit(claim, "delayed", delayed_queued, True)
                    assert delayed.wait(5)
                    first = executor.submit(claim, "registered", first_queued)
                    assert first_queued.wait(5)
                    resume.set()
                    assert delayed_queued.wait(5)
            finally:
                resume.set()
            first.result(timeout=5)
            late.result(timeout=5)
        assert observed == ["registered", "delayed"]

    def test_refuses_a_claim_exceeding_total_capacity(self, tmp_path):
        with pytest.raises(AdmissionTooLarge):
            with LocalResourcePool(tmp_path).reserve(
                Resources(1, 101), Resources(2, 100), checkpoint=lambda: None
            ):
                pytest.fail("oversized claim was admitted")

    def test_waits_for_prior_configuration_to_drain(self, tmp_path, waiter):
        pool = LocalResourcePool(tmp_path)
        polled, checkpoint = waiter
        released = threading.Event()

        def claim():
            with pool.reserve(
                Resources(1, 10), Resources(4, 1000), checkpoint=checkpoint
            ):
                return released.is_set()

        with ThreadPoolExecutor(1) as executor:
            with pool.reserve(
                Resources(1, 10), Resources(2, 100), checkpoint=lambda: None
            ):
                future = executor.submit(claim)
                assert polled.wait(5)
                assert not future.done()
                released.set()
            assert future.result(timeout=5) is True

    def test_reclaims_abandoned_invalid_ticket(self, tmp_path):
        (tmp_path / "abandoned.ticket").write_text("truncated")

        with LocalResourcePool(tmp_path).reserve(
            Resources(1, 100), Resources(1, 100), checkpoint=lambda: None
        ) as permit:
            assert permit.resources == Resources(1, 100)
            assert not (tmp_path / "abandoned.ticket").exists()

    def test_refuses_corrupted_live_ticket(self, tmp_path):
        import fcntl

        bad = tmp_path / "bad.ticket"
        with bad.open("wb") as handle:
            handle.write(b"truncated")
            handle.flush()
            fcntl.flock(handle, fcntl.LOCK_EX)

            with pytest.raises(AdmissionCorrupted):
                with LocalResourcePool(tmp_path).reserve(
                    Resources(1, 1), Resources(1, 100), checkpoint=lambda: None
                ):
                    pytest.fail("corrupt live credit was ignored")

    def test_cancelled_waiter_leaves_capacity_available(self, tmp_path):
        pool = LocalResourcePool(tmp_path)
        calls = 0

        def withdraw():
            nonlocal calls
            calls += 1
            if calls == 3:
                raise InterruptedError("withdrawn")

        with pool.reserve(
            Resources(1, 100), Resources(1, 100), checkpoint=lambda: None
        ):
            with pytest.raises(InterruptedError, match="withdrawn"):
                with pool.reserve(
                    Resources(1, 100), Resources(1, 100), checkpoint=withdraw
                ):
                    pytest.fail("waiting claim unexpectedly admitted")

        with pool.reserve(
            Resources(1, 100), Resources(1, 100), checkpoint=lambda: None
        ) as permit:
            assert permit.resources == Resources(1, 100)

    def test_closed_permit_cannot_supply_descriptor(self, tmp_path):
        with LocalResourcePool(tmp_path).reserve(
            Resources(1, 100), Resources(1, 100), checkpoint=lambda: None
        ) as permit:
            assert permit.resources == Resources(1, 100)

        with pytest.raises(ValueError, match="closed"):
            _ = permit.fileno

    def test_inherits_without_procfs(self, tmp_path, monkeypatch):
        with LocalResourcePool(tmp_path).reserve(
            Resources(1, 100), Resources(1, 100), checkpoint=lambda: None
        ) as permit:
            ticket = next(tmp_path.glob("*.ticket"))
            original = os.open

            def open_path(path, *args, **kwargs):
                if str(path).startswith("/proc/"):
                    raise FileNotFoundError("procfs unavailable")
                return original(path, *args, **kwargs)

            monkeypatch.setattr(os, "open", open_path)
            with NativePermit.inherit(permit.fileno, ticket) as inherited:
                assert inherited.resources == permit.resources
                assert inherited.identity == permit.identity
                assert inherited.path == ticket

    def test_refuses_replaced_ticket_path(self, tmp_path):
        with LocalResourcePool(tmp_path).reserve(
            Resources(1, 100), Resources(1, 100), checkpoint=lambda: None
        ) as permit:
            ticket = next(tmp_path.glob("*.ticket"))
            payload = ticket.read_bytes()
            ticket.unlink()
            ticket.write_bytes(payload)
            with pytest.raises(AdmissionCorrupted, match="identity"):
                with NativePermit.inherit(permit.fileno, ticket):
                    pytest.fail("replaced path supplied authority")

    def test_refuses_inherited_ticket_without_live_credit(self, tmp_path):
        with LocalResourcePool(tmp_path).reserve(
            Resources(1, 100), Resources(1, 100), checkpoint=lambda: None
        ):
            pass
        ticket = next(tmp_path.glob("*.ticket"))

        with ticket.open("rb") as descriptor:
            with pytest.raises(AdmissionCorrupted, match="no held credit"):
                with NativePermit.inherit(descriptor.fileno(), ticket):
                    pytest.fail("unheld ticket was inherited")

    def test_rejects_a_reopened_descriptor_to_another_owners_credit(self, tmp_path):
        with LocalResourcePool(tmp_path).reserve(
            Resources(1, 100), Resources(1, 100), checkpoint=lambda: None
        ):
            ticket = next(tmp_path.glob("*.ticket"))
            with ticket.open("r+b") as other:
                with pytest.raises(AdmissionCorrupted, match="does not own credit"):
                    with NativePermit.inherit(other.fileno(), ticket):
                        pytest.fail("a different file description borrowed the credit")

    def test_refuses_inherited_queued_ticket(self, tmp_path):
        with LocalResourcePool(tmp_path).reserve(
            Resources(1, 100), Resources(1, 100), checkpoint=lambda: None
        ) as permit:
            ticket = next(tmp_path.glob("*.ticket"))
            document = json.loads(ticket.read_text())
            document["state"] = "queued"
            ticket.write_text(json.dumps(document))

            with pytest.raises(AdmissionCorrupted, match="not active"):
                with NativePermit.inherit(permit.fileno, ticket):
                    pytest.fail("queued ticket was treated as active")

    def test_large_waiter_precedes_later_small_claims(self, tmp_path, waiter):
        pool = LocalResourcePool(tmp_path)
        polled, checkpoint = waiter
        large_active = threading.Event()
        release_large = threading.Event()
        small_active = threading.Event()

        def large():
            with pool.reserve(
                Resources(1, 80), Resources(3, 100), checkpoint=checkpoint
            ):
                large_active.set()
                assert release_large.wait(5)

        def small():
            with pool.reserve(
                Resources(1, 40), Resources(3, 100), checkpoint=lambda: None
            ):
                small_active.set()

        with ThreadPoolExecutor(2) as executor:
            try:
                with pool.reserve(
                    Resources(1, 40), Resources(3, 100), checkpoint=lambda: None
                ):
                    first = executor.submit(large)
                    assert polled.wait(5)
                    second = executor.submit(small)
                assert large_active.wait(5)
                assert not small_active.is_set()
            finally:
                release_large.set()
            first.result(timeout=5)
            second.result(timeout=5)
        assert small_active.is_set()

    def test_slow_checkpoint_does_not_hold_coordinator(self, tmp_path):
        blocked = threading.Event()
        resume = threading.Event()
        pool = LocalResourcePool(tmp_path)

        def checkpoint():
            blocked.set()
            assert resume.wait(5)

        def first():
            with pool.reserve(
                Resources(1, 100), Resources(1, 100), checkpoint=checkpoint
            ):
                return True

        with ThreadPoolExecutor(1) as executor:
            future = executor.submit(first)
            try:
                assert blocked.wait(5)
                with pool.reserve(
                    Resources(1, 100), Resources(1, 100), checkpoint=lambda: None
                ) as permit:
                    assert permit.resources == Resources(1, 100)
            finally:
                resume.set()
            assert future.result(timeout=5) is True

    def test_child_retains_credit_after_parent_death(self, tmp_path, waiter, orphan):
        parent, child = orphan
        polled, checkpoint = waiter
        released = threading.Event()

        def claim():
            with LocalResourcePool(tmp_path).reserve(
                Resources(1, 100), Resources(1, 100), checkpoint=checkpoint
            ):
                return released.is_set()

        parent.kill()
        parent.wait(timeout=5)
        with ThreadPoolExecutor(1) as executor:
            future = executor.submit(claim)
            try:
                assert polled.wait(5)
                assert not future.done()
                released.set()
            finally:
                os.kill(child, signal.SIGKILL)
            assert future.result(timeout=5) is True

    @pytest.mark.parametrize("orphan", ["guarded-orphan"], indirect=True)
    def test_guardian_retains_credit_until_exec_descendants_exit(
        self, tmp_path, orphan
    ):
        from app.modules.media.native_process import process_tree_rss_bytes

        parent, native = orphan
        descendants = json.loads((tmp_path / "native-pids.json").read_text())
        parent.kill()
        parent.wait(timeout=5)
        pool = LocalResourcePool(tmp_path)
        resources = Resources(1, 256 * 1024**2)

        deadline = time.monotonic() + 10

        def check_deadline():
            if time.monotonic() >= deadline:
                raise TimeoutError("guardian did not release resource credit")

        with pool.reserve(resources, resources, checkpoint=check_deadline):
            assert process_tree_rss_bytes(native) in (None, 0)
            assert process_tree_rss_bytes(descendants[1]) in (None, 0)
            assert not (tmp_path / "printstash-mesh-guarded").exists()
