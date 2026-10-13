"""Disposable ingest bytes remain owned and charged until proven cleanup."""

import hashlib
import os
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, select

from app.db.models import CapacityReservation, IngestRequestKind, JobState, StagingLease
from app.db.models.ingestion_scratch import IngestionScratchWindow
from app.db.session import get_session_factory
from app.modules.ingestion import scratch_windows, staging_leases


class TestScratchWindows:
    def test_records_ownership_before_first_byte(self, local_storage):
        with scratch_windows.open_window(
            kind=scratch_windows.WindowKind.LOCAL_COPY, max_bytes=16
        ) as window:
            with get_session_factory().scoped_session() as session:
                row = session.get(IngestionScratchWindow, window.id)
                assert row is not None
                assert row.device == window.directory.stat().st_dev
                assert row.inode == window.directory.stat().st_ino
                assert (
                    session.get(CapacityReservation, row.capacity_operation_id)
                    is not None
                )
            (window.directory / "part.gcode").write_bytes(b"owned")
            identifier = window.id
        with get_session_factory().scoped_session() as session:
            assert session.get(IngestionScratchWindow, identifier) is None

    def test_retains_failed_cleanup_for_replay(self, local_storage, monkeypatch):
        window = scratch_windows.create_window(
            kind=scratch_windows.WindowKind.LOCAL_COPY, max_bytes=16
        )
        output = window.directory / "part.gcode"
        output.write_bytes(b"owned")
        unlink = os.unlink

        def refuse(path, *args, **kwargs):
            if Path(path).name == "part.gcode":
                raise OSError("controlled unlink refusal")
            return unlink(path, *args, **kwargs)

        monkeypatch.setattr(os, "unlink", refuse)
        with pytest.raises(scratch_windows.WindowCleanupError):
            window.close()
        with get_session_factory().scoped_session() as session:
            row = session.get(IngestionScratchWindow, window.id)
            assert row is not None
            assert (
                session.get(CapacityReservation, row.capacity_operation_id) is not None
            )
        monkeypatch.setattr(os, "unlink", unlink)
        assert scratch_windows.cleanup_window(window.id)
        with get_session_factory().scoped_session() as session:
            assert session.get(IngestionScratchWindow, window.id) is None
            assert session.get(CapacityReservation, window.operation_id) is None

    def test_preserves_replaced_directory(self, local_storage):
        window = scratch_windows.create_window(
            kind=scratch_windows.WindowKind.LOCAL_COPY, max_bytes=16
        )
        directory = window.directory
        displaced = directory.with_name(directory.name + "-displaced")
        directory.rename(displaced)
        directory.mkdir()
        replacement = directory / "foreign"
        replacement.write_bytes(b"foreign bytes")
        with pytest.raises(scratch_windows.WindowCleanupError):
            window.close()
        assert replacement.read_bytes() == b"foreign bytes"
        assert not scratch_windows.cleanup_window(window.id)
        assert displaced.exists()
        with get_session_factory().scoped_session() as session:
            assert session.get(IngestionScratchWindow, window.id) is not None

    def test_defers_recovery_while_writer_is_active(self, local_storage):
        with scratch_windows.open_window(
            kind=scratch_windows.WindowKind.LOCAL_COPY, max_bytes=16
        ) as window:
            output = window.directory / "partial"
            output.write_bytes(b"partial")
            assert not scratch_windows.cleanup_window(window.id)
            assert output.read_bytes() == b"partial"
            with get_session_factory().scoped_session() as session:
                assert session.get(IngestionScratchWindow, window.id) is not None


