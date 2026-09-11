"""Families group independent Models through an authenticated relational API.

No ordinary grouping action may change an Artifact, Revision, print outcome or
source. Mixed permissions must reject a write before any relationship changes.
"""

from datetime import datetime, timezone

import pytest
from sqlmodel import select

from app.db.models import (
    CollectionRole,
    FileRevisionStatus,
    FileType,
    ModelFamilyMember,
    PrintJobState,
)


class TestAddMember:
    def test_rejects_an_existing_reservation_in_another_family(
        self,
        client,
        auth_headers,
        db_session,
        make_family,
        make_model,
        make_family_member,
    ):
        source, destination, model = make_family(), make_family(), make_model()
        member = make_family_member(source, model)
        before = member.model_dump()

        response = client.post(
            f"/api/v1/families/{destination.id}/members",
            headers=auth_headers,
            json={"model_id": model.id, "version": destination.version},
        )
        db_session.refresh(member)

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "family_membership_conflict"
        assert member.model_dump() == before

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
    def test_preserves_current_canonical_selection(
        self,
        client,
        auth_headers,
        db_session,
        make_family,
        make_model,
        make_family_member,
    ):
        family = make_family()
        canonical = make_family_member(family, make_model(), canonical=True)
        sibling = make_family_member(
            family, make_model(), mirrored=True, mirror_verified=False
        )
        before = db_session.get(ModelFamilyMember, sibling.id).model_dump()

        response = client.post(
            f"/api/v1/families/{family.id}/canonical",
            json={
                "version": family.version,
                "member_id": canonical.id,
                "previous_role": "identical",
            },
            headers=auth_headers,
        )
        db_session.expire_all()

        assert response.status_code == 200, response.text
        assert family.version == 1
        assert db_session.get(ModelFamilyMember, sibling.id).model_dump() == before

    def test_clears_unreliable_relative_scale(
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
        replacement = make_family_member(family, make_model(), scale_factor=None)
        sibling = make_family_member(
            family,
            make_model(),
            role="repaired",
            scale_factor=2,
            relative_to_member_id=original.id,
            transformation_note="Preserve this note",
        )

        response = client.post(
            f"/api/v1/families/{family.id}/canonical",
            json={
                "version": 1,
                "member_id": replacement.id,
                "previous_role": "identical",
            },
            headers=auth_headers,
        )
        db_session.expire_all()

        assert response.status_code == 200, response.text
        assert original.scale_factor is None
        assert sibling.scale_factor is None
        assert sibling.relative_to_member_id is None
        assert replacement.scale_factor == 1
        assert sibling.transformation_note == "Preserve this note"
        assert sibling.role == "repaired"

    def test_recomposes_verified_mirror_relation(
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
            mirrored=True,
            mirror_verified=True,
            mirror_reference_member_id=original.id,
        )
        sibling = make_family_member(
            family,
            make_model(),
            mirrored=True,
            mirror_verified=True,
            mirror_reference_member_id=original.id,
        )

        response = client.post(
            f"/api/v1/families/{family.id}/canonical",
            json={
                "version": 1,
                "member_id": replacement.id,
                "previous_role": "mirrored",
            },
            headers=auth_headers,
        )
        db_session.expire_all()

        assert response.status_code == 200, response.text
        assert original.mirrored is True
        assert sibling.mirrored is False
        assert sibling.mirror_reference_member_id == replacement.id
        assert sibling.mirror_verified is True
        assert sibling.relative_review_required is False

    def test_preserves_unverified_mirror_provenance(
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
        replacement = make_family_member(family, make_model())
        sibling = make_family_member(
            family,
            make_model(),
            mirrored=True,
            mirror_verified=False,
            mirror_reference_member_id=original.id,
        )

        response = client.post(
            f"/api/v1/families/{family.id}/canonical",
            json={
                "version": 1,
                "member_id": replacement.id,
                "previous_role": "identical",
            },
            headers=auth_headers,
        )
        db_session.expire_all()

        assert response.status_code == 200, response.text
        assert sibling.mirrored is True
        assert sibling.mirror_reference_member_id == original.id
        assert sibling.mirror_verified is False
        assert sibling.relative_review_required is True

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
    def test_clears_a_detached_cover_reference(
        self,
        client,
        auth_headers,
        db_session,
        make_family,
        make_model,
        make_family_member,
    ):
        model = make_model()
        family = make_family(cover_model_id=model.id)
        member = make_family_member(family, model)

        response = client.delete(
            f"/api/v1/families/{family.id}/members/{member.id}",
            params={"version": family.version},
            headers=auth_headers,
        )
        db_session.refresh(family)
        db_session.refresh(model)

        assert response.status_code == 204, response.text
        assert family.cover_model_id is None
        assert model.deleted_at is None

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


class TestUpdateMember:
    @pytest.mark.parametrize("reservation", ["foreign", "detached"])
    def test_rejects_a_member_outside_the_active_family(
        self,
        client,
        auth_headers,
        db_session,
        make_family,
        make_model,
        make_family_member,
        reservation,
    ):
        family = make_family()
        member = make_family_member(
            make_family() if reservation == "foreign" else family,
            make_model(),
            detached="removed" if reservation == "detached" else None,
        )
        before = member.model_dump()

        response = client.patch(
            f"/api/v1/families/{family.id}/members/{member.id}",
            headers=auth_headers,
            json={"version": family.version, "transformation_note": "Attempt"},
        )
        db_session.refresh(member)

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "family_member_not_found"
        assert member.model_dump() == before

    @pytest.mark.parametrize(
        "changes",
        [
            {"role": "repaired"},
            {"scale_factor": 2},
            {"mirrored": True},
        ],
    )
    def test_preserves_canonical_identity_during_member_edits(
        self,
        client,
        auth_headers,
        db_session,
        make_family,
        make_model,
        make_family_member,
        changes,
    ):
        family = make_family()
        member = make_family_member(family, make_model(), canonical=True)
        db_session.refresh(member)
        before = member.model_dump()

        response = client.patch(
            f"/api/v1/families/{family.id}/members/{member.id}",
            headers=auth_headers,
            json={"version": family.version, **changes},
        )
        db_session.refresh(member)

        assert response.status_code == 422, response.text
        assert response.json()["detail"] == "family_canonical_invalid"
        assert member.model_dump() == before

    @pytest.mark.parametrize("vacant", [False, True])
    def test_records_the_reference_for_explicit_relative_edits(
        self,
        client,
        auth_headers,
        db_session,
        make_family,
        make_model,
        make_family_member,
        vacant,
    ):
        family = make_family()
        canonical = (
            None if vacant else make_family_member(family, make_model(), canonical=True)
        )
        member = make_family_member(family, make_model())

        response = client.patch(
            f"/api/v1/families/{family.id}/members/{member.id}",
            headers=auth_headers,
            json={
                "version": family.version,
                "role": "rescaled",
                "scale_factor": 2,
                "mirrored": True,
                "mirror_verified": True,
            },
        )
        db_session.refresh(member)

        assert response.status_code == 200, response.text
        assert response.json()["role"] == "rescaled"
        assert member.scale_factor == 2
        assert member.mirrored is True
        assert member.mirror_verified is True
        assert member.relative_to_member_id == (canonical.id if canonical else None)
        assert member.mirror_reference_member_id == (
            canonical.id if canonical else None
        )
        assert member.relative_review_required is vacant


class TestListMembers:
    def test_returns_member_metadata(
        self,
        client,
        auth_headers,
        make_family,
        make_model,
        make_family_member,
        make_file,
        make_print_job,
    ):
        family, model = make_family(), make_model("Benchy")
        member = make_family_member(
            family, model, canonical=True, transformation_note="Original hull"
        )
        mesh = make_file(
            model,
            file_type=FileType.STL,
            metadata={
                "bbox_x_mm": 60,
                "bbox_y_mm": 30,
                "bbox_z_mm": 48,
                "triangle_count": 225706,
            },
        )
        revision = make_file(
            model,
            file_type=FileType.GCODE,
            recommended=True,
            status=FileRevisionStatus.KNOWN_GOOD,
        )
        make_file(model, file_type=FileType.GCODE, status=FileRevisionStatus.NEEDS_TEST)
        make_print_job(revision, state=PrintJobState.COMPLETED)

        response = client.get(
            f"/api/v1/families/{family.id}/members", headers=auth_headers
        )

        assert response.status_code == 200, response.text
        item = response.json()["items"][0]
        assert item["id"] == member.id
        assert item["model"]["id"] == model.id
        assert item["transformation_note"] == "Original hull"
        assert item["formats"] == ["gcode", "stl"]
        assert item["source_file_count"] == 1
        assert item["gcode_revision_count"] == 2
        assert item["known_good_count"] == 1
        assert item["latest_print_outcome"] == "completed"
        assert item["preview_file"]["id"] == mesh.id
        assert item["preview_file"]["metadata"]["triangle_count"] == 225706
        assert item["units"] == "unknown"

    @pytest.mark.parametrize(
        "query,names",
        [
            pytest.param("role=canonical", ["Original"], id="canonical"),
            pytest.param("role=rescaled", ["Scaled"], id="role"),
            pytest.param("file_type=3mf", ["Scaled"], id="format"),
            pytest.param("known_good=true", ["Original"], id="known-good"),
            pytest.param("known_good=false", ["Scaled", "Empty"], id="not-known-good"),
            pytest.param("has_revisions=true", ["Original", "Scaled"], id="revisions"),
            pytest.param("has_revisions=false", ["Empty"], id="no-revisions"),
            pytest.param("source=external", ["Scaled"], id="external"),
            pytest.param("source=vault", ["Original"], id="vault"),
            pytest.param("q=Scaled", ["Scaled"], id="search"),
        ],
    )
    def test_filters_family_member_grid(
        self,
        client,
        auth_headers,
        make_family,
        make_model,
        make_family_member,
        make_file,
        query,
        names,
    ):
        family = make_family()
        original, scaled, empty = (
            make_model("Original"),
            make_model("Scaled"),
            make_model("Empty"),
        )
        make_family_member(family, original, canonical=True, sort_order=0)
        make_family_member(family, scaled, role="rescaled", sort_order=1)
        make_family_member(family, empty, sort_order=2)
        make_file(original, file_type=FileType.STL)
        make_file(
            original, file_type=FileType.GCODE, status=FileRevisionStatus.KNOWN_GOOD
        )
        make_file(scaled, file_type=FileType.THREE_MF, external=True)
        make_file(scaled, file_type=FileType.GCODE, external=True)

        response = client.get(
            f"/api/v1/families/{family.id}/members?{query}", headers=auth_headers
        )

        assert response.status_code == 200, response.text
        assert [item["model"]["name"] for item in response.json()["items"]] == names
        assert response.json()["total"] == len(names)

    @pytest.mark.parametrize(
        "sort,names",
        [
            pytest.param(
                "scale-asc",
                ["Small", "Middle A", "Middle B", "Unknown"],
                id="scale-asc",
            ),
            pytest.param(
                "scale-desc",
                ["Middle B", "Middle A", "Small", "Unknown"],
                id="scale-desc",
            ),
            pytest.param(
                "date-asc", ["Small", "Unknown", "Middle A", "Middle B"], id="date-asc"
            ),
            pytest.param(
                "date-desc",
                ["Middle B", "Middle A", "Unknown", "Small"],
                id="date-desc",
            ),
            pytest.param(
                "success-desc",
                ["Middle B", "Middle A", "Unknown", "Small"],
                id="success-null-ties",
            ),
        ],
    )
    def test_sorts_family_members_deterministically(
        self,
        client,
        auth_headers,
        make_family,
        make_model,
        make_family_member,
        sort,
        names,
    ):
        family = make_family()
        first = datetime(2026, 1, 1, tzinfo=timezone.utc)
        second = datetime(2026, 2, 1, tzinfo=timezone.utc)
        make_family_member(
            family, make_model("Small"), scale_factor=0.5, created_at=first
        )
        make_family_member(
            family, make_model("Unknown"), scale_factor=None, created_at=first
        )
        make_family_member(
            family, make_model("Middle A"), scale_factor=1, created_at=second
        )
        make_family_member(
            family, make_model("Middle B"), scale_factor=1, created_at=second
        )
        initial = client.get(
            f"/api/v1/families/{family.id}/members",
            params={"sort": sort, "limit": 2},
            headers=auth_headers,
        )

        following = client.get(
            f"/api/v1/families/{family.id}/members",
            params={"sort": sort, "limit": 2, "cursor": initial.json()["next_cursor"]},
            headers=auth_headers,
        )

        assert initial.status_code == 200, initial.text
        assert following.status_code == 200, following.text
        assert [
            item["model"]["name"]
            for item in initial.json()["items"] + following.json()["items"]
        ] == names
        assert following.json()["next_cursor"] is None

    def test_hides_invisible_member_metadata(
        self,
        client,
        make_user,
        headers_for,
        make_collection,
        grant_role,
        make_family,
        make_model,
        make_family_member,
        make_file,
    ):
        user = make_user()
        shared, hidden = make_collection("Shared"), make_collection("Private")
        grant_role(user, shared, CollectionRole.VIEW)
        family = make_family()
        model = make_model("Visible", collection=shared)
        secret = make_model("Secret prototype", collection=hidden)
        make_family_member(family, model)
        make_family_member(
            family, secret, canonical=True, transformation_note="Private dimensions"
        )
        make_family_member(
            family, make_model("Trashed", collection=shared, trashed=True)
        )
        make_file(secret, filename="classified.stl")

        response = client.get(
            f"/api/v1/families/{family.id}/members", headers=headers_for(user)
        )

        assert response.status_code == 200, response.text
        assert response.json()["total"] == 1
        assert [item["model_id"] for item in response.json()["items"]] == [model.id]
        assert "Secret prototype" not in response.text
        assert "Private dimensions" not in response.text
        assert "classified" not in response.text
