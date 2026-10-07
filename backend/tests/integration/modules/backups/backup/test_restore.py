"""Pending restore evidence cannot authorize an unknown storage destination."""

import json
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlmodel import Session, delete, select

from app.db.models import LibraryRevision, Model, RestoreMarker
from app.modules.backups.backup import creation, restore, restore_journal
from app.modules.library.model_views.browse import revision
from app.modules.storage.storage_backend.contracts import UnavailableStorageBackend
from app.modules.storage.storage_backend.local import LocalStorageBackend
from app.modules.storage.storage_backend.runtime import bind_backend
from app.runtime.maintenance import (
    RestoreConflictError,
    hold_restore_maintenance,
    restore_in_progress,
)
from tests.factories import build_document, build_model, build_multipart_model
from tests.integration._backup_harness import (
    BackupEnv,
    backup_admin_headers,
    seed_model_with_blob,
)


class TestRecoveryDestination:
    @pytest.mark.parametrize("case", ["unknown-identity", "legacy-remote"])
    def test_refuses_recovery_without_current_destination_proof(self, backup_env, case):
        backup_id = "abc123"
        started = {
            "event": "started",
            "version": 2,
            "backup_id": backup_id,
            "operation_nonce": "a" * 64,
            "archive_sha256": "b" * 64,
            "provider_ref": "c" * 64,
        }
        if case == "unknown-identity":

            class UnknownIdentity(LocalStorageBackend):
                @property
                def endpoint_url(self):
                    raise OSError("provider configuration unavailable")

            bind_backend(UnknownIdentity())
            expected = "restore_storage_provider_unknown"
        else:
            started = {"event": "started", "version": 1, "backup_id": backup_id}
            bind_backend(UnavailableStorageBackend("remote provider unavailable"))
            expected = "restore_journal_mismatch"
        journal = backup_env.backup_dir / f".restore-{backup_id}.journal"
        content = json.dumps(started) + "\n"
        journal.write_text(content)
        database_before = backup_env.db_file.read_bytes()
        hold_restore_maintenance()

        with pytest.raises(RestoreConflictError, match=expected):
            restore.restore_backup(backup_id)

        assert journal.read_text() == content
        assert backup_env.db_file.read_bytes() == database_before
        assert restore_in_progress()


