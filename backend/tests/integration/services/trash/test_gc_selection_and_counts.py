"""Selection, accounting, and idempotency contracts for trash GC."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlmodel import Session

from app.db.models import Document, DocumentKind, Model, Tag
from app.services import trash

FROZEN_NOW = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)


def test_hard_delete_expired_models_selects_only_expired_trashed_rows(
    db_session: Session,
    monkeypatch,
) -> None:
    monkeypatch.setattr(trash, "utcnow", lambda: FROZEN_NOW)
    expired = Model(
        name="expired",
        slug="expired",
        hash="expired-hash",
        deleted_at=FROZEN_NOW - timedelta(days=2),
    )
    cutoff = Model(
        name="cutoff",
        slug="cutoff",
        hash="cutoff-hash",
        deleted_at=FROZEN_NOW - timedelta(days=1),
    )
    newer = Model(
        name="newer",
        slug="newer",
        hash="newer-hash",
        deleted_at=FROZEN_NOW,
    )
    restored = Model(name="restored", slug="restored", hash="restored-hash")
    db_session.add_all([expired, cutoff, newer, restored])
    db_session.commit()

    purged_ids = trash.hard_delete_expired_models(db_session, retention_days=1)
    db_session.commit()

    assert set(purged_ids) == {expired.id, cutoff.id}
    assert db_session.get(Model, expired.id) is None
    assert db_session.get(Model, cutoff.id) is None
    assert db_session.get(Model, newer.id) is not None
    assert db_session.get(Model, restored.id) is not None


def test_gc_reports_exact_count_for_mixed_expired_resource_categories(
    db_session: Session,
    monkeypatch,
) -> None:
    monkeypatch.setattr(trash, "utcnow", lambda: FROZEN_NOW)
    expired_at = FROZEN_NOW - timedelta(days=1)
    model = Model(
        name="expired-model",
        slug="expired-model",
        hash="expired-model-hash",
        deleted_at=expired_at,
    )
    document = Document(
        name="expired-document",
        kind=DocumentKind.MARKDOWN,
        body="obsolete",
        deleted_at=expired_at,
    )
    tag = Tag(name="expired-tag", slug="expired-tag", deleted_at=expired_at)
    db_session.add_all([model, document, tag])
    db_session.commit()
    model_id = model.id
    document_id = document.id
    tag_id = tag.id

    result = trash.gc_soft_deleted(retention_days=0)

    assert result["rows"] == 3
    assert result["storage_completed"] == 0
    db_session.expire_all()
    assert db_session.get(Model, model_id) is None
    assert db_session.get(Document, document_id) is None
    assert db_session.get(Tag, tag_id) is None


def test_repeated_gc_reports_no_work_for_an_already_purged_resource(
    db_session: Session,
    monkeypatch,
) -> None:
    monkeypatch.setattr(trash, "utcnow", lambda: FROZEN_NOW)
    model = Model(
        name="purged-once",
        slug="purged-once",
        hash="purged-once-hash",
        deleted_at=FROZEN_NOW - timedelta(days=1),
    )
    db_session.add(model)
    db_session.commit()
    model_id = model.id

    first = trash.gc_soft_deleted(retention_days=0)
    second = trash.gc_soft_deleted(retention_days=0)

    assert first["rows"] == 1
    assert second["rows"] == 0
    db_session.expire_all()
    assert db_session.get(Model, model_id) is None
