"""Family metadata and personal favorites never rewrite their member Models."""

import pytest
from sqlmodel import select

from app.db.models import CollectionRole, ModelFamilyStar


class TestFamilyMetadata:
    def test_moves_only_the_family_collection(
        self,
        client,
        auth_headers,
        db_session,
        make_collection,
        make_model,
        make_family,
        make_family_member,
    ):
        original, destination = (
            make_collection("Original"),
            make_collection("Destination"),
        )
        model = make_model(collection=original)
        family = make_family(collection=original)
        make_family_member(family, model, canonical=True)
        db_session.refresh(model)
        before = model.model_dump()

        response = client.patch(
            f"/api/v1/families/{family.id}",
            headers=auth_headers,
            json={"version": family.version, "collection_id": destination.id},
        )
        db_session.refresh(family)

        assert response.status_code == 200, response.text
        assert response.json()["collection_id"] == destination.id
        assert family.collection_id == destination.id
        db_session.refresh(model)
        assert model.model_dump() == before

    def test_rejects_a_read_only_destination_collection(
        self,
        client,
        db_session,
        make_user,
        headers_for,
        make_collection,
        grant_role,
        make_model,
        make_family,
        make_family_member,
    ):
        actor = make_user()
        original, destination = (
            make_collection("Original"),
            make_collection("Destination"),
        )
        grant_role(actor, original, CollectionRole.EDIT)
        grant_role(actor, destination, CollectionRole.VIEW)
        family = make_family(collection=original)
        make_family_member(family, make_model(collection=original), canonical=True)
        db_session.refresh(family)
        before = family.model_dump()

        response = client.patch(
            f"/api/v1/families/{family.id}",
            headers=headers_for(actor),
            json={"version": family.version, "collection_id": destination.id},
        )
        db_session.refresh(family)

        assert response.status_code == 403, response.text
        assert family.model_dump() == before

    @pytest.mark.parametrize("reservation", ["ungrouped", "foreign", "detached"])
    def test_rejects_a_cover_outside_active_membership(
        self,
        client,
        auth_headers,
        db_session,
        make_family,
        make_family_member,
        make_model,
        reservation,
    ):
        family, model = make_family(), make_model()
        if reservation == "foreign":
            make_family_member(make_family(), model)
        elif reservation == "detached":
            make_family_member(family, model, detached="removed")
        db_session.refresh(family)
        before = family.model_dump()

        response = client.patch(
            f"/api/v1/families/{family.id}",
            headers=auth_headers,
            json={"version": family.version, "cover_model_id": model.id},
        )
        db_session.refresh(family)

        assert response.status_code == 422, response.text
        assert response.json()["detail"] == "family_cover_invalid"
        assert family.model_dump() == before


class TestPersonalFamilyStar:
    @pytest.mark.parametrize("initial", [False, True])
    @pytest.mark.parametrize("desired", [False, True])
    def test_replays_only_the_actors_favorite(
        self,
        client,
        db_session,
        make_user,
        headers_for,
        make_family,
        make_family_star,
        initial,
        desired,
    ):
        actor, other = make_user(superuser=True), make_user(superuser=True)
        family = make_family()
        make_family_star(other, family)
        if initial:
            make_family_star(actor, family)
        db_session.refresh(family)
        before = family.model_dump()

        for _ in range(2):
            response = client.request(
                "PUT" if desired else "DELETE",
                f"/api/v1/families/{family.id}/star",
                headers=headers_for(actor),
            )
            assert response.status_code == 204, response.text
        db_session.refresh(family)

        starred_users = set(
            db_session.exec(
                select(ModelFamilyStar.user_id).where(
                    ModelFamilyStar.family_id == family.id
                )
            ).all()
        )
        assert starred_users == ({actor.id, other.id} if desired else {other.id})
        assert family.model_dump() == before