class TestPreparationRecovery:
    def test_unproven_lock_never_holds_capacity(self, local_storage):
        def refuse_lock_identity(session, *_):
            for row in session.dirty:
                if (
                    isinstance(row, IngestionScratchWindow)
                    and row.lock_inode is not None
                ):
                    raise OperationalError(
                        "commit lock identity", {}, RuntimeError("controlled fault")
                    )

        event.listen(Session, "before_flush", refuse_lock_identity)
        try:
            with pytest.raises(OperationalError, match="controlled fault"):
                scratch_windows.create_window(
                    kind=scratch_windows.WindowKind.LOCAL_COPY, max_bytes=16
                )
        finally:
            event.remove(Session, "before_flush", refuse_lock_identity)
        with get_session_factory().scoped_session() as session:
            row = session.exec(select(IngestionScratchWindow)).one()
            assert row.phase is scratch_windows.ScratchWindowPhase.PREPARING
            assert row.lock_inode is None
            assert not Path(row.path).exists()
            assert Path(row.lock_path).stat().st_size == 0
            assert session.get(CapacityReservation, row.capacity_operation_id) is None
        assert not scratch_windows.cleanup_window(row.id)
        assert Path(row.lock_path).exists()

    def test_replays_directory_identity_commit_failure(self, local_storage):
        def refuse_directory_identity(session, *_):
            for row in session.dirty:
                if (
                    isinstance(row, IngestionScratchWindow)
                    and row.phase is scratch_windows.ScratchWindowPhase.OPEN
                ):
                    raise OperationalError(
                        "commit directory identity",
                        {},
                        RuntimeError("controlled fault"),
                    )

        event.listen(Session, "before_flush", refuse_directory_identity)
        try:
            with pytest.raises(OperationalError, match="controlled fault"):
                scratch_windows.create_window(
                    kind=scratch_windows.WindowKind.LOCAL_COPY, max_bytes=16
                )
        finally:
            event.remove(Session, "before_flush", refuse_directory_identity)
        with get_session_factory().scoped_session() as session:
            assert session.exec(select(IngestionScratchWindow)).all() == []
            assert session.exec(select(CapacityReservation)).all() == []


class TestSQLCleanupFailure:
    def test_preserves_body_exception_when_cleanup_database_fails(
        self, local_storage, db_session
    ):
        primary = ValueError("original import failure")
        bind = db_session.get_bind()

        def reject_receipt_read(
            conn, cursor, statement, parameters, context, executemany
        ):
            if (
                statement.startswith("SELECT")
                and "ingestion_scratch_windows" in statement
            ):
                raise OperationalError(
                    statement, parameters, RuntimeError("cleanup database unavailable")
                )

        try:
            with pytest.raises(ValueError, match="original import failure") as captured:
                with scratch_windows.open_window(
                    kind=scratch_windows.WindowKind.LOCAL_COPY, max_bytes=16
                ) as window:
                    (window.directory / "partial").write_bytes(b"partial")
                    event.listen(bind, "before_cursor_execute", reject_receipt_read)
                    raise primary
        finally:
            event.remove(bind, "before_cursor_execute", reject_receipt_read)
        assert captured.value is primary
        assert any(
            "scratch cleanup failed" in note for note in captured.value.__notes__
        )
        with get_session_factory().scoped_session() as session:
            assert session.get(IngestionScratchWindow, window.id) is not None
            assert session.get(CapacityReservation, window.operation_id) is not None
        assert scratch_windows.cleanup_window(window.id)

    def test_cleanup_database_failure_retains_durable_claim(
        self, local_storage, db_session
    ):
        window = scratch_windows.create_window(
            kind=scratch_windows.WindowKind.LOCAL_COPY, max_bytes=16
        )
        (window.directory / "partial").write_bytes(b"partial")
        window.detach()
        bind = db_session.get_bind()

        def reject_receipt_read(
            conn, cursor, statement, parameters, context, executemany
        ):
            if (
                statement.startswith("SELECT")
                and "ingestion_scratch_windows" in statement
            ):
                raise OperationalError(
                    statement, parameters, RuntimeError("cleanup database unavailable")
                )

        event.listen(bind, "before_cursor_execute", reject_receipt_read)
        try:
            assert scratch_windows.cleanup_window(window.id) is False
        finally:
            event.remove(bind, "before_cursor_execute", reject_receipt_read)
        assert (window.directory / "partial").read_bytes() == b"partial"
        with get_session_factory().scoped_session() as session:
            assert session.get(CapacityReservation, window.operation_id) is not None
        assert scratch_windows.cleanup_window(window.id)


