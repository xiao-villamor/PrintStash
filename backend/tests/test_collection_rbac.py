from __future__ import annotations

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db.models import (
    CollectionPermission,
    CollectionRole,
    File,
    FileType,
    Model,
    Printer,
    PrinterFile,
    User,
)
from app.services.auth import create_access_token, hash_password
from app.services import rbac, taxonomy


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
    session: Session,
    user: User,
    collection_id: int,
    role: CollectionRole,
) -> None:
    session.add(
        CollectionPermission(
            user_id=user.id,
            collection_id=collection_id,
            role=role,
        )
    )
    session.commit()


def _model(session: Session, name: str, collection_id: int | None) -> Model:
    model = Model(
        name=name,
        slug=name.lower().replace(" ", "-"),
        hash=(name[:1].lower() or "a") * 64,
        collection_id=collection_id,
    )
    session.add(model)
    session.commit()
    session.refresh(model)
    return model


def test_effective_role_inherits_from_parent(db_session: Session) -> None:
    user = _user(db_session, "viewer")
    parent = taxonomy.resolve_or_create_collection(db_session, "Shared")
    child = taxonomy.resolve_or_create_collection(db_session, "Shared/Fixtures")
    assert parent is not None and child is not None
    _grant(db_session, user, parent.id, CollectionRole.EDIT)

    assert (
        rbac.effective_collection_role(db_session, user, child.id)
        == CollectionRole.EDIT
    )
    assert child.id in rbac.accessible_collection_ids(db_session, user)


def test_grant_does_not_leak_to_prefix_sibling(db_session: Session) -> None:
    """A grant on 'func' must not reach a sibling 'func-tools' that merely
    shares the string prefix — inheritance is path-segment based, not substring."""
    user = _user(db_session, "viewer")
    func = taxonomy.resolve_or_create_collection(db_session, "Func")
    func_tools = taxonomy.resolve_or_create_collection(db_session, "Func Tools")
    assert func is not None and func_tools is not None
    assert func.path == "func" and func_tools.path == "func-tools"

    _grant(db_session, user, func.id, CollectionRole.ADMIN)

    assert rbac.effective_collection_role(db_session, user, func.id) == CollectionRole.ADMIN
    assert rbac.effective_collection_role(db_session, user, func_tools.id) is None
    assert func_tools.id not in rbac.accessible_collection_ids(db_session, user)


def test_grant_on_child_does_not_leak_up_to_parent(db_session: Session) -> None:
    """Permissions inherit downward (parent→child), never upward."""
    user = _user(db_session, "viewer")
    parent = taxonomy.resolve_or_create_collection(db_session, "Shared")
    child = taxonomy.resolve_or_create_collection(db_session, "Shared/Fixtures")
    assert parent is not None and child is not None

    _grant(db_session, user, child.id, CollectionRole.ADMIN)

    assert rbac.effective_collection_role(db_session, user, child.id) == CollectionRole.ADMIN
    assert rbac.effective_collection_role(db_session, user, parent.id) is None
    assert parent.id not in rbac.accessible_collection_ids(db_session, user)


def test_trashed_collection_grants_no_role(db_session: Session) -> None:
    """A grant on a collection that has been trashed must not grant access."""
    user = _user(db_session, "viewer")
    coll = taxonomy.resolve_or_create_collection(db_session, "Temp")
    assert coll is not None
    _grant(db_session, user, coll.id, CollectionRole.EDIT)
    assert rbac.effective_collection_role(db_session, user, coll.id) == CollectionRole.EDIT

    from app.core.time import utcnow

    coll.deleted_at = utcnow()
    db_session.add(coll)
    db_session.commit()

    assert rbac.effective_collection_role(db_session, user, coll.id) is None


def test_model_reads_filter_denied_collections(
    client: TestClient,
    db_session: Session,
) -> None:
    user = _user(db_session, "reader")
    allowed = taxonomy.resolve_or_create_collection(db_session, "Allowed")
    denied = taxonomy.resolve_or_create_collection(db_session, "Denied")
    assert allowed is not None and denied is not None
    _grant(db_session, user, allowed.id, CollectionRole.VIEW)
    allowed_model = _model(db_session, "Allowed Model", allowed.id)
    denied_model = _model(db_session, "Denied Model", denied.id)

    response = client.get("/api/v1/models", headers=_headers(user))
    assert response.status_code == 200
    assert [row["id"] for row in response.json()] == [allowed_model.id]

    denied_detail = client.get(
        f"/api/v1/models/{denied_model.id}",
        headers=_headers(user),
    )
    assert denied_detail.status_code == 404


def test_file_download_denies_collection_without_view(
    client: TestClient,
    db_session: Session,
) -> None:
    user = _user(db_session, "no-files")
    collection = taxonomy.resolve_or_create_collection(db_session, "Private")
    assert collection is not None
    model = _model(db_session, "Private Model", collection.id)
    file_row = File(
        model_id=model.id,
        path="/tmp/private.stl",
        original_filename="private.stl",
        file_type=FileType.STL,
        version=1,
        size_bytes=1,
        sha256="f" * 64,
    )
    db_session.add(file_row)
    db_session.commit()
    db_session.refresh(file_row)

    response = client.get(
        f"/api/v1/files/{file_row.id}/download",
        headers=_headers(user),
    )
    assert response.status_code == 403


