"""Verified migration isolation and generation contracts."""

from pathlib import Path

import pytest

from app.core.config import _overlay
from app.modules.storage.storage_backend.local import LocalStorageBackend


def test_local_backend_retains_its_roots_when_configuration_changes(tmp_path: Path):
    source = tmp_path / "source"
    thumbs = tmp_path / "thumbs"
    _overlay.update(data_dir=source, thumb_dir=thumbs)
    backend = LocalStorageBackend()
    _overlay.update(
        data_dir=tmp_path / "destination", thumb_dir=tmp_path / "new-thumbs"
    )
    assert backend.blob_key("model", 1, "part.stl") == str(source / "model/v1/part.stl")
    assert backend.thumbnail_key(1) == str(thumbs / "1.webp")
    assert backend.namespace_for(str(source / "part.stl")) == f"data:{source}"


def test_candidate_local_backend_never_changes_active_settings(tmp_path: Path):
    from app.modules.storage.storage_backend.factory import build_configured_backend
    from app.modules.storage.storage_providers import parse_provider_config

    _overlay.update(data_dir=tmp_path / "active", thumb_dir=tmp_path / "active-thumbs")
    candidate = build_configured_backend(
        parse_provider_config(
            {
                "provider": "local",
                "data_dir": str(tmp_path / "candidate"),
                "thumb_dir": str(tmp_path / "candidate-thumbs"),
            }
        )
    )
    assert candidate.blob_key("model", 1, "part.stl") == str(
        tmp_path / "candidate/model/v1/part.stl"
    )
    assert _overlay["data_dir"] == tmp_path / "active"
    assert _overlay["thumb_dir"] == tmp_path / "active-thumbs"


def test_reader_keeps_source_adapter_after_activation(tmp_path: Path):
    from app.modules.storage.storage_backend import generations
    from app.modules.storage.storage_backend.runtime import bind_backend, get_backend

    source = LocalStorageBackend(
        data_dir=tmp_path / "source", thumb_dir=tmp_path / "thumbs"
    )
    destination = LocalStorageBackend(
        data_dir=tmp_path / "destination", thumb_dir=tmp_path / "new-thumbs"
    )
    bind_backend(source)
    pinned = generations.pin()
    with generations.use(pinned):
        assert get_backend() is source
        pinned.planned()
        with generations.activation():
            generations.publish(destination, "migration-1")
        assert get_backend() is source
    assert get_backend() is destination
    assert generations.current_epoch() == "migration-1"


def test_activation_cannot_mix_an_unplanned_reader(tmp_path: Path):
    import pytest

    from app.modules.storage.storage_backend import generations
    from app.modules.storage.storage_backend.runtime import bind_backend, get_backend

    source = LocalStorageBackend(
        data_dir=tmp_path / "source", thumb_dir=tmp_path / "thumbs"
    )
    bind_backend(source)
    pinned = generations.pin()
    try:
        with pytest.raises(TimeoutError, match="reader_drain"):
            with generations.activation(timeout=0):
                raise AssertionError("must not enter activation")
        assert get_backend() is source
    finally:
        pinned.close()


def test_retention_exemption_is_exactly_one_candidate(tmp_path: Path):
    import pytest

    from app.core.errors import OperationError
    from app.runtime.maintenance import (
        allow_retained_destination_destruction,
        guarded_storage_destruction,
        retain_storage_objects,
    )

    class Endpoint:
        @guarded_storage_destruction
        def remove(self):
            return "removed"

    source, candidate, foreign = Endpoint(), Endpoint(), Endpoint()
    with retain_storage_objects():
        with allow_retained_destination_destruction(
            source=source, destination=candidate
        ):
            assert candidate.remove() == "removed"
            for protected in (source, foreign):
                with pytest.raises(OperationError, match="storage_snapshot_retained"):
                    protected.remove()
        with pytest.raises(OperationError, match="storage_snapshot_retained"):
            candidate.remove()


@pytest.fixture
def migration_owner(db_session, tmp_path, monkeypatch):
    from app.modules.storage.storage_backend.runtime import bind_backend
    from tests._env import use_local_storage

    use_local_storage(tmp_path)
    bind_backend(LocalStorageBackend())
    from app.db.session import get_session_factory
    from app.modules.storage import vault_migration
    from app.runtime.maintenance import end_restore_maintenance

    monkeypatch.setattr(
        vault_migration,
        "_backup",
        lambda *_args, **_kwargs: {"archive_sha256": "verified"},
    )
    from app.modules.storage.storage_backend import generations
    from tests.factories import build_user

    monkeypatch.setattr(generations, "_epoch", "0")
    actor = build_user(db_session, superuser=True)
    owner = vault_migration.VaultMigrations(get_session_factory())
    original_preflight = owner.preflight
    monkeypatch.setattr(
        owner,
        "preflight",
        lambda *args, **kwargs: original_preflight(*args, actor_id=actor.id, **kwargs),
    )
    yield owner
    for run_id in list(vault_migration._retentions):
        vault_migration._release(run_id)
    end_restore_maintenance()


