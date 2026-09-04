"""The rules `saved_views` enforces that the router cannot show.

Ownership scoping lives here, in every query, and the router only ever sees its result:
`get_for_user`, `update` and `delete` all answer "not yours" the same way as "does not
exist", which is what lets the API return 404 without leaking that a view belongs to
someone else. The unique constraint is per user, and a violated one has to leave the
session usable — the service rolls back and raises `SavedViewConflict` rather than
letting an `IntegrityError` poison the request's transaction.
"""

from __future__ import annotations

import pytest
from sqlmodel import Session, select

from app.db.models import SavedView
from app.schemas.saved_views import SavedViewCreate, SavedViewFilters, SavedViewUpdate
from app.services import saved_views
from tests.factories import build_user


def _payload(name: str, **filters: object) -> SavedViewCreate:
    return SavedViewCreate(
        name=name,
        filters=SavedViewFilters(**filters) if filters else SavedViewFilters(),
    )


class TestListForUser:
    def test_orders_views_by_name(self, db_session: Session) -> None:
        user = build_user(db_session, "sorter")
        for name in ("Zebra", "Apple", "Mango"):
            saved_views.create(db_session, user.id, _payload(name))

        listed = saved_views.list_for_user(db_session, user.id)

        assert [view.name for view in listed] == ["Apple", "Mango", "Zebra"]


class TestGetForUser:
    def test_returns_none_for_another_users_view(self, db_session: Session) -> None:
        owner = build_user(db_session, "get-owner")
        other = build_user(db_session, "get-other")
        view = saved_views.create(db_session, owner.id, _payload("Private"))

        assert saved_views.get_for_user(db_session, other.id, view.id) is None


class TestCreate:
    def test_trims_surrounding_whitespace_from_the_name(
        self, db_session: Session
    ) -> None:
        user = build_user(db_session, "trimmer")

        view = saved_views.create(db_session, user.id, _payload("  Favorites  "))

        assert view.name == "Favorites"

    def test_raises_conflict_on_a_duplicate_name(self, db_session: Session) -> None:
        user = build_user(db_session, "conflicter")
        saved_views.create(db_session, user.id, _payload("Favorites"))

        with pytest.raises(saved_views.SavedViewConflict):
            saved_views.create(db_session, user.id, _payload("Favorites"))

        rows = db_session.exec(
            select(SavedView).where(SavedView.user_id == user.id)
        ).all()
        assert len(rows) == 1

    def test_leaves_the_session_usable_after_a_conflict(
        self, db_session: Session
    ) -> None:
        user = build_user(db_session, "recoverer")
        saved_views.create(db_session, user.id, _payload("Favorites"))
        with pytest.raises(saved_views.SavedViewConflict):
            saved_views.create(db_session, user.id, _payload("Favorites"))

        recovered = saved_views.create(db_session, user.id, _payload("Something else"))

        assert recovered.id, "the rollback left the session able to commit again"


class TestUpdate:
    def test_trims_a_renamed_name(self, db_session: Session) -> None:
        user = build_user(db_session, "rename-trimmer")
        view = saved_views.create(db_session, user.id, _payload("Before"))

        updated = saved_views.update(
            db_session, user.id, view.id, SavedViewUpdate(name="  Renamed ")
        )

        assert updated is not None
        assert updated.name == "Renamed"


class TestDelete:
    def test_reports_whether_a_delete_removed_anything(
        self, db_session: Session
    ) -> None:
        user = build_user(db_session, "reporter")
        view = saved_views.create(db_session, user.id, _payload("Temporary"))

        assert saved_views.delete(db_session, user.id, view.id) is True
        assert saved_views.delete(db_session, user.id, view.id) is False