def test_view_role_cannot_edit_but_edit_role_can(
    client: TestClient,
    db_session: Session,
) -> None:
    user = _user(db_session, "editor")
    collection = taxonomy.resolve_or_create_collection(db_session, "Work")
    assert collection is not None
    _grant(db_session, user, collection.id, CollectionRole.VIEW)
    model = _model(db_session, "Work Model", collection.id)

    denied = client.patch(
        f"/api/v1/models/{model.id}",
        headers=_headers(user),
        json={"description": "nope"},
    )
    assert denied.status_code == 403

    permission = db_session.exec(
        select(CollectionPermission).where(
            CollectionPermission.user_id == user.id,
            CollectionPermission.collection_id == collection.id,
        )
    ).one()
    permission.role = CollectionRole.EDIT
    db_session.add(permission)
    db_session.commit()

    allowed = client.patch(
        f"/api/v1/models/{model.id}",
        headers=_headers(user),
        json={"description": "ok"},
    )
    assert allowed.status_code == 200
    assert allowed.json()["description"] == "ok"


def test_collection_admin_can_manage_direct_permissions(
    client: TestClient,
    db_session: Session,
) -> None:
    admin = _user(db_session, "collection-admin")
    viewer = _user(db_session, "share-target")
    collection = taxonomy.resolve_or_create_collection(db_session, "Shared")
    assert collection is not None
    _grant(db_session, admin, collection.id, CollectionRole.ADMIN)

    put = client.put(
        f"/api/v1/collections/{collection.id}/permissions/{viewer.id}",
        headers=_headers(admin),
        json={"role": "view"},
    )
    assert put.status_code == 200
    assert put.json()["role"] == "view"

    listed = client.get(
        f"/api/v1/collections/{collection.id}/permissions",
        headers=_headers(admin),
    )
    assert listed.status_code == 200
    assert {row["username"] for row in listed.json()} == {
        "collection-admin",
        "share-target",
    }


def test_non_superuser_ingest_requires_collection(
    client: TestClient,
    db_session: Session,
) -> None:
    user = _user(db_session, "uploader")
    response = client.post(
        "/api/v1/ingest/model",
        headers=_headers(user),
        files={"file": ("cube.stl", b"solid cube\nendsolid cube\n", "application/sla")},
        data={"model_name": "Cube"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "collection_required"


def test_non_superuser_cannot_see_printer_presence(
    client: TestClient,
    db_session: Session,
) -> None:
    user = _user(db_session, "printer-blind")
    collection = taxonomy.resolve_or_create_collection(db_session, "Visible")
    assert collection is not None
    _grant(db_session, user, collection.id, CollectionRole.VIEW)
    model = _model(db_session, "Visible Printed Model", collection.id)
    file_row = File(
        model_id=model.id,
        path="/tmp/visible.gcode",
        original_filename="visible.gcode",
        file_type=FileType.GCODE,
        version=1,
        size_bytes=1,
        sha256="p" * 64,
    )
    printer = Printer(name="Hidden Printer", moonraker_url="http://10.0.0.1:7125")
    db_session.add_all([file_row, printer])
    db_session.commit()
    db_session.refresh(file_row)
    db_session.refresh(printer)
    db_session.add(
        PrinterFile(
            printer_id=printer.id,
            file_id=file_row.id,
            remote_filename="visible.gcode",
            matched_by="filename",
        )
    )
    db_session.commit()

    listed = client.get("/api/v1/models", headers=_headers(user))
    assert listed.status_code == 200
    assert listed.json()[0]["printer_presence"] == []

    filtered = client.get("/api/v1/models?printer_presence=any", headers=_headers(user))
    assert filtered.status_code == 403
    assert filtered.json()["detail"] == "admin_required"

    printer_files = client.get(
        f"/api/v1/models/{model.id}/printer-files",
        headers=_headers(user),
    )
    assert printer_files.status_code == 403
    assert printer_files.json()["detail"] == "admin_required"

    print_jobs = client.get(
        f"/api/v1/models/{model.id}/print-jobs",
        headers=_headers(user),
    )
    assert print_jobs.status_code == 403
    assert print_jobs.json()["detail"] == "admin_required"


def test_deleting_a_child_does_not_block_deleting_the_parent(
    client: TestClient, db_session: Session
) -> None:
    """Regression: a soft-deleted child must not keep its parent un-deletable.

    The non-recursive has-children guard previously counted trashed children, so
    create-child -> delete-child -> delete-parent returned 409 forever.
    """
    admin = _user(db_session, "admin-del", superuser=True)
    h = _headers(admin)

    parent = client.post("/api/v1/collections", json={"name": "Parent"}, headers=h).json()
    child = client.post(
        "/api/v1/collections",
        json={"name": "Child", "parent_id": parent["id"]},
        headers=h,
    ).json()

    # A LIVE child still blocks a non-recursive parent delete.
    assert (
        client.delete(f"/api/v1/collections/{parent['id']}", headers=h).status_code == 409
    )

    # After the child is trashed, the parent deletes cleanly.
    assert client.delete(f"/api/v1/collections/{child['id']}", headers=h).status_code == 204
    assert client.delete(f"/api/v1/collections/{parent['id']}", headers=h).status_code == 204