def migration_candidate(tmp_path):
    data, thumbs = tmp_path / "destination", tmp_path / "destination-thumbnails"
    data.mkdir()
    thumbs.mkdir()
    return {"provider": "local", "data_dir": str(data), "thumb_dir": str(thumbs)}


def test_baseline_copies_trash_without_deleting_source(
    db_session, tmp_path, migration_owner
):
    from app.core.errors import OperationError
    from app.core.time import utcnow
    from app.modules.storage.storage_backend.runtime import get_backend
    from app.modules.storage.storage_ownership import matching_creation_receipt
    from tests.factories import build_model, build_stored_file

    source = get_backend()
    model = build_model(db_session, deleted_at=utcnow())
    artifact = build_stored_file(
        db_session, source, model, data=b"original bytes", deleted_at=utcnow()
    )
    old_key = artifact.path
    receipt = matching_creation_receipt(db_session, source, old_key)
    assert receipt is not None
    result = migration_owner.preflight(
        migration_candidate(tmp_path), backup_id="verified-backup"
    )
    migration_owner.start(result["id"], result["plan_digest"])
    with pytest.raises(OperationError, match="storage_snapshot_retained"):
        source.rollback_create(receipt)
    result = migration_owner.advance(result["id"])
    assert result["state"] == "ready"
    assert result["verified_objects"] == 1
    assert source.read_bytes(old_key) == b"original bytes"
    assert get_backend() is source
    assert db_session.get(type(artifact), artifact.id).path == old_key


def test_destination_collision_keeps_foreign_bytes(
    db_session, tmp_path, migration_owner
):
    candidate = migration_candidate(tmp_path)
    foreign = Path(candidate["data_dir"]) / "foreign.stl"
    foreign.write_bytes(b"not owned")
    with pytest.raises(ValueError, match="destination_not_empty"):
        migration_owner.preflight(candidate, backup_id="verified-backup")
    assert foreign.read_bytes() == b"not owned"


def test_stale_plan_cannot_start(db_session, tmp_path, migration_owner):
    result = migration_owner.preflight(
        migration_candidate(tmp_path), backup_id="verified-backup"
    )
    with pytest.raises(ValueError, match="plan_stale"):
        migration_owner.start(result["id"], "different-plan")
    assert migration_owner.get(result["id"])["state"] == "planned"


def test_source_audit_refuses_missing_primary(db_session, tmp_path, migration_owner):
    from tests.factories import build_file, build_model

    build_file(db_session, build_model(db_session), path=str(tmp_path / "missing.stl"))
    with pytest.raises(ValueError, match="migration_audit_failed"):
        migration_owner.preflight(
            migration_candidate(tmp_path), backup_id="verified-backup"
        )


def test_cutover_includes_baseline_ingestion_delta(
    db_session, tmp_path, migration_owner
):
    from app.db.models import File, VaultGeneration
    from app.modules.storage.storage_backend.runtime import get_backend
    from tests.factories import build_model, build_stored_file

    source = get_backend()
    baseline = build_stored_file(
        db_session, source, build_model(db_session), data=b"baseline"
    )
    baseline_id, old_key = baseline.id, baseline.path
    plan = migration_owner.preflight(
        migration_candidate(tmp_path), backup_id="verified-backup"
    )
    migration_owner.start(plan["id"], plan["plan_digest"])
    migration_owner.advance(plan["id"])
    delta = build_stored_file(
        db_session, source, build_model(db_session), data=b"delta"
    )
    delta_id, delta_key = delta.id, delta.path
    result = migration_owner.cutover(plan["id"])
    assert result["state"] == "active"
    assert result["delta_objects"] == 1
    assert result["verified_objects"] == 2
    assert result["post_audit"]["critical_count"] == 0
    db_session.expire_all()
    assert (
        get_backend().read_bytes(db_session.get(File, baseline_id).path) == b"baseline"
    )
    assert get_backend().read_bytes(db_session.get(File, delta_id).path) == b"delta"
    assert source.read_bytes(old_key) == b"baseline"
    assert source.read_bytes(delta_key) == b"delta"
    assert db_session.get(VaultGeneration, 1).activation_run_id == plan["id"]


