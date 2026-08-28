"""Defends lifespan starts background tasks and shuts down cleanly at the db models integration boundary.

A regression could commit partial, unauthenticated, or internally inconsistent database state.
"""

from __future__ import annotations

from ._main_lifespan_shared import (
    BytesIO,
    CaptureUploadSlot,
    CaptureUploadSlotState,
    Image,
    InboxItem,
    InboxItemState,
    InboxSourceKind,
    InProcessBus,
    Model,
    ModelProvenanceSource,
    ObjectIdentity,
    OwnedStorageObject,
    Path,
    Printer,
    PrinterProvider,
    PrinterStatus,
    StagingLease,
    StorageCapabilities,
    TestClient,
    User,
    _done,
    _fake_request,
    _overlay,
    app_main,
    asyncio,
    create_access_token,
    hash_password,
    hashlib,
    logging,
    pytest,
    select,
    storage_backend,
)


def test_lifespan_starts_background_tasks_and_shuts_down_cleanly(
    _local_storage: None, db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.main import app

    user = User(
        username="lifespan-admin",
        hashed_password=hash_password("Password123"),
        is_active=True,
        is_superuser=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
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
        assert app.state.printer_hub._session_factory is app_main.get_session_factory()
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


def test_safe_db_url_returns_placeholder_for_unparseable_url() -> None:
    # make_url() raises on garbage input; the helper must degrade instead of
    # crashing the startup log line.
    assert app_main._safe_db_url("not a valid :// url") == "<invalid-db-url>"
    assert app_main._safe_db_url("sqlite:///tmp/x.db").endswith("x.db")


def test_storage_composition_runs_publication_recovery_after_binding(
    _local_storage: None, monkeypatch: pytest.MonkeyPatch
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


def test_storage_composition_rejects_unacknowledged_unguarded_backend(
    _local_storage: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = storage_backend.LocalStorageBackend()
    backend._capabilities = StorageCapabilities(
        conditional_create=False,
        object_identity=ObjectIdentity.NONE,
        verified_delete=False,
        conditional_replace=False,
        namespace_ownership=True,
        direct_path=False,
    )
    monkeypatch.setattr(backend, "ensure_setup", lambda: None)
    monkeypatch.setattr(app_main, "LocalStorageBackend", lambda: backend)

    with pytest.raises(RuntimeError, match="VAULT_STORAGE_ALLOW_UNVERIFIED=true"):
        app_main._compose_storage_backend()


def test_storage_composition_accepts_acknowledged_unguarded_backend(
    _local_storage: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import inbox

    backend = storage_backend.LocalStorageBackend()
    backend._capabilities = StorageCapabilities(
        conditional_create=False,
        object_identity=ObjectIdentity.NONE,
        verified_delete=False,
        conditional_replace=False,
        namespace_ownership=True,
        direct_path=False,
    )
    monkeypatch.setattr(backend, "ensure_setup", lambda: None)
    monkeypatch.setattr(app_main, "LocalStorageBackend", lambda: backend)
    monkeypatch.setattr(app_main, "bind_backend", lambda value: value)
    monkeypatch.setattr(inbox, "reconcile_storage_publications", lambda: 0)
    monkeypatch.setitem(_overlay, "storage_allow_unverified", True)

    assert app_main._compose_storage_backend() is backend


def test_storage_composition_warns_without_blocking_guarded_backend(
    _local_storage: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from app.services import inbox

    backend = storage_backend.LocalStorageBackend()
    backend._capabilities = StorageCapabilities(
        conditional_create=True,
        object_identity=ObjectIdentity.ETAG,
        verified_delete=False,
        conditional_replace=False,
        namespace_ownership=True,
        direct_path=False,
    )
    monkeypatch.setattr(backend, "ensure_setup", lambda: None)
    monkeypatch.setattr(app_main, "LocalStorageBackend", lambda: backend)
    monkeypatch.setattr(app_main, "bind_backend", lambda value: value)
    monkeypatch.setattr(inbox, "reconcile_storage_publications", lambda: 0)

    with caplog.at_level("WARNING"):
        assert app_main._compose_storage_backend() is backend

    assert "storage capability warning" in caplog.text
    assert "cannot conditionally replace" in caplog.text


def test_storage_composition_recovers_cover_published_before_restart_binding(
    _local_storage: None, db_session
) -> None:
    from app.services import source_covers, storage_backend
    from app.services.storage_backend import LocalStorageBackend, get_backend

    model = Model(
        name="Startup recovery model",
        slug="startup-recovery-model",
        hash="a" * 64,
    )
    db_session.add(model)
    db_session.flush()
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
    _local_storage: None, db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A completed browser capture must recover its publication at startup.

    This is deliberately a bounded startup seam test instead of a TestClient
    lifespan portal: the ordering under test is synchronous and should not
    depend on background-task shutdown completing.
    """
    from app.db.session import get_session_factory
    from app.services import inbox, staging_leases, storage_backend
    from app.services.storage_backend import LocalStorageBackend

    owner = User(username="startup-capture-owner", hashed_password="hash")
    db_session.add(owner)
    db_session.flush()
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

        def adopt_existing(self, key: str, *, expected_size: int, expected_sha256: str):
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


def test_parse_cors_origins_accepts_list_and_filters_blanks() -> None:
    assert app_main._parse_cors_origins(
        ["http://a.example", "  ", "http://b.example"]
    ) == ["http://a.example", "http://b.example"]
    assert app_main._parse_cors_origins(42) == []


@pytest.mark.asyncio
async def test_cancel_tasks_awaits_cleanup_and_consumes_cancellation() -> None:
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


@pytest.mark.asyncio
async def test_close_outbound_clients_closes_provider_after_existing_client_fails(
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
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
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


@pytest.mark.asyncio
async def test_unhandled_exception_handler_logs_traceback_in_debug(
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


@pytest.mark.asyncio
async def test_bind_audit_context_ignores_non_numeric_token_sub(
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


def test_refresh_printer_gauge_populates_from_db(db_session) -> None:
    printer = Printer(
        name="Gauge Printer",
        moonraker_url="http://gauge.local:7125",
        provider=PrinterProvider.MOONRAKER,
        status=PrinterStatus.READY,
    )
    db_session.add(printer)
    db_session.commit()

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
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(app_main, "get_session_factory", _boom)
    with caplog.at_level(logging.ERROR, logger=app_main.logger.name):
        app_main._refresh_printer_gauge()  # must not raise
    assert any(
        "failed to refresh printer gauge" in r.getMessage() for r in caplog.records
    )


def test_refresh_fleet_gauges_survives_db_error(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(app_main, "get_session_factory", _boom)
    with caplog.at_level(logging.ERROR, logger=app_main.logger.name):
        app_main._refresh_fleet_gauges()  # must not raise
    assert any(
        "failed to refresh fleet gauges" in r.getMessage() for r in caplog.records
    )


def test_run_due_external_scans_noop_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import external_library, runtime_config

    monkeypatch.setattr(
        runtime_config, "external_libraries_enabled", lambda _session: False
    )
    called = {"scan": False}
    monkeypatch.setattr(
        external_library, "scan_library", lambda _id: called.__setitem__("scan", True)
    )

    app_main._run_due_external_scans()

    assert called["scan"] is False


def test_run_due_external_scans_logs_and_continues_on_scan_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
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
        "scheduled scan failed for library 1" in r.getMessage() for r in caplog.records
    )