class TestSealedCustody:
    def test_preserves_replaced_sealed_output(self, local_storage):
        window = scratch_windows.create_window(
            kind=scratch_windows.WindowKind.DOWNLOAD, max_bytes=16
        )
        output = window.directory / "result.stl"
        output.write_bytes(b"owned")
        window.seal(output)
        displaced = output.with_name("displaced.stl")
        output.rename(displaced)
        output.write_bytes(b"foreign")
        with pytest.raises(scratch_windows.WindowCleanupError):
            window.close()
        assert output.read_bytes() == b"foreign"
        assert displaced.read_bytes() == b"owned"
        with get_session_factory().scoped_session() as session:
            assert session.get(CapacityReservation, window.operation_id) is not None

    def test_rejects_release_of_unrelated_workspace_child(self, local_storage):
        window = scratch_windows.create_window(
            kind=scratch_windows.WindowKind.DOWNLOAD, max_bytes=16
        )
        output = window.directory / "result.stl"
        output.write_bytes(b"owned")
        window.seal(output)
        window.detach()
        assert (
            scratch_windows.release_path(window.directory / "other.stl")
            is scratch_windows.ReleaseDisposition.UNCERTAIN
        )
        assert output.read_bytes() == b"owned"
        assert scratch_windows.cleanup_window(window.id)

    def test_lease_commit_protects_output_before_handoff(
        self, local_storage, db_session, make_ingest_request, make_user
    ):
        request = make_ingest_request(
            make_user(), kind=IngestRequestKind.UPLOAD, state=JobState.FAILED
        )
        window = scratch_windows.create_window(
            kind=scratch_windows.WindowKind.DOWNLOAD, max_bytes=16
        )
        output = window.directory / "result.stl"
        output.write_bytes(b"owned")
        window.seal(output)
        lease = staging_leases.create_job_lease(
            db_session,
            job_id=request.job_id,
            owner_user_id=request.owner_user_id,
            path=output,
            size_bytes=5,
            sha256=hashlib.sha256(b"owned").hexdigest(),
        )
        db_session.commit()
        window.detach()
        assert not scratch_windows.cleanup_window(window.id)
        assert (
            scratch_windows.release_path(output)
            is scratch_windows.ReleaseDisposition.TRANSFERRED
        )
        assert output.read_bytes() == b"owned"
        with get_session_factory().scoped_session() as session:
            assert session.get(CapacityReservation, window.operation_id) is not None
        db_session.delete(lease)
        db_session.commit()
        assert scratch_windows.cleanup_window(window.id)

    def test_handoff_keeps_capacity_until_input_lease_retires(
        self, local_storage, db_session, make_ingest_request, make_user
    ):
        request = make_ingest_request(
            make_user(), kind=IngestRequestKind.UPLOAD, state=JobState.FAILED
        )
        with scratch_windows.open_window(
            kind=scratch_windows.WindowKind.DOWNLOAD, max_bytes=16
        ) as window:
            output = window.directory / "result.stl"
            output.write_bytes(b"owned")
            lease = staging_leases.create_job_lease(
                db_session,
                job_id=request.job_id,
                owner_user_id=request.owner_user_id,
                path=output,
                size_bytes=5,
                sha256=hashlib.sha256(b"owned").hexdigest(),
            )
            db_session.commit()
            window.handoff(output)
        assert output.read_bytes() == b"owned"
        with get_session_factory().scoped_session() as session:
            row = session.get(IngestionScratchWindow, window.id)
            assert row is not None
            assert row.phase is scratch_windows.ScratchWindowPhase.TRANSFERRED
            assert session.get(CapacityReservation, window.operation_id) is not None
        db_session.delete(lease)
        db_session.commit()
        assert scratch_windows.cleanup_window(window.id)
        assert not output.exists()