def test_baseline_blocks_logical_purge_before_claiming_rows(
    db_session, tmp_path, migration_owner
):
    from app.core.errors import OperationError
    from app.db.models import Model
    from app.modules.library.trash import hard_delete_model, soft_delete_model
    from app.modules.storage.storage_backend.runtime import get_backend
    from tests.factories import build_model, build_stored_file

    model = build_model(db_session)
    artifact = build_stored_file(db_session, get_backend(), model, data=b"retained")
    model_id, key = model.id, artifact.path
    plan = migration_owner.preflight(
        migration_candidate(tmp_path), backup_id="verified"
    )
    migration_owner.start(plan["id"], plan["plan_digest"])
    soft_delete_model(db_session, model)
    db_session.commit()
    with pytest.raises(OperationError, match="storage_snapshot_retained"):
        hard_delete_model(db_session, model)
    db_session.expire_all()
    retained = db_session.get(Model, model_id)
    assert retained.deleted_at is not None
    assert retained.purge_token is None
    assert get_backend().read_bytes(key) == b"retained"


def test_baseline_blocks_provider_reconfiguration(
    db_session, tmp_path, migration_owner
):
    from app.core.errors import OperationError
    from app.modules.administration.runtime_config import update_storage_provider
    from app.modules.storage.storage_backend.runtime import get_backend

    source = get_backend()
    candidate = migration_candidate(tmp_path)
    plan = migration_owner.preflight(candidate, backup_id="verified")
    migration_owner.start(plan["id"], plan["plan_digest"])
    with pytest.raises(OperationError, match="storage_snapshot_retained"):
        update_storage_provider(db_session, provider="local", raw_config=candidate)
    assert get_backend() is source


def test_baseline_blocks_restore_before_backup_lookup(
    db_session, tmp_path, migration_owner
):
    from app.core.errors import OperationError
    from app.modules.backups.backup.restore import restore_backup

    plan = migration_owner.preflight(
        migration_candidate(tmp_path), backup_id="verified"
    )
    migration_owner.start(plan["id"], plan["plan_digest"])
    with pytest.raises(OperationError, match="storage_snapshot_retained"):
        restore_backup("does-not-exist")


@pytest.mark.parametrize("phase", ["draining", "delta_copy", "verifying", "activating"])
def test_restart_before_activation_requires_explicit_resume(
    db_session, tmp_path, migration_owner, monkeypatch, phase
):
    from app.modules.storage import migration_journal
    from app.modules.storage.storage_backend.runtime import get_backend
    from tests.factories import build_model, build_stored_file

    class SimulatedCrash(BaseException):
        pass

    source = get_backend()
    artifact = build_stored_file(
        db_session, source, build_model(db_session), data=b"restart"
    )
    key = artifact.path
    plan = migration_owner.preflight(
        migration_candidate(tmp_path), backup_id="verified"
    )
    migration_owner.start(plan["id"], plan["plan_digest"])
    migration_owner.advance(plan["id"])
    original = migration_owner._phase

    def crash_after_phase(run_id, state, **kwargs):
        original(run_id, state, **kwargs)
        if state == phase:
            raise SimulatedCrash()

    with monkeypatch.context() as crash:
        crash.setattr(migration_owner, "_phase", crash_after_phase)
        with pytest.raises(SimulatedCrash):
            migration_owner.cutover(plan["id"])
    assert migration_journal.inspect_before_writes()
    result = migration_owner.recover(plan["id"])
    assert result["state"] == "paused"
    assert get_backend().read_bytes(key) == b"restart"
    migration_owner.resume(plan["id"])
    result = migration_owner.advance(plan["id"])
    assert result["skipped_objects"] == 1
    assert migration_owner.cutover(plan["id"])["state"] == "active"


def test_restart_after_database_commit_keeps_destination_authoritative(
    db_session, tmp_path, migration_owner, monkeypatch
):
    from app.db.models import File
    from app.modules.storage import migration_journal
    from app.modules.storage.storage_backend.runtime import get_backend
    from tests.factories import build_model, build_stored_file

    class SimulatedCrash(BaseException):
        pass

    source = get_backend()
    artifact = build_stored_file(
        db_session, source, build_model(db_session), data=b"committed"
    )
    artifact_id, old_key = artifact.id, artifact.path
    plan = migration_owner.preflight(
        migration_candidate(tmp_path), backup_id="verified"
    )
    migration_owner.start(plan["id"], plan["plan_digest"])
    original = migration_journal.append

    def crash_after_commit(event):
        original(event)
        if event["phase"] == "database_active":
            raise SimulatedCrash()

    with monkeypatch.context() as crash:
        crash.setattr(migration_journal, "append", crash_after_commit)
        with pytest.raises(SimulatedCrash):
            migration_owner.cutover(plan["id"])
    assert migration_journal.inspect_before_writes()
    result = migration_owner.recover(plan["id"])
    assert result["state"] == "active"
    db_session.expire_all()
    new_key = db_session.get(File, artifact_id).path
    assert new_key != old_key
    assert get_backend().read_bytes(new_key) == b"committed"
    assert source.read_bytes(old_key) == b"committed"


