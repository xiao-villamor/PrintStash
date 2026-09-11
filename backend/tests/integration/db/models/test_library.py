"""Family constraints protect reserved membership and detached history.

Deleting a grouping may release its references but never deletes a Model.
Live uniqueness also applies while the referenced Model is in the trash.
"""

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.db.models import Model, ModelFamilyMember


class TestModelFamilyMember:
    def test_rejects_detached_membership_without_reason(
        self, db_session, make_family, make_model, make_family_member
    ):
        member = make_family_member(make_family(), make_model(), detached="removed")

        member.detach_reason = None
        db_session.add(member)
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

        assert member.detach_reason == "removed"

    def test_allows_regrouping_after_family_trash(
        self, db_session, make_family, make_model, make_family_member
    ):
        model = make_model()
        old = make_family_member(make_family(trashed=True), model)

        current = make_family_member(make_family(), model)

        assert old.detached_at is not None
        assert current.detached_at is None
        assert current.model_id == old.model_id == model.id

    @pytest.mark.parametrize("trashed", [False, True], ids=["live", "trashed"])
    def test_rejects_second_reserved_membership(
        self, db_session, make_family, make_model, make_family_member, trashed
    ):
        model = make_model(trashed=trashed)
        first, second = make_family(), make_family()
        member = make_family_member(first, model)
        member_id = member.id

        with pytest.raises(IntegrityError):
            make_family_member(second, model)
        db_session.rollback()

        assert db_session.exec(select(ModelFamilyMember.id)).all() == [member_id]

    def test_rejects_second_active_canonical(
        self, db_session, make_family, make_model, make_family_member
    ):
        family = make_family()
        chosen = make_family_member(family, make_model(), canonical=True)
        other = make_model()

        with pytest.raises(IntegrityError):
            make_family_member(family, other, role="canonical")
        db_session.rollback()

        assert family.canonical_member_id == chosen.id
        assert db_session.exec(select(ModelFamilyMember.id)).all() == [chosen.id]

    @pytest.mark.parametrize(
        "scale", [0, -1, float("inf")], ids=["zero", "negative", "infinity"]
    )
    def test_rejects_invalid_scale(
        self, db_session, make_family, make_model, make_family_member, scale
    ):
        family, model = make_family(), make_model()

        with pytest.raises(IntegrityError):
            make_family_member(family, model, scale_factor=scale)
        db_session.rollback()

        assert db_session.exec(select(ModelFamilyMember)).all() == []

    def test_refuses_implicit_model_cascade(
        self, db_session, make_family, make_model, make_family_member
    ):
        model = make_model()
        member = make_family_member(make_family(), model)

        db_session.delete(model)
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

        assert member.model_id == model.id
        assert db_session.get(Model, model.id) is not None

    def test_preserves_models_when_family_is_purged(
        self, db_session, make_family, make_model, make_family_member
    ):
        family, model = make_family(), make_model()
        member = make_family_member(family, model, canonical=True)
        member_id, model_id = member.id, model.id

        db_session.delete(family)
        db_session.commit()
        db_session.expire_all()

        assert db_session.get(ModelFamilyMember, member_id) is None
        assert db_session.get(Model, model_id) is not None

    def test_preserves_purged_member_history(self, make_family, make_family_member):
        family = make_family(trashed=True)

        member = make_family_member(family, None, detached="model_purged")

        assert member.model_id is None
        assert member.detach_reason == "model_purged"
        assert member.detached_at is not None

    def test_rejects_active_missing_model(
        self, db_session, make_family, make_family_member
    ):
        family = make_family()

        with pytest.raises(IntegrityError):
            make_family_member(family, None)
        db_session.rollback()

        assert db_session.exec(select(ModelFamilyMember)).all() == []
