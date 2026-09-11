"""Family trash releases reservations; restore reacquires them atomically.

Model trash instead reserves membership and the human canonical selection.
These transitions never trash or replace a sibling's content.
"""

from sqlmodel import select

from app.db.models import ModelFamilyMember
from app.modules.library import trash


class TestTrashFamily:
    def test_trashes_family_without_trashing_models(
        self,
        client,
        auth_headers,
        db_session,
        make_family,
        make_model,
        make_family_member,
    ):
        family = make_family()
        model = make_model()
        member = make_family_member(family, model, canonical=True)

        response = client.delete(
            f"/api/v1/families/{family.id}?version={family.version}",
            headers=auth_headers,
        )
        db_session.expire_all()

        assert response.status_code == 204, response.text
        assert family.deleted_at is not None
        assert member.detach_reason == "family_trashed"
        assert member.detached_at is not None
        assert model.deleted_at is None
        assert family.canonical_member_id == member.id

    def test_replays_family_trash(
        self,
        client,
        auth_headers,
        db_session,
        make_family,
        make_model,
        make_family_member,
    ):
        family = make_family(trashed=True)
        member = make_family_member(family, make_model(), canonical=True)
        before = family.deleted_at, member.detached_at, family.version

        response = client.delete(
            f"/api/v1/families/{family.id}?version=1", headers=auth_headers
        )
        db_session.expire_all()

        assert response.status_code == 204, response.text
        assert (family.deleted_at, member.detached_at, family.version) == before


class TestRestoreFamily:
    def test_restores_family_atomically(
        self,
        client,
        auth_headers,
        db_session,
        make_family,
        make_model,
        make_family_member,
    ):
        family = make_family(trashed=True)
        first = make_family_member(family, make_model(), canonical=True)
        second = make_family_member(family, make_model())

        response = client.post(
            f"/api/v1/families/{family.id}/restore",
            headers=auth_headers,
            json={"version": family.version},
        )
        db_session.expire_all()

        assert response.status_code == 200, response.text
        assert response.json()["family"]["member_count"] == 2
        assert response.json()["omitted_member_ids"] == []
        assert family.deleted_at is None
        assert first.detached_at is None
        assert second.detached_at is None
        assert family.canonical_member_id == first.id

    def test_refuses_conflicting_family_restore(
        self,
        client,
        auth_headers,
        db_session,
        make_family,
        make_model,
        make_family_member,
    ):
        family = make_family(trashed=True)
        available = make_family_member(family, make_model(), canonical=True)
        model = make_model()
        conflicting = make_family_member(family, model)
        current = make_family_member(make_family(), model)

        response = client.post(
            f"/api/v1/families/{family.id}/restore",
            headers=auth_headers,
            json={"version": family.version},
        )
        db_session.expire_all()

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "family_restore_conflict"
        assert family.deleted_at is not None
        assert available.detached_at is not None
        assert conflicting.detached_at is not None
        assert current.detached_at is None

    def test_restores_after_member_purge(
        self,
        client,
        auth_headers,
        db_session,
        make_family,
        make_model,
        make_family_member,
    ):
        family = make_family(trashed=True)
        surviving = make_family_member(family, make_model())
        model = make_model(trashed=True)
        lost = make_family_member(family, model, canonical=True)
        lost_id = lost.id
        trash.hard_delete_model(db_session, model)
        db_session.commit()

        response = client.post(
            f"/api/v1/families/{family.id}/restore",
            headers=auth_headers,
            json={"version": family.version},
        )
        db_session.expire_all()

        assert response.status_code == 200, response.text
        assert response.json()["omitted_member_ids"] == [lost_id]
        assert response.json()["family"]["canonical_model_id"] is None
        assert surviving.detached_at is None


class TestModelFamilyLifecycle:
    def test_reserves_membership_during_model_trash(
        self,
        client,
        auth_headers,
        db_session,
        make_family,
        make_model,
        make_family_member,
    ):
        family = make_family()
        model = make_model()
        member = make_family_member(family, model, canonical=True)

        trash.soft_delete_model(db_session, model)
        db_session.commit()
        response = client.get(f"/api/v1/families/{family.id}", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.json()["member_count"] == 0
        assert response.json()["canonical_model_id"] is None
        assert member.detached_at is None
        assert family.canonical_member_id == member.id

    def test_restores_reserved_canonical(
        self,
        client,
        auth_headers,
        db_session,
        make_family,
        make_model,
        make_family_member,
    ):
        family = make_family()
        model = make_model(trashed=True)
        member = make_family_member(family, model, canonical=True)

        trash.restore_model(db_session, model)
        db_session.commit()
        response = client.get(f"/api/v1/families/{family.id}", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.json()["canonical_model_id"] == model.id
        assert response.json()["canonical_member_id"] == member.id

    def test_preserves_replacement_canonical(
        self,
        client,
        auth_headers,
        db_session,
        make_family,
        make_model,
        make_family_member,
    ):
        family = make_family()
        model = make_model(trashed=True)
        former = make_family_member(family, model, canonical=True)
        replacement = make_family_member(family, make_model())

        changed = client.post(
            f"/api/v1/families/{family.id}/canonical",
            headers=auth_headers,
            json={
                "member_id": replacement.id,
                "previous_role": "identical",
                "version": family.version,
            },
        )
        trash.restore_model(db_session, model)
        db_session.commit()
        response = client.get(f"/api/v1/families/{family.id}", headers=auth_headers)

        assert changed.status_code == 200, changed.text
        assert response.json()["canonical_model_id"] == replacement.model_id
        assert former.role == "identical"

    def test_purges_member_references_explicitly(
        self, db_session, make_family, make_model, make_family_member
    ):
        family = make_family()
        model = make_model(trashed=True)
        member = make_family_member(family, model, canonical=True)

        trash.hard_delete_model(db_session, model)
        db_session.commit()
        db_session.expire_all()

        assert family.canonical_member_id is None
        assert db_session.exec(select(ModelFamilyMember.model_id)).all() == [None]
        assert member.detach_reason == "model_purged"