def test_destination_recovery_respects_post_activation_deletion(
    db_session, tmp_path, migration_owner
):
    from app.db.models import Model
    from app.modules.library.trash import hard_delete_model
    from app.modules.storage import migration_journal, vault_migration
    from app.modules.storage.storage_backend.runtime import get_backend
    from app.modules.storage.storage_deletion import process_storage_delete_intents
    from tests.factories import build_model, build_stored_file

    source = get_backend()
    model = build_model(db_session)
    artifact = build_stored_file(db_session, source, model, data=b"deleted later")
    model_id, source_key = model.id, artifact.path
    plan = migration_owner.preflight(
        migration_candidate(tmp_path), backup_id="verified"
    )
    migration_owner.start(plan["id"], plan["plan_digest"])
    migration_owner.cutover(plan["id"])
    destination = get_backend()
    vault_migration.record_first_destination_write()
    db_session.expire_all()
    destination_key = artifact.path
    hard_delete_model(db_session, db_session.get(Model, model_id))
    db_session.commit()
    process_storage_delete_intents()
    assert not destination.exists(destination_key)

    result = migration_owner.recover(plan["id"])

    assert result["state"] == "active"
    assert result["post_audit"]["state"] == "completed"
    assert result["post_audit"]["critical_count"] == 0
    assert get_backend().blob_key("probe", 1, "a.stl") == destination.blob_key(
        "probe", 1, "a.stl"
    )
    assert db_session.get(Model, model_id) is None
    assert source.read_bytes(source_key) == b"deleted later"
    assert not migration_journal.inspect_before_writes()


def test_destination_recovery_refuses_corrupted_current_artifact(
    db_session, tmp_path, migration_owner
):
    from app.modules.storage import vault_migration
    from app.modules.storage.storage_backend.runtime import get_backend
    from app.runtime.maintenance import restore_in_progress
    from tests.factories import build_model, build_stored_file

    artifact = build_stored_file(
        db_session, get_backend(), build_model(db_session), data=b"current"
    )
    plan = migration_owner.preflight(
        migration_candidate(tmp_path), backup_id="verified"
    )
    migration_owner.start(plan["id"], plan["plan_digest"])
    migration_owner.cutover(plan["id"])
    vault_migration.record_first_destination_write()
    db_session.expire_all()
    Path(artifact.path).write_bytes(b"corrupt")

    with pytest.raises(ValueError, match="migration_recovery_destination_changed"):
        migration_owner.recover(plan["id"])

    assert restore_in_progress()
    assert get_backend().read_bytes(artifact.path) == b"corrupt"


def test_drain_timeout_leaves_source_authoritative(
    db_session, tmp_path, migration_owner, monkeypatch
):
    from app.modules.storage.storage_backend.runtime import get_backend
    from app.runtime import maintenance
    from tests.factories import build_model, build_stored_file

    source = get_backend()
    artifact = build_stored_file(
        db_session, source, build_model(db_session), data=b"source"
    )
    original_key = artifact.path
    plan = migration_owner.preflight(
        migration_candidate(tmp_path), backup_id="verified"
    )
    migration_owner.start(plan["id"], plan["plan_digest"])
    monkeypatch.setattr(maintenance, "_RESTORE_DRAIN_TIMEOUT_S", 0)
    assert maintenance.begin_mutating_operation()
    try:
        with pytest.raises(maintenance.RestoreConflictError):
            migration_owner.cutover(plan["id"])
    finally:
        maintenance.end_mutating_operation()
    assert not maintenance.restore_in_progress()
    db_session.expire_all()
    assert artifact.path == original_key
    assert get_backend().read_bytes(original_key) == b"source"
    assert migration_owner.get(plan["id"])["state"] == "paused"


def test_changed_destination_receipt_is_never_adopted_on_resume(
    db_session, tmp_path, migration_owner
):
    from sqlmodel import select

    from app.db.models import VaultMigrationObject
    from app.modules.storage.storage_backend.runtime import get_backend
    from tests.factories import build_model, build_stored_file

    source = get_backend()
    artifact = build_stored_file(
        db_session, source, build_model(db_session), data=b"original"
    )
    plan = migration_owner.preflight(
        migration_candidate(tmp_path), backup_id="verified"
    )
    migration_owner.start(plan["id"], plan["plan_digest"])
    migration_owner.advance(plan["id"])
    obj = db_session.exec(
        select(VaultMigrationObject).where(VaultMigrationObject.run_id == plan["id"])
    ).one()
    destination = Path(obj.destination_key)
    destination.unlink()
    destination.write_bytes(b"foreign replacement")
    migration_owner.resume(plan["id"])
    with pytest.raises(ValueError, match="migration_destination_identity_changed"):
        migration_owner.advance(plan["id"])
    result = migration_owner.get(plan["id"])
    assert result["state"] == "paused"
    assert result["retryable"] is False
    assert source.read_bytes(artifact.path) == b"original"
    assert destination.read_bytes() == b"foreign replacement"