class TestJobReplayAdmission:
    def test_releases_previous_epoch_before_admitting_new_window(
        self, local_storage, make_ingest_request, make_user
    ):
        request = make_ingest_request(
            make_user(), kind=IngestRequestKind.UPLOAD, state=JobState.FAILED
        )
        old = scratch_windows.create_window(
            kind=scratch_windows.WindowKind.DOWNLOAD,
            max_bytes=16,
            owner=scratch_windows.JobWindowOwner(request.job_id, "old-epoch"),
        )
        output = old.directory / "partial"
        output.write_bytes(b"partial")
        old.seal(output)
        old.detach()
        with scratch_windows.open_window(
            kind=scratch_windows.WindowKind.DOWNLOAD,
            max_bytes=16,
            owner=scratch_windows.JobWindowOwner(request.job_id, "new-epoch"),
        ) as new:
            assert not output.exists()
            with get_session_factory().scoped_session() as session:
                assert session.get(IngestionScratchWindow, old.id) is None
                assert session.get(CapacityReservation, old.operation_id) is None
                assert session.get(CapacityReservation, new.operation_id) is not None

    def test_blocks_new_epoch_while_previous_bytes_cannot_be_released(
        self, local_storage, make_ingest_request, make_user, monkeypatch
    ):
        request = make_ingest_request(
            make_user(), kind=IngestRequestKind.UPLOAD, state=JobState.FAILED
        )
        old = scratch_windows.create_window(
            kind=scratch_windows.WindowKind.DOWNLOAD,
            max_bytes=16,
            owner=scratch_windows.JobWindowOwner(request.job_id, "old-epoch"),
        )
        output = old.directory / "partial"
        output.write_bytes(b"partial")
        old.seal(output)
        old.detach()
        unlink = os.unlink

        def refuse(path, *args, **kwargs):
            if Path(path).name == "partial":
                raise OSError("controlled unlink refusal")
            return unlink(path, *args, **kwargs)

        monkeypatch.setattr(os, "unlink", refuse)
        with pytest.raises(scratch_windows.WindowCleanupError):
            scratch_windows.create_window(
                kind=scratch_windows.WindowKind.DOWNLOAD,
                max_bytes=16,
                owner=scratch_windows.JobWindowOwner(request.job_id, "new-epoch"),
            )
        with get_session_factory().scoped_session() as session:
            assert session.exec(select(IngestionScratchWindow.id)).all() == [old.id]
            assert session.exec(select(CapacityReservation.operation_id)).all() == [
                old.operation_id
            ]
        assert (
            old.directory.with_name(old.id + ".retired") / output.name
        ).read_bytes() == b"partial"
        monkeypatch.setattr(os, "unlink", unlink)
        assert scratch_windows.cleanup_window(old.id)


class TestRecoveryBounds:
    def test_rejects_more_than_64_prior_windows(
        self,
        local_storage,
        db_session,
        make_ingest_request,
        make_user,
        make_ingestion_scratch_window,
    ):
        from app.db.models import Job

        request = make_ingest_request(
            make_user(), kind=IngestRequestKind.UPLOAD, state=JobState.FAILED
        )
        job = db_session.get(Job, request.job_id)
        assert job is not None
        prior = [
            make_ingestion_scratch_window(job=job, execution_epoch="old-epoch")
            for _ in range(65)
        ]
        with pytest.raises(scratch_windows.WindowCleanupError):
            scratch_windows.create_window(
                kind=scratch_windows.WindowKind.DOWNLOAD,
                max_bytes=16,
                owner=scratch_windows.JobWindowOwner(request.job_id, "new-epoch"),
            )
        with get_session_factory().scoped_session() as session:
            assert set(session.exec(select(IngestionScratchWindow.id)).all()) == {
                row.id for row in prior
            }
            assert session.exec(select(CapacityReservation)).all() == []
        assert all(not Path(row.path).exists() for row in prior)

    def test_preserves_nested_windows_in_the_same_epoch(
        self, local_storage, make_ingest_request, make_user
    ):
        request = make_ingest_request(
            make_user(), kind=IngestRequestKind.UPLOAD, state=JobState.FAILED
        )
        owner = scratch_windows.JobWindowOwner(request.job_id, "shared-epoch")
        with scratch_windows.open_window(
            kind=scratch_windows.WindowKind.DOWNLOAD, max_bytes=16, owner=owner
        ) as outer:
            source = outer.directory / "archive.zip"
            source.write_bytes(b"archive input")
            outer.seal(source)
            with scratch_windows.open_window(
                kind=scratch_windows.WindowKind.ARCHIVE_ENTRY, max_bytes=16, owner=owner
            ) as inner:
                output = inner.directory / "entry.stl"
                output.write_bytes(b"entry")
                inner.seal(output)
                assert source.read_bytes() == b"archive input"
                with get_session_factory().scoped_session() as session:
                    assert (
                        session.get(CapacityReservation, outer.operation_id) is not None
                    )
                    assert (
                        session.get(CapacityReservation, inner.operation_id) is not None
                    )
            assert not output.exists()
            assert source.read_bytes() == b"archive input"
            with get_session_factory().scoped_session() as session:
                assert session.get(IngestionScratchWindow, inner.id) is None
                assert session.get(CapacityReservation, outer.operation_id) is not None
        assert not source.exists()


