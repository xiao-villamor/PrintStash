"""Boots the real app lifespan (startup + shutdown), not just handler-level tests.

Every other test in the suite gets its FastAPI ``app`` fixture pre-wired
(``app.state.printer_hub`` set manually, no ``with TestClient(app) as client``),
so ``app/main.py``'s ``lifespan()`` — DB init, storage init, background task
wiring, graceful shutdown — had no direct coverage (58% per the 0.11 audit).
This starts it for real via Starlette's TestClient context-manager protocol.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import select
from starlette.requests import Request as StarletteRequest

import app.main as app_main
from app.core.config import _overlay
from app.db.models import (
    CaptureUploadSlot,
    CaptureUploadSlotState,
    InboxItem,
    InboxItemState,
    InboxSourceKind,
    ModelProvenanceSource,
    OwnedStorageObject,
    PrinterProvider,
    PrinterStatus,
    StagingLease,
    SystemConfig,
)
from app.services import storage_backend
from app.services.auth import create_access_token
from app.services.realtime import InProcessBus
from app.services.storage_backend import (
    ObjectIdentity,
    StorageCapabilities,
    StorageConfigurationError,
)
from tests.factories import build_model, build_printer, build_user


@pytest.fixture
def _local_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _overlay.update(
        {
            "storage_backend": "local",
            "data_dir": tmp_path / "files",
            "thumb_dir": tmp_path / "thumbs",
            "backup_dir": tmp_path / "backups",
            "staging_dir": tmp_path / "staging",
        }
    )
    for role, root in (
        ("data", tmp_path / "files"),
        ("thumb", tmp_path / "thumbs"),
    ):
        root.mkdir(parents=True, exist_ok=True)
        (root / ".printstash-storage-root.json").write_text(
            '{"format":1,"installation":"%s","role":"%s"}' % ("a" * 64, role),
            encoding="utf-8",
        )
    monkeypatch.setattr(storage_backend, "_backend", None)
    yield
    for field in (
        "storage_backend",
        "data_dir",
        "thumb_dir",
        "backup_dir",
        "staging_dir",
    ):
        _overlay.pop(field, None)


class TestStorageComposition:
    def test_provider_probe_failure_binds_unavailable_recovery_backend(
        self, _local_storage: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A missing remote root leaves health/config reachable and mutations closed."""
        from app.services import storage_opendal

        _overlay["storage_backend"] = "sftp"
        _overlay["storage_provider_config"] = (
            '{"provider":"sftp","root":"vault-data",'
            '"host":"sftp.example","port":22,"username":"user",'
            '"host_key":"sftp.example ssh-ed25519 AAAA",'
            '"password":"secret"}'
        )

        class _ProbeFailureBackend:
            backend_name = "sftp"
            capabilities = StorageCapabilities(
                conditional_create=True,
                object_identity=ObjectIdentity.NONE,
                verified_delete=False,
                conditional_replace=False,
                namespace_ownership=True,
                direct_path=False,
            )

            def ensure_setup(self) -> None:
                raise ConnectionError("remote root is unavailable")

        monkeypatch.setattr(
            storage_opendal,
            "OpenDALStorageBackend",
            lambda _spec: _ProbeFailureBackend(),
        )

        backend = app_main._prepare_storage_for_startup(recover_publications=True)

        assert backend.backend_name == "unavailable"
        assert backend.health_probe()["ok"] is False
        with pytest.raises(StorageConfigurationError, match="storage_unavailable"):
            backend.create_stream(BytesIO(b"payload"), "ignored")

    def test_recovery_composition_skips_setup_probes(
        self, _local_storage: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An unresolved restore binds read/recovery access without startup I/O."""
        from app.services import inbox

        events: list[str] = []

        class _RecoveryBackend:
            backend_name = "local"
            capabilities = StorageCapabilities(
                conditional_create=True,
                object_identity=ObjectIdentity.INODE,
                verified_delete=True,
                conditional_replace=True,
                namespace_ownership=True,
                direct_path=True,
            )

            def ensure_setup(self) -> None:
                events.append("unexpected-setup")
                raise AssertionError("recovery startup must not probe storage")

        backend = _RecoveryBackend()
        monkeypatch.setattr(app_main, "LocalStorageBackend", lambda: backend)
        monkeypatch.setattr(
            app_main,
            "bind_backend",
            lambda value: events.append("bind") or value,
        )
        monkeypatch.setattr(
            inbox,
            "reconcile_storage_publications",
            lambda: events.append("unexpected-publication-reconcile") or 1,
        )

        assert (
            app_main._prepare_storage_for_startup(
                recover_publications=False, recovery_only=True
            )
            is backend
        )
        assert events == ["bind"]

    def test_storage_composition_runs_publication_recovery_after_binding(
        self, _local_storage: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services import inbox

        events: list[str] = []

        class _Backend:
            backend_name = "local"
            capabilities = StorageCapabilities(
                conditional_create=True,
                object_identity=ObjectIdentity.INODE,
                verified_delete=True,
                conditional_replace=True,
                namespace_ownership=True,
                direct_path=True,
            )

            def ensure_setup(self) -> None:
                events.append("ensure")

        backend = _Backend()
        monkeypatch.setattr(app_main, "LocalStorageBackend", lambda: backend)
        monkeypatch.setattr(
            app_main,
            "bind_backend",
            lambda value: events.append("bind") or value,
        )
        monkeypatch.setattr(
            inbox,
            "reconcile_storage_publications",
            lambda: events.append("recover") or 1,
        )

        assert app_main._compose_storage_backend() is backend
        assert events == ["ensure", "bind", "recover"]

    def test_storage_composition_recovers_cover_published_before_restart_binding(
        self, _local_storage: None, db_session
    ) -> None:
        from app.services import source_covers, storage_backend
        from app.services.storage_backend import LocalStorageBackend, get_backend

        model = build_model(
            db_session,
            name="Startup recovery model",
            slug="startup-recovery-model",
            hash="a" * 64,
        )
        source = ModelProvenanceSource(
            model_id=model.id,
            provider="test",
            canonical_url="https://example.test/startup-recovery",
            identity_key="startup-recovery",
        )
        db_session.add(source)
        db_session.commit()

        # The fixture has a local backend bound for service setup. Simulate a
        # process crash after publication and before the caller's receipt commit.
        storage_backend.bind_backend(LocalStorageBackend())
        image = BytesIO()
        Image.new("RGB", (1, 1), "navy").save(image, format="PNG")
        result = source_covers.put(
            db_session,
            get_backend(),
            provenance_source_id=source.id,
            actor_id=None,
            data=image.getvalue(),
            content_type="image/png",
        )
        db_session.rollback()
        assert db_session.exec(select(StagingLease)).all()

        storage_backend._backend = None
        app_main._compose_storage_backend()
        assert db_session.exec(select(StagingLease)).all() == []
        assert (
            db_session.exec(select(OwnedStorageObject)).one().key
            == result.cover.storage_key
        )

    def test_startup_reconciles_completed_capture_slot_after_storage_binding(
        self, _local_storage: None, db_session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A completed browser capture must recover its publication at startup.

        This is deliberately a bounded startup seam test instead of a TestClient
        lifespan portal: the ordering under test is synchronous and should not
        depend on background-task shutdown completing.
        """
        from app.db.session import get_session_factory
        from app.services import inbox, staging_leases, storage_backend
        from app.services.storage_backend import LocalStorageBackend

        owner = build_user(db_session, "startup-capture-owner")
        assert owner.id is not None
        item = InboxItem(
            owner_user_id=owner.id,
            source_kind=InboxSourceKind.BROWSER,
            state=InboxItemState.COMPLETED,
        )
        db_session.add(item)
        db_session.flush()
        assert item.id is not None

        payload = b"completed capture slot publication"
        slot_id = "startup-completed-slot"
        unbound_backend = LocalStorageBackend()
        storage_key = unbound_backend.capture_upload_slot_key(slot_id)
        slot = CaptureUploadSlot(
            id=slot_id,
            inbox_item_id=item.id,
            role="file",
            filename="capture.stl",
            media_type="model/stl",
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            state=CaptureUploadSlotState.PENDING,
            storage_key=storage_key,
        )
        db_session.add(slot)
        db_session.flush()
        staging_leases.create_capture_slot_lease(
            db_session,
            slot_id=slot.id,
            owner_user_id=owner.id,
            destination_key=storage_key,
            size_bytes=slot.size_bytes,
            sha256=slot.sha256,
        )
        db_session.commit()
        Path(storage_key).parent.mkdir(parents=True, exist_ok=True)
        Path(storage_key).write_bytes(payload)

        events: list[str] = []

        class _TrackingBackend(LocalStorageBackend):
            def ensure_setup(self) -> None:
                events.append("ensure")
                super().ensure_setup()

            def adopt_existing(
                self, key: str, *, expected_size: int, expected_sha256: str
            ):
                events.append("recover")
                return super().adopt_existing(
                    key, expected_size=expected_size, expected_sha256=expected_sha256
                )

        backend = _TrackingBackend()
        monkeypatch.setattr(app_main, "LocalStorageBackend", lambda: backend)
        real_bind = storage_backend.bind_backend

        def record_bind(value):
            events.append("bind")
            return real_bind(value)

        monkeypatch.setattr(app_main, "bind_backend", record_bind)

        def record_interrupted_reconcile() -> int:
            events.append("inbox")
            assert storage_backend.get_backend() is backend
            with get_session_factory().scoped_session() as session:
                recovered = session.get(CaptureUploadSlot, slot_id)
                assert recovered is not None
                assert recovered.state == CaptureUploadSlotState.UPLOADED
                assert recovered.receipt_json
            return 0

        monkeypatch.setattr(
            inbox, "reconcile_interrupted_items", record_interrupted_reconcile
        )
        storage_backend._backend = None

        assert app_main._prepare_storage_for_startup() is backend
        assert events == ["ensure", "bind", "recover", "inbox"]


async def _done() -> None:
    pass


def _fake_request(
    path: str = "/x", headers: dict[str, str] | None = None
) -> StarletteRequest:
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": raw_headers,
        "client": ("127.0.0.1", 12345),
        "query_string": b"",
    }
    return StarletteRequest(scope)


