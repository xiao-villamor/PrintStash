"""Defends ``test_batch_move_succeeds_with_edit_on_source_and_dest`` behavior for the ``models`` production unit.

A failure means this boundary no longer preserves its observable contract.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db.models import (
    CollectionRole,
    File,
    FileType,
    Model,
    ModelTagLink,
    Tag,
    User,
)
from app.services import taxonomy
from app.services.auth import create_access_token, hash_password


def _user(session: Session, username: str, *, superuser: bool = False) -> User:
    user = User(
        username=username,
        hashed_password=hash_password("Password123"),
        is_active=True,
        is_superuser=superuser,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _headers(user: User) -> dict[str, str]:
    scope = "admin" if user.is_superuser else "write"
    token = create_access_token(user.id, user.username, scope=scope)
    return {"Authorization": f"Bearer {token}"}


def _grant(
    session: Session, user: User, collection_id: int, role: CollectionRole
) -> None:
    from app.db.models import CollectionPermission

    session.add(
        CollectionPermission(user_id=user.id, collection_id=collection_id, role=role)
    )
    session.commit()


def _model(session: Session, name: str, collection_id: int | None) -> Model:
    import hashlib

    model = Model(
        name=name,
        slug=name.lower().replace(" ", "-"),
        hash=hashlib.sha256(name.encode()).hexdigest(),
        collection_id=collection_id,
    )
    session.add(model)
    session.commit()
    session.refresh(model)
    return model


def _tag_slugs(session: Session, model_id: int) -> set[str]:
    rows = session.exec(
        select(Tag.slug)
        .join(ModelTagLink, ModelTagLink.tag_id == Tag.id)
        .where(ModelTagLink.model_id == model_id)
    ).all()
    return set(rows)


def _revision(
    session: Session, model: Model, version: int, label: str | None = None
) -> File:
    row = File(
        model_id=model.id,
        path=f"/tmp/{model.id}-{version}.gcode",
        original_filename=f"rev-{version}.gcode",
        file_type=FileType.GCODE,
        version=version,
        size_bytes=10,
        sha256=f"{model.id:032x}{version:032x}"[-64:],
        revision_label=label,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


# --- move -----------------------------------------------------------------


def test_batch_move_succeeds_with_edit_on_source_and_dest(
    client: TestClient, db_session: Session
) -> None:
    user = _user(db_session, "mover")
    src = taxonomy.resolve_or_create_collection(db_session, "Source")
    dst = taxonomy.resolve_or_create_collection(db_session, "Dest")
    assert src is not None and dst is not None
    _grant(db_session, user, src.id, CollectionRole.EDIT)
    _grant(db_session, user, dst.id, CollectionRole.EDIT)
    a = _model(db_session, "A", src.id)
    b = _model(db_session, "B", src.id)

    res = client.post(
        "/api/v1/models/batch/move",
        headers=_headers(user),
        json={"model_ids": [a.id, b.id], "collection": "Dest"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["succeeded_count"] == 2
    assert set(body["succeeded_ids"]) == {a.id, b.id}
    assert body["failed"] == []

    db_session.expire_all()
    assert db_session.get(Model, a.id).collection_id == dst.id
    assert db_session.get(Model, b.id).collection_id == dst.id


def test_batch_move_is_atomic_on_mixed_permission(
    client: TestClient, db_session: Session
) -> None:
    user = _user(db_session, "partial-mover")
    a_col = taxonomy.resolve_or_create_collection(db_session, "Allowed")
    b_col = taxonomy.resolve_or_create_collection(db_session, "Forbidden")
    dst = taxonomy.resolve_or_create_collection(db_session, "Target")
    assert a_col is not None and b_col is not None and dst is not None
    _grant(db_session, user, a_col.id, CollectionRole.EDIT)
    _grant(db_session, user, dst.id, CollectionRole.EDIT)
    allowed = _model(db_session, "Allowed Model", a_col.id)
    forbidden = _model(db_session, "Forbidden Model", b_col.id)

    res = client.post(
        "/api/v1/models/batch/move",
        headers=_headers(user),
        json={"model_ids": [allowed.id, forbidden.id], "collection": "Target"},
    )
    assert res.status_code == 403

    db_session.expire_all()
    assert db_session.get(Model, allowed.id).collection_id == a_col.id
    assert db_session.get(Model, forbidden.id).collection_id == b_col.id


def test_batch_move_to_root_requires_superuser(
    client: TestClient, db_session: Session
) -> None:
    user = _user(db_session, "rooter")
    col = taxonomy.resolve_or_create_collection(db_session, "Box")
    assert col is not None
    _grant(db_session, user, col.id, CollectionRole.EDIT)
    m = _model(db_session, "Boxed", col.id)

    denied = client.post(
        "/api/v1/models/batch/move",
        headers=_headers(user),
        json={"model_ids": [m.id], "collection": ""},
    )
    assert denied.status_code == 403
    assert denied.json()["detail"] == "root_collection_admin_required"

    admin = _user(db_session, "root-admin", superuser=True)
    allowed = client.post(
        "/api/v1/models/batch/move",
        headers=_headers(admin),
        json={"model_ids": [m.id], "collection": ""},
    )
    assert allowed.status_code == 200
    db_session.expire_all()
    assert db_session.get(Model, m.id).collection_id is None


def test_batch_move_without_dest_permission_fails_whole_request(
    client: TestClient, db_session: Session
) -> None:
    user = _user(db_session, "no-dest")
    src = taxonomy.resolve_or_create_collection(db_session, "Src")
    dst = taxonomy.resolve_or_create_collection(db_session, "Dst")
    assert src is not None and dst is not None
    _grant(db_session, user, src.id, CollectionRole.EDIT)
    m = _model(db_session, "M", src.id)

    res = client.post(
        "/api/v1/models/batch/move",
        headers=_headers(user),
        json={"model_ids": [m.id], "collection": "Dst"},
    )
    assert res.status_code == 403
    db_session.expire_all()
    assert db_session.get(Model, m.id).collection_id == src.id


def test_batch_move_rejects_whole_request_on_missing_model(
    client: TestClient, db_session: Session
) -> None:
    user = _user(db_session, "missing", superuser=True)
    dst = taxonomy.resolve_or_create_collection(db_session, "Here")
    assert dst is not None
    m = _model(db_session, "Real", None)

    res = client.post(
        "/api/v1/models/batch/move",
        headers=_headers(user),
        json={"model_ids": [m.id, 999999], "collection": "Here"},
    )
    assert res.status_code == 404
    assert res.json()["detail"] == "model_not_found"
    db_session.refresh(m)
    assert m.collection_id is None


# --- tags -----------------------------------------------------------------


def test_batch_tags_add_is_additive_and_idempotent(
    client: TestClient, db_session: Session
) -> None:
    user = _user(db_session, "tagger", superuser=True)
    m = _model(db_session, "Tagged", None)
    existing = taxonomy.resolve_or_create_tags(db_session, ["keep"])
    db_session.add(ModelTagLink(model_id=m.id, tag_id=existing[0].id))
    db_session.commit()

    res = client.post(
        "/api/v1/models/batch/tags",
        headers=_headers(user),
        json={"model_ids": [m.id], "add": ["keep", "new"]},
    )
    assert res.status_code == 200
    assert res.json()["succeeded_count"] == 1
    db_session.expire_all()
    assert _tag_slugs(db_session, m.id) == {"keep", "new"}


def test_batch_tags_remove_only_named(client: TestClient, db_session: Session) -> None:
    user = _user(db_session, "untagger", superuser=True)
    m = _model(db_session, "HasTags", None)
    tags = taxonomy.resolve_or_create_tags(db_session, ["alpha", "beta"])
    for t in tags:
        db_session.add(ModelTagLink(model_id=m.id, tag_id=t.id))
    db_session.commit()

    res = client.post(
        "/api/v1/models/batch/tags",
        headers=_headers(user),
        json={"model_ids": [m.id], "remove": ["alpha", "ghost"]},
    )
    assert res.status_code == 200
    db_session.expire_all()
    assert _tag_slugs(db_session, m.id) == {"beta"}


def test_batch_tags_is_atomic_on_mixed_permission(
    client: TestClient, db_session: Session
) -> None:
    user = _user(db_session, "tag-partial")
    a_col = taxonomy.resolve_or_create_collection(db_session, "TagAllowed")
    b_col = taxonomy.resolve_or_create_collection(db_session, "TagForbidden")
    assert a_col is not None and b_col is not None
    _grant(db_session, user, a_col.id, CollectionRole.EDIT)
    allowed = _model(db_session, "TA", a_col.id)
    forbidden = _model(db_session, "TF", b_col.id)

    res = client.post(
        "/api/v1/models/batch/tags",
        headers=_headers(user),
        json={"model_ids": [allowed.id, forbidden.id], "add": ["shared"]},
    )
    assert res.status_code == 403
    db_session.expire_all()
    assert _tag_slugs(db_session, allowed.id) == set()
    assert _tag_slugs(db_session, forbidden.id) == set()


# --- delete ---------------------------------------------------------------


def test_batch_delete_soft_deletes(client: TestClient, db_session: Session) -> None:
    user = _user(db_session, "deleter", superuser=True)
    a = _model(db_session, "DelA", None)
    b = _model(db_session, "DelB", None)

    res = client.post(
        "/api/v1/models/batch/delete",
        headers=_headers(user),
        json={"model_ids": [a.id, b.id]},
    )
    assert res.status_code == 200
    assert res.json()["succeeded_count"] == 2
    db_session.expire_all()
    assert db_session.get(Model, a.id).deleted_at is not None
    assert db_session.get(Model, b.id).deleted_at is not None


def test_batch_delete_is_atomic_on_mixed_permission(
    client: TestClient, db_session: Session
) -> None:
    user = _user(db_session, "del-partial")
    a_col = taxonomy.resolve_or_create_collection(db_session, "DelAllowed")
    b_col = taxonomy.resolve_or_create_collection(db_session, "DelForbidden")
    assert a_col is not None and b_col is not None
    _grant(db_session, user, a_col.id, CollectionRole.EDIT)
    allowed = _model(db_session, "DA", a_col.id)
    forbidden = _model(db_session, "DF", b_col.id)

    res = client.post(
        "/api/v1/models/batch/delete",
        headers=_headers(user),
        json={"model_ids": [allowed.id, forbidden.id]},
    )
    assert res.status_code == 403
    db_session.expire_all()
    assert db_session.get(Model, allowed.id).deleted_at is None
    assert db_session.get(Model, forbidden.id).deleted_at is None


# --- no side effects when every item fails --------------------------------


def test_batch_move_all_failed_does_not_create_collection(
    client: TestClient, db_session: Session
) -> None:
    """A move where every id fails must not leave an orphan destination."""
    from app.db.models import Collection
    from app.services import taxonomy as tax

    admin = _user(db_session, "orphan-mover", superuser=True)
    # Only a missing model id — nothing will move.
    res = client.post(
        "/api/v1/models/batch/move",
        headers=_headers(admin),
        json={"model_ids": [999999], "collection": "Brand New Box"},
    )
    assert res.status_code == 404

    db_session.expire_all()
    created = db_session.exec(
        select(Collection).where(Collection.path == tax.slugify("Brand New Box"))
    ).first()
    assert created is None, "destination collection created despite zero moves"


def test_batch_tags_all_failed_does_not_create_tag(
    client: TestClient, db_session: Session
) -> None:
    """An add-tags batch where every model is non-editable must not create tags."""
    user = _user(db_session, "orphan-tagger")
    forbidden_col = taxonomy.resolve_or_create_collection(db_session, "Locked")
    assert forbidden_col is not None
    m = _model(db_session, "Untouchable", forbidden_col.id)

    res = client.post(
        "/api/v1/models/batch/tags",
        headers=_headers(user),
        json={"model_ids": [m.id], "add": ["should-not-exist"]},
    )
    assert res.status_code == 403

    db_session.expire_all()
    tag = db_session.exec(select(Tag).where(Tag.slug == "should-not-exist")).first()
    assert tag is None, "tag created despite zero editable models"


# --- validation -----------------------------------------------------------


def test_batch_revision_labels_sets_and_clears_labels(
    client: TestClient, db_session: Session
) -> None:
    user = _user(db_session, "revision-editor")
    collection = taxonomy.resolve_or_create_collection(db_session, "Revisions")
    assert collection is not None
    _grant(db_session, user, collection.id, CollectionRole.EDIT)
    model = _model(db_session, "Revision Model", collection.id)
    first = _revision(db_session, model, 1, "old")
    second = _revision(db_session, model, 2)

    response = client.patch(
        "/api/v1/models/batch/revision-labels",
        headers=_headers(user),
        json={"file_ids": [first.id, second.id], "revision_label": "  PETG fast  "},
    )
    assert response.status_code == 200
    assert response.json() == {
        "succeeded_ids": [first.id, second.id],
        "succeeded_count": 2,
    }
    db_session.expire_all()
    assert db_session.get(File, first.id).revision_label == "PETG fast"
    assert db_session.get(File, second.id).revision_label == "PETG fast"

    cleared = client.patch(
        "/api/v1/models/batch/revision-labels",
        headers=_headers(user),
        json={"file_ids": [first.id, second.id], "revision_label": ""},
    )
    assert cleared.status_code == 200
    db_session.expire_all()
    assert db_session.get(File, first.id).revision_label is None
    assert db_session.get(File, second.id).revision_label is None


def test_batch_revision_labels_rbac_failure_is_atomic(
    client: TestClient, db_session: Session
) -> None:
    user = _user(db_session, "revision-limited")
    allowed_collection = taxonomy.resolve_or_create_collection(
        db_session, "Rev Allowed"
    )
    denied_collection = taxonomy.resolve_or_create_collection(db_session, "Rev Denied")
    assert allowed_collection is not None and denied_collection is not None
    _grant(db_session, user, allowed_collection.id, CollectionRole.EDIT)
    allowed = _revision(
        db_session,
        _model(db_session, "Allowed Revision", allowed_collection.id),
        1,
        "A",
    )
    denied = _revision(
        db_session, _model(db_session, "Denied Revision", denied_collection.id), 1, "B"
    )

    response = client.patch(
        "/api/v1/models/batch/revision-labels",
        headers=_headers(user),
        json={"file_ids": [allowed.id, denied.id], "revision_label": "changed"},
    )
    assert response.status_code == 403
    db_session.expire_all()
    assert db_session.get(File, allowed.id).revision_label == "A"
    assert db_session.get(File, denied.id).revision_label == "B"


def test_batch_revision_labels_rolls_back_partial_service_failure(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import model_views

    admin = _user(db_session, "revision-admin", superuser=True)
    model = _model(db_session, "Rollback Revisions", None)
    first = _revision(db_session, model, 1, "first")
    second = _revision(db_session, model, 2, "second")

    def fail_after_first(
        session: Session, files: list[File], revision_label: str | None
    ) -> None:
        files[0].revision_label = revision_label
        session.add(files[0])
        session.flush()
        raise RuntimeError("injected batch failure")

    monkeypatch.setattr(model_views, "set_revision_labels", fail_after_first)
    with pytest.raises(RuntimeError, match="injected batch failure"):
        client.patch(
            "/api/v1/models/batch/revision-labels",
            headers=_headers(admin),
            json={"file_ids": [first.id, second.id], "revision_label": "changed"},
        )
    db_session.expire_all()
    assert db_session.get(File, first.id).revision_label == "first"
    assert db_session.get(File, second.id).revision_label == "second"


# --- validation -----------------------------------------------------------


def test_batch_empty_ids_rejected(client: TestClient, db_session: Session) -> None:
    user = _user(db_session, "validator", superuser=True)
    res = client.post(
        "/api/v1/models/batch/delete",
        headers=_headers(user),
        json={"model_ids": []},
    )
    assert res.status_code == 422


def test_batch_too_many_ids_rejected(client: TestClient, db_session: Session) -> None:
    user = _user(db_session, "validator2", superuser=True)
    res = client.post(
        "/api/v1/models/batch/delete",
        headers=_headers(user),
        json={"model_ids": list(range(1, 502))},
    )
    assert res.status_code == 422
