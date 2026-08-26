"""Defends list runs orders newest first and respects limit at the services vault audit integration boundary.

A regression could miss corruption or repair ownership and metadata incorrectly.
"""

from __future__ import annotations

from ._vault_audit_internals_shared import (
    BackgroundJob,
    ExternalLibrary,
    FileType,
    InboxItem,
    InboxItemState,
    OwnedBlob,
    Session,
    VaultAuditFinding,
    VaultAuditMode,
    VaultAuditRun,
    VaultAuditRunState,
    _make_file,
    _make_model,
    _make_run,
    _make_user,
    get_backend,
    pytest,
    timedelta,
    utcnow,
    vault_audit,
)


def test_list_runs_orders_newest_first_and_respects_limit(db_session: Session) -> None:
    user = _make_user(db_session, "runs-owner")
    older = _make_run(db_session, user)
    older.created_at = utcnow() - timedelta(hours=1)
    db_session.add(older)
    db_session.commit()
    newer = _make_run(db_session, user)

    rows = vault_audit.list_runs(db_session, limit=1)

    assert len(rows) == 1
    assert rows[0].id == newer.id


def test_latest_run_returns_most_recent(db_session: Session) -> None:
    user = _make_user(db_session, "latest-owner")
    _make_run(db_session, user)
    newest = _make_run(db_session, user)

    result = vault_audit.latest_run(db_session)

    assert result is not None
    assert result.id == newest.id


def test_request_cancel_flags_active_run(db_session: Session) -> None:
    user = _make_user(db_session, "cancel-owner")
    run = _make_run(db_session, user)

    result = vault_audit.request_cancel(db_session, run.id)

    assert result is not None
    assert result.cancel_requested is True


def test_request_cancel_ignores_terminal_run(db_session: Session) -> None:
    user = _make_user(db_session, "cancel-owner2")
    run = _make_run(db_session, user)
    run.state = VaultAuditRunState.COMPLETED
    db_session.add(run)
    db_session.commit()

    result = vault_audit.request_cancel(db_session, run.id)

    assert result is not None
    assert result.cancel_requested is False


def test_request_cancel_missing_run_returns_none(db_session: Session) -> None:
    assert vault_audit.request_cancel(db_session, 999999) is None


def test_reconcile_interrupted_runs_marks_running_as_failed(
    db_session: Session,
) -> None:
    user = _make_user(db_session, "reconcile-owner")
    run = _make_run(db_session, user)
    run.state = VaultAuditRunState.RUNNING
    db_session.add(run)
    db_session.commit()
    run_id = run.id

    count = vault_audit.reconcile_interrupted_runs()

    assert count >= 1
    result = db_session.get(VaultAuditRun, run_id)
    db_session.refresh(result)
    assert result.state == VaultAuditRunState.FAILED
    assert result.error_code == "audit_interrupted"


def test_check_primary_flags_size_and_hash_mismatch(db_session: Session) -> None:
    user = _make_user(db_session, "primary-owner")
    run = _make_run(db_session, user, VaultAuditMode.FULL)
    get_backend().write_bytes(b"actual-bytes", "size-mismatch.stl")
    get_backend().write_bytes(b"hash-mismatch-content", "hash-mismatch.stl")
    blobs = [
        OwnedBlob(
            key="size-mismatch.stl",
            resource_type="file",
            resource_id=1,
            expected_size=999,
        ),
        OwnedBlob(
            key="hash-mismatch.stl",
            resource_type="file",
            resource_id=2,
            expected_sha256="0" * 64,
        ),
    ]

    completed = vault_audit._check_primary(db_session, run, blobs)

    assert completed is True
    codes = {
        finding.code
        for finding in db_session.exec(
            __import__("sqlmodel")
            .select(VaultAuditFinding)
            .where(VaultAuditFinding.run_id == run.id)
        ).all()
    }
    assert "owned_blob_size_mismatch" in codes
    assert "owned_blob_hash_mismatch" in codes


