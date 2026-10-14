"""The authorization revision advances when access can change, and only then.

An open library view discards everything private it holds when this revision
moves, so a write that changes nobody's access must leave it alone: an import
creating its folders used to reset every open library once per folder.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlmodel import Session

from app.db import library_authorization_v2
from app.db.models import CollectionRole
from app.modules.library.model_views.browse import revision
from tests.factories import identity, library


def _access(session: Session) -> str:
    session.commit()
    return revision(session).authorization_revision


class TestCollections:
    def test_creating_a_collection_keeps_access(self, db_session: Session) -> None:
        parent = library.build_collection(db_session, "Root")
        before = _access(db_session)

        library.build_collection(db_session, "Child", parent=parent)

        assert _access(db_session) == before

    def test_describing_a_collection_keeps_access(self, db_session: Session) -> None:
        folder = library.build_collection(db_session, "Prints")
        before = _access(db_session)

        db_session.execute(
            text("UPDATE collections SET name='Renamed', readme='notes' WHERE id=:id"),
            {"id": folder.id},
        )

        assert _access(db_session) == before

    @pytest.mark.parametrize(
        "statement",
        [
            "UPDATE collections SET parent_id=:other WHERE id=:id",
            "UPDATE collections SET path='Elsewhere' WHERE id=:id",
            "UPDATE collections SET deleted_at=:now WHERE id=:id",
            "DELETE FROM collections WHERE id=:id",
        ],
        ids=["moved", "re-pathed", "trashed", "deleted"],
    )
    def test_a_change_to_the_tree_advances_access(
        self, db_session: Session, statement: str
    ) -> None:
        other = library.build_collection(db_session, "Other")
        folder = library.build_collection(db_session, "Moving")
        before = _access(db_session)

        db_session.execute(
            text(statement),
            {"id": folder.id, "other": other.id, "now": datetime.now(timezone.utc)},
        )

        assert _access(db_session) != before

    def test_sharing_a_collection_still_advances_access(
        self, db_session: Session
    ) -> None:
        folder = library.build_collection(db_session, "Shared")
        user = identity.build_user(db_session)
        before = _access(db_session)

        identity.grant_collection_role(db_session, user, folder, CollectionRole.VIEW)

        assert _access(db_session) != before


class TestUsers:
    def test_creating_a_user_keeps_access(self, db_session: Session) -> None:
        before = _access(db_session)

        identity.build_user(db_session)

        assert _access(db_session) == before

    def test_editing_a_users_profile_keeps_access(self, db_session: Session) -> None:
        user = identity.build_user(db_session)
        before = _access(db_session)

        db_session.execute(
            text("UPDATE users SET email='new@example.com' WHERE id=:id"),
            {"id": user.id},
        )

        assert _access(db_session) == before

    @pytest.mark.parametrize(
        "statement",
        [
            "UPDATE users SET is_superuser=1 WHERE id=:id",
            "UPDATE users SET is_active=0 WHERE id=:id",
            "UPDATE users SET auth_version=auth_version+1 WHERE id=:id",
            "UPDATE users SET deleted_at=:now WHERE id=:id",
            "DELETE FROM users WHERE id=:id",
        ],
        ids=["promoted", "disabled", "sessions-revoked", "trashed", "deleted"],
    )
    def test_a_change_to_a_users_access_advances_it(
        self, db_session: Session, statement: str
    ) -> None:
        user = identity.build_user(db_session)
        before = _access(db_session)

        db_session.execute(
            text(statement), {"id": user.id, "now": datetime.now(timezone.utc)}
        )

        assert _access(db_session) != before


class TestInstallation:
    def test_a_fresh_database_carries_v2_in_place_of_v1(
        self, db_session: Session
    ) -> None:
        names = set(
            db_session.execute(
                text(
                    "SELECT name FROM sqlite_master WHERE type='trigger' "
                    "AND (name LIKE 'ps_auth_users%' OR name LIKE 'ps_auth_collections%')"
                )
            ).scalars()
        )

        assert names == {
            "ps_auth_collections_update_v2",
            "ps_auth_collections_delete_v2",
            "ps_auth_users_update_v2",
            "ps_auth_users_delete_v2",
        }

    def test_uninstalling_restores_v1(self, db_session: Session) -> None:
        connection = db_session.connection()
        library_authorization_v2.uninstall(connection)
        try:
            restored = library_authorization_v2.installed(connection)
            before = _access(db_session)
            library.build_collection(db_session, "Counted again")
            advanced = _access(db_session) != before
        finally:
            library_authorization_v2.install(db_session.connection())
            db_session.commit()

        assert (restored, advanced) == (False, True)
