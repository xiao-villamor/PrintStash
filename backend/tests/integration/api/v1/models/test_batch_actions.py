"""Applying one action to many models at once: move, tag, label, delete.

Every batch here is **all-or-nothing**, and that is the property the whole file exists to
defend. A user selects forty models in the grid and hits move; if the request half-applies
because one of them sits in a collection they cannot edit, they are left with a library in
a state they never asked for and no way to tell which half moved. So a batch preflights
every id, refuses the *whole* request on the first failure, and writes nothing.

"Writes nothing" is stronger than "moves nothing". A move names a destination and a tag
batch names tags, and both are created on demand — so a batch that fails must not leave an
empty destination collection or an unused tag behind as evidence of an operation that
never happened.

The refusal codes are load-bearing in the same way as the single-model PATCH: `404
model_not_found` when an id is not there at all, `403 root_collection_admin_required` for
the root, `403 collection_permission_denied` for a collection the user may not use.
"""

from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db.models import (
    Collection,
    CollectionRole,
    File,
    FileType,
    Model,
    ModelTagLink,
    Tag,
)
from app.services import taxonomy
from tests.factories import build_file, build_model

MAX_BATCH = 500


@pytest.fixture
def collection_named(db_session: Session):
    def build(name: str) -> Collection:
        row = taxonomy.resolve_or_create_collection(db_session, name)
        assert row is not None
        return row

    return build


@pytest.fixture
def model_in(db_session: Session):
    def build(name: str, collection_id: int | None) -> Model:
        row = build_model(
            db_session,
            name=name,
            slug=name.lower().replace(" ", "-"),
            hash=hashlib.sha256(name.encode()).hexdigest(),
            collection_id=collection_id,
        )
        return row

    return build


@pytest.fixture
def revision(db_session: Session):
    def build(model: Model, version: int, label: str | None = None) -> File:
        row = build_file(
            db_session,
            model,
            path=f"/tmp/{model.id}-{version}.gcode",
            filename=f"rev-{version}.gcode",
            file_type=FileType.GCODE,
            version=version,
            size_bytes=10,
            sha256=f"{model.id:032x}{version:032x}"[-64:],
            revision_label=label,
        )
        return row

    return build


def _tag_slugs(session: Session, model_id: int) -> set[str]:
    return set(
        session.exec(
            select(Tag.slug)
            .join(ModelTagLink, ModelTagLink.tag_id == Tag.id)
            .where(ModelTagLink.model_id == model_id)
        ).all()
    )


