"""Snapshot and archive creation under the maintenance operation lock."""

from __future__ import annotations

import gzip
import io
import json
import os
import tarfile
import tempfile
import uuid
from pathlib import Path

import app.modules.backups.backup.archive_format as _archive_format_module
import app.modules.backups.backup.contracts as _contracts_module
import app.modules.backups.backup.snapshot as _snapshot_module
import app.modules.backups.backup_destination as _backup_destination_module
from app.core.config import settings
from app.core.logging import get_logger
from app.core.time import utcnow
from app.db.session import get_session_factory
from app.modules.administration import audit
from app.modules.storage import capacity_estimates
from app.modules.storage.capacity import CapacityManager
from app.modules.storage.storage_backend.runtime import get_backend
from app.modules.storage.storage_inventory import inventory
from app.runtime.maintenance import exclusive_backup_operation

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@exclusive_backup_operation
def create_backup(
    *,
    trigger: _backup_destination_module.BackupTrigger = _backup_destination_module.BackupTrigger.MANUAL,
) -> _contracts_module.BackupMeta:
    """Build one archive and durably account for every selected destination."""
    from app.modules.backups import backup_runs

    _snapshot_module._require_database_backup_support()
    backup_id = uuid.uuid4().hex[:12]
    timestamp = utcnow()
    archive_name = f"{_contracts_module._BACKUP_NAME_PREFIX}{timestamp.strftime('%Y%m%d-%H%M%S')}-{backup_id}.tar.gz"
    selected = backup_runs.begin_run(
        backup_id=backup_id,
        archive_name=archive_name,
        trigger=trigger,
        created_at=timestamp,
    )
    if not selected.selected:
        backup_runs.finish_run(
            selected.run_id, error_code="backup_destination_required"
        )
        raise backup_runs.BackupRunError("backup_destination_required", selected.run_id)
    try:
        with get_session_factory().scoped_session() as capacity_session:
            estimate = inventory(capacity_session).unique_owned_bytes
        # The database file and WAL overlap the archive while snapshotting.
        db_path = _snapshot_module._db_path()
        if db_path is not None:
            estimate += db_path.stat().st_size
            wal = Path(str(db_path) + "-wal")
            if wal.exists():
                estimate += wal.stat().st_size
        with CapacityManager(get_session_factory()).hold(
            f"backup:{backup_id}",
            capacity_estimates.backup_create(estimate, Path(selected.local_directory)),
        ) as capacity_claim:
            meta = _create_selected_backup(
                selected,
                backup_id=backup_id,
                timestamp=timestamp,
                archive_name=archive_name,
                trigger=trigger,
                capacity_claim=capacity_claim,
            )
    except BaseException as exc:
        reason = (
            "backup_all_destinations_failed"
            if str(exc) == "backup_all_destinations_failed"
            else "backup_archive_build_failed"
        )
        backup_runs.finish_run(selected.run_id, error_code=reason)
        if isinstance(exc, Exception) and reason == "backup_all_destinations_failed":
            raise backup_runs.BackupRunError(reason, selected.run_id) from exc
        raise
    meta.run_id = selected.run_id
    meta.outcome = backup_runs.finish_run(selected.run_id)
    meta.destination_results = backup_runs.run_detail(selected.run_id)["destinations"]
    return meta