def test_local_destination_cannot_overlap_source_roots(
    db_session, tmp_path, migration_owner
):
    from app.modules.storage.storage_backend.runtime import get_backend

    candidate = migration_candidate(tmp_path)
    candidate["data_dir"] = str(get_backend().data_dir / "nested")
    Path(candidate["data_dir"]).mkdir()
    with pytest.raises(ValueError, match="migration_destination_roots_overlap"):
        migration_owner.preflight(candidate, backup_id="verified")


def test_incomplete_native_upload_is_fenced_to_retained_source(
    db_session, tmp_path, migration_owner
):
    import json

    from app.core.secrets import encrypt_secret
    from app.db.models import ArtifactUploadSession, ArtifactUploadState
    from app.modules.ingestion.artifact_uploads import (
        ArtifactUploadError,
        SqlArtifactUploadManager,
    )
    from app.modules.storage.migration_identity import namespace_ref
    from app.modules.storage.storage_backend.runtime import get_backend
    from tests.factories import build_artifact_upload, build_user

    source = get_backend()
    actor = build_user(db_session)
    upload = build_artifact_upload(
        db_session,
        actor,
        adapter_id="native_parts",
        state=ArtifactUploadState.UPLOADING,
        destination_ref=namespace_ref(source),
        protected_native_id=encrypt_secret(
            json.dumps(
                {
                    "key": source.capture_upload_slot_key("native-incomplete"),
                    "upload_id": "retained-native-handle",
                    "ownership_token": "exact-owner",
                }
            )
        ),
    )
    upload_id = upload.id
    plan = migration_owner.preflight(
        migration_candidate(tmp_path),
        backup_id="verified",
        policy={"retention_days": 0},
    )
    migration_owner.start(plan["id"], plan["plan_digest"])
    migration_owner.cutover(plan["id"])
    db_session.expire_all()
    fenced = db_session.get(ArtifactUploadSession, upload_id)
    assert fenced.state == "failed"
    assert fenced.error_code == "artifact_upload_vault_generation_changed"
    assert fenced.retryable is False
    manager = SqlArtifactUploadManager(db_session)
    with pytest.raises(ArtifactUploadError, match="state_conflict"):
        manager.finalize(upload_id, actor)
    assert namespace_ref(manager.adapter_for(fenced).backend) == namespace_ref(source)
    with pytest.raises(ValueError, match="migration_source_upload_cleanup_required"):
        migration_owner.cleanup(plan["id"], confirmation=plan["id"], source=True)


def test_completed_native_staging_relocates_encrypted_receipt(
    db_session, tmp_path, migration_owner
):
    import hashlib
    import json
    from dataclasses import asdict

    from app.core.secrets import encrypt_secret
    from app.db.models import ArtifactUploadSession, ArtifactUploadState
    from app.modules.ingestion.artifact_uploads import SqlArtifactUploadManager
    from app.modules.ingestion.artifact_uploads.native_parts import (
        NativeMultipartUploadAdapter,
    )
    from app.modules.storage.migration_identity import namespace_ref
    from app.modules.storage.storage_backend.runtime import get_backend
    from tests.factories import build_artifact_upload, build_user

    source = get_backend()
    actor = build_user(db_session)
    receipt = source.create_bytes(
        b"data", source.capture_upload_slot_key("native-completed")
    )
    protected = encrypt_secret(
        json.dumps(
            {
                "key": receipt.key,
                "upload_id": "completed-native-handle",
                "ownership_token": "exact-owner",
                "completion_receipt": asdict(receipt),
            }
        )
    )
    upload = build_artifact_upload(
        db_session,
        actor,
        adapter_id="native_parts",
        state=ArtifactUploadState.VERIFYING,
        destination_ref=namespace_ref(source),
        client_sha256=hashlib.sha256(b"data").hexdigest(),
        protected_native_id=protected,
        error_code="artifact_upload_verification_interrupted",
        retryable=True,
    )
    upload_id = upload.id
    plan = migration_owner.preflight(
        migration_candidate(tmp_path), backup_id="verified"
    )
    migration_owner.start(plan["id"], plan["plan_digest"])
    migration_owner.cutover(plan["id"])
    db_session.expire_all()
    relocated = db_session.get(ArtifactUploadSession, upload_id)
    assert relocated.destination_ref == namespace_ref(get_backend())
    moved = NativeMultipartUploadAdapter.completion_receipt(relocated)
    assert moved.key != receipt.key
    assert get_backend().creation_matches(moved)
    assert source.creation_matches(receipt)
    _, staged = SqlArtifactUploadManager(db_session).finalize(upload_id, actor)
    assert staged.materialize().read_bytes() == b"data"