class TestBatchMove:
    def test_moves_every_named_model(
        self,
        client: TestClient,
        db_session: Session,
        make_user,
        headers_for,
        grant_role,
        collection_named,
        model_in,
    ) -> None:
        user = make_user("mover")
        source = collection_named("Source")
        dest = collection_named("Dest")
        grant_role(user, source, CollectionRole.EDIT)
        grant_role(user, dest, CollectionRole.EDIT)
        one = model_in("A", source.id)
        two = model_in("B", source.id)

        response = client.post(
            "/api/v1/models/batch/move",
            headers=headers_for(user),
            json={"model_ids": [one.id, two.id], "collection": "Dest"},
        )

        assert response.status_code == 200, response.text
        db_session.expire_all()
        assert db_session.get(Model, one.id).collection_id == dest.id
        assert db_session.get(Model, two.id).collection_id == dest.id

    def test_reports_what_it_moved(
        self,
        client: TestClient,
        make_user,
        headers_for,
        grant_role,
        collection_named,
        model_in,
    ) -> None:
        user = make_user("move-reporter")
        source = collection_named("Source")
        dest = collection_named("Dest")
        grant_role(user, source, CollectionRole.EDIT)
        grant_role(user, dest, CollectionRole.EDIT)
        one = model_in("A", source.id)

        response = client.post(
            "/api/v1/models/batch/move",
            headers=headers_for(user),
            json={"model_ids": [one.id], "collection": "Dest"},
        )

        assert response.json()["succeeded_ids"] == [one.id]
        assert response.json()["failed"] == []

    def test_creates_a_destination_that_does_not_exist_yet_for_a_superuser(
        self, client: TestClient, auth_headers, make_model
    ) -> None:
        model = make_model("Bracket")

        response = client.post(
            "/api/v1/models/batch/move",
            headers=auth_headers,
            json={"model_ids": [model.id], "collection": "new/spot"},
        )

        assert response.status_code == 200, response.text
        assert response.json()["failed_count"] == 0

    def test_moves_to_the_root_for_a_superuser(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers,
        collection_named,
        model_in,
    ) -> None:
        source = collection_named("Boxed")
        model = model_in("Boxed model", source.id)

        response = client.post(
            "/api/v1/models/batch/move",
            headers=auth_headers,
            json={"model_ids": [model.id], "collection": ""},
        )

        assert response.status_code == 200, response.text
        db_session.expire_all()
        assert db_session.get(Model, model.id).collection_id is None

    def test_refuses_the_whole_request_when_one_model_is_not_editable(
        self,
        client: TestClient,
        db_session: Session,
        make_user,
        headers_for,
        grant_role,
        collection_named,
        model_in,
    ) -> None:
        user = make_user("partial-mover")
        allowed_in = collection_named("Allowed")
        forbidden_in = collection_named("Forbidden")
        dest = collection_named("Target")
        grant_role(user, allowed_in, CollectionRole.EDIT)
        grant_role(user, dest, CollectionRole.EDIT)
        allowed = model_in("Allowed Model", allowed_in.id)
        forbidden = model_in("Forbidden Model", forbidden_in.id)

        response = client.post(
            "/api/v1/models/batch/move",
            headers=headers_for(user),
            json={"model_ids": [allowed.id, forbidden.id], "collection": "Target"},
        )

        # Half a move leaves a library nobody asked for and no way to tell which half.
        assert response.status_code == 403, response.text
        db_session.expire_all()
        assert db_session.get(Model, allowed.id).collection_id == allowed_in.id

    def test_refuses_when_the_destination_is_not_editable(
        self,
        client: TestClient,
        db_session: Session,
        make_user,
        headers_for,
        grant_role,
        collection_named,
        model_in,
    ) -> None:
        user = make_user("no-dest")
        source = collection_named("Src")
        collection_named("Dst")
        grant_role(user, source, CollectionRole.EDIT)
        model = model_in("M", source.id)

        response = client.post(
            "/api/v1/models/batch/move",
            headers=headers_for(user),
            json={"model_ids": [model.id], "collection": "Dst"},
        )

        assert response.status_code == 403, response.text
        db_session.expire_all()
        assert db_session.get(Model, model.id).collection_id == source.id

    def test_refuses_a_move_to_the_root_from_a_collection_editor(
        self,
        client: TestClient,
        make_user,
        headers_for,
        grant_role,
        collection_named,
        model_in,
    ) -> None:
        user = make_user("rooter")
        source = collection_named("Box")
        grant_role(user, source, CollectionRole.EDIT)
        model = model_in("Boxed", source.id)

        response = client.post(
            "/api/v1/models/batch/move",
            headers=headers_for(user),
            json={"model_ids": [model.id], "collection": ""},
        )

        assert response.status_code == 403, response.text
        assert response.json()["detail"] == "root_collection_admin_required"

    def test_refuses_to_move_a_model_out_of_the_root_for_a_collection_editor(
        self,
        client: TestClient,
        make_user,
        headers_for,
        grant_role,
        collection_named,
        model_in,
    ) -> None:
        user = make_user("root-source")
        dest = collection_named("Dest")
        grant_role(user, dest, CollectionRole.EDIT)
        model = model_in("Root model", None)

        response = client.post(
            "/api/v1/models/batch/move",
            headers=headers_for(user),
            json={"model_ids": [model.id], "collection": "Dest"},
        )

        # A model at the root belongs to whoever owns the root, whatever rights
        # the caller has on the destination.
        assert response.status_code == 403, response.text
        assert response.json()["detail"] == "root_collection_admin_required"

    def test_refuses_a_destination_that_does_not_exist_for_a_collection_editor(
        self, client: TestClient, make_user, headers_for, make_model
    ) -> None:
        user = make_user("dest-creator")
        model = make_model("Bracket")

        response = client.post(
            "/api/v1/models/batch/move",
            headers=headers_for(user),
            json={"model_ids": [model.id], "collection": "does/not/exist"},
        )

        assert response.status_code == 403, response.text
        assert response.json()["detail"] == "collection_permission_denied"

    def test_refuses_the_whole_request_when_one_id_does_not_exist(
        self, client: TestClient, db_session: Session, auth_headers, model_in
    ) -> None:
        real = model_in("Real", None)

        response = client.post(
            "/api/v1/models/batch/move",
            headers=auth_headers,
            json={"model_ids": [real.id, 999999], "collection": "Here"},
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "model_not_found"
        db_session.refresh(real)
        assert real.collection_id is None

    def test_creates_no_destination_when_every_id_fails(
        self, client: TestClient, db_session: Session, auth_headers
    ) -> None:
        client.post(
            "/api/v1/models/batch/move",
            headers=auth_headers,
            json={"model_ids": [999999], "collection": "Brand New Box"},
        )

        # An orphan collection is evidence of an operation that never happened.
        db_session.expire_all()
        created = db_session.exec(
            select(Collection).where(
                Collection.path == taxonomy.slugify("Brand New Box")
            )
        ).first()
        assert created is None

    def test_rejects_an_unauthenticated_caller(
        self, client: TestClient, make_model
    ) -> None:
        model = make_model("Bracket")

        response = client.post(
            "/api/v1/models/batch/move",
            json={"model_ids": [model.id], "collection": "somewhere"},
        )

        assert response.status_code == 401, response.text


class TestBatchTags:
    def test_adds_a_tag_to_every_named_model(
        self, client: TestClient, db_session: Session, auth_headers, model_in
    ) -> None:
        one = model_in("Tagged one", None)
        two = model_in("Tagged two", None)

        response = client.post(
            "/api/v1/models/batch/tags",
            headers=auth_headers,
            json={"model_ids": [one.id, two.id], "add": ["shiny"]},
        )

        assert response.status_code == 200, response.text
        db_session.expire_all()
        assert _tag_slugs(db_session, one.id) == {"shiny"}
        assert _tag_slugs(db_session, two.id) == {"shiny"}

    def test_leaves_tags_the_model_already_had(
        self, client: TestClient, db_session: Session, auth_headers, model_in
    ) -> None:
        model = model_in("Tagged", None)
        existing = taxonomy.resolve_or_create_tags(db_session, ["keep"])
        db_session.add(ModelTagLink(model_id=model.id, tag_id=existing[0].id))
        db_session.commit()

        client.post(
            "/api/v1/models/batch/tags",
            headers=auth_headers,
            json={"model_ids": [model.id], "add": ["keep", "new"]},
        )

        db_session.expire_all()
        assert _tag_slugs(db_session, model.id) == {"keep", "new"}

    def test_removes_only_the_tags_it_was_asked_to(
        self, client: TestClient, db_session: Session, auth_headers, model_in
    ) -> None:
        model = model_in("HasTags", None)
        for tag in taxonomy.resolve_or_create_tags(db_session, ["alpha", "beta"]):
            db_session.add(ModelTagLink(model_id=model.id, tag_id=tag.id))
        db_session.commit()

        client.post(
            "/api/v1/models/batch/tags",
            headers=auth_headers,
            json={"model_ids": [model.id], "remove": ["alpha", "ghost"]},
        )

        db_session.expire_all()
        assert _tag_slugs(db_session, model.id) == {"beta"}

    def test_ignores_a_blank_remove_entry(
        self, client: TestClient, db_session: Session, auth_headers, model_in
    ) -> None:
        """A blank entry removes nothing — and specifically not the tag `model`.

        `slugify` falls back to `"model"` for anything it cannot make a slug out
        of, so the guard used to slug first and then ask whether the *slug* was
        empty. It never was, so `remove: ["   "]` quietly meant "remove the tag
        named model" from every model in the batch. Guarding the input instead
        makes the blank case do what it reads like it does.
        """
        model = model_in("Fallback", None)
        tag = taxonomy.resolve_or_create_tags(db_session, ["model"])[0]
        db_session.add(ModelTagLink(model_id=model.id, tag_id=tag.id))
        db_session.commit()

        client.post(
            "/api/v1/models/batch/tags",
            headers=auth_headers,
            json={"model_ids": [model.id], "remove": ["   "]},
        )

        db_session.expire_all()
        assert _tag_slugs(db_session, model.id) == {"model"}

    def test_refuses_the_whole_request_when_one_model_is_not_editable(
        self,
        client: TestClient,
        db_session: Session,
        make_user,
        headers_for,
        grant_role,
        collection_named,
        model_in,
    ) -> None:
        user = make_user("tag-partial")
        allowed_in = collection_named("TagAllowed")
        forbidden_in = collection_named("TagForbidden")
        grant_role(user, allowed_in, CollectionRole.EDIT)
        allowed = model_in("TA", allowed_in.id)
        forbidden = model_in("TF", forbidden_in.id)

        response = client.post(
            "/api/v1/models/batch/tags",
            headers=headers_for(user),
            json={"model_ids": [allowed.id, forbidden.id], "add": ["shared"]},
        )

        assert response.status_code == 403, response.text
        db_session.expire_all()
        assert _tag_slugs(db_session, allowed.id) == set()

    def test_creates_no_tag_when_every_model_fails(
        self,
        client: TestClient,
        db_session: Session,
        make_user,
        headers_for,
        collection_named,
        model_in,
    ) -> None:
        user = make_user("orphan-tagger")
        locked = collection_named("Locked")
        model = model_in("Untouchable", locked.id)

        client.post(
            "/api/v1/models/batch/tags",
            headers=headers_for(user),
            json={"model_ids": [model.id], "add": ["should-not-exist"]},
        )

        db_session.expire_all()
        assert (
            db_session.exec(select(Tag).where(Tag.slug == "should-not-exist")).first()
            is None
        )

    def test_rejects_an_unauthenticated_caller(
        self, client: TestClient, make_model
    ) -> None:
        model = make_model("Bracket")

        response = client.post(
            "/api/v1/models/batch/tags",
            json={"model_ids": [model.id], "add": ["shiny"]},
        )

        assert response.status_code == 401, response.text


class TestBatchRevisionLabels:
    def test_labels_every_named_revision(
        self,
        client: TestClient,
        db_session: Session,
        make_user,
        headers_for,
        grant_role,
        collection_named,
        model_in,
        revision,
    ) -> None:
        user = make_user("revision-editor")
        collection = collection_named("Revisions")
        grant_role(user, collection, CollectionRole.EDIT)
        model = model_in("Revision Model", collection.id)
        first = revision(model, 1, "old")
        second = revision(model, 2)

        response = client.patch(
            "/api/v1/models/batch/revision-labels",
            headers=headers_for(user),
            json={"file_ids": [first.id, second.id], "revision_label": "  PETG fast  "},
        )

        assert response.status_code == 200, response.text
        db_session.expire_all()
        assert db_session.get(File, first.id).revision_label == "PETG fast"
        assert db_session.get(File, second.id).revision_label == "PETG fast"

    def test_clears_a_label_set_to_nothing(
        self, client: TestClient, db_session: Session, auth_headers, model_in, revision
    ) -> None:
        model = model_in("Clearable", None)
        row = revision(model, 1, "old")

        client.patch(
            "/api/v1/models/batch/revision-labels",
            headers=auth_headers,
            json={"file_ids": [row.id], "revision_label": ""},
        )

        db_session.expire_all()
        assert db_session.get(File, row.id).revision_label is None

    def test_refuses_a_file_that_is_not_gcode(
        self, client: TestClient, db_session: Session, auth_headers, model_in
    ) -> None:
        model = model_in("Mesh holder", None)
        mesh = build_file(
            db_session,
            model,
            path="/tmp/mesh.stl",
            filename="mesh.stl",
            file_type=FileType.STL,
            version=1,
            size_bytes=10,
            sha256="f" * 64,
        )

        response = client.patch(
            "/api/v1/models/batch/revision-labels",
            headers=auth_headers,
            json={"file_ids": [mesh.id], "revision_label": "x"},
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "revision_not_supported"

    def test_refuses_a_file_that_does_not_exist(
        self, client: TestClient, auth_headers
    ) -> None:
        response = client.patch(
            "/api/v1/models/batch/revision-labels",
            headers=auth_headers,
            json={"file_ids": [999999], "revision_label": "x"},
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "file_not_found"

    def test_refuses_the_whole_request_when_one_file_is_not_editable(
        self,
        client: TestClient,
        db_session: Session,
        make_user,
        headers_for,
        grant_role,
        collection_named,
        model_in,
        revision,
    ) -> None:
        user = make_user("revision-limited")
        allowed_in = collection_named("Rev Allowed")
        denied_in = collection_named("Rev Denied")
        grant_role(user, allowed_in, CollectionRole.EDIT)
        allowed = revision(model_in("Allowed Revision", allowed_in.id), 1, "A")
        denied = revision(model_in("Denied Revision", denied_in.id), 1, "B")

        response = client.patch(
            "/api/v1/models/batch/revision-labels",
            headers=headers_for(user),
            json={"file_ids": [allowed.id, denied.id], "revision_label": "changed"},
        )

        assert response.status_code == 403, response.text
        db_session.expire_all()
        assert db_session.get(File, allowed.id).revision_label == "A"

    def test_rolls_back_a_labelling_that_fails_partway(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers,
        model_in,
        revision,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.services import model_views

        model = model_in("Rollback Revisions", None)
        first = revision(model, 1, "first")
        second = revision(model, 2, "second")

        def fail_after_first(session, files, revision_label):
            files[0].revision_label = revision_label
            session.add(files[0])
            session.flush()
            raise RuntimeError("injected batch failure")

        monkeypatch.setattr(model_views, "set_revision_labels", fail_after_first)

        with pytest.raises(RuntimeError, match="injected batch failure"):
            client.patch(
                "/api/v1/models/batch/revision-labels",
                headers=auth_headers,
                json={"file_ids": [first.id, second.id], "revision_label": "changed"},
            )

        db_session.expire_all()
        assert db_session.get(File, first.id).revision_label == "first"
        assert db_session.get(File, second.id).revision_label == "second"

    def test_rejects_an_unauthenticated_caller(
        self, client: TestClient, model_in, revision
    ) -> None:
        row = revision(model_in("Anonymous", None), 1)

        response = client.patch(
            "/api/v1/models/batch/revision-labels",
            json={"file_ids": [row.id], "revision_label": "x"},
        )

        assert response.status_code == 401, response.text


class TestBatchDelete:
    def test_trashes_every_named_model(
        self, client: TestClient, db_session: Session, auth_headers, model_in
    ) -> None:
        one = model_in("DelA", None)
        two = model_in("DelB", None)

        response = client.post(
            "/api/v1/models/batch/delete",
            headers=auth_headers,
            json={"model_ids": [one.id, two.id]},
        )

        assert response.status_code == 200, response.text
        db_session.expire_all()
        assert db_session.get(Model, one.id).deleted_at is not None
        assert db_session.get(Model, two.id).deleted_at is not None

    def test_reports_what_it_trashed(
        self, client: TestClient, auth_headers, model_in
    ) -> None:
        model = model_in("Reported", None)

        response = client.post(
            "/api/v1/models/batch/delete",
            headers=auth_headers,
            json={"model_ids": [model.id]},
        )

        assert response.json()["succeeded_ids"] == [model.id]

    def test_refuses_the_whole_request_when_one_model_is_not_editable(
        self,
        client: TestClient,
        db_session: Session,
        make_user,
        headers_for,
        grant_role,
        collection_named,
        model_in,
    ) -> None:
        user = make_user("del-partial")
        allowed_in = collection_named("DelAllowed")
        forbidden_in = collection_named("DelForbidden")
        grant_role(user, allowed_in, CollectionRole.EDIT)
        allowed = model_in("DA", allowed_in.id)
        forbidden = model_in("DF", forbidden_in.id)

        response = client.post(
            "/api/v1/models/batch/delete",
            headers=headers_for(user),
            json={"model_ids": [allowed.id, forbidden.id]},
        )

        assert response.status_code == 403, response.text
        db_session.expire_all()
        assert db_session.get(Model, allowed.id).deleted_at is None

    def test_rejects_an_unauthenticated_caller(
        self, client: TestClient, make_model
    ) -> None:
        model = make_model("Bracket")

        response = client.post(
            "/api/v1/models/batch/delete", json={"model_ids": [model.id]}
        )

        assert response.status_code == 401, response.text


class TestBatchValidation:
    def test_rejects_an_empty_batch(self, client: TestClient, auth_headers) -> None:
        response = client.post(
            "/api/v1/models/batch/delete", headers=auth_headers, json={"model_ids": []}
        )

        assert response.status_code == 422, response.text

    def test_rejects_a_batch_past_the_cap(
        self, client: TestClient, auth_headers
    ) -> None:
        response = client.post(
            "/api/v1/models/batch/delete",
            headers=auth_headers,
            json={"model_ids": list(range(1, MAX_BATCH + 2))},
        )

        assert response.status_code == 422, response.text
