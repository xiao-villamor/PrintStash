"""Durable migration rows with valid proofs and unique workflow identities."""

import hashlib
import json
from datetime import timedelta
from typing import Any

from sqlmodel import Session

from app.core.config import settings
from app.core.time import utcnow
from app.db.models import VaultGeneration, VaultMigrationObject, VaultMigrationRun
from tests.factories._support import save


def build_vault_migration(session: Session, **overrides: Any) -> VaultMigrationRun:
    config = json.dumps(
        {
            "provider": "local",
            "data_dir": str(settings.data_dir),
            "thumb_dir": str(settings.thumb_dir),
        }
    )
    defaults = {
        "source_epoch": "0",
        "source_config": config,
        "destination_config": config,
        "source_digest": hashlib.sha256(config.encode()).hexdigest(),
        "plan_digest": "a" * 64,
        "backup_id": "verified-backup",
        "expires_at": utcnow() + timedelta(minutes=30),
    }
    return save(session, VaultMigrationRun(**(defaults | overrides)))


def build_vault_migration_object(
    session: Session, run: VaultMigrationRun, **overrides: Any
) -> VaultMigrationObject:
    source = str(overrides.get("source_key", settings.data_dir / f"{run.id}/part.stl"))
    defaults = {
        "run_id": run.id,
        "source_key": source,
        "source_key_digest": hashlib.sha256(source.encode()).hexdigest(),
        "destination_key": source + ".migrated",
        "resource_type": "file",
        "resource_id": "1",
        "size_bytes": 4,
        "sha256": hashlib.sha256(b"data").hexdigest(),
    }
    return save(session, VaultMigrationObject(**(defaults | overrides)))


def build_vault_generation(
    session: Session, run: VaultMigrationRun, **overrides: Any
) -> VaultGeneration:
    return save(
        session,
        VaultGeneration(
            **(
                {
                    "epoch": run.destination_epoch,
                    "activation_run_id": run.id,
                    "manifest_sha256": run.manifest_sha256,
                }
                | overrides
            )
        ),
    )