def test_api_chunk_session_can_finish_after_cutover(
    db_session, tmp_path, migration_owner
):
    import hashlib

    from app.modules.ingestion.artifact_uploads import (
        SqlArtifactUploadManager,
        UploadRequest,
    )
    from app.modules.ingestion.artifact_uploads.contracts import ChunkReceipt
    from tests.factories import build_user

    actor = build_user(db_session)
    upload = SqlArtifactUploadManager(db_session).create(
        UploadRequest(
            purpose="model",
            target_role="new_model",
            target_id=None,
            filename="part.stl",
            media_type="model/stl",
            size_bytes=4,
            client_sha256=None,
            options={},
        ),
        actor,
    )
    plan = migration_owner.preflight(
        migration_candidate(tmp_path), backup_id="verified"
    )
    migration_owner.start(plan["id"], plan["plan_digest"])
    migration_owner.cutover(plan["id"])
    manager = SqlArtifactUploadManager(db_session)
    manager.record_chunk(
        upload.id,
        ChunkReceipt(
            index=0, offset=0, size_bytes=4, sha256=hashlib.sha256(b"data").hexdigest()
        ),
        b"data",
        actor,
    )
    _, staged = manager.finalize(upload.id, actor)
    assert staged.materialize().read_bytes() == b"data"


@pytest.fixture
def active_copy(db_session, tmp_path, migration_owner):
    from app.modules.storage.storage_backend.runtime import get_backend
    from tests.factories import build_model, build_stored_file

    source = get_backend()
    artifact = build_stored_file(
        db_session, source, build_model(db_session), data=b"retain"
    )
    plan = migration_owner.preflight(
        migration_candidate(tmp_path),
        backup_id="verified",
        policy={"retention_days": 0},
    )
    migration_owner.start(plan["id"], plan["plan_digest"])
    migration_owner.cutover(plan["id"])
    migration_owner.full_audit(plan["id"])
    return migration_owner, plan, source, artifact.path


def test_cleanup_preserves_changed_source_identity(active_copy):
    owner, plan, source, key = active_copy
    path = Path(key)
    path.unlink()
    path.write_bytes(b"foreign replacement")
    result = owner.cleanup(
        plan["id"], confirmation=plan["id"], source=True, backup_id="fresh"
    )
    assert result["state"] == "active"
    assert result["cleanup_findings"][0]["code"] == "identity_changed"
    assert source.read_bytes(key) == b"foreign replacement"


@pytest.mark.parametrize("remove_credentials", [False, True])
def test_explicit_retention_never_deletes_source(
    active_copy, db_session, remove_credentials
):
    from app.db.models import VaultMigrationRun

    owner, plan, source, key = active_copy
    result = owner.retain(plan["id"], remove_credentials=remove_credentials)
    assert result["state"] == "complete"
    assert result["cleanup_after"] is None
    assert result["cleanup_outcome"] == (
        "manual_cleanup" if remove_credentials else "retained_indefinitely"
    )
    assert source.read_bytes(key) == b"retain"
    db_session.expire_all()
    assert (
        db_session.get(VaultMigrationRun, plan["id"]).source_config == "{}"
    ) is remove_credentials


def test_cleanup_waits_for_old_generation_stream(db_session, tmp_path, migration_owner):
    from app.modules.storage.storage_backend import generations
    from app.modules.storage.storage_backend.runtime import get_backend
    from tests.factories import build_model, build_stored_file

    source = get_backend()
    artifact = build_stored_file(
        db_session, source, build_model(db_session), data=b"stream"
    )
    key = artifact.path
    plan = migration_owner.preflight(
        migration_candidate(tmp_path),
        backup_id="verified",
        policy={"retention_days": 0},
    )
    migration_owner.start(plan["id"], plan["plan_digest"])
    pinned = generations.pin()
    pinned.planned()
    try:
        migration_owner.cutover(plan["id"])
        migration_owner.full_audit(plan["id"])
        with pytest.raises(ValueError, match="migration_source_readers_busy"):
            migration_owner.cleanup(
                plan["id"], confirmation=plan["id"], source=True, backup_id="fresh"
            )
        assert source.read_bytes(key) == b"stream"
    finally:
        pinned.close()
    assert (
        migration_owner.cleanup(
            plan["id"], confirmation=plan["id"], source=True, backup_id="fresh"
        )["state"]
        == "cleaned"
    )
    assert not source.exists(key)