def _create_selected_backup(
    selected, *, backup_id, timestamp, archive_name, trigger, capacity_claim=None
):
    from app.modules.backups.backup_replication import prepare_destinations

    target, remote_destinations = prepare_destinations(selected)
    ts = timestamp.isoformat()
    archive_path = Path(selected.local_directory) / archive_name
    backend_name = settings.storage_backend

    written_files = 0
    with _snapshot_module._sqlite_snapshot_file() as db_snapshot:
        censused_sizes = dict(_snapshot_module._find_snapshot_blobs(db_snapshot))
        file_entries = _snapshot_module._manifest_blobs(db_snapshot)
        for entry in file_entries:
            key = str(entry["key"])
            if key in censused_sizes and int(entry["size"]) != censused_sizes[key]:
                logger.error("backup %s failed while streaming owned blobs", backup_id)
                raise RuntimeError("backup_blob_size_changed")
        total_size = db_snapshot.stat().st_size + sum(
            int(entry["size"]) for entry in file_entries
        )
        if capacity_claim is not None:
            capacity_claim.renew(
                capacity_estimates.backup_create(
                    total_size, Path(selected.local_directory)
                )
            )
        manifest_namespaces = sorted(
            {str(entry["namespace"]) for entry in file_entries}
        )
        from app.modules.storage.migration_identity import namespace_ref

        manifest = {
            "version": _contracts_module.MANIFEST_VERSION,
            "created_at": ts,
            "app_version": settings.app_version,
            "storage_backend": backend_name,
            "vault_target_ref": namespace_ref(get_backend())
            if get_backend().storage_target is not None
            else None,
            "provider_id": str(
                getattr(get_backend(), "provider_id", get_backend().backend_name)
            ),
            "transport": str(
                getattr(get_backend(), "transport", get_backend().backend_name)
            ),
            "namespace": (
                manifest_namespaces[0] if len(manifest_namespaces) == 1 else None
            ),
            "namespaces": manifest_namespaces,
            "file_count": len(file_entries),
            "total_size_bytes": total_size,
            "files": file_entries,
        }
        manifest_bytes = json.dumps(manifest, indent=2).encode("utf-8")

        settings.backup_dir.mkdir(parents=True, exist_ok=True)
        fd, raw_archive_temp = tempfile.mkstemp(
            prefix=".printstash-backup-build-", dir=settings.backup_dir
        )
        archive_temp = Path(raw_archive_temp)
        try:
            with os.fdopen(fd, "wb") as archive_file:
                with gzip.GzipFile(fileobj=archive_file, mode="wb") as gz:
                    with tarfile.open(fileobj=gz, mode="w|") as tar:
                        man_info = tarfile.TarInfo(name="manifest.json")
                        man_info.size = len(manifest_bytes)
                        tar.addfile(man_info, io.BytesIO(manifest_bytes))

                        # tarfile streams this file; the database is never loaded
                        # as one in-memory bytes object.
                        tar.add(db_snapshot, arcname="db.sqlite3", recursive=False)

                        for entry in file_entries:
                            key = str(entry["key"])
                            arc = str(entry["member"])
                            written = _snapshot_module._add_file_to_tar(tar, key, arc)
                            expected = int(entry["size"])
                            if written != expected:
                                raise RuntimeError("backup_blob_size_changed")
                            written_files += 1
            _snapshot_module._validate_created_archive_payload(archive_temp)
        except Exception:
            archive_temp.unlink(missing_ok=True)
            logger.exception("backup %s failed while streaming owned blobs", backup_id)
            raise

    final_size = archive_temp.stat().st_size
    archive_sha256 = _archive_format_module._sha256_path(archive_temp)
    from app.modules.backups import backup_replication, backup_runs

    backup_runs.archive_ready(
        selected.run_id,
        digest=archive_sha256,
        size=final_size,
        file_count=len(file_entries),
    )
    try:
        created_sources = backup_replication.publish_archive(
            selected,
            archive_temp=archive_temp,
            archive_path=archive_path,
            archive_name=archive_name,
            backup_id=backup_id,
            ts=ts,
            backend_name=backend_name,
            file_count=len(file_entries),
            written_files=written_files,
            final_size=final_size,
            archive_sha256=archive_sha256,
            target=target,
            remote_destinations=remote_destinations,
        )
    finally:
        archive_temp.unlink(missing_ok=True)

    archive_temp.unlink(missing_ok=True)
    if not created_sources:
        raise RuntimeError("backup_all_destinations_failed")

    # Destination ledger rows were committed immediately after each
    # publication. Keep this audit transaction separate from ownership state.
    with get_session_factory().session() as session:
        audit.record(
            session,
            action="backup.create",
            resource_type="backup",
            diff={
                "backup_id": backup_id,
                "size_bytes": final_size,
                "file_count": written_files,
                "trigger": trigger.value,
                "locations": [source.location for source in created_sources],
            },
        )
        session.commit()

    return created_sources[0]
