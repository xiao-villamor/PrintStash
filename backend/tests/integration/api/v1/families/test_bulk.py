"""Explicit Family bulk actions keep authorization and one transaction per request."""

import pytest
from sqlmodel import select

from app.core.errors import ErrorKind, OperationError
from app.db.models import (
    CollectionRole,
    FileRevisionStatus,
    FileType,
    ModelFamily,
    ModelStar,
    ModelTagLink,
)


class TestFamilyBulk:
    def test_rolls_back_mutation_when_audit_fails(
        self,
        client,
        auth_headers,
        db_session,
        make_family,
        make_model,
        make_family_member,
        monkeypatch,
    ):
        from app.modules.library.families import bulk

        family = make_family()
        [make_family_member(family, make_model()) for _ in range(501)]
        family_id = family.id

        def unavailable(*_args):
            raise OperationError("audit_unavailable", kind=ErrorKind.UNAVAILABLE)

        monkeypatch.setattr(bulk, "record", unavailable)

        response = client.post(
            f"/api/v1/families/{family_id}/members/tags",
            json={"version": 1, "add": ["Ready"]},
            headers=auth_headers,
        )
        db_session.expire_all()

        assert response.status_code == 503, response.text
        assert db_session.exec(select(ModelTagLink)).all() == []
        assert db_session.get(ModelFamily, family_id).version == 1

    @pytest.mark.parametrize(
        "endpoint,payload",
        [
            pytest.param("tags", {"add": ["Ready"]}, id="tags"),
            pytest.param("collection", {"collection": "Destination"}, id="collection"),
            pytest.param("star", {}, id="star"),
        ],
    )
    def test_rejects_stale_bulk_request(
        self,
        client,
        auth_headers,
        db_session,
        make_family,
        make_model,
        make_family_member,
        endpoint,
        payload,
    ):
        family, model = make_family(version=2), make_model()
        make_family_member(family, model, canonical=True)

        response = client.post(
            f"/api/v1/families/{family.id}/members/{endpoint}",
            json={"version": 1, **payload},
            headers=auth_headers,
        )
        db_session.expire_all()

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "family_revision_conflict"
        assert db_session.exec(select(ModelTagLink)).all() == []
        assert db_session.exec(select(ModelStar)).all() == []
        assert model.collection_id is None
        assert family.version == 2

    def test_rejects_bulk_read_scope(
        self,
        client,
        db_session,
        make_user,
        headers_for,
        make_family,
        make_model,
        make_family_member,
    ):
        user, family = make_user(superuser=True), make_family()
        make_family_member(family, make_model(), canonical=True)

        response = client.post(
            f"/api/v1/families/{family.id}/members/star",
            json={"version": 1},
            headers=headers_for(user, scope="read"),
        )

        assert response.status_code == 401, response.text
        assert db_session.exec(select(ModelStar)).all() == []

    @pytest.mark.parametrize("tag", [" ", "x" * 65], ids=["blank", "too-long"])
    def test_rejects_invalid_family_tags(
        self,
        client,
        auth_headers,
        db_session,
        make_family,
        make_model,
        make_family_member,
        tag,
    ):
        family = make_family()
        make_family_member(family, make_model(), canonical=True)

        response = client.post(
            f"/api/v1/families/{family.id}/members/tags",
            json={"version": 1, "add": [tag]},
            headers=auth_headers,
        )

        assert response.status_code == 422, response.text
        assert db_session.exec(select(ModelTagLink)).all() == []

    def test_preserves_known_good_per_member(
        self,
        client,
        auth_headers,
        db_session,
        make_family,
        make_model,
        make_family_member,
        make_file,
    ):
        family = make_family()
        original, sibling = make_model(), make_model()
        make_family_member(family, original, canonical=True)
        make_family_member(family, sibling)
        good = make_file(
            original,
            file_type=FileType.GCODE,
            status=FileRevisionStatus.KNOWN_GOOD,
            recommended=True,
        )
        pending = make_file(
            sibling, file_type=FileType.GCODE, status=FileRevisionStatus.NEEDS_TEST
        )

        response = client.post(
            f"/api/v1/families/{family.id}/members/tags",
            json={"version": 1, "add": ["Ready"]},
            headers=auth_headers,
        )
        db_session.expire_all()

        assert response.status_code == 200, response.text
        assert good.revision_status == FileRevisionStatus.KNOWN_GOOD
        assert good.is_recommended is True
        assert pending.revision_status == FileRevisionStatus.NEEDS_TEST
        assert pending.is_recommended is False

    def test_applies_bulk_tags_explicitly(
        self,
        client,
        auth_headers,
        make_family,
        make_model,
        make_family_member,
        make_tag,
        tag_model,
    ):
        family = make_family()
        original, scaled = make_model(), make_model()
        make_family_member(family, original, canonical=True)
        make_family_member(family, scaled)
        tag_model(original, make_tag("Keep"))

        response = client.post(
            f"/api/v1/families/{family.id}/members/tags",
            json={"version": family.version, "add": ["Ready"]},
            headers=auth_headers,
        )

        assert response.status_code == 200, response.text
        assert response.json()["succeeded_count"] == 2
        first = client.get(f"/api/v1/models/{original.id}", headers=auth_headers).json()
        second = client.get(f"/api/v1/models/{scaled.id}", headers=auth_headers).json()
        assert first["tags"] == ["Keep", "Ready"]
        assert second["tags"] == ["Ready"]
        assert (
            client.get(f"/api/v1/families/{family.id}", headers=auth_headers).json()[
                "tags"
            ]
            == []
        )

    def test_moves_all_members_with_destination_permission(
        self,
        client,
        make_user,
        headers_for,
        make_collection,
        grant_role,
        make_family,
        make_model,
        make_family_member,
        db_session,
    ):
        user = make_user()
        source, destination = make_collection("Source"), make_collection("Destination")
        grant_role(user, source, CollectionRole.EDIT)
        grant_role(user, destination, CollectionRole.EDIT)
        family = make_family()
        models = [make_model(collection=source) for _ in range(2)]
        [make_family_member(family, model) for model in models]

        response = client.post(
            f"/api/v1/families/{family.id}/members/collection",
            json={"version": family.version, "collection": destination.path},
            headers=headers_for(user),
        )
        db_session.expire_all()

        assert response.status_code == 200, response.text
        assert response.json()["succeeded_count"] == 2
        assert [model.collection_id for model in models] == [
            destination.id,
            destination.id,
        ]

    def test_rejects_partial_bulk_move(
        self,
        client,
        make_user,
        headers_for,
        make_collection,
        grant_role,
        make_family,
        make_model,
        make_family_member,
        db_session,
    ):
        user = make_user()
        source, restricted, destination = (
            make_collection("Source"),
            make_collection("Restricted"),
            make_collection("Destination"),
        )
        grant_role(user, source, CollectionRole.EDIT)
        grant_role(user, restricted, CollectionRole.VIEW)
        grant_role(user, destination, CollectionRole.EDIT)
        family = make_family()
        editable, forbidden = (
            make_model(collection=source),
            make_model(collection=restricted),
        )
        make_family_member(family, editable, canonical=True)
        make_family_member(family, forbidden)

        response = client.post(
            f"/api/v1/families/{family.id}/members/collection",
            json={"version": family.version, "collection": destination.path},
            headers=headers_for(user),
        )
        db_session.expire_all()

        assert response.status_code == 403, response.text
        assert editable.collection_id == source.id
        assert forbidden.collection_id == restricted.id
        assert family.version == 1

    def test_stars_visible_members_for_current_user(
        self,
        client,
        db_session,
        make_user,
        headers_for,
        make_collection,
        grant_role,
        make_family,
        make_model,
        make_family_member,
    ):
        user = make_user()
        visible, hidden = make_collection("Visible"), make_collection("Hidden")
        grant_role(user, visible, CollectionRole.VIEW)
        family = make_family()
        model = make_model(collection=visible)
        make_family_member(family, model, canonical=True)
        make_family_member(family, make_model(collection=hidden))
        make_family_member(family, make_model(collection=visible, trashed=True))

        response = client.post(
            f"/api/v1/families/{family.id}/members/star",
            json={"version": family.version},
            headers=headers_for(user),
        )

        assert response.status_code == 200, response.text
        assert response.json()["succeeded_ids"] == [model.id]
        assert db_session.exec(select(ModelStar.user_id, ModelStar.model_id)).all() == [
            (user.id, model.id)
        ]
