from __future__ import annotations

from sqlmodel import Session

from app.db.models import (
    File,
    FileType,
    Model,
    User,
    VaultAuditMode,
    VaultAuditRunState,
)
from app.services import vault_audit


def test_quick_audit_persists_missing_owned_blob_finding(db_session: Session) -> None:
    user = User(username="auditor", hashed_password="x", is_superuser=True)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    model = Model(name="Missing", slug="missing", hash="a" * 64)
    db_session.add(model)
    db_session.commit()
    db_session.refresh(model)
    db_session.add(
        File(
            model_id=model.id,
            path="definitely-missing.stl",
            original_filename="missing.stl",
            file_type=FileType.STL,
            size_bytes=12,
            sha256="b" * 64,
        )
    )
    db_session.commit()

    run, created = vault_audit.create_run(db_session, user.id, VaultAuditMode.QUICK)
    assert created is True
    vault_audit.execute_run(run.id)
    db_session.expire_all()

    result = vault_audit.read_run(db_session, db_session.get(type(run), run.id))
    assert result.state == VaultAuditRunState.COMPLETED
    assert any(item.code == "owned_blob_missing" for item in result.findings)
    assert all("/" not in item.resource_identifier for item in result.findings)


def test_concurrent_audit_start_returns_active_run(db_session: Session) -> None:
    user = User(username="auditor-2", hashed_password="x", is_superuser=True)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    first, created = vault_audit.create_run(db_session, user.id, VaultAuditMode.QUICK)
    second, duplicate_created = vault_audit.create_run(
        db_session, user.id, VaultAuditMode.FULL
    )
    assert created is True
    assert duplicate_created is False
    assert second.id == first.id
