"""Moving a member is one transaction across both authorized Families."""

import pytest
from sqlmodel import col, select

from app.db.models import CollectionRole, ModelFamilyMember


class TestMoveMember:
    def test_rejects_the_same_family_as_destination(
        self,
        client,
        auth_headers,
        db_session,
        make_family,
        make_model,
        make_family_member,
    ):
        family, model = make_family(), make_model()
        member = make_family_member(family, model)
        before = member.model_dump()

        response = client.post(
            f"/api/v1/families/{family.id}/move-member",
            headers=auth_headers,
            json={
                "model_id": model.id,
                "source_family_id": family.id,
                "source_version": family.version,
                "destination_version": family.version,
            },
        )
        db_session.refresh(member)

        assert response.status_code == 422, response.text
        assert response.json()["detail"] == "family_move_destination_invalid"
        assert member.model_dump() == before

    @pytest.mark.parametrize("reserved_elsewhere", [False, True])
    def test_rejects_a_move_from_an_outdated_source_family(
        self,
        client,
        auth_headers,
        db_session,
        make_family,
        make_model,
        make_family_member,
        reserved_elsewhere,
    ):
        source, destination, model = make_family(), make_family(), make_model()
        original = (
            make_family_member(make_family(), model) if reserved_elsewhere else None
        )

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
        db_session.refresh(source)
        db_session.refresh(destination)

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "family_membership_conflict"
        assert source.version == destination.version == 1
        assert db_session.exec(select(ModelFamilyMember.id)).all() == (
            [original.id] if original else []
        )

    def test_uses_explicit_relative_metadata_at_the_destination(
        self,
        client,
        auth_headers,
        db_session,
        make_family,
        make_model,
        make_family_member,
    ):
        source, destination, model = make_family(), make_family(), make_model()
        make_family_member(source, model, mirrored=True, transformation_note="Old note")
        canonical = make_family_member(destination, make_model(), canonical=True)

        response = client.post(
            f"/api/v1/families/{destination.id}/move-member",
            headers=auth_headers,
            json={
                "model_id": model.id,
                "source_family_id": source.id,
                "source_version": source.version,
                "destination_version": destination.version,
                "mirrored": False,
                "mirror_verified": True,
                "scale_factor": 2,
                "transformation_note": "Measured against the new canonical",
            },
        )

        assert response.status_code == 200, response.text
        member = db_session.get(ModelFamilyMember, response.json()["id"])
        assert member.transformation_note == "Measured against the new canonical"
        assert member.mirrored is False
        assert member.mirror_verified is True
        assert member.scale_factor == 2
        assert member.relative_to_member_id == canonical.id
        assert member.mirror_reference_member_id == canonical.id
        assert member.relative_review_required is False

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
