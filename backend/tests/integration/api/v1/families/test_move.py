"""Moving a member is one transaction across both authorized Families."""

from sqlmodel import col, select

from app.db.models import CollectionRole, ModelFamilyMember


class TestMoveMember:
    def test_moves_member_atomically(
        self,
        client,
        auth_headers,
        db_session,
        make_family,
        make_model,
        make_family_member,
    ):
        source, destination, model = make_family(), make_family(), make_model()
        original = make_family_member(
            source, model, transformation_note="Human context"
        )
        make_family_member(destination, make_model(), canonical=True)

        response = client.post(
            f"/api/v1/families/{destination.id}/move-member",
            headers=auth_headers,
            json={
                "model_id": model.id,
                "source_family_id": source.id,
                "source_version": source.version,
                "destination_version": destination.version,
                "role": "print_variant",
            },
        )
        db_session.expire_all()

        assert response.status_code == 200, response.text
        assert response.json()["transformation_note"] == "Human context"
        assert original.detach_reason == "moved"
        assert db_session.exec(
            select(ModelFamilyMember.family_id).where(
                ModelFamilyMember.model_id == model.id,
                col(ModelFamilyMember.detached_at).is_(None),
            )
        ).all() == [destination.id]

    def test_rolls_back_failed_member_move(
        self,
        client,
        auth_headers,
        db_session,
        make_family,
        make_model,
        make_family_member,
    ):
        source, destination, model = make_family(), make_family(version=2), make_model()
        original = make_family_member(source, model, canonical=True)

        response = client.post(
            f"/api/v1/families/{destination.id}/move-member",
            headers=auth_headers,
            json={
                "model_id": model.id,
                "source_family_id": source.id,
                "source_version": source.version,
                "destination_version": 1,
            },
        )
        db_session.expire_all()

        assert response.status_code == 409, response.text
        assert source.version == 1
        assert source.canonical_member_id == original.id
        assert original.detached_at is None
        assert db_session.exec(select(ModelFamilyMember.id)).all() == [original.id]

    def test_preserves_destination_canonical_on_move(
        self,
        client,
        auth_headers,
        db_session,
        make_family,
        make_model,
        make_family_member,
    ):
        source, destination, model = make_family(), make_family(), make_model()
        make_family_member(source, model, canonical=True)
        selected = make_family_member(destination, make_model(), canonical=True)

        response = client.post(
            f"/api/v1/families/{destination.id}/move-member",
            headers=auth_headers,
            json={
                "model_id": model.id,
                "source_family_id": source.id,
                "source_version": source.version,
                "destination_version": destination.version,
            },
        )
        db_session.expire_all()

        assert response.status_code == 200, response.text
        assert source.canonical_member_id is None
        assert destination.canonical_member_id == selected.id
        assert response.json()["role"] == "identical"

    def test_rechecks_permissions_before_move(
        self,
        client,
        db_session,
        make_family,
        make_model,
        make_family_member,
        make_user,
        make_collection,
        grant_role,
        headers_for,
    ):
        user = make_user()
        collection = make_collection("Shared")
        permission = grant_role(user, collection, CollectionRole.EDIT)
        source, destination = (
            make_family(collection=collection),
            make_family(collection=collection),
        )
        model = make_model(collection=collection)
        original = make_family_member(source, model, canonical=True)
        make_family_member(
            destination, make_model(collection=collection), canonical=True
        )
        shown = client.get(f"/api/v1/families/{source.id}", headers=headers_for(user))
        permission.role = CollectionRole.VIEW
        db_session.add(permission)
        db_session.commit()

        response = client.post(
            f"/api/v1/families/{destination.id}/move-member",
            headers=headers_for(user),
            json={
                "model_id": model.id,
                "source_family_id": source.id,
                "source_version": source.version,
                "destination_version": destination.version,
            },
        )

        assert shown.status_code == 200, shown.text
        assert response.status_code == 403, response.text
        assert original.detached_at is None
        assert original.family_id == source.id