class TestReceiptIntegrity:
    @pytest.mark.parametrize(
        "replacement",
        [
            "parent",
            "lock",
            "marker-token",
            "marker-missing",
            "marker-directory",
            "marker-oversized",
        ],
    )
    def test_uncertain_custody_preserves_payload(self, local_storage, replacement):
        window = scratch_windows.create_window(
            kind=scratch_windows.WindowKind.DOWNLOAD, max_bytes=16
        )
        output = window.directory / "result.stl"
        output.write_bytes(b"owned")
        window.seal(output)
        window.detach()
        marker = window.directory / ".ownership.json"
        parent = window.directory.parent
        lock = parent / (window.id + ".lock")
        if replacement == "parent":
            displaced = parent.with_name(parent.name + "-displaced")
            parent.rename(displaced)
            parent.mkdir(mode=0o700)
            output = displaced / window.id / output.name
        elif replacement == "lock":
            lock.rename(lock.with_suffix(".displaced"))
            lock.write_bytes(b"foreign-lock")
        elif replacement == "marker-token":
            marker.write_text('{"id":"foreign","token":"foreign"}')
        elif replacement == "marker-missing":
            marker.unlink()
        elif replacement == "marker-directory":
            marker.unlink()
            marker.mkdir()
        else:
            marker.write_bytes(b"x" * 257)

        assert not scratch_windows.cleanup_window(window.id)
        assert output.read_bytes() == b"owned"
        with get_session_factory().scoped_session() as session:
            assert session.get(IngestionScratchWindow, window.id) is not None
            assert session.get(CapacityReservation, window.operation_id) is not None
        if replacement == "lock":
            assert lock.read_bytes() == b"foreign-lock"

    @pytest.mark.parametrize("replacement", ["symlink", "directory"])
    def test_sealing_rejects_nonregular_output(
        self, local_storage, tmp_path, replacement
    ):
        with scratch_windows.open_window(
            kind=scratch_windows.WindowKind.DOWNLOAD, max_bytes=16
        ) as window:
            output = window.directory / "result.stl"
            foreign = tmp_path / "foreign.stl"
            foreign.write_bytes(b"foreign")
            if replacement == "symlink":
                output.symlink_to(foreign)
            else:
                output.mkdir()
            with pytest.raises(scratch_windows.WindowCleanupError):
                window.seal(output)
            assert foreign.read_bytes() == b"foreign"
            # Remove the deliberate foreign entry so normal context teardown
            # can release the otherwise intact workspace.
            if replacement == "symlink":
                output.unlink()
            else:
                output.rmdir()

    def test_sealing_rejects_output_outside_custody(self, local_storage, tmp_path):
        foreign = tmp_path / "foreign.stl"
        foreign.write_bytes(b"foreign")
        with scratch_windows.open_window(
            kind=scratch_windows.WindowKind.DOWNLOAD, max_bytes=16
        ) as window:
            with pytest.raises(ValueError, match="scratch_output_outside_window"):
                window.seal(foreign)
            assert foreign.read_bytes() == b"foreign"