def test_second_preflight_cannot_create_an_overlapping_workflow(
    db_session, tmp_path, migration_owner
):
    migration_owner.preflight(migration_candidate(tmp_path), backup_id="verified")
    another = tmp_path / "another"
    another.mkdir()
    candidate = migration_candidate(another)
    with pytest.raises(ValueError, match="migration_already_running"):
        migration_owner.preflight(candidate, backup_id="verified")
    assert list(Path(candidate["data_dir"]).iterdir()) == []


def test_discard_deletes_only_receipt_proven_destination_copy(
    db_session, tmp_path, migration_owner
):
    from sqlmodel import select

    from app.db.models import VaultMigrationObject
    from app.modules.storage.storage_backend.runtime import get_backend
    from tests.factories import build_model, build_stored_file

    source = get_backend()
    artifact = build_stored_file(
        db_session, source, build_model(db_session), data=b"original"
    )
    plan = migration_owner.preflight(
        migration_candidate(tmp_path), backup_id="verified"
    )
    migration_owner.start(plan["id"], plan["plan_digest"])
    migration_owner.advance(plan["id"])
    destination_key = (
        db_session.exec(
            select(VaultMigrationObject).where(
                VaultMigrationObject.run_id == plan["id"]
            )
        )
        .one()
        .destination_key
    )
    result = migration_owner.cleanup(plan["id"], confirmation=plan["id"], source=False)
    assert result["state"] == "discarded"
    assert not Path(destination_key).exists()
    assert get_backend() is source
    assert source.read_bytes(artifact.path) == b"original"


def test_uncertain_destination_copy_is_preserved_on_discard(
    db_session, tmp_path, migration_owner
):
    from sqlmodel import select

    from app.db.models import VaultMigrationObject
    from app.modules.storage.storage_backend.runtime import get_backend
    from tests.factories import build_model, build_stored_file

    source = get_backend()
    build_stored_file(db_session, source, build_model(db_session), data=b"original")
    plan = migration_owner.preflight(
        migration_candidate(tmp_path), backup_id="verified"
    )
    obj = db_session.exec(
        select(VaultMigrationObject).where(VaultMigrationObject.run_id == plan["id"])
    ).one()
    destination = Path(obj.destination_key)
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"uncertain copy")
    result = migration_owner.cleanup(plan["id"], confirmation=plan["id"], source=False)
    assert result["state"] == "planned"
    assert result["cleanup_findings"] == [
        {"object_id": obj.id, "code": "ownership_unverified"}
    ]
    assert destination.read_bytes() == b"uncertain copy"


