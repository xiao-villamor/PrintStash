"""Boots the real app lifespan (startup + shutdown), not just handler-level tests.

Every other test in the suite gets its FastAPI ``app`` fixture pre-wired
(``app.state.printer_hub`` set manually, no ``with TestClient(app) as client``),
so ``app/main.py``'s ``lifespan()`` — DB init, storage init, background task
wiring, graceful shutdown — had no direct coverage (58% per the 0.11 audit).
This starts it for real via Starlette's TestClient context-manager protocol.
"""

from __future__ import annotations

import hashlib
import logging
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import select
from starlette.requests import Request as StarletteRequest

import app.bootstrap.lifecycle as lifecycle
import app.main as app_main
import app.modules.storage.storage_backend.runtime as storage_runtime
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
    User,
)
from app.modules.identity.auth import create_access_token
from app.modules.storage.storage_backend.contracts import (
    ObjectIdentity,
    StorageCapabilities,
    StorageConfigurationError,
)
from app.runtime.realtime import InProcessBus
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
    monkeypatch.setattr(storage_runtime, "_backend", None)
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
    def test_invalid_provider_keeps_storage_unavailable(self, monkeypatch):
        monkeypatch.setitem(
            _overlay, "storage_provider_error", "invalid_provider_config"
        )

        backend = lifecycle._compose_storage_backend()

        assert backend.backend_name == "unavailable"
        with pytest.raises(StorageConfigurationError, match="storage_unavailable"):
            backend.create_stream(BytesIO(b"must not publish"), "never-created.stl")

    @pytest.mark.parametrize(
        "provider",
        [
            "synology_webdav",
            "qnap_webdav",
            "hetzner_storage_box",
            "hetzner_storage_box_webdav",
            "koofr",
        ],
    )
    def test_persisted_preset_restarts_with_its_remote_transport(
        self,
        provider: str,
        _local_storage: None,
        db_session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.modules.administration import runtime_config
        from app.modules.storage import storage_opendal
        from app.modules.storage.storage_providers import PRESETS
        from tests.fixtures.storage_presets import preset_configuration

        runtime_config.update_storage_provider(
            db_session,
            provider=provider,
            raw_config=preset_configuration(provider),
            apply_runtime=False,
        )
        runtime_config.apply_overlay(db_session)
        observed = []

        def remote_backend(spec):
            observed.append(spec)
            return storage_opendal_backend(spec)

        storage_opendal_backend = storage_opendal.OpenDALStorageBackend
        monkeypatch.setattr(storage_opendal, "OpenDALStorageBackend", remote_backend)
        backend = lifecycle._compose_storage_backend(
            recovery_only=True,
            recover_publications=False,
        )
        assert backend.backend_name == provider
        assert len(observed) == 1
        assert observed[0].kind.value == PRESETS[provider]["transport"]
        assert observed[0].provider == provider
        assert observed[0].options["root"] == "presets"

    def test_provider_probe_failure_binds_unavailable_recovery_backend(
        self, _local_storage: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A missing remote root leaves health/config reachable and mutations closed."""
        from app.modules.storage import storage_opendal

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

        backend = lifecycle._prepare_storage_for_startup(recover_publications=True)

        assert backend.backend_name == "unavailable"
        assert backend.health_probe()["ok"] is False
        with pytest.raises(StorageConfigurationError, match="storage_unavailable"):
            backend.create_stream(BytesIO(b"payload"), "ignored")

    def test_recovery_composition_skips_setup_probes(
        self, _local_storage: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An unresolved restore binds read/recovery access without startup I/O."""
        from app.modules.ingestion import inbox

        events: list[str] = []

        class _RecoveryBackend(lifecycle.LocalStorageBackend):
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
        monkeypatch.setattr(lifecycle, "LocalStorageBackend", lambda: backend)
        monkeypatch.setattr(
            lifecycle,
            "bind_backend",
            lambda value: events.append("bind") or value,
        )
        monkeypatch.setattr(
            inbox,
            "reconcile_storage_publications",
            lambda: events.append("unexpected-publication-reconcile") or 1,
        )

        assert (
            lifecycle._prepare_storage_for_startup(
                recover_publications=False, recovery_only=True
            )
            is backend
        )
        assert events == ["bind"]

    def test_storage_composition_runs_publication_recovery_after_binding(
        self, _local_storage: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.modules.ingestion import inbox

        events: list[str] = []

        class _Backend(lifecycle.LocalStorageBackend):
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
        monkeypatch.setattr(lifecycle, "LocalStorageBackend", lambda: backend)
        monkeypatch.setattr(
            lifecycle,
            "bind_backend",
            lambda value: events.append("bind") or value,
        )
        monkeypatch.setattr(
            inbox,
            "reconcile_storage_publications",
            lambda: events.append("recover") or 1,
        )

        assert lifecycle._compose_storage_backend() is backend
        assert events == ["ensure", "bind", "recover"]

    def test_storage_composition_recovers_cover_published_before_restart_binding(
        self, _local_storage: None, db_session
    ) -> None:
        from app.modules.library import source_covers
        from app.modules.storage.storage_backend.local import LocalStorageBackend
        from app.modules.storage.storage_backend.runtime import get_backend

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
        storage_runtime.bind_backend(LocalStorageBackend())
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

        storage_runtime._backend = None
        lifecycle._compose_storage_backend()
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
        from app.modules.ingestion import inbox, staging_leases
        from app.modules.storage.storage_backend.local import LocalStorageBackend

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
        monkeypatch.setattr(lifecycle, "LocalStorageBackend", lambda: backend)
        real_bind = storage_runtime.bind_backend

        def record_bind(value):
            events.append("bind")
            return real_bind(value)

        monkeypatch.setattr(lifecycle, "bind_backend", record_bind)

        def record_interrupted_reconcile() -> int:
            events.append("inbox")
            assert storage_runtime.get_backend() is backend
            with get_session_factory().scoped_session() as session:
                recovered = session.get(CaptureUploadSlot, slot_id)
                assert recovered is not None
                assert recovered.state == CaptureUploadSlotState.UPLOADED
                assert recovered.receipt_json
            return 0

        monkeypatch.setattr(
            inbox, "reconcile_interrupted_items", record_interrupted_reconcile
        )
        storage_runtime._backend = None

        assert lifecycle._prepare_storage_for_startup() is backend
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

        import app.modules.ingestion.capture_provider_transport as provider_transport
        import app.modules.printing.moonraker as moonraker

        monkeypatch.setattr(moonraker, "close_http_client", close_http_client)
        monkeypatch.setattr(
            provider_transport, "close_provider_transport", close_provider_transport
        )

        with pytest.raises(RuntimeError, match="shared client failed"):
            await lifecycle._close_outbound_clients()

        assert events == ["shared", "provider"]

    @pytest.mark.asyncio
    async def test_close_outbound_clients_logs_provider_close_failure(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        async def close_provider_transport() -> None:
            raise RuntimeError("provider close failed")

        import app.modules.ingestion.capture_provider_transport as provider_transport
        import app.modules.printing.moonraker as moonraker

        monkeypatch.setattr(moonraker, "close_http_client", lambda: _done())
        monkeypatch.setattr(
            provider_transport, "close_provider_transport", close_provider_transport
        )

        with caplog.at_level(logging.ERROR, logger=lifecycle.logger.name):
            await lifecycle._close_outbound_clients()

        assert "failed to close capture provider transport" in caplog.text
        assert "RuntimeError" in caplog.text
        assert "provider close failed" not in caplog.text


class TestSafeDbUrl:
    def test_safe_db_url_returns_placeholder_for_unparseable_url(self) -> None:
        # make_url() raises on garbage input; the helper must degrade instead of
        # crashing the startup log line.
        assert lifecycle._safe_db_url("not a valid :// url") == "<invalid-db-url>"
        assert lifecycle._safe_db_url("sqlite:///tmp/x.db").endswith("x.db")


@pytest.fixture
def startup_until_storage_binding(monkeypatch: pytest.MonkeyPatch):
    """Run startup up to storage binding and report the accounts it left behind."""
    from app.modules.storage import storage_paths

    class StopAtStorageBinding(Exception):
        pass

    observed: dict[str, list[str]] = {}

    def stop(*, recover_publications=True, recovery_only=False):
        with lifecycle.get_session_factory().scoped_session() as session:
            observed["users"] = [user.username for user in session.exec(select(User))]
        raise StopAtStorageBinding

    monkeypatch.setattr(storage_paths, "validate_runtime_storage_paths", lambda: None)
    monkeypatch.setattr(lifecycle, "acquire_process_lock", lambda: object())
    monkeypatch.setattr(lifecycle, "init_db", lambda: None)
    monkeypatch.setattr(lifecycle, "_prepare_storage_for_startup", stop)

    async def run(*, restore: bool) -> list[str]:
        monkeypatch.setattr(lifecycle, "inspect_restore_recovery", lambda: restore)
        with pytest.raises(StopAtStorageBinding):
            async with lifecycle.lifespan(app_main.app):
                pass
        return observed["users"]

    return run


class TestLifespan:
    @pytest.mark.asyncio
    async def test_normal_startup_persists_missing_legacy_s3_root_before_composition(
        self, db_session, make_system_config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        make_system_config(storage_backend="s3", s3_bucket="legacy-bucket")
        monkeypatch.setattr(
            lifecycle.settings._frozen,
            "s3_root",
            "operator-drift",  # noqa: SLF001
        )

        class StopAfterComposition(Exception):
            pass

        observed: dict[str, object] = {}

        def stop_after_overlay(*, recover_publications=True, recovery_only=False):
            with lifecycle.get_session_factory().scoped_session() as session:
                stored = session.get(SystemConfig, 1)
                observed["root"] = stored.s3_root if stored else None
                observed["overlay_root"] = _overlay.get("s3_root")
            observed["recovery_only"] = recovery_only
            raise StopAfterComposition

        from app.modules.storage import storage_paths

        monkeypatch.setattr(
            storage_paths, "validate_runtime_storage_paths", lambda: None
        )
        monkeypatch.setattr(lifecycle, "acquire_process_lock", lambda: object())
        monkeypatch.setattr(lifecycle, "init_db", lambda: None)
        monkeypatch.setattr(lifecycle, "inspect_restore_recovery", lambda: False)
        monkeypatch.setattr(
            lifecycle, "_prepare_storage_for_startup", stop_after_overlay
        )

        with pytest.raises(StopAfterComposition):
            async with lifecycle.lifespan(app_main.app):
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
            lifecycle.settings._frozen,
            "s3_root",
            "operator-drift",  # noqa: SLF001
        )

        class StopAfterComposition(Exception):
            pass

        observed: dict[str, object] = {}

        def stop_after_overlay(*, recover_publications=True, recovery_only=False):
            with lifecycle.get_session_factory().scoped_session() as session:
                stored = session.get(SystemConfig, 1)
                observed["root"] = stored.s3_root if stored else None
                observed["overlay_root"] = _overlay.get("s3_root")
            observed["recovery_only"] = recovery_only
            raise StopAfterComposition

        from app.modules.storage import storage_paths

        monkeypatch.setattr(
            storage_paths, "validate_runtime_storage_paths", lambda: None
        )
        monkeypatch.setattr(lifecycle, "acquire_process_lock", lambda: object())
        monkeypatch.setattr(lifecycle, "init_db", lambda: None)
        monkeypatch.setattr(lifecycle, "inspect_restore_recovery", lambda: True)
        monkeypatch.setattr(
            lifecycle, "_prepare_storage_for_startup", stop_after_overlay
        )

        with pytest.raises(StopAfterComposition):
            async with lifecycle.lifespan(app_main.app):
                pass

        assert observed == {
            "root": None,
            "overlay_root": "vault-data",
            "recovery_only": True,
        }

    @pytest.mark.asyncio
    async def test_provisions_the_environment_administrator_before_binding_storage(
        self, db_session, environment_admin, startup_until_storage_binding
    ) -> None:
        environment_admin("store-owner", "StoreFormPassword123")

        users = await startup_until_storage_binding(restore=False)

        assert users == ["store-owner"]

    @pytest.mark.asyncio
    async def test_never_provisions_during_restore_maintenance(
        self, db_session, environment_admin, startup_until_storage_binding
    ) -> None:
        # A restore is rebuilding the database the owner would be written to.
        environment_admin("store-owner", "StoreFormPassword123")

        users = await startup_until_storage_binding(restore=True)

        assert users == []

    def test_lifespan_keeps_admin_surface_when_sftp_probe_fails(
        self, _local_storage: None, db_session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A lost SFTP root leaves diagnostics reachable and storage fail-closed."""
        from app.modules.administration import runtime_config
        from app.modules.ingestion import inbox
        from app.modules.storage import storage_opendal
        from app.modules.storage.storage_backend.runtime import get_backend

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
        from app.modules.administration import runtime_config

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
            # Supervisors and the event bus wired onto app.state by the real
            # lifespan, not the per-test fixture shortcut.
            for attr in ("printer_hub", "library_watcher", "event_bus"):
                assert hasattr(app.state, attr), f"app.state.{attr} not set by lifespan"
            assert isinstance(app.state.printer_hub.bus, InProcessBus)
            assert (
                app.state.printer_hub._session_factory
                is lifecycle.get_session_factory()
            )

            response = client.get("/api/v1/health/details", headers=headers)
            assert response.status_code == 200
            body = response.json()
            # Not asserting overall body["status"] == "ok": components like backup
            # (none configured) legitimately report degraded on a fresh install —
            # this test is about the wiring lifespan sets up, not full green health.
            assert body["components"]["fleet_scheduler"]["running"] is True
            assert body["components"]["storage"]["ok"] is True

            liveness = client.get("/api/v1/health")
            assert liveness.status_code == 200

    def test_lifespan_starts_background_work(
        self, _local_storage: None, db_session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.bootstrap import work as work_bootstrap
        from app.main import app
        from app.modules.administration import runtime_config

        monkeypatch.setattr(
            runtime_config, "ensure_storage_identity", _fixed_storage_identity
        )

        with TestClient(app):
            runtime = work_bootstrap.current()
            launched = runtime is not None and runtime.engine.launched

        assert launched is True

    def test_lifespan_stops_background_work_on_shutdown(
        self,
        _local_storage: None,
        db_session,
        monkeypatch: pytest.MonkeyPatch,
        work_engine,
    ) -> None:
        from app.bootstrap import work as work_bootstrap
        from app.main import app
        from app.modules.administration import runtime_config

        monkeypatch.setattr(
            runtime_config, "ensure_storage_identity", _fixed_storage_identity
        )

        with TestClient(app):
            pass

        assert (work_bootstrap.current(), work_engine.launched) == (None, False)

    def test_lifespan_holds_background_work_while_a_restore_is_unresolved(
        self, _local_storage: None, db_session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.bootstrap import work as work_bootstrap
        from app.main import app

        monkeypatch.setattr(lifecycle, "inspect_restore_recovery", lambda: True)

        with TestClient(app):
            held = work_bootstrap.current() is None

        # The restore journal governs: no Job may run against a database that
        # is about to be replaced or is mid-recovery.
        assert held is True

    def test_lifespan_starts_held_work_once_recovery_resolves_the_restore(
        self, _local_storage: None, db_session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Nothing restarts the process after recovery, so the work it held
        # starts in place; otherwise every nudge would reach no engine.
        from app.bootstrap import work as work_bootstrap
        from app.main import app
        from app.runtime.maintenance import end_restore_maintenance

        monkeypatch.setattr(lifecycle, "inspect_restore_recovery", lambda: True)

        with TestClient(app):
            end_restore_maintenance()
            released = work_bootstrap.release_held()
            runtime = work_bootstrap.current()
            launched = runtime is not None and runtime.engine.launched

        assert (released, launched) == (True, True)

    def test_lifespan_finishes_starting_up_after_the_watcher_fails(
        self,
        _local_storage: None,
        db_session,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        from app.modules.sources import library_watcher

        async def _boom_start_all(self):
            raise RuntimeError("watcher init failed")

        monkeypatch.setattr(
            library_watcher.LibraryWatcher, "start_all", _boom_start_all
        )

        from app.main import app

        with caplog.at_level(logging.WARNING, logger=lifecycle.logger.name):
            with TestClient(app) as client:
                # Startup completed: the watcher exception was swallowed as
                # best-effort rather than propagating out of lifespan.
                assert client.get("/api/v1/health").status_code == 200
                assert hasattr(app.state, "library_watcher")

        assert any(
            "library watcher failed to start" in record.getMessage()
            for record in caplog.records
        )


def _fixed_storage_identity(_session) -> str:
    """Keep the fixture roots bound: the lifespan runs on the shared test DB."""
    _overlay["storage_identity"] = "a" * 64
    return "a" * 64


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
            with caplog.at_level(logging.ERROR, logger=lifecycle.logger.name):
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
            with caplog.at_level(logging.ERROR, logger=lifecycle.logger.name):
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

        import app.modules.identity.auth as auth_module

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