class TestInterruptedRetirement:
    @pytest.mark.parametrize("boundary", ["before-empty-rmdir", "after-lock-unlink"])
    def test_replays_half_finished_retirement(
        self, local_storage, monkeypatch, boundary
    ):
        window = scratch_windows.create_window(
            kind=scratch_windows.WindowKind.DOWNLOAD, max_bytes=16
        )
        output = window.directory / "result.stl"
        output.write_bytes(b"owned")
        window.seal(output)
        window.detach()
        unlink, rmdir = os.unlink, os.rmdir
        retired = window.directory.with_name(window.id + ".retired")

        def interrupt_unlink(name, *args, **kwargs):
            result = unlink(name, *args, **kwargs)
            if Path(name).name == window.id + ".lock":
                raise OSError("crashed after unlink")
            return result

        def interrupt_rmdir(name, *args, **kwargs):
            if Path(name).name == retired.name:
                raise OSError("crashed before final rmdir")
            return rmdir(name, *args, **kwargs)

        if boundary == "before-empty-rmdir":
            monkeypatch.setattr(os, "rmdir", interrupt_rmdir)
        else:
            monkeypatch.setattr(os, "unlink", interrupt_unlink)
        assert not scratch_windows.cleanup_window(window.id)
        with get_session_factory().scoped_session() as session:
            assert session.get(IngestionScratchWindow, window.id) is not None
            assert session.get(CapacityReservation, window.operation_id) is not None
        assert not output.exists()
        monkeypatch.setattr(os, "unlink", unlink)
        monkeypatch.setattr(os, "rmdir", rmdir)

        assert scratch_windows.cleanup_window(window.id)
        assert scratch_windows.cleanup_window(window.id)
        assert not retired.exists()
        with get_session_factory().scoped_session() as session:
            assert session.get(IngestionScratchWindow, window.id) is None
            assert session.get(CapacityReservation, window.operation_id) is None


class TestPathRelease:
    def test_unowned_path_is_preserved(self, local_storage, tmp_path):
        foreign = tmp_path / "foreign.stl"
        foreign.write_bytes(b"foreign")
        assert (
            scratch_windows.release_path(foreign)
            is scratch_windows.ReleaseDisposition.NOT_OWNED
        )
        scratch_windows.handoff_path(foreign)
        assert foreign.read_bytes() == b"foreign"

    def test_live_writer_defers_path_release(self, local_storage):
        with scratch_windows.open_window(
            kind=scratch_windows.WindowKind.DOWNLOAD, max_bytes=16
        ) as window:
            output = window.directory / "result.stl"
            output.write_bytes(b"owned")
            window.seal(output)
            assert (
                scratch_windows.release_path(output)
                is scratch_windows.ReleaseDisposition.DEFERRED
            )
            assert output.read_bytes() == b"owned"
            with get_session_factory().scoped_session() as session:
                assert session.get(CapacityReservation, window.operation_id) is not None

    def test_detached_output_releases_exact_custody(self, local_storage):
        window = scratch_windows.create_window(
            kind=scratch_windows.WindowKind.DOWNLOAD, max_bytes=16
        )
        output = window.directory / "result.stl"
        output.write_bytes(b"owned")
        window.seal(output)
        window.detach()
        assert (
            scratch_windows.release_path(output)
            is scratch_windows.ReleaseDisposition.RELEASED
        )
        assert not output.exists()
        with get_session_factory().scoped_session() as session:
            assert session.get(IngestionScratchWindow, window.id) is None
            assert session.get(CapacityReservation, window.operation_id) is None

    @pytest.mark.parametrize("replacement", ["missing", "regular", "symlink"])
    def test_unproven_lock_cannot_release_output(
        self, local_storage, tmp_path, replacement
    ):
        window = scratch_windows.create_window(
            kind=scratch_windows.WindowKind.DOWNLOAD, max_bytes=16
        )
        output = window.directory / "result.stl"
        output.write_bytes(b"owned")
        window.seal(output)
        window.detach()
        lock = window.directory.parent / (window.id + ".lock")
        lock.rename(lock.with_suffix(".displaced"))
        if replacement == "regular":
            lock.write_bytes(b"foreign")
        elif replacement == "symlink":
            foreign = tmp_path / "foreign-lock"
            foreign.write_bytes(b"foreign")
            lock.symlink_to(foreign)
        assert (
            scratch_windows.release_path(output)
            is scratch_windows.ReleaseDisposition.UNCERTAIN
        )
        assert output.read_bytes() == b"owned"
        with get_session_factory().scoped_session() as session:
            assert session.get(CapacityReservation, window.operation_id) is not None


