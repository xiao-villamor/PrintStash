"""Each Family mutation records its human actor and relationship change atomically."""

import json

import pytest
from sqlmodel import col, select

from app.db.models import AuditLog


class TestFamilyAudit:
    @pytest.mark.parametrize(
        "operation",
        [
            "create",
            "edit",
            "add",
            "detach",
            "role",
            "canonical",
            "move",
            "trash",
            "restore",
        ],
    )
    def test_audit_logs_family_mutation(
        self,
        client,
        db_session,
        make_user,
        headers_for,
        make_family,
        make_model,
        make_family_member,
        operation,
    ):
        actor = make_user(superuser=True)
        family, destination, archived = (
            make_family(),
            make_family(),
            make_family(trashed=True),
        )
        original, sibling, available = make_model(), make_model(), make_model()
        canonical = make_family_member(family, original, canonical=True)
        member = make_family_member(family, sibling)
        historic = make_family_member(archived, make_model(), canonical=True)
        url = f"/api/v1/families/{family.id}"
        requests = {
            "create": (
                "POST",
                "/api/v1/families",
                {
                    "name": "New family",
                    "canonical_model_id": available.id,
                    "members": [{"model_id": available.id}],
                },
                {},
                {"family.create"},
                {"model_ids": [available.id], "canonical_model_id": available.id},
            ),
            "edit": (
                "PATCH",
                url,
                {"version": 1, "name": "Renamed"},
                {},
                {"family.update"},
                {"after": {"name": "Renamed"}},
            ),
            "add": (
                "POST",
                f"{url}/members",
                {"version": 1, "model_id": available.id},
                {},
                {"family.add"},
                {"model_id": available.id},
            ),
            "detach": (
                "DELETE",
                f"{url}/members/{member.id}",
                None,
                {"version": 1},
                {"family.detach"},
                {"member_id": member.id, "model_id": sibling.id},
            ),
            "role": (
                "PATCH",
                f"{url}/members/{member.id}",
                {"version": 1, "role": "repaired"},
                {},
                {"family.member_update"},
                {
                    "member_id": member.id,
                    "before": {"role": "identical"},
                    "after": {"role": "repaired"},
                },
            ),
            "canonical": (
                "POST",
                f"{url}/canonical",
                {
                    "version": 1,
                    "member_id": member.id,
                    "previous_role": "print_variant",
                },
                {},
                {"family.canonical"},
                {
                    "member_id": member.id,
                    "previous_member_id": canonical.id,
                    "previous_role": "print_variant",
                },
            ),
            "move": (
                "POST",
                f"/api/v1/families/{destination.id}/move-member",
                {
                    "source_family_id": family.id,
                    "source_version": 1,
                    "destination_version": 1,
                    "model_id": sibling.id,
                },
                {},
                {"family.move_out", "family.move_in"},
                {
                    "model_id": sibling.id,
                    "source_family_id": family.id,
                    "destination_family_id": destination.id,
                },
            ),
            "trash": (
                "DELETE",
                url,
                None,
                {"version": 1},
                {"family.trash"},
                {"member_ids": [canonical.id, member.id]},
            ),
            "restore": (
                "POST",
                f"/api/v1/families/{archived.id}/restore",
                {"version": 1},
                {},
                {"family.restore"},
                {"member_ids": [historic.id], "omitted_member_ids": []},
            ),
        }
        method, path, payload, params, actions, expected = requests[operation]

        response = client.request(
            method, path, json=payload, params=params, headers=headers_for(actor)
        )

        assert response.status_code in (200, 201, 204), response.text
        records = db_session.exec(
            select(AuditLog).where(col(AuditLog.action).like("family.%"))
        ).all()
        assert {row.action for row in records} == actions
        assert {row.actor_id for row in records} == {actor.id}
        assert {row.resource_type for row in records} == {"model_families"}
        assert [
            {key: json.loads(row.diff_json)[key] for key in expected} for row in records
        ] == [expected] * len(actions)