class TestCancelTasks:
    @pytest.mark.asyncio
    async def test_cancel_tasks_waits_for_each_task_to_finish_cleaning_up(self) -> None:
        cleaned_up = asyncio.Event()

        async def worker() -> None:
            try:
                await asyncio.Event().wait()
            finally:
                cleaned_up.set()

        task = asyncio.create_task(worker())
        await asyncio.sleep(0)

        await app_main._cancel_tasks(task)

        assert cleaned_up.is_set()
        assert task.cancelled()


class TestCloseOutboundClients:
    @pytest.mark.asyncio
    async def test_close_outbound_clients_closes_provider_after_existing_client_fails(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        events: list[str] = []

        async def close_http_client() -> None:
            events.append("shared")
            raise RuntimeError("shared client failed")

        async def close_provider_transport() -> None:
            events.append("provider")

        import app.services.capture_provider_transport as provider_transport
        import app.services.moonraker as moonraker

        monkeypatch.setattr(moonraker, "close_http_client", close_http_client)
        monkeypatch.setattr(
            provider_transport, "close_provider_transport", close_provider_transport
        )

        with pytest.raises(RuntimeError, match="shared client failed"):
            await app_main._close_outbound_clients()

        assert events == ["shared", "provider"]

    @pytest.mark.asyncio
    async def test_close_outbound_clients_logs_provider_close_failure(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        async def close_provider_transport() -> None:
            raise RuntimeError("provider close failed")

        import app.services.capture_provider_transport as provider_transport
        import app.services.moonraker as moonraker

        monkeypatch.setattr(moonraker, "close_http_client", lambda: _done())
        monkeypatch.setattr(
            provider_transport, "close_provider_transport", close_provider_transport
        )

        with caplog.at_level(logging.ERROR, logger=app_main.logger.name):
            await app_main._close_outbound_clients()

        assert "failed to close capture provider transport" in caplog.text
        assert "RuntimeError" in caplog.text
        assert "provider close failed" not in caplog.text


class TestSafeDbUrl:
    def test_safe_db_url_returns_placeholder_for_unparseable_url(self) -> None:
        # make_url() raises on garbage input; the helper must degrade instead of
        # crashing the startup log line.
        assert app_main._safe_db_url("not a valid :// url") == "<invalid-db-url>"
        assert app_main._safe_db_url("sqlite:///tmp/x.db").endswith("x.db")


class TestLifespan:
    @pytest.mark.asyncio
    async def test_normal_startup_persists_missing_legacy_s3_root_before_composition(
        self, db_session, make_system_config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        make_system_config(storage_backend="s3", s3_bucket="legacy-bucket")
        monkeypatch.setattr(
            app_main.settings._frozen,
            "s3_root",
            "operator-drift",  # noqa: SLF001
        )

        class StopAfterComposition(Exception):
            pass

        observed: dict[str, object] = {}

        def stop_after_overlay(*, recover_publications=True, recovery_only=False):
            with app_main.get_session_factory().scoped_session() as session:
                stored = session.get(SystemConfig, 1)
                observed["root"] = stored.s3_root if stored else None
                observed["overlay_root"] = _overlay.get("s3_root")
            observed["recovery_only"] = recovery_only
            raise StopAfterComposition

        from app.services import storage_paths

        monkeypatch.setattr(
            storage_paths, "validate_runtime_storage_paths", lambda: None
        )
        monkeypatch.setattr(app_main, "acquire_process_lock", lambda: object())
        monkeypatch.setattr(app_main, "init_db", lambda: None)
        monkeypatch.setattr(app_main, "inspect_restore_recovery", lambda: False)
        monkeypatch.setattr(
            app_main, "_prepare_storage_for_startup", stop_after_overlay
        )

        with pytest.raises(StopAfterComposition):
            async with app_main.lifespan(app_main.app):
                pass

        assert observed == {
            "root": "vault-data",
            "overlay_root": "vault-data",
            "recovery_only": False,
        }

    @pytest.mark.asyncio
    async def test_restore_startup_projects_legacy_s3_root_without_persisting(
        self, db_session, make_system_config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        make_system_config(storage_backend="s3", s3_bucket="legacy-bucket")
        monkeypatch.setattr(
            app_main.settings._frozen,
            "s3_root",
            "operator-drift",  # noqa: SLF001
        )

        class StopAfterComposition(Exception):
            pass

        observed: dict[str, object] = {}

        def stop_after_overlay(*, recover_publications=True, recovery_only=False):
            with app_main.get_session_factory().scoped_session() as session:
                stored = session.get(SystemConfig, 1)
                observed["root"] = stored.s3_root if stored else None
                observed["overlay_root"] = _overlay.get("s3_root")
            observed["recovery_only"] = recovery_only
            raise StopAfterComposition

        from app.services import storage_paths

        monkeypatch.setattr(
            storage_paths, "validate_runtime_storage_paths", lambda: None
        )
        monkeypatch.setattr(app_main, "acquire_process_lock", lambda: object())
        monkeypatch.setattr(app_main, "init_db", lambda: None)
        monkeypatch.setattr(app_main, "inspect_restore_recovery", lambda: True)
        monkeypatch.setattr(
            app_main, "_prepare_storage_for_startup", stop_after_overlay
        )

        with pytest.raises(StopAfterComposition):
            async with app_main.lifespan(app_main.app):
                pass

        assert observed == {
            "root": None,
            "overlay_root": "vault-data",
            "recovery_only": True,
        }

    def test_lifespan_keeps_admin_surface_when_sftp_probe_fails(
        self, _local_storage: None, db_session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A lost SFTP root leaves diagnostics reachable and storage fail-closed."""
        from app.services import inbox, runtime_config, storage_opendal
        from app.services.storage_backend import get_backend

        _overlay["storage_backend"] = "sftp"
        _overlay["storage_provider_config"] = (
            '{"provider":"sftp","root":"vault-data",'
            '"host":"sftp.example","port":22,"username":"user",'
            '"host_key":"sftp.example ssh-ed25519 AAAA",'
            '"password":"secret"}'
        )

        class _ProbeFailureBackend:
            backend_name = "sftp"
            capabilities = StorageCapabilities(
                conditional_create=True,
                object_identity=ObjectIdentity.NONE,
                verified_delete=False,
                conditional_replace=False,
                namespace_ownership=True,
                direct_path=False,
            )

            def ensure_setup(self) -> None:
                raise OSError("configured SFTP root is unavailable")

        monkeypatch.setattr(
            storage_opendal,
            "OpenDALStorageBackend",
            lambda _spec: _ProbeFailureBackend(),
        )

        def _unexpected_recovery() -> int:
            raise AssertionError("storage recovery must wait for provider recovery")

        monkeypatch.setattr(
            inbox, "reconcile_storage_publications", _unexpected_recovery
        )
        monkeypatch.setattr(inbox, "reconcile_interrupted_items", _unexpected_recovery)
        _silence_fleet_scheduler(monkeypatch)

        runtime_config.update_storage_provider(
            db_session,
            provider="sftp",
            raw_config={
                "provider": "sftp",
                "root": "vault-data",
                "host": "sftp.example",
                "port": 22,
                "username": "user",
                "host_key": "sftp.example ssh-ed25519 AAAA",
                "password": "secret",
            },
        )

        user = build_user(
            db_session,
            username="sftp-recovery-admin",
            password="Password123",
            active=True,
            superuser=True,
        )
        token = create_access_token(user.id, user.username, scope="admin")
        headers = {"Authorization": f"Bearer {token}"}
        db_session.close()

        from app.main import app

        with TestClient(app) as client:
            liveness = client.get("/api/v1/health")
            details = client.get("/api/v1/health/details", headers=headers)
            config = client.get("/api/v1/config", headers=headers)

            assert liveness.status_code == 200
            assert liveness.json()["storage"]["provider"] == "sftp"
            assert liveness.json()["storage"]["diagnostics"]["available"] is False
            assert details.status_code == 200
            assert details.json()["components"]["storage"]["backend"] == "unavailable"
            assert details.json()["components"]["storage"]["error"] == "OSError"
            assert config.status_code == 200
            assert config.json()["storage_provider"] == "sftp"
            assert config.json()["storage_probe_diagnostics"]["available"] is False

            assert get_backend().backend_name == "unavailable"
            with pytest.raises(StorageConfigurationError, match="storage_unavailable"):
                get_backend().create_stream(BytesIO(b"must not publish"), "ignored")

    def test_lifespan_wires_every_background_task_then_cancels_them_on_shutdown(
        self, _local_storage: None, db_session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.main import app

        # This test deliberately runs the real lifespan against the shared
        # in-memory fixture DB.  Keep its generated installation identity
        # stable so the already-enrolled fixture roots remain bound, while
        # leaving production's persisted-identity path untouched.
        from app.services import runtime_config

        def fixed_identity(_session) -> str:
            _overlay["storage_identity"] = "a" * 64
            return "a" * 64

        monkeypatch.setattr(runtime_config, "ensure_storage_identity", fixed_identity)

        user = build_user(
            db_session,
            username="lifespan-admin",
            password="Password123",
            active=True,
            superuser=True,
        )
        token = create_access_token(user.id, user.username, scope="admin")
        headers = {"Authorization": f"Bearer {token}"}

        with TestClient(app) as client:
            # Background tasks wired onto app.state by the real lifespan, not the
            # per-test fixture shortcut.
            for attr in (
                "printer_hub",
                "library_watcher",
                "gc_task",
                "external_scan_task",
                "notification_task",
                "fleet_scheduler_task",
                "task_queue",
            ):
                assert hasattr(app.state, attr), f"app.state.{attr} not set by lifespan"
            assert isinstance(app.state.printer_hub.bus, InProcessBus)
            assert (
                app.state.printer_hub._session_factory is app_main.get_session_factory()
            )
            assert not app.state.fleet_scheduler_task.done()
            assert not app.state.gc_task.done()

            response = client.get("/api/v1/health/details", headers=headers)
            assert response.status_code == 200
            body = response.json()
            # Not asserting overall body["status"] == "ok": components like backup
            # (none configured) legitimately report degraded on a fresh install —
            # this test is about the scheduler/storage wiring lifespan sets up,
            # not full green health.
            assert body["components"]["fleet_scheduler"]["ok"] is True
            assert body["components"]["fleet_scheduler"]["running"] is True
            assert body["components"]["storage"]["ok"] is True

            liveness = client.get("/api/v1/health")
            assert liveness.status_code == 200

        # Shutdown (TestClient.__exit__) must cancel every background task.
        assert app.state.fleet_scheduler_task.cancelled()
        assert app.state.gc_task.cancelled()
        assert app.state.external_scan_task.cancelled()
        assert app.state.notification_task.cancelled()

    def test_lifespan_warns_once_for_every_nonzero_reconcile_count(
        self,
        _local_storage: None,
        db_session,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        _stub_reconcilers(monkeypatch)
        _silence_fleet_scheduler(monkeypatch)

        from app.main import app

        with caplog.at_level(logging.WARNING, logger=app_main.logger.name):
            with TestClient(app):
                pass

        messages = [record.getMessage() for record in caplog.records]
        assert any("reset 2 external library scan" in m for m in messages)
        assert any("reconciled 3 interrupted background job" in m for m in messages)
        assert any("reconciled 4 interrupted vault audit" in m for m in messages)
        assert any("reconciled 5 interrupted pending import" in m for m in messages)
        assert any("reconciled 6 stranded fleet dispatch" in m for m in messages)

    def test_lifespan_finishes_starting_up_after_the_watcher_fails(
        self,
        _local_storage: None,
        db_session,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        from app.services import library_watcher

        async def _boom_start_all(self):
            raise RuntimeError("watcher init failed")

        monkeypatch.setattr(
            library_watcher.LibraryWatcher, "start_all", _boom_start_all
        )
        _silence_fleet_scheduler(monkeypatch)

        from app.main import app

        with caplog.at_level(logging.WARNING, logger=app_main.logger.name):
            with TestClient(app) as client:
                # Startup completed: the watcher exception was swallowed as
                # best-effort rather than propagating out of lifespan.
                assert client.get("/api/v1/health").status_code == 200
                assert hasattr(app.state, "library_watcher")

        assert any(
            "library watcher failed to start" in record.getMessage()
            for record in caplog.records
        )


def _stub_reconcilers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every reconcile_* call report a distinct nonzero count.

    Distinct so the assertions can tell which warning came from which reconciler —
    identical counts would pass even if one call's message named the wrong thing.
    """
    from app.services import external_library, inbox, jobs, vault_audit

    monkeypatch.setattr(external_library, "reset_orphaned_scans", lambda _session: 2)
    monkeypatch.setattr(jobs, "reconcile_interrupted_jobs", lambda: 3)
    monkeypatch.setattr(vault_audit, "reconcile_interrupted_runs", lambda: 4)
    monkeypatch.setattr(inbox, "reconcile_interrupted_items", lambda: 5)
    monkeypatch.setattr(app_main, "reconcile_stranded_dispatches", lambda: 6)


def _silence_fleet_scheduler(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the scheduler from racing a test's log assertions.

    It keeps the production composition signature, so lifespan still wires it the
    way it really does; it just returns instead of looping.
    """

    async def _noop_scheduler(_task_queue, _provider_builder) -> None:
        return None

    monkeypatch.setattr(app_main, "run_fleet_scheduler", _noop_scheduler)


class TestGcLoop:
    @pytest.mark.asyncio
    async def test_gc_loop_runs_every_step_even_when_each_one_fails(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """_gc_loop runs GC, delivery pruning, and inbox pruning independently —
        one failing must not prevent the other two from running."""
        import asyncio

        from app.services import inbox, notifications

        monkeypatch.setattr(
            app_main,
            "gc_soft_deleted",
            lambda: (_ for _ in ()).throw(RuntimeError("gc fail")),
        )
        monkeypatch.setattr(
            notifications,
            "prune_deliveries",
            lambda: (_ for _ in ()).throw(RuntimeError("prune fail")),
        )
        monkeypatch.setattr(
            inbox,
            "prune_history",
            lambda: (_ for _ in ()).throw(RuntimeError("history fail")),
        )

        with caplog.at_level(logging.ERROR, logger=app_main.logger.name):
            task = asyncio.create_task(app_main._gc_loop())
            # One pass runs immediately (no initial sleep). Each step is a real
            # asyncio.to_thread() round-trip, so a fixed short sleep is flaky
            # under CI load — poll for all three log lines instead, bounded by
            # a generous timeout, before cancelling ahead of sleep(3600).
            deadline = asyncio.get_event_loop().time() + 5
            expected = {
                "scheduled GC failed",
                "notification delivery pruning failed",
                "pending import history pruning failed",
            }
            while asyncio.get_event_loop().time() < deadline:
                messages = [r.getMessage() for r in caplog.records]
                if all(any(exp in m for m in messages) for exp in expected):
                    break
                await asyncio.sleep(0.01)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        messages = [r.getMessage() for r in caplog.records]
        assert any("scheduled GC failed" in m for m in messages)
        assert any("notification delivery pruning failed" in m for m in messages)
        assert any("pending import history pruning failed" in m for m in messages)

    @pytest.mark.asyncio
    async def test_gc_loop_never_runs_storage_maintenance_when_unconfigured(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        called = False
        real_sleep = asyncio.sleep

        def gc() -> None:
            nonlocal called
            called = True

        async def stop_after_first_tick(_seconds: float) -> None:
            await real_sleep(0)
            raise asyncio.CancelledError

        monkeypatch.setattr(app_main, "gc_soft_deleted", gc)
        monkeypatch.setattr(app_main.asyncio, "sleep", stop_after_first_tick)

        with pytest.raises(asyncio.CancelledError):
            await app_main._gc_loop(storage_maintenance_enabled=False)

        assert called is False


class TestExternalScanLoop:
    @pytest.mark.asyncio
    async def test_external_scan_loop_skips_during_restore_then_logs_scan_failure(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """_external_scan_loop must (a) skip a tick entirely while a restore is in
        progress, (b) log-and-continue if the scan tick itself raises, and (c)
        release every admitted mutation slot."""
        import asyncio

        _real_sleep = asyncio.sleep

        # Collapse the loop's per-tick 60s sleep so several iterations happen fast.
        monkeypatch.setattr(app_main.asyncio, "sleep", lambda _s: _real_sleep(0))

        calls = {
            "admission_checks": 0,
            "admitted": 0,
            "scan_calls": 0,
            "mutation_ends": 0,
        }

        def _begin_mutating_operation() -> bool:
            calls["admission_checks"] += 1
            # First tick: restore in progress (must skip). Second+ tick: clear.
            admitted = calls["admission_checks"] > 1
            if admitted:
                calls["admitted"] += 1
            return admitted

        def _end_mutating_operation() -> None:
            calls["mutation_ends"] += 1

        def _run_due_external_scans() -> None:
            calls["scan_calls"] += 1
            raise RuntimeError("scan tick blew up")

        monkeypatch.setattr(
            app_main, "begin_mutating_operation", _begin_mutating_operation
        )
        monkeypatch.setattr(app_main, "end_mutating_operation", _end_mutating_operation)
        monkeypatch.setattr(
            app_main, "_run_due_external_scans", _run_due_external_scans
        )

        with caplog.at_level(logging.ERROR, logger=app_main.logger.name):
            task = asyncio.create_task(app_main._external_scan_loop())
            # Let a few ticks run before cancelling.
            for _ in range(5):
                await asyncio.sleep(0)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        # Skipped on the first tick (restore in progress): scan must not have run then.
        assert calls["admission_checks"] >= 2
        assert calls["scan_calls"] >= 1
        # Cancellation may land after admission but before the worker thread starts;
        # every admitted slot must still be released exactly once.
        assert calls["mutation_ends"] == calls["admitted"]
        assert any(
            "external library scan tick failed" in r.getMessage()
            for r in caplog.records
        )


class TestRunDueExternalScans:
    def test_run_due_external_scans_noop_when_disabled(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.services import external_library, runtime_config

        monkeypatch.setattr(
            runtime_config, "external_libraries_enabled", lambda _session: False
        )
        called = {"scan": False}
        monkeypatch.setattr(
            external_library,
            "scan_library",
            lambda _id: called.__setitem__("scan", True),
        )

        app_main._run_due_external_scans()

        assert called["scan"] is False

    def test_runs_every_due_scan_even_after_one_of_them_fails(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        from app.services import external_library, runtime_config

        monkeypatch.setattr(
            runtime_config, "external_libraries_enabled", lambda _session: True
        )
        monkeypatch.setattr(
            external_library, "libraries_due_for_scan", lambda _session: [1, 2]
        )
        scanned: list[int] = []

        def _scan(library_id: int) -> None:
            scanned.append(library_id)
            if library_id == 1:
                raise RuntimeError("scan blew up")

        monkeypatch.setattr(external_library, "scan_library", _scan)

        with caplog.at_level(logging.ERROR, logger=app_main.logger.name):
            app_main._run_due_external_scans()

        # Both libraries are attempted even though the first raised.
        assert scanned == [1, 2]
        assert any(
            "scheduled scan failed for library 1" in r.getMessage()
            for r in caplog.records
        )


class TestParseCorsOrigins:
    def test_drops_blank_entries_from_a_list_of_origins(self) -> None:
        origins = app_main._parse_cors_origins(
            ["http://a.example", "  ", "http://b.example"]
        )

        assert origins == ["http://a.example", "http://b.example"]

    def test_returns_no_origins_for_a_value_that_is_not_a_list(self) -> None:
        assert app_main._parse_cors_origins(42) == []


class TestUnhandledExceptionHandler:
    @pytest.mark.asyncio
    async def test_unhandled_exception_handler_logs_traceback_in_debug(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        _overlay["log_level"] = "DEBUG"
        try:
            request = _fake_request(path="/boom")
            with caplog.at_level(logging.ERROR, logger=app_main.logger.name):
                response = await app_main.unhandled_exception_handler(
                    request, ValueError("boom")
                )
        finally:
            _overlay.pop("log_level", None)
        assert response.status_code == 500
        # DEBUG branch logs with a traceback (exc_info); confirm it actually ran
        # logger.exception, not just logger.error.
        assert any(record.exc_info for record in caplog.records)

    @pytest.mark.asyncio
    async def test_unhandled_exception_handler_logs_summary_outside_debug(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        _overlay["log_level"] = "INFO"
        try:
            request = _fake_request(path="/boom")
            with caplog.at_level(logging.ERROR, logger=app_main.logger.name):
                response = await app_main.unhandled_exception_handler(
                    request, ValueError("boom")
                )
        finally:
            _overlay.pop("log_level", None)
        assert response.status_code == 500
        # Non-debug branch logs the error class name, no traceback.
        assert not any(record.exc_info for record in caplog.records)
        assert any("ValueError" in record.getMessage() for record in caplog.records)


class TestBindAuditContext:
    @pytest.mark.asyncio
    async def test_bind_audit_context_ignores_non_numeric_token_sub(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: dict = {}

        def _fake_set_audit_context(*, actor_id, ip):
            captured["actor_id"] = actor_id
            captured["ip"] = ip

        monkeypatch.setattr(app_main, "set_audit_context", _fake_set_audit_context)
        monkeypatch.setattr(app_main, "clear_audit_context", lambda: None)

        import app.services.auth as auth_module

        monkeypatch.setattr(
            auth_module, "verify_access_token", lambda _token: {"sub": "not-an-int"}
        )

        request = _fake_request(headers={"authorization": "Bearer whatever"})

        async def call_next(_request):
            from starlette.responses import Response

            return Response(status_code=200)

        response = await app_main.bind_audit_context(request, call_next)
        assert response.status_code == 200
        # A non-numeric "sub" claim must not blow up the middleware; actor_id
        # falls back to None instead of propagating a ValueError/TypeError.
        assert captured["actor_id"] is None


class TestRefreshPrinterGauge:
    def test_refresh_printer_gauge_populates_from_db(self, db_session) -> None:
        build_printer(
            db_session,
            name="Gauge Printer",
            moonraker_url="http://gauge.local:7125",
            provider=PrinterProvider.MOONRAKER,
            status=PrinterStatus.READY,
        )

        app_main._refresh_printer_gauge()

        sample_found = any(
            sample.labels.get("provider") == "moonraker"
            and sample.labels.get("status") == "ready"
            and sample.value >= 1
            for metric in app_main.printer_status.collect()
            for sample in metric.samples
        )
        assert sample_found

    def test_refresh_printer_gauge_survives_db_error(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        def _boom():
            raise RuntimeError("db down")

        monkeypatch.setattr(app_main, "get_session_factory", _boom)
        with caplog.at_level(logging.ERROR, logger=app_main.logger.name):
            app_main._refresh_printer_gauge()  # must not raise
        assert any(
            "failed to refresh printer gauge" in r.getMessage() for r in caplog.records
        )


class TestRefreshFleetGauges:
    def test_refresh_fleet_gauges_survives_db_error(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        def _boom():
            raise RuntimeError("db down")

        monkeypatch.setattr(app_main, "get_session_factory", _boom)
        with caplog.at_level(logging.ERROR, logger=app_main.logger.name):
            app_main._refresh_fleet_gauges()  # must not raise
        assert any(
            "failed to refresh fleet gauges" in r.getMessage() for r in caplog.records
        )