class TestInputHandoff:
    @pytest.mark.parametrize("method", ["live-window", "detached-output"])
    def test_handoff_requires_committed_input_lease(self, local_storage, method):
        window = scratch_windows.create_window(
            kind=scratch_windows.WindowKind.DOWNLOAD, max_bytes=16
        )
        output = window.directory / "result.stl"
        output.write_bytes(b"owned")
        window.seal(output)
        with pytest.raises(scratch_windows.WindowCleanupError):
            if method == "live-window":
                window.handoff(output)
            else:
                window.detach()
                scratch_windows.handoff_path(output)
        assert output.read_bytes() == b"owned"
        with get_session_factory().scoped_session() as session:
            assert session.get(CapacityReservation, window.operation_id) is not None
        window.detach()
        assert scratch_windows.cleanup_window(window.id)

    def test_detached_handoff_keeps_exact_input_lease(
        self, local_storage, db_session, make_ingest_request, make_user
    ):
        request = make_ingest_request(
            make_user(), kind=IngestRequestKind.UPLOAD, state=JobState.FAILED
        )
        window = scratch_windows.create_window(
            kind=scratch_windows.WindowKind.DOWNLOAD, max_bytes=16
        )
        output = window.directory / "result.stl"
        output.write_bytes(b"owned")
        window.seal(output)
        lease = staging_leases.create_job_lease(
            db_session,
            job_id=request.job_id,
            owner_user_id=request.owner_user_id,
            path=output,
            size_bytes=5,
            sha256=hashlib.sha256(b"owned").hexdigest(),
        )
        db_session.commit()
        window.detach()
        scratch_windows.handoff_path(output)
        assert not scratch_windows.cleanup_window(window.id)
        with get_session_factory().scoped_session() as session:
            row = session.get(IngestionScratchWindow, window.id)
            assert row.phase is scratch_windows.ScratchWindowPhase.TRANSFERRED
            assert row.transferred_path == str(output)
            assert session.get(StagingLease, lease.id) is not None
            assert session.get(CapacityReservation, window.operation_id) is not None
        assert output.read_bytes() == b"owned"
        db_session.delete(lease)
        db_session.commit()
        assert scratch_windows.cleanup_window(window.id)


class TestAdmissionValidation:
    @pytest.mark.parametrize(
        "limit", [0, -1, True, 1.5], ids=["zero", "negative", "bool", "fractional"]
    )
    def test_refuses_invalid_window_limit(self, local_storage, limit):
        with pytest.raises(ValueError, match="invalid_scratch_window"):
            scratch_windows.create_window(
                kind=scratch_windows.WindowKind.DOWNLOAD, max_bytes=limit
            )
        with get_session_factory().scoped_session() as session:
            assert session.exec(select(IngestionScratchWindow)).all() == []
            assert session.exec(select(CapacityReservation)).all() == []

    @pytest.mark.parametrize(
        "job_id,epoch",
        [("", "epoch"), ("job", "")],
        ids=["missing-job", "missing-epoch"],
    )
    def test_refuses_incomplete_job_owner(self, job_id, epoch):
        with pytest.raises(ValueError, match="invalid_scratch_job_owner"):
            scratch_windows.JobWindowOwner(job_id, epoch)

    def test_refuses_empty_request_token(self):
        with pytest.raises(ValueError, match="invalid_scratch_request_owner"):
            scratch_windows.RequestWindowOwner("")

    def test_request_custody_records_supplied_token(self, local_storage):
        with scratch_windows.open_window(
            kind=scratch_windows.WindowKind.DOWNLOAD,
            max_bytes=16,
            owner=scratch_windows.RequestWindowOwner("request-token"),
        ) as window:
            with get_session_factory().scoped_session() as session:
                row = session.get(IngestionScratchWindow, window.id)
                assert row.request_token == "request-token"
                assert row.job_id is None
                assert row.execution_epoch is None