class TestRestoredLibraryAuthority:
    def test_marker_renews_both_stamps_without_relying_on_counters(
        self, backup_env: BackupEnv
    ) -> None:
        with backup_env.new_session() as session:
            authority = session.get_one(LibraryRevision, 1)
            authority.revision = 42
            authority.authorization_revision = 7
            session.commit()
            before = revision(session)
        restore_journal._stage_restore_marker(
            backup_env.db_file,
            "restore-epoch",
            operation_nonce="a" * 64,
            archive_sha256="b" * 64,
        )
        with backup_env.new_session() as session:
            after = revision(session)
            authority = session.get_one(LibraryRevision, 1)
            assert authority.revision == 42
            assert authority.authorization_revision == 7
            assert after.browse_revision != before.browse_revision
            assert after.authorization_revision != before.authorization_revision
            assert (
                session.exec(select(RestoreMarker)).one().backup_id == "restore-epoch"
            )

    def test_rolls_back_failed_marker_publication_atomically(
        self, backup_env: BackupEnv
    ) -> None:
        with backup_env.new_session() as session:
            session.add(
                RestoreMarker(
                    backup_id="previous",
                    operation_nonce="c" * 64,
                    archive_sha256="d" * 64,
                    state="database_active",
                )
            )
            session.commit()
            before = revision(session)

        def fail_after_flush(session: Session) -> None:
            session.flush()
            raise OSError("private marker commit failed")

        event.listen(Session, "before_commit", fail_after_flush)
        try:
            with pytest.raises(OSError, match="private marker commit failed"):
                restore_journal._stage_restore_marker(
                    backup_env.db_file,
                    "replacement",
                    operation_nonce="a" * 64,
                    archive_sha256="b" * 64,
                )
        finally:
            event.remove(Session, "before_commit", fail_after_flush)
        with backup_env.new_session() as session:
            assert revision(session) == before
            assert session.exec(select(RestoreMarker)).one().backup_id == "previous"

    def test_missing_revision_refuses_marker_publication(
        self, backup_env: BackupEnv
    ) -> None:
        with backup_env.new_session() as session:
            session.add(
                RestoreMarker(
                    backup_id="previous",
                    operation_nonce="c" * 64,
                    archive_sha256="d" * 64,
                    state="database_active",
                )
            )
            session.exec(delete(LibraryRevision))
            session.commit()
        with pytest.raises(RuntimeError, match="restore_library_revision_missing"):
            restore_journal._stage_restore_marker(
                backup_env.db_file,
                "replacement",
                operation_nonce="a" * 64,
                archive_sha256="b" * 64,
            )
        with backup_env.new_session() as session:
            assert session.get(LibraryRevision, 1) is None
            assert session.exec(select(RestoreMarker)).one().backup_id == "previous"

    def test_repeated_physical_restore_never_reuses_archived_epoch(
        self, backup_env: BackupEnv
    ) -> None:
        model_id, key = seed_model_with_blob(
            backup_env, name="Authority snapshot", content=b"snapshot"
        )
        with backup_env.new_session() as session:
            archived = revision(session)
        backup = creation.create_backup()
        epochs = {archived.browse_revision.split(":")[0]}
        for _ in range(2):
            Path(key).unlink()
            restore.restore_backup(backup.id)
            with backup_env.new_session() as session:
                current = revision(session)
                editing_epoch = session.get_one(Model, model_id).edit_epoch
            epoch = current.browse_revision.split(":")[0]
            assert editing_epoch == epoch
            assert epoch not in epochs
            assert current.authorization_revision.split(":")[0] == epoch
            epochs.add(epoch)
            assert Path(key).read_bytes() == b"snapshot"

    def test_post_swap_ack_retry_keeps_published_epoch(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        model_id, key = seed_model_with_blob(
            backup_env, name="Authority acknowledgement", content=b"ack"
        )
        with backup_env.new_session() as session:
            before = revision(session)
        backup = creation.create_backup()
        Path(key).unlink()
        append = restore_journal._append_restore_journal

        def fail_active_ack(path: Path, event: dict[str, object]) -> None:
            if event.get("event") == "database_active":
                raise OSError("lost active acknowledgement")
            append(path, event)

        monkeypatch.setattr(restore_journal, "_append_restore_journal", fail_active_ack)
        with pytest.raises(
            RestoreConflictError, match="restore_post_swap_recovery_required"
        ):
            restore.restore_backup(backup.id)
        with backup_env.new_session() as session:
            published = revision(session)
            published_editing = session.get_one(Model, model_id).edit_epoch
        assert (
            published.browse_revision.split(":")[0]
            != before.browse_revision.split(":")[0]
        )
        monkeypatch.setattr(restore_journal, "_append_restore_journal", append)
        monkeypatch.setattr(
            restore_journal,
            "_stage_restore_marker",
            lambda *_args, **_kwargs: pytest.fail("published epoch rotated on retry"),
        )
        restore.restore_backup(backup.id)
        with backup_env.new_session() as session:
            assert revision(session) == published
            assert session.get_one(Model, model_id).edit_epoch == published_editing
        assert Path(key).read_bytes() == b"ack"
        assert not (backup_env.backup_dir / f".restore-{backup.id}.journal").exists()


class TestRestoredEditingIdentity:
    @pytest.mark.parametrize(
        ("factory", "endpoint", "method"),
        [
            (build_model, "models", "patch"),
            (build_multipart_model, "multipart-models", "patch"),
            (build_document, "documents", "put"),
        ],
        ids=["model", "multipart", "document"],
    )
    def test_rejects_a_draft_from_the_previous_restored_history(
        self, backup_env, client, factory, endpoint, method
    ):
        headers = backup_admin_headers(backup_env)
        with backup_env.new_session() as session:
            row = factory(session, name="Archived content")
            row_id = row.id
        path = f"/api/v1/{endpoint}/{row_id}"
        backup = creation.create_backup()
        previous = client.request(
            method, path, headers=headers, json={"name": "Previous history"}
        )
        assert previous.status_code == 200, previous.text
        old_tag = previous.headers["etag"]
        old_version = previous.json()["edit_version"]

        restore.restore_backup(backup.id)
        current = client.request(
            method, path, headers=headers, json={"name": "Restored history"}
        )
        assert current.status_code == 200, current.text
        # Exercise an actual integer collision, not an ordinary version conflict.
        assert current.json()["edit_version"] == old_version
        rejected = client.request(
            method,
            path,
            headers={
                **headers,
                "If-Match": old_tag,
                "X-PrintStash-Edit-Contract": "conditional-v1",
            },
            json={"name": "Stale draft"},
        )

        assert rejected.status_code == 412, rejected.text
        assert client.get(path, headers=headers).json()["name"] == "Restored history"
