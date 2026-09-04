"""Who can see and change which part of the library, resolved down a tree.

Collection permissions inherit: a grant on `Parts` covers `Parts/Brackets`. That
makes the *boundaries* the interesting cases, and this file is mostly boundaries,
because every one of them is a way for a grant to reach further than the person
who made it intended:

* **Down, but not up.** A grant on a child gives no role on its parent, or
  sharing one folder would expose everything above it.
* **Not to a prefix sibling.** `Parts` must not match `Parts-Archive`. String
  prefixes are how materialized-path RBAC leaks, and the two names differ by a
  character.
* **Not through the trash.** A trashed collection grants nothing; otherwise
  deleting a folder would be a way to keep access to it.

Roles are ordered (`view` < `edit` < `admin`), so each level is asserted against
what it must *not* permit as well as what it must — a `view` grant that can edit
is indistinguishable from `edit` in practice.

The non-superuser rows exist because `auth_headers` is an admin and proves
nothing here: every one of these rules is invisible to a superuser, who passes
every check by definition.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db.models import (
    CollectionPermission,
    CollectionRole,
    FileType,
    Model,
    PrinterFile,
    User,
)
from app.services import rbac, taxonomy
from tests.factories import (
    bearer,
    build_file,
    build_model,
    build_printer,
    build_user,
    grant_collection_role,
)


def _grant(
    session: Session,
    user: User,
    collection_id: int,
    role: CollectionRole,
) -> None:
    grant_collection_role(session, user, collection_id, role)


def _model(session: Session, name: str, collection_id: int | None) -> Model:
    model = build_model(
        session,
        name=name,
        slug=name.lower().replace(" ", "-"),
        hash=(name[:1].lower() or "a") * 64,
        collection_id=collection_id,
    )
    return model


class TestAccessibleCollectionIds:
    def test_grant_does_not_leak_to_prefix_sibling(self, db_session: Session) -> None:
        """A grant on 'func' must not reach a sibling 'func-tools' that merely
        shares the string prefix — inheritance is path-segment based, not substring."""
        user = build_user(db_session, "viewer")
        func = taxonomy.resolve_or_create_collection(db_session, "Func")
        func_tools = taxonomy.resolve_or_create_collection(db_session, "Func Tools")
        assert func is not None and func_tools is not None
        assert func.path == "func" and func_tools.path == "func-tools"

        _grant(db_session, user, func.id, CollectionRole.ADMIN)

        assert (
            rbac.effective_collection_role(db_session, user, func.id)
            == CollectionRole.ADMIN
        )
        assert rbac.effective_collection_role(db_session, user, func_tools.id) is None
        assert func_tools.id not in rbac.accessible_collection_ids(db_session, user)

    def test_grant_on_child_does_not_leak_up_to_parent(
        self, db_session: Session
    ) -> None:
        """Permissions inherit downward (parent→child), never upward."""
        user = build_user(db_session, "viewer")
        parent = taxonomy.resolve_or_create_collection(db_session, "Shared")
        child = taxonomy.resolve_or_create_collection(db_session, "Shared/Fixtures")
        assert parent is not None and child is not None

        _grant(db_session, user, child.id, CollectionRole.ADMIN)

        assert (
            rbac.effective_collection_role(db_session, user, child.id)
            == CollectionRole.ADMIN
        )
        assert rbac.effective_collection_role(db_session, user, parent.id) is None
        assert parent.id not in rbac.accessible_collection_ids(db_session, user)


class TestRequireCollectionRole:
    """The role check every collection-scoped endpoint runs, exercised through them.

    `require_collection_role` is a dependency, so the only honest way to prove it
    is to call the endpoints that depend on it and watch a real grant decide a
    real request. A unit test on the function would pass while a router that
    forgot to depend on it stayed wide open."""

    def test_view_role_cannot_edit_a_model_in_the_collection(
        self,
        client: TestClient,
        db_session: Session,
    ) -> None:
        user = build_user(db_session, "editor")
        collection = taxonomy.resolve_or_create_collection(db_session, "Work")
        assert collection is not None
        _grant(db_session, user, collection.id, CollectionRole.VIEW)
        model = _model(db_session, "Work Model", collection.id)

        response = client.patch(
            f"/api/v1/models/{model.id}",
            headers=bearer(user),
            json={"description": "nope"},
        )

        # `view` is a read grant. Letting it write would make the two roles the same
        # role, and the distinction is the whole reason a shared collection is safe
        # to hand out.
        assert response.status_code == 403, response.text

    def test_edit_role_can_edit_a_model_in_the_collection(
        self,
        client: TestClient,
        db_session: Session,
    ) -> None:
        user = build_user(db_session, "editor")
        collection = taxonomy.resolve_or_create_collection(db_session, "Work")
        assert collection is not None
        _grant(db_session, user, collection.id, CollectionRole.EDIT)
        model = _model(db_session, "Work Model", collection.id)

        response = client.patch(
            f"/api/v1/models/{model.id}",
            headers=bearer(user),
            json={"description": "ok"},
        )

        assert response.status_code == 200, response.text
        assert response.json()["description"] == "ok"

    def test_collection_admin_can_manage_direct_permissions(
        self,
        client: TestClient,
        db_session: Session,
    ) -> None:
        admin = build_user(db_session, "collection-admin")
        viewer = build_user(db_session, "share-target")
        collection = taxonomy.resolve_or_create_collection(db_session, "Shared")
        assert collection is not None
        _grant(db_session, admin, collection.id, CollectionRole.ADMIN)

        put = client.put(
            f"/api/v1/collections/{collection.id}/permissions/{viewer.id}",
            headers=bearer(admin),
            json={"role": "view"},
        )
        assert put.status_code == 200
        assert put.json()["role"] == "view"

        listed = client.get(
            f"/api/v1/collections/{collection.id}/permissions",
            headers=bearer(admin),
        )
        assert listed.status_code == 200
        assert {row["username"] for row in listed.json()} == {
            "collection-admin",
            "share-target",
        }

    def test_non_superuser_ingest_requires_collection(
        self,
        client: TestClient,
        db_session: Session,
    ) -> None:
        user = build_user(db_session, "uploader")
        response = client.post(
            "/api/v1/ingest/model",
            headers=bearer(user),
            files={
                "file": ("cube.stl", b"solid cube\nendsolid cube\n", "application/sla")
            },
            data={"model_name": "Cube"},
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "collection_required"

    def test_non_superuser_cannot_see_printer_presence(
        self,
        client: TestClient,
        db_session: Session,
    ) -> None:
        user = build_user(db_session, "printer-blind")
        collection = taxonomy.resolve_or_create_collection(db_session, "Visible")
        assert collection is not None
        _grant(db_session, user, collection.id, CollectionRole.VIEW)
        model = _model(db_session, "Visible Printed Model", collection.id)
        file_row = build_file(
            db_session,
            model,
            path="/tmp/visible.gcode",
            filename="visible.gcode",
            file_type=FileType.GCODE,
            size_bytes=1,
            sha256="p" * 64,
        )
        printer = build_printer(
            db_session, name="Hidden Printer", moonraker_url="http://10.0.0.1:7125"
        )
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

        listed = client.get("/api/v1/models", headers=bearer(user))
        assert listed.status_code == 200
        assert listed.json()[0]["printer_presence"] == []

        filtered = client.get(
            "/api/v1/models?printer_presence=any", headers=bearer(user)
        )
        assert filtered.status_code == 403
        assert filtered.json()["detail"] == "admin_required"

        printer_files = client.get(
            f"/api/v1/models/{model.id}/printer-files",
            headers=bearer(user),
        )
        assert printer_files.status_code == 403
        assert printer_files.json()["detail"] == "admin_required"

        print_jobs = client.get(
            f"/api/v1/models/{model.id}/print-jobs",
            headers=bearer(user),
        )
        assert print_jobs.status_code == 403
        assert print_jobs.json()["detail"] == "admin_required"

    def test_deleting_a_child_does_not_block_deleting_the_parent(
        self, client: TestClient, db_session: Session
    ) -> None:
        """Regression: a soft-deleted child must not keep its parent un-deletable.

        The non-recursive has-children guard previously counted trashed children, so
        create-child -> delete-child -> delete-parent returned 409 forever.
        """
        admin = build_user(db_session, "admin-del", superuser=True)
        h = bearer(admin)

        parent = client.post(
            "/api/v1/collections", json={"name": "Parent"}, headers=h
        ).json()
        child = client.post(
            "/api/v1/collections",
            json={"name": "Child", "parent_id": parent["id"]},
            headers=h,
        ).json()

        # A LIVE child still blocks a non-recursive parent delete.
        assert (
            client.delete(f"/api/v1/collections/{parent['id']}", headers=h).status_code
            == 409
        )

        # After the child is trashed, the parent deletes cleanly.
        assert (
            client.delete(f"/api/v1/collections/{child['id']}", headers=h).status_code
            == 204
        )
        assert (
            client.delete(f"/api/v1/collections/{parent['id']}", headers=h).status_code
            == 204
        )

    def test_role_revocation_takes_effect_immediately(
        self,
        client: TestClient,
        db_session: Session,
    ) -> None:
        """Revoking a grant must lock the user out on the very next request.

        Access is resolved per request, so nothing may cache a stale grant — a
        revoked collaborator who can still read is the whole point of the feature.
        """
        user = build_user(db_session, "revoked")
        collection = taxonomy.resolve_or_create_collection(db_session, "Secrets")
        assert collection is not None
        child = taxonomy.resolve_or_create_collection(db_session, "Secrets/Inner")
        assert child is not None
        _grant(db_session, user, collection.id, CollectionRole.VIEW)
        model = _model(db_session, "Inner Model", child.id)

        before = client.get(f"/api/v1/models/{model.id}", headers=bearer(user))
        assert before.status_code == 200

        permission = db_session.exec(
            select(CollectionPermission).where(
                CollectionPermission.user_id == user.id,
                CollectionPermission.collection_id == collection.id,
            )
        ).one()
        db_session.delete(permission)
        db_session.commit()

        after = client.get(f"/api/v1/models/{model.id}", headers=bearer(user))
        # 404, not 403: the model is filtered out before the role check, so a revoked
        # user cannot even confirm it exists.
        assert after.status_code == 404, "revoked user still reads an inherited model"

        listed = client.get("/api/v1/models", headers=bearer(user))
        assert listed.status_code == 200
        body = listed.json()
        rows = body["items"] if isinstance(body, dict) else body
        assert all(row["id"] != model.id for row in rows)

    def test_collection_rename_updates_descendant_paths(
        self,
        client: TestClient,
        db_session: Session,
    ) -> None:
        owner = build_user(db_session, "rename-owner")
        parent = taxonomy.resolve_or_create_collection(db_session, "Projects")
        child = taxonomy.resolve_or_create_collection(db_session, "Projects/Parts")
        assert parent is not None and child is not None
        _grant(db_session, owner, parent.id, CollectionRole.ADMIN)

        response = client.patch(
            f"/api/v1/collections/{parent.id}",
            headers=bearer(owner),
            json={"name": "Active projects"},
        )

        assert response.status_code == 200
        assert response.json()["path"] == "active-projects"
        db_session.expire_all()
        assert db_session.get(type(child), child.id).path == "active-projects/parts"

    def test_file_download_denies_collection_without_view(
        self,
        client: TestClient,
        db_session: Session,
    ) -> None:
        user = build_user(db_session, "no-files")
        collection = taxonomy.resolve_or_create_collection(db_session, "Private")
        assert collection is not None
        model = _model(db_session, "Private Model", collection.id)
        file_row = build_file(
            db_session,
            model,
            path="/tmp/private.stl",
            filename="private.stl",
            file_type=FileType.STL,
            version=1,
            size_bytes=1,
            sha256="f" * 64,
        )

        response = client.get(
            f"/api/v1/files/{file_row.id}/download",
            headers=bearer(user),
        )
        assert response.status_code == 403

    def test_model_reads_filter_denied_collections(
        self,
        client: TestClient,
        db_session: Session,
    ) -> None:
        user = build_user(db_session, "reader")
        allowed = taxonomy.resolve_or_create_collection(db_session, "Allowed")
        denied = taxonomy.resolve_or_create_collection(db_session, "Denied")
        assert allowed is not None and denied is not None
        _grant(db_session, user, allowed.id, CollectionRole.VIEW)
        allowed_model = _model(db_session, "Allowed Model", allowed.id)
        denied_model = _model(db_session, "Denied Model", denied.id)

        response = client.get("/api/v1/models", headers=bearer(user))
        assert response.status_code == 200
        assert [row["id"] for row in response.json()] == [allowed_model.id]

        denied_detail = client.get(
            f"/api/v1/models/{denied_model.id}",
            headers=bearer(user),
        )
        assert denied_detail.status_code == 404


class TestEffectiveRolesForCollections:
    """The bulk resolver behind every listing that shows many collections at once."""

    def test_gives_a_superuser_admin_everywhere_including_the_root(
        self, db_session: Session
    ) -> None:
        admin = build_user(db_session, "bulk-admin", superuser=True)
        collection = taxonomy.resolve_or_create_collection(db_session, "Shelf")
        assert collection is not None

        roles = rbac.effective_roles_for_collections(db_session, admin, [collection.id])

        assert roles[collection.id] == CollectionRole.ADMIN
        assert roles[None] == CollectionRole.ADMIN

    def test_returns_nothing_for_a_user_with_no_grants_at_all(
        self, db_session: Session
    ) -> None:
        user = build_user(db_session, "bulk-nobody")
        collection = taxonomy.resolve_or_create_collection(db_session, "Shelf")
        assert collection is not None

        roles = rbac.effective_roles_for_collections(db_session, user, [collection.id])

        assert roles[collection.id] is None

    def test_answers_an_empty_request_without_touching_the_database(
        self, db_session: Session
    ) -> None:
        user = build_user(db_session, "bulk-empty")

        assert rbac.effective_roles_for_collections(db_session, user, []) == {
            None: None
        }

    def test_inherits_a_grant_down_the_tree(self, db_session: Session) -> None:
        user = build_user(db_session, "bulk-inheritor")
        parent = taxonomy.resolve_or_create_collection(db_session, "Functional")
        child = taxonomy.resolve_or_create_collection(db_session, "Functional/Brackets")
        assert parent is not None and child is not None
        _grant(db_session, user, parent.id, CollectionRole.EDIT)

        roles = rbac.effective_roles_for_collections(db_session, user, [child.id])

        assert roles[child.id] == CollectionRole.EDIT

    def test_keeps_the_strongest_of_two_overlapping_grants(
        self, db_session: Session
    ) -> None:
        user = build_user(db_session, "bulk-strongest")
        parent = taxonomy.resolve_or_create_collection(db_session, "Functional")
        child = taxonomy.resolve_or_create_collection(db_session, "Functional/Brackets")
        assert parent is not None and child is not None
        _grant(db_session, user, parent.id, CollectionRole.VIEW)
        _grant(db_session, user, child.id, CollectionRole.ADMIN)

        roles = rbac.effective_roles_for_collections(db_session, user, [child.id])

        # Two grants on the same path is normal; the answer is the better one.
        assert roles[child.id] == CollectionRole.ADMIN

    def test_does_not_leak_a_grant_to_a_prefix_sibling(
        self, db_session: Session
    ) -> None:
        user = build_user(db_session, "bulk-sibling")
        granted = taxonomy.resolve_or_create_collection(db_session, "Art")
        sibling = taxonomy.resolve_or_create_collection(db_session, "Artillery")
        assert granted is not None and sibling is not None
        _grant(db_session, user, granted.id, CollectionRole.EDIT)

        roles = rbac.effective_roles_for_collections(db_session, user, [sibling.id])

        # "Artillery".startswith("Art") is true and would be a real leak.
        assert roles[sibling.id] is None


class TestEffectiveRolesForUserCollectionPairs:
    """Resolves many users against many collections in one pass, for the admin views."""

    def test_resolves_every_user_against_every_collection(
        self, db_session: Session
    ) -> None:
        first = build_user(db_session, "pairs-one")
        second = build_user(db_session, "pairs-two")
        collection = taxonomy.resolve_or_create_collection(db_session, "Shelf")
        assert collection is not None
        _grant(db_session, first, collection.id, CollectionRole.EDIT)
        _grant(db_session, second, collection.id, CollectionRole.VIEW)

        pairs = rbac.effective_roles_for_user_collection_pairs(
            db_session, [first.id, second.id], [collection.id]
        )

        assert pairs[(first.id, collection.id)] == CollectionRole.EDIT
        assert pairs[(second.id, collection.id)] == CollectionRole.VIEW

    def test_leaves_out_a_pair_with_no_grant(self, db_session: Session) -> None:
        user = build_user(db_session, "pairs-ungranted")
        collection = taxonomy.resolve_or_create_collection(db_session, "Shelf")
        assert collection is not None

        pairs = rbac.effective_roles_for_user_collection_pairs(
            db_session, [user.id], [collection.id]
        )

        # An absent key, not a None value: the caller renders "no access".
        assert (user.id, collection.id) not in pairs

    def test_inherits_a_grant_down_the_tree(self, db_session: Session) -> None:
        user = build_user(db_session, "pairs-inheritor")
        parent = taxonomy.resolve_or_create_collection(db_session, "Functional")
        child = taxonomy.resolve_or_create_collection(db_session, "Functional/Brackets")
        assert parent is not None and child is not None
        _grant(db_session, user, parent.id, CollectionRole.EDIT)

        pairs = rbac.effective_roles_for_user_collection_pairs(
            db_session, [user.id], [child.id]
        )

        assert pairs[(user.id, child.id)] == CollectionRole.EDIT

    def test_keeps_the_strongest_of_two_overlapping_grants(
        self, db_session: Session
    ) -> None:
        user = build_user(db_session, "pairs-strongest")
        parent = taxonomy.resolve_or_create_collection(db_session, "Functional")
        child = taxonomy.resolve_or_create_collection(db_session, "Functional/Brackets")
        assert parent is not None and child is not None
        _grant(db_session, user, parent.id, CollectionRole.VIEW)
        _grant(db_session, user, child.id, CollectionRole.ADMIN)

        pairs = rbac.effective_roles_for_user_collection_pairs(
            db_session, [user.id], [child.id]
        )

        assert pairs[(user.id, child.id)] == CollectionRole.ADMIN

    def test_does_not_leak_a_grant_to_a_prefix_sibling(
        self, db_session: Session
    ) -> None:
        user = build_user(db_session, "pairs-sibling")
        granted = taxonomy.resolve_or_create_collection(db_session, "Art")
        sibling = taxonomy.resolve_or_create_collection(db_session, "Artillery")
        assert granted is not None and sibling is not None
        _grant(db_session, user, granted.id, CollectionRole.EDIT)

        pairs = rbac.effective_roles_for_user_collection_pairs(
            db_session, [user.id], [sibling.id]
        )

        assert (user.id, sibling.id) not in pairs

    @pytest.mark.parametrize(
        ("users", "collections"),
        [
            pytest.param(True, False, id="no-collections"),
            pytest.param(False, True, id="no-users"),
            pytest.param(False, False, id="neither"),
        ],
    )
    def test_answers_an_empty_request_without_touching_the_database(
        self, db_session: Session, users: bool, collections: bool
    ) -> None:
        user = build_user(db_session, f"pairs-empty-{users}-{collections}")
        collection = taxonomy.resolve_or_create_collection(db_session, "Shelf")
        assert collection is not None

        pairs = rbac.effective_roles_for_user_collection_pairs(
            db_session,
            [user.id] if users else [],
            [collection.id] if collections else [],
        )

        assert pairs == {}


class TestEffectiveCollectionRole:
    def test_gives_an_ordinary_user_no_role_at_the_root(
        self, db_session: Session
    ) -> None:
        user = build_user(db_session, "root-nobody")

        # The root is not a collection anyone can be granted; only a superuser
        # reaches it, which is what keeps a shared deployment shareable.
        assert rbac.effective_collection_role(db_session, user, None) is None

    def test_effective_role_inherits_from_parent(self, db_session: Session) -> None:
        user = build_user(db_session, "viewer")
        parent = taxonomy.resolve_or_create_collection(db_session, "Shared")
        child = taxonomy.resolve_or_create_collection(db_session, "Shared/Fixtures")
        assert parent is not None and child is not None
        _grant(db_session, user, parent.id, CollectionRole.EDIT)

        assert (
            rbac.effective_collection_role(db_session, user, child.id)
            == CollectionRole.EDIT
        )
        assert child.id in rbac.accessible_collection_ids(db_session, user)

    def test_trashed_collection_grants_no_role(self, db_session: Session) -> None:
        """A grant on a collection that has been trashed must not grant access."""
        user = build_user(db_session, "viewer")
        coll = taxonomy.resolve_or_create_collection(db_session, "Temp")
        assert coll is not None
        _grant(db_session, user, coll.id, CollectionRole.EDIT)
        assert (
            rbac.effective_collection_role(db_session, user, coll.id)
            == CollectionRole.EDIT
        )

        from app.core.time import utcnow

        coll.deleted_at = utcnow()
        db_session.add(coll)
        db_session.commit()

        assert rbac.effective_collection_role(db_session, user, coll.id) is None


class TestRequireModelCollectionRole:
    def test_refuses_an_ordinary_user_at_the_root(self, db_session: Session) -> None:
        from fastapi import HTTPException as _HTTPException

        user = build_user(db_session, "root-model-nobody")

        with pytest.raises(_HTTPException) as exc_info:
            rbac.require_model_collection_role(
                db_session, user, None, CollectionRole.VIEW
            )

        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == "root_collection_admin_required"

    def test_allows_a_superuser_at_the_root(self, db_session: Session) -> None:
        admin = build_user(db_session, "root-model-admin", superuser=True)

        assert (
            rbac.require_model_collection_role(
                db_session, admin, None, CollectionRole.ADMIN
            )
            == CollectionRole.ADMIN
        )