def test_full_census_preserves_managed_objects_but_excludes_linked_files(
    db_session, tmp_path, migration_owner
):
    import hashlib
    import json

    from app.db.models import DocumentKind
    from app.modules.ingestion.staging_leases import create_capture_slot_lease
    from app.modules.storage.migration_census import census
    from app.modules.storage.storage_backend.factory import build_configured_backend
    from app.modules.storage.storage_backend.runtime import get_backend
    from app.modules.storage.storage_providers import parse_provider_config
    from tests.factories import (
        build_capture_slot,
        build_collection,
        build_document,
        build_file,
        build_inbox_item,
        build_model,
        build_stored_file,
        build_user,
        store_owned_bytes,
    )

    source = get_backend()
    model = build_model(db_session)
    primary = build_stored_file(db_session, source, model, data=b"primary")
    external_path = tmp_path / "linked.stl"
    external_path.write_bytes(b"external")
    external = build_file(
        db_session,
        model,
        path=str(external_path),
        external=True,
        size_bytes=8,
        sha256=hashlib.sha256(b"external").hexdigest(),
    )
    document = build_document(
        db_session,
        kind=DocumentKind.PDF,
        size_bytes=1,
        sha256=hashlib.sha256(b"x").hexdigest(),
    )
    document.body = f"![embedded](/api/v1/documents/{document.id}/images/diagram.png)"
    collection = build_collection(db_session)
    collection.readme = (
        f"![embedded](/api/v1/collections/{collection.id}/images/photo.png)"
    )
    owner = build_user(db_session)
    item = build_inbox_item(db_session, owner)
    slot = build_capture_slot(db_session, item, sha256=hashlib.sha256(b"x").hexdigest())
    slot.storage_key = source.capture_upload_slot_key(slot.id)
    lease = create_capture_slot_lease(
        db_session,
        slot_id=slot.id,
        owner_user_id=owner.id,
        destination_key=slot.storage_key,
        size_bytes=1,
        sha256=slot.sha256,
    )
    managed = {
        primary.path,
        source.thumbnail_key(primary.id),
        source.thumbnail_key(external.id),
        source.stl_cache_key(primary.sha256),
        source.document_file_key(document.id, document.filename),
        source.document_image_key(document.id, "diagram.png"),
        source.collection_image_key(collection.id, "photo.png"),
        slot.storage_key,
    }
    for key in managed - {primary.path}:
        store_owned_bytes(db_session, source, key, b"x")
    for row in (document, collection, slot, lease):
        db_session.add(row)
    db_session.commit()
    source_slot, lease_path = slot.storage_key, lease.path
    candidate_config = migration_candidate(tmp_path)
    candidate = build_configured_backend(parse_provider_config(candidate_config))
    blobs = census(db_session, source, candidate)
    assert {blob.source_key for blob in blobs} == managed
    assert str(external_path) not in {blob.source_key for blob in blobs}
    plan = migration_owner.preflight(candidate_config, backup_id="verified")
    migration_owner.start(plan["id"], plan["plan_digest"])
    migration_owner.cutover(plan["id"])
    destination = get_backend()
    for blob in blobs:
        assert destination.read_bytes(blob.destination_key) == source.read_bytes(
            blob.source_key
        )
    db_session.expire_all()
    assert external.path == str(external_path)
    assert external_path.read_bytes() == b"external"
    assert slot.storage_key != source_slot
    assert lease.destination_key == slot.storage_key
    assert lease.path == lease_path
    assert json.loads(lease.receipt_json)["key"] == slot.storage_key
    assert json.loads(slot.receipt_json)["key"] == slot.storage_key


@pytest.mark.asyncio
async def test_http_stream_retains_coherent_source_generation(db_session, tmp_path):
    import asyncio

    from app.api.vault_generation import VaultGenerationMiddleware
    from app.modules.storage.storage_backend import generations
    from app.modules.storage.storage_backend.runtime import bind_backend, get_backend

    source = get_backend()
    old_key = source.blob_key("reader", 1, "part.stl")
    source.create_bytes(b"old generation", old_key)
    candidate = LocalStorageBackend(
        data_dir=tmp_path / "candidate", thumb_dir=tmp_path / "thumbs"
    )
    headers = asyncio.Event()
    continue_stream = asyncio.Event()
    body = []

    async def application(scope, receive, send):
        catalog_key = old_key
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await continue_stream.wait()
        await send(
            {
                "type": "http.response.body",
                "body": get_backend().read_bytes(catalog_key),
            }
        )

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        if message["type"] == "http.response.start":
            headers.set()
        elif message["type"] == "http.response.body":
            body.append(message["body"])

    task = asyncio.create_task(
        VaultGenerationMiddleware(application)(
            {"type": "http", "path": "/api/v1/files/1/download", "method": "GET"},
            receive,
            send,
        )
    )
    await asyncio.wait_for(headers.wait(), 2)
    with generations.activation():
        generations.publish(candidate, "http-next")
    continue_stream.set()
    await asyncio.wait_for(task, 2)
    assert body == [b"old generation"]
    assert get_backend() is candidate
    bind_backend(source)


@pytest.mark.asyncio
async def test_cutover_drains_background_work_after_response_headers(db_session):
    import asyncio
    import threading

    from app.api.vault_generation import VaultGenerationMiddleware
    from app.runtime.maintenance import (
        begin_restore_maintenance,
        end_restore_maintenance,
        restore_in_progress,
    )

    assert not restore_in_progress()
    headers, complete_background = asyncio.Event(), asyncio.Event()
    entered = threading.Event()

    async def application(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})
        await complete_background.wait()

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        if message["type"] == "http.response.start":
            headers.set()

    request = asyncio.create_task(
        VaultGenerationMiddleware(application)(
            {"type": "http", "path": "/api/v1/maintenance/audits", "method": "POST"},
            receive,
            send,
        )
    )
    await asyncio.wait_for(headers.wait(), 2)

    def cutover():
        begin_restore_maintenance()
        entered.set()

    drain = threading.Thread(target=cutover)
    drain.start()
    async with asyncio.timeout(2):
        while not restore_in_progress():
            await asyncio.sleep(0)
    assert not entered.is_set()
    complete_background.set()
    try:
        await asyncio.wait_for(request, 2)
        drain.join(timeout=2)
        assert entered.is_set()
    finally:
        end_restore_maintenance()
        drain.join(timeout=2)
        assert not drain.is_alive()
