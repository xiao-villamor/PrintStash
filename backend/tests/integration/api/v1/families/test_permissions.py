"""Family reads disclose live authorized members; shared writes require all EDIT.

Reservations for trashed Models still contribute to authorization. A grouping
must not become a way to discover hidden siblings or their previews.
"""

import pytest
from sqlmodel import select

from app.db.models import CollectionRole, ModelFamily


class TestFamilyPermissions:
    @pytest.mark.parametrize(
        "trashed", [False, True], ids=["live", "trashed-reservation"]
    )
    def test_denies_mutation_with_mixed_edit_roles(
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
        trashed,
    ):
        user = make_user()
        editable, readonly = make_collection("Editable"), make_collection("Private")
        grant_role(user, editable, CollectionRole.EDIT)
        grant_role(user, readonly, CollectionRole.VIEW)
        family = make_family(created_by=user.id)
        make_family_member(family, make_model(collection=editable), canonical=True)
        make_family_member(family, make_model(collection=readonly, trashed=trashed))
        original_name = family.name

        response = client.patch(
            f"/api/v1/families/{family.id}",
            headers=headers_for(user),
            json={
                "name": "Forbidden edit",
                "version": family.version,
            },
        )
        db_session.expire_all()

        assert response.status_code == 403, response.text
        assert family.name == original_name

    def test_hides_invisible_siblings(
        self,
        client,
        make_user,
        headers_for,
        make_collection,
        grant_role,
        make_model,
        make_family,
        make_family_member,
    ):
        user = make_user()
        shared, hidden = make_collection("Editable"), make_collection("Private")
        grant_role(user, shared, CollectionRole.VIEW)
        family = make_family()
        make_family_member(family, make_model(collection=shared), canonical=True)
        make_family_member(family, make_model("Private prototype", collection=hidden))

        response = client.get(
            f"/api/v1/families/{family.id}", headers=headers_for(user)
        )

        assert response.status_code == 200, response.text
        assert response.json()["member_count"] == 1
        assert "Private prototype" not in response.text
        assert response.json()["effective_role"] == "view"

    def test_hides_invisible_canonical(
        self,
        client,
        make_user,
        headers_for,
        make_collection,
        grant_role,
        make_model,
        make_family,
        make_family_member,
    ):
        user = make_user()
        shared, hidden = make_collection("Editable"), make_collection("Private")
        grant_role(user, shared, CollectionRole.VIEW)
        family = make_family()
        make_family_member(
            family,
            make_model(collection=hidden, thumbnail_path="7.png"),
            canonical=True,
        )
        make_family_member(
            family, make_model(collection=shared, thumbnail_path="8.png")
        )

        response = client.get(
            f"/api/v1/families/{family.id}", headers=headers_for(user)
        )

        assert response.status_code == 200, response.text
        assert response.json()["canonical_model_id"] is None
        assert response.json()["canonical_member_id"] is None
        assert response.json()["cover_thumbnail_url"] is None

    def test_denies_unauthenticated_family_write(self, client, db_session, make_model):
        model = make_model()

        response = client.post(
            "/api/v1/families",
            json={
                "name": "Unauthenticated",
                "canonical_model_id": model.id,
                "members": [{"model_id": model.id}],
            },
        )

        assert response.status_code == 401, response.text
        assert db_session.exec(select(ModelFamily)).all() == []

    def test_denies_read_scope_family_write(
        self,
        client,
        db_session,
        make_user,
        headers_for,
        make_model,
    ):
        user, model = make_user(superuser=True), make_model()

        response = client.post(
            "/api/v1/families",
            headers=headers_for(user, scope="read"),
            json={
                "name": "Read scope",
                "canonical_model_id": model.id,
                "members": [{"model_id": model.id}],
            },
        )

        assert response.status_code == 401, response.text
        assert db_session.exec(select(ModelFamily)).all() == []

    def test_hides_family_in_inaccessible_collection(
        self,
        client,
        make_user,
        headers_for,
        make_collection,
        grant_role,
        make_model,
        make_family,
        make_family_member,
    ):
        user, shared, hidden = (
            make_user(),
            make_collection("Editable"),
            make_collection("Private"),
        )
        grant_role(user, shared, CollectionRole.EDIT)
        family = make_family(collection=hidden)
        make_family_member(family, make_model(collection=shared), canonical=True)

        response = client.get(
            f"/api/v1/families/{family.id}", headers=headers_for(user)
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "family_not_found"
