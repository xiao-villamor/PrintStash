"""Families group independent Models through an authenticated relational API.

No ordinary grouping action may change an Artifact, Revision, print outcome or
source. Mixed permissions must reject a write before any relationship changes.
"""

from sqlmodel import select

from app.db.models import ModelFamilyMember


class TestAddMember:
    def test_replays_identical_member_add(
        self,
        client,
        auth_headers,
        db_session,
        make_family,
        make_model,
        make_family_member,
    ):
        family = make_family()
        make_family_member(family, make_model(), canonical=True)
        added = make_model()
        data = {
            "model_id": added.id,
            "role": "rescaled",
            "scale_factor": 2,
            "version": family.version,
        }

        first = client.post(
            f"/api/v1/families/{family.id}/members", headers=auth_headers, json=data
        )
        replay = client.post(
            f"/api/v1/families/{family.id}/members", headers=auth_headers, json=data
        )

        assert first.status_code == 200, first.text
        assert replay.status_code == 200, replay.text
        assert replay.json() == first.json()
        assert db_session.exec(
            select(ModelFamilyMember.id).where(ModelFamilyMember.model_id == added.id)
        ).all() == [first.json()["id"]]

    def test_requires_patch_for_different_add_payload(
        self, client, auth_headers, make_family, make_model, make_family_member
    ):
        family, model = make_family(), make_model()
        member = make_family_member(
            family, model, role="repaired", transformation_note="Original note"
        )

        response = client.post(
            f"/api/v1/families/{family.id}/members",
            headers=auth_headers,
            json={
                "model_id": model.id,
                "role": "mirrored",
                "version": family.version,
            },
        )

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "family_member_update_required"
        assert member.transformation_note == "Original note"


class TestChangeCanonical:
    def test_changes_canonical_atomically(
        self,
        client,
        auth_headers,
        db_session,
        make_family,
        make_model,
        make_family_member,
    ):
        family = make_family()
        original = make_family_member(family, make_model(), canonical=True)
        replacement = make_family_member(
            family,
            make_model(),
            role="rescaled",
            scale_factor=2,
            relative_to_member_id=original.id,
        )

        response = client.post(
            f"/api/v1/families/{family.id}/canonical",
            headers=auth_headers,
            json={
                "member_id": replacement.id,
                "previous_role": "print_variant",
                "version": family.version,
            },
        )
        db_session.expire_all()

        assert response.status_code == 200, response.text
        assert response.json()["canonical_model_id"] == replacement.model_id
        assert original.role == "print_variant"
        assert original.scale_factor == 0.5
        assert replacement.role == "canonical"
        assert replacement.scale_factor == 1.0

    def test_rejects_foreign_canonical(
        self, client, auth_headers, make_family, make_model, make_family_member
    ):
        family = make_family()
        original = make_family_member(family, make_model(), canonical=True)
        outsider = make_family_member(make_family(), make_model())

        response = client.post(
            f"/api/v1/families/{family.id}/canonical",
            headers=auth_headers,
            json={
                "member_id": outsider.id,
                "previous_role": "identical",
                "version": family.version,
            },
        )

        assert response.status_code == 422, response.text
        assert family.canonical_member_id == original.id

    def test_rejects_stale_canonical_edit(
        self, client, auth_headers, make_family, make_model, make_family_member
    ):
        family = make_family(version=2)
        original = make_family_member(family, make_model(), canonical=True)
        replacement = make_family_member(family, make_model())

        response = client.post(
            f"/api/v1/families/{family.id}/canonical",
            headers=auth_headers,
            json={
                "member_id": replacement.id,
                "previous_role": "identical",
                "version": 1,
            },
        )

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "family_revision_conflict"
        assert family.canonical_member_id == original.id


class TestDetachMember:
    def test_detaches_canonical_to_vacancy(
        self,
        client,
        auth_headers,
        db_session,
        make_family,
        make_model,
        make_family_member,
    ):
        family, model = make_family(), make_model()
        selected = make_family_member(family, model, canonical=True)
        family_id = family.id

        response = client.delete(
            f"/api/v1/families/{family_id}/members/{selected.id}?version={family.version}",
            headers=auth_headers,
        )
        db_session.expire_all()

        assert response.status_code == 204, response.text
        assert family.canonical_member_id is None
        assert selected.detach_reason == "removed"
        assert model.deleted_at is None