def test_check_primary_unreadable_blob_becomes_finding(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _make_user(db_session, "primary-owner2")
    run = _make_run(db_session, user)
    get_backend().write_bytes(b"data", "unreadable.stl")

    def boom(_key: str) -> int:
        raise OSError("disk exploded")

    monkeypatch.setattr(get_backend(), "stat_size", boom)

    completed = vault_audit._check_primary(
        db_session,
        run,
        [OwnedBlob(key="unreadable.stl", resource_type="file", resource_id=3)],
    )

    assert completed is True
    from sqlmodel import select

    findings = db_session.exec(
        select(VaultAuditFinding).where(VaultAuditFinding.run_id == run.id)
    ).all()
    assert any(f.code == "owned_blob_unreadable" for f in findings)


def test_check_primary_stops_when_cancelled(db_session: Session) -> None:
    user = _make_user(db_session, "primary-owner3")
    run = _make_run(db_session, user)
    run.cancel_requested = True
    db_session.add(run)
    db_session.commit()

    completed = vault_audit._check_primary(
        db_session,
        run,
        [OwnedBlob(key="whatever.stl", resource_type="file", resource_id=4)],
    )

    assert completed is False
    db_session.refresh(run)
    assert run.state == VaultAuditRunState.CANCELLED


def test_check_primary_skips_trashed_artifacts(db_session: Session) -> None:
    user = _make_user(db_session, "primary-trashed")
    run = _make_run(db_session, user)
    model = _make_model(db_session, "trashed-artifact")
    file_row = _make_file(db_session, model, path="missing-from-trash.stl")
    model.deleted_at = utcnow()
    db_session.add(model)
    db_session.commit()

    completed = vault_audit._check_primary(
        db_session,
        run,
        [
            OwnedBlob(
                key=file_row.path,
                resource_type="file",
                resource_id=file_row.id,
                display_name=file_row.original_filename,
            )
        ],
    )

    assert completed is True
    from sqlmodel import select

    findings = db_session.exec(
        select(VaultAuditFinding).where(VaultAuditFinding.run_id == run.id)
    ).all()
    assert findings == []


def test_check_database_flags_model_without_live_artifact(db_session: Session) -> None:
    user = _make_user(db_session, "db-owner")
    run = _make_run(db_session, user)
    _make_model(db_session, "no-files")

    vault_audit._check_database(db_session, run)

    from sqlmodel import select

    findings = db_session.exec(
        select(VaultAuditFinding).where(VaultAuditFinding.run_id == run.id)
    ).all()
    assert any(f.code == "model_without_live_artifact" for f in findings)


def test_check_database_flags_missing_recommended_revision(db_session: Session) -> None:
    user = _make_user(db_session, "db-owner2")
    run = _make_run(db_session, user)
    missing_rec = _make_model(db_session, "no-rec")
    _make_file(
        db_session,
        missing_rec,
        file_type=FileType.GCODE,
        is_recommended=False,
        path="a.gcode",
    )

    vault_audit._check_database(db_session, run)

    from sqlmodel import select

    findings = db_session.exec(
        select(VaultAuditFinding).where(VaultAuditFinding.run_id == run.id)
    ).all()
    assert any(f.code == "recommended_revision_missing" for f in findings)


def test_check_database_flags_metadata_missing(db_session: Session) -> None:
    user = _make_user(db_session, "db-owner3")
    run = _make_run(db_session, user)
    model = _make_model(db_session, "no-meta")
    _make_file(db_session, model)

    vault_audit._check_database(db_session, run)

    from sqlmodel import select

    findings = db_session.exec(
        select(VaultAuditFinding).where(VaultAuditFinding.run_id == run.id)
    ).all()
    assert any(f.code == "metadata_missing" for f in findings)


def test_check_database_flags_missing_thumbnail(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _make_user(db_session, "db-owner4")
    run = _make_run(db_session, user)
    model = _make_model(db_session, "no-thumb")
    file_row = _make_file(db_session, model)
    model.thumbnail_file_id = file_row.id
    db_session.add(model)
    db_session.commit()

    # `settings.thumb_dir` is a real, shared absolute path across the whole
    # suite (not per-test tmp_path), so don't rely on it happening to be
    # empty — pin `exists()` so this test can't collide with a leftover
    # thumbnail file another test wrote for the same file id.
    monkeypatch.setattr(get_backend(), "exists", lambda _key: False)

    vault_audit._check_database(db_session, run)

    from sqlmodel import select

    findings = db_session.exec(
        select(VaultAuditFinding).where(VaultAuditFinding.run_id == run.id)
    ).all()
    assert any(f.code == "thumbnail_missing" for f in findings)


def test_check_database_stops_when_cancelled(db_session: Session) -> None:
    user = _make_user(db_session, "db-owner5")
    run = _make_run(db_session, user)
    run.cancel_requested = True
    db_session.add(run)
    db_session.commit()
    _make_model(db_session, "irrelevant")

    vault_audit._check_database(db_session, run)

    db_session.refresh(run)
    assert run.state == VaultAuditRunState.CANCELLED


def test_check_external_flags_unavailable_root(db_session: Session) -> None:
    user = _make_user(db_session, "ext-owner")
    run = _make_run(db_session, user)
    library = ExternalLibrary(name="nas", root_path="/nowhere/does-not-exist")
    db_session.add(library)
    db_session.commit()

    vault_audit._check_external(db_session, run, [])

    from sqlmodel import select

    findings = db_session.exec(
        select(VaultAuditFinding).where(VaultAuditFinding.run_id == run.id)
    ).all()
    assert any(f.code == "external_root_unavailable" for f in findings)


def test_check_external_flags_missing_linked_file(db_session: Session) -> None:
    user = _make_user(db_session, "ext-owner2")
    run = _make_run(db_session, user)
    model = _make_model(db_session, "ext-model")
    file_row = _make_file(
        db_session,
        model,
        path="/nowhere/missing.stl",
        is_external=True,
        external_library_id=None,
    )

    vault_audit._check_external(
        db_session,
        run,
        [
            OwnedBlob(
                key=file_row.path,
                resource_type="file",
                resource_id=file_row.id,
                display_name="missing.stl",
            )
        ],
    )

    from sqlmodel import select

    findings = db_session.exec(
        select(VaultAuditFinding).where(VaultAuditFinding.run_id == run.id)
    ).all()
    linked = [f for f in findings if f.code == "linked_file_missing"]
    assert len(linked) == 1
    assert linked[0].repair_action is None


def test_check_external_skips_trashed_linked_file(db_session: Session) -> None:
    user = _make_user(db_session, "ext-trashed")
    run = _make_run(db_session, user)
    model = _make_model(db_session, "ext-trashed-model")
    file_row = _make_file(
        db_session,
        model,
        path="/nowhere/trashed.stl",
        is_external=True,
        deleted_at=utcnow(),
    )

    vault_audit._check_external(
        db_session,
        run,
        [
            OwnedBlob(
                key=file_row.path,
                resource_type="file",
                resource_id=file_row.id,
                display_name=file_row.original_filename,
            )
        ],
    )

    from sqlmodel import select

    findings = db_session.exec(
        select(VaultAuditFinding).where(VaultAuditFinding.run_id == run.id)
    ).all()
    assert not any(finding.code == "linked_file_missing" for finding in findings)


def test_check_background_jobs_flags_stuck_job_and_pending_import(
    db_session: Session,
) -> None:
    user = _make_user(db_session, "jobs-owner")
    run = _make_run(db_session, user)
    stuck_job = BackgroundJob(
        id="stuck-job-1", kind="thumbnail_rebuild", state="running"
    )
    stuck_job.updated_at = utcnow() - timedelta(hours=2)
    db_session.add(stuck_job)
    stuck_import = InboxItem(
        owner_user_id=user.id,
        source_url="https://example.com/x",
        state=InboxItemState.FAILED,
        retryable=True,
    )
    db_session.add(stuck_import)
    db_session.commit()

    vault_audit._check_background_jobs(db_session, run)

    from sqlmodel import select

    findings = db_session.exec(
        select(VaultAuditFinding).where(VaultAuditFinding.run_id == run.id)
    ).all()
    resource_types = {f.resource_type for f in findings}
    assert "background_job" in resource_types
    assert "pending_import" in resource_types


def test_execute_run_ignores_non_pending_run(db_session: Session) -> None:
    user = _make_user(db_session, "exec-owner")
    run = _make_run(db_session, user)
    run.state = VaultAuditRunState.COMPLETED
    db_session.add(run)
    db_session.commit()

    vault_audit.execute_run(run.id)

    db_session.refresh(run)
    assert run.state == VaultAuditRunState.COMPLETED


def test_execute_run_cancelled_before_primary_check_completes(
    db_session: Session,
) -> None:
    user = _make_user(db_session, "exec-owner2")
    run = _make_run(db_session, user)
    model = _make_model(db_session, "cancel-mid")
    _make_file(db_session, model)
    run.cancel_requested = True
    db_session.add(run)
    db_session.commit()

    vault_audit.execute_run(run.id)

    db_session.refresh(run)
    assert run.state == VaultAuditRunState.CANCELLED


def test_execute_run_marks_failed_on_unexpected_exception(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _make_user(db_session, "exec-owner3")
    run = _make_run(db_session, user)

    def boom(_session):
        raise RuntimeError("snapshot exploded")

    monkeypatch.setattr(vault_audit, "ownership_snapshot", boom)

    vault_audit.execute_run(run.id)

    db_session.refresh(run)
    assert run.state == VaultAuditRunState.FAILED
    assert run.error_code == "audit_failed"
