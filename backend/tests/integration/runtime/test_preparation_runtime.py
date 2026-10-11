"""Disk-backed credits remain owned until abandoned workspaces are removed."""

import fcntl
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.bootstrap import native_resources
from app.runtime import native_runtime, preparation_runtime
from app.runtime.native_admission import LocalResourcePool, Resources
from app.runtime.preparation_runtime import make_pools


@pytest.fixture
def preparation_pools(tmp_path):
    pools = make_pools(tmp_path / "prepared", tmp_path / "io")
    previous = preparation_runtime.bind_pools(pools)
    try:
        yield pools
    finally:
        preparation_runtime.bind_pools(previous)


class TestPreparationRecovery:
    def test_pending_cleanup_keeps_resource_ownership(self, tmp_path):
        entered, finish, polled = (threading.Event() for _ in range(3))
        amount = Resources(1, 100)
        old_identity = None

        def reclaim(identity):
            if identity != old_identity:
                return
            with (tmp_path / "coordinator").open("r+b") as coordinator:
                fcntl.flock(coordinator, fcntl.LOCK_EX | fcntl.LOCK_NB)
            entered.set()
            assert finish.wait(5)

        pool = LocalResourcePool(tmp_path, reclaim=reclaim)
        with pool.reserve(amount, amount, checkpoint=lambda: None) as old:
            old_identity = old.identity
        calls = 0

        def contender():
            def check():
                nonlocal calls
                calls += 1
                if calls >= 3:
                    polled.set()

            with pool.reserve(amount, amount, checkpoint=check):
                return finish.is_set()

        def recover():
            with pool.reserve(amount, amount, checkpoint=lambda: None):
                return finish.is_set()

        with ThreadPoolExecutor(2) as executor:
            first = executor.submit(recover)
            try:
                assert entered.wait(5)
                second = executor.submit(contender)
                assert polled.wait(5)
                assert not first.done() and not second.done()
            finally:
                finish.set()
            assert first.result(timeout=5) is True
            assert second.result(timeout=5) is True

    def test_failed_cleanup_keeps_recoverable_disk_ownership(self, tmp_path):
        owner = make_pools(tmp_path / "prepared", tmp_path / "io").prepared
        amount = Resources(1, 100)
        with owner.reserve(amount, amount, checkpoint=lambda: None) as old:
            workspace = owner.directory / "sources" / old.identity
            workspace.mkdir(parents=True)
            (workspace / "source.stl").write_bytes(b"original")
        attempts = []

        def refuse(identity):
            attempts.append(identity)
            raise PermissionError("cleanup unavailable")

        with pytest.raises(PermissionError, match="cleanup unavailable"):
            with LocalResourcePool(owner.directory, reclaim=refuse).reserve(
                amount, amount, checkpoint=lambda: None
            ):
                pytest.fail("unreclaimed bytes admitted")
        assert old.identity in attempts
        assert (owner.directory / (old.identity + ".ticket")).exists()
        assert (workspace / "source.stl").read_bytes() == b"original"
        with owner.reserve(amount, amount, checkpoint=lambda: None):
            assert not workspace.exists()

    def test_inherited_descriptor_protects_prepared_workspace(self, tmp_path):
        owner = make_pools(tmp_path / "prepared", tmp_path / "io").prepared
        amount = Resources(1, 100)
        polled = threading.Event()
        calls = 0
        with owner.reserve(amount, amount, checkpoint=lambda: None) as old:
            workspace = owner.directory / "sources" / old.identity
            workspace.mkdir(parents=True)
            source = workspace / "source.stl"
            source.write_bytes(b"protected")
            descriptor = os.dup(old.fileno)

        def reclaim():
            def check():
                nonlocal calls
                calls += 1
                if calls >= 3:
                    polled.set()

            with owner.reserve(amount, amount, checkpoint=check):
                return not source.exists()

        with ThreadPoolExecutor(1) as executor:
            future = executor.submit(reclaim)
            try:
                assert polled.wait(5)
                assert not future.done()
                assert source.read_bytes() == b"protected"
            finally:
                os.close(descriptor)
            assert future.result(timeout=5) is True

    def test_prepared_recovery_domain_survives_host_reboot(self, tmp_path, monkeypatch):
        pools = []
        monkeypatch.setattr(native_resources, "bind_pools", pools.append)
        monkeypatch.setattr(native_resources, "bind_pool", lambda _pool: None)
        boot_ids = iter(
            (
                "00000000-0000-0000-0000-000000000001",
                "00000000-0000-0000-0000-000000000002",
            )
        )
        read_text = Path.read_text

        def boot_id(path, *args, **kwargs):
            if path == Path("/proc/sys/kernel/random/boot_id"):
                return next(boot_ids)
            return read_text(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", boot_id)
        native_resources.configure(tmp_path)
        owner = pools[-1].prepared
        amount = Resources(1, 100)
        with owner.reserve(amount, amount, checkpoint=lambda: None) as old:
            workspace = owner.directory / "sources" / old.identity
            workspace.mkdir(parents=True)
            (workspace / "source.stl").write_bytes(b"abandoned before reboot")
        native_resources.configure(tmp_path)
        with pools[-1].prepared.reserve(amount, amount, checkpoint=lambda: None):
            assert not workspace.exists()


class TestPreparationPriority:
    def test_transports_priority_to_preparation_receipts(self, tmp_path):
        import json

        from app.core.work_priority import WorkPriority, priority_scope
        from app.runtime import preparation_runtime

        pools = make_pools(tmp_path / "prepared", tmp_path / "io")
        previous = preparation_runtime.bind_pools(pools)
        try:
            with priority_scope(WorkPriority.BACKFILL):
                with preparation_runtime.reserve(
                    Resources(1, 100), Resources(1, 100), checkpoint=lambda: None
                ) as permit:
                    assert (
                        json.loads(permit.path.read_bytes())["priority"] == "backfill"
                    )
                    with preparation_runtime.io_slot(1, checkpoint=lambda: None):
                        receipt = next(pools.io.directory.glob("*.ticket"))
                        assert (
                            json.loads(receipt.read_bytes())["priority"] == "backfill"
                        )
        finally:
            preparation_runtime.bind_pools(previous)


class TestPreparationLifecycle:
    @pytest.mark.parametrize("identity", ["short", "g" * 32], ids=["short", "nonhex"])
    def test_refuses_malformed_recovery_identities(
        self, preparation_pools, tmp_path, identity
    ):
        prepared = preparation_pools.prepared
        prepared.directory.mkdir()
        receipt = prepared.directory / (identity + ".ticket")
        receipt.write_bytes(b"abandoned")
        sentinel = tmp_path / "untouched.stl"
        sentinel.write_bytes(b"source")

        with pytest.raises(ValueError, match="invalid preparation workspace identity"):
            with prepared.reserve(
                Resources(1, 100), Resources(1, 100), checkpoint=lambda: None
            ):
                pytest.fail("invalid recovery admitted work")

        assert receipt.read_bytes() == b"abandoned"
        assert sentinel.read_bytes() == b"source"

    def test_retains_recovery_ownership_after_partial_unlink_failure(
        self, preparation_pools, monkeypatch
    ):
        amount = Resources(1, 100)
        with preparation_runtime.reserve(
            amount, amount, checkpoint=lambda: None
        ) as old:
            workspace = preparation_runtime.workspace(old)
            source = workspace / "source.stl"
            source.write_bytes(b"retained")

        def missing_descendant(path):
            assert path == workspace
            raise FileNotFoundError("missing descendant during cleanup")

        monkeypatch.setattr(preparation_runtime.shutil, "rmtree", missing_descendant)

        with pytest.raises(
            FileNotFoundError, match="missing descendant during cleanup"
        ):
            with preparation_runtime.reserve(amount, amount, checkpoint=lambda: None):
                pytest.fail("unreclaimed workspace admitted work")

        assert old.path.exists()
        assert source.read_bytes() == b"retained"
        assert preparation_runtime.current_permit() is None

    def test_refuses_workspace_access_outside_permit_lifetime(self, preparation_pools):
        amount = Resources(1, 100)
        with preparation_runtime.reserve(
            amount, amount, checkpoint=lambda: None
        ) as permit:
            active_workspace = preparation_runtime.workspace(permit)

        with pytest.raises(RuntimeError, match="requires its active permit"):
            preparation_runtime.workspace(permit)

        assert active_workspace.exists()

    def test_refuses_preparation_before_pool_binding(self):
        previous = preparation_runtime.bind_pools(None)
        try:
            with pytest.raises(
                RuntimeError, match="pools are not bound by process bootstrap"
            ):
                with preparation_runtime.reserve(
                    Resources(1, 100), Resources(1, 100), checkpoint=lambda: None
                ):
                    pytest.fail("unbound preparation admitted work")
        finally:
            preparation_runtime.bind_pools(previous)

        assert preparation_runtime.current_permit() is None

    def test_refuses_nested_preparation(self, preparation_pools):
        amount = Resources(1, 100)
        with preparation_runtime.reserve(
            amount, amount, checkpoint=lambda: None
        ) as permit:
            with pytest.raises(
                RuntimeError,
                match="prepare all sources before acquiring native resources",
            ):
                with preparation_runtime.reserve(
                    amount, amount, checkpoint=lambda: None
                ):
                    pytest.fail("nested preparation admitted work")

            assert preparation_runtime.current_permit() is permit
            assert preparation_runtime.workspace(permit).exists()

    def test_refuses_preparation_after_native_admission(
        self, preparation_pools, tmp_path
    ):
        amount = Resources(1, 100)
        native_pool = LocalResourcePool(tmp_path / "native")
        with native_pool.reserve(amount, amount, checkpoint=lambda: None) as native:
            with native_runtime.inherit(native.fileno, native.path):
                with pytest.raises(
                    RuntimeError,
                    match="prepare all sources before acquiring native resources",
                ):
                    with preparation_runtime.reserve(
                        amount, amount, checkpoint=lambda: None
                    ):
                        pytest.fail("native-first preparation admitted work")

        assert not (preparation_pools.prepared.directory / "sources").exists()

    def test_refuses_io_without_preparation(self, preparation_pools):
        with pytest.raises(
            RuntimeError,
            match="source I/O requires preparation before native admission",
        ):
            with preparation_runtime.io_slot(1, checkpoint=lambda: None):
                pytest.fail("unprepared source I/O admitted work")

        assert not preparation_pools.io.directory.exists()

    def test_refuses_source_io_after_native_admission(
        self, preparation_pools, tmp_path
    ):
        amount = Resources(1, 100)
        native_pool = LocalResourcePool(tmp_path / "native")
        with preparation_runtime.reserve(amount, amount, checkpoint=lambda: None):
            with native_pool.reserve(amount, amount, checkpoint=lambda: None) as native:
                with native_runtime.inherit(native.fileno, native.path):
                    with pytest.raises(
                        RuntimeError,
                        match="source I/O requires preparation before native admission",
                    ):
                        with preparation_runtime.io_slot(1, checkpoint=lambda: None):
                            pytest.fail("native-held source I/O admitted work")

        assert not preparation_pools.io.directory.exists()

    def test_clears_preparation_scope_after_consumer_failure(self, preparation_pools):
        amount = Resources(1, 100)
        with pytest.raises(ValueError, match="consumer failure"):
            with preparation_runtime.reserve(
                amount, amount, checkpoint=lambda: None
            ) as permit:
                assert preparation_runtime.current_permit() is permit
                raise ValueError("consumer failure")

        assert preparation_runtime.current_permit() is None