class TestLeaseIdentity:
    def test_handoff_rejects_a_replaced_leased_input(
        self, local_storage, db_session, make_ingest_request, make_user, tmp_path
    ):
        request = make_ingest_request(
            make_user(), kind=IngestRequestKind.UPLOAD, state=JobState.FAILED
        )
        window = scratch_windows.create_window(
            kind=scratch_windows.WindowKind.DOWNLOAD, max_bytes=16
        )
        output = window.directory / "result.stl"
        output.write_bytes(b"owned")
        window.seal(output)
        lease = staging_leases.create_job_lease(
            db_session,
            job_id=request.job_id,
            owner_user_id=request.owner_user_id,
            path=output,
            size_bytes=5,
            sha256=hashlib.sha256(b"owned").hexdigest(),
        )
        db_session.commit()
        retained = tmp_path / "original.stl"
        output.rename(retained)
        output.write_bytes(b"other")
        window.seal(output)
        window.detach()

        with pytest.raises(scratch_windows.WindowCleanupError):
            scratch_windows.handoff_path(output)
        assert not scratch_windows.cleanup_window(window.id)
        assert output.read_bytes() == b"other"
        assert retained.read_bytes() == b"owned"
        with get_session_factory().scoped_session() as session:
            assert session.get(StagingLease, lease.id) is not None
            assert session.get(CapacityReservation, window.operation_id) is not None

    def test_retry_admission_preserves_committed_prior_input(
        self, local_storage, db_session, make_ingest_request, make_user
    ):
        request = make_ingest_request(
            make_user(), kind=IngestRequestKind.UPLOAD, state=JobState.FAILED
        )
        old = scratch_windows.create_window(
            kind=scratch_windows.WindowKind.DOWNLOAD,
            max_bytes=16,
            owner=scratch_windows.JobWindowOwner(request.job_id, "old-epoch"),
        )
        output = old.directory / "result.stl"
        output.write_bytes(b"owned")
        old.seal(output)
        lease = staging_leases.create_job_lease(
            db_session,
            job_id=request.job_id,
            owner_user_id=request.owner_user_id,
            path=output,
            size_bytes=5,
            sha256=hashlib.sha256(b"owned").hexdigest(),
        )
        db_session.commit()
        old.detach()
        with scratch_windows.open_window(
            kind=scratch_windows.WindowKind.DOWNLOAD,
            max_bytes=16,
            owner=scratch_windows.JobWindowOwner(request.job_id, "new-epoch"),
        ) as new:
            assert output.read_bytes() == b"owned"
            with get_session_factory().scoped_session() as session:
                assert session.get(StagingLease, lease.id) is not None
                assert session.get(CapacityReservation, old.operation_id) is not None
                assert session.get(CapacityReservation, new.operation_id) is not None
        db_session.delete(lease)
        db_session.commit()
        assert scratch_windows.cleanup_window(old.id)


class TestParentAdmission:
    @pytest.mark.parametrize("kind", ["public-directory", "symlink"])
    def test_refuses_untrusted_workspace_parent(self, local_storage, tmp_path, kind):
        from app.core.config import settings

        parent = settings.incoming_dir / "scratch-windows"
        parent.parent.mkdir(parents=True, exist_ok=True)
        if kind == "public-directory":
            parent.mkdir(mode=0o755)
        else:
            target = tmp_path / "foreign-parent"
            target.mkdir(mode=0o700)
            parent.symlink_to(target, target_is_directory=True)
        with pytest.raises(scratch_windows.WindowCleanupError):
            scratch_windows.create_window(
                kind=scratch_windows.WindowKind.DOWNLOAD, max_bytes=16
            )
        with get_session_factory().scoped_session() as session:
            assert session.exec(select(IngestionScratchWindow)).all() == []
            assert session.exec(select(CapacityReservation)).all() == []
        assert list(parent.iterdir()) == []
