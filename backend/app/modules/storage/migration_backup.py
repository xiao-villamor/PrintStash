"""Bind migration's recovery prerequisite to one verified archive and Vault."""

import hashlib
from datetime import datetime, timedelta

from app.core.time import ensure_utc, utcnow
from app.modules.backups.backup.archive_format import read_archive_manifest
from app.modules.backups.backup.catalogue import get_backup, get_backup_archive_path
from app.modules.backups.backup.verification import verify_backup
from app.modules.storage.migration_identity import namespace_ref
from app.modules.storage.storage_backend.runtime import get_bound_backend


def verify_migration_backup(
    backup_id: str,
    source_ref: str | None,
    *,
    created_after: datetime | None = None,
    expected_digest: str | None = None,
) -> dict[str, object]:
    meta = get_backup(backup_id, source_ref=source_ref)
    if meta is None:
        raise ValueError("migration_recent_backup_required")
    created = ensure_utc(datetime.fromisoformat(meta.created_at))
    if not timedelta(0) <= utcnow() - created <= timedelta(days=7):
        raise ValueError("migration_recent_backup_required")
    if created_after is not None and created <= ensure_utc(created_after):
        raise ValueError("migration_post_activation_backup_required")
    exact_source = meta.source_ref or source_ref
    archive = get_backup_archive_path(backup_id, source_ref=exact_source)
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if expected_digest is not None and expected_digest != digest:
        raise ValueError("migration_backup_identity_changed")
    proof = verify_backup(backup_id, archive_path=archive, record_audit=False)
    if not proof.valid or not proof.app_compatible:
        raise ValueError("migration_verified_compatible_backup_required")
    try:
        manifest = read_archive_manifest(archive)
        target = get_bound_backend().storage_target
        if target is None or manifest.get("vault_target_ref") != namespace_ref(
            get_bound_backend()
        ):
            raise ValueError("migration_backup_vault_mismatch")
    except RuntimeError as exc:
        raise ValueError("migration_backup_vault_mismatch") from exc
    with archive.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
            raise ValueError("migration_backup_identity_changed")
    return {
        "backup_id": backup_id,
        "source_ref": exact_source,
        "archive_sha256": digest,
        "vault_target_ref": namespace_ref(get_bound_backend()),
        "created_at": created.isoformat(),
        "verified_at": utcnow().isoformat(),
    }
