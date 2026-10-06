"""A catalog commit during page construction is detected before publishing mixed cards."""

import pytest
from sqlalchemy import event, text
from sqlmodel import Session, SQLModel, create_engine

from app.core.errors import OperationError
from app.db.models import User
from app.db.session import _set_sqlite_pragmas
from app.modules.library.model_views.browse import page_items
from app.schemas.library_browse import BrowseQuery
from tests.factories import build_model, build_user


@pytest.fixture
def browse_database(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'browse.sqlite'}",
        connect_args={"check_same_thread": False},
    )
    event.listen(engine, "connect", _set_sqlite_pragmas)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        user = build_user(session, superuser=True)
        model = build_model(session, "Original")
        yield engine, user.id, model.id
    engine.dispose()


class TestPageItems:
    def test_rejects_read_revision_race(self, browse_database):
        engine, user_id, model_id = browse_database
        race = {"committed": False}

        def committed_writer(_conn, _cursor, statement, _parameters, _context, _many):
            if (
                "browse_entries" in statement
                and "LIMIT" in statement
                and not race["committed"]
            ):
                race["committed"] = True
                with Session(engine) as writer:
                    writer.execute(
                        text("UPDATE models SET name='During read' WHERE id=:id"),
                        {"id": model_id},
                    )
                    writer.commit()

        with Session(engine) as reader:
            user = reader.get(User, user_id)
            event.listen(reader.connection(), "after_cursor_execute", committed_writer)

            with pytest.raises(OperationError, match="browse_refresh_required"):
                page_items(reader, user, BrowseQuery())

        assert race["committed"] is True


class TestThumbnailItems:
    @pytest.mark.parametrize("count", [1, 24])
    def test_uses_constant_projection_queries(self, browse_database, count):
        from app.modules.library.model_views.browse import thumbnail_items

        engine, user_id, first_id = browse_database
        with Session(engine) as reader:
            ids = [first_id] + [build_model(reader).id for _ in range(count - 1)]
            user = reader.get(User, user_id)
            statements = []

            def record(_conn, _cursor, statement, _parameters, _context, _many):
                statements.append(statement)

            event.listen(reader.connection(), "after_cursor_execute", record)
            result = thumbnail_items(reader, user, ids)
        assert len(result.items) == count
        assert len(statements) == 4
        assert all(
            statement.lstrip().upper().startswith("SELECT") for statement in statements
        )
        assert not any(
            "model_tags" in statement or "metadata" in statement
            for statement in statements
        )

    def test_rejects_permission_race(self, browse_database):
        from app.modules.library.model_views.browse import thumbnail_items

        engine, user_id, model_id = browse_database
        race = {"committed": False}

        def committed_writer(_conn, _cursor, statement, _parameters, _context, _many):
            if "FROM models" in statement and not race["committed"]:
                race["committed"] = True
                with Session(engine) as writer:
                    writer.execute(
                        text("UPDATE users SET is_superuser=0 WHERE id=:id"),
                        {"id": user_id},
                    )
                    writer.commit()

        with Session(engine) as reader:
            user = reader.get(User, user_id)
            event.listen(reader.connection(), "after_cursor_execute", committed_writer)
            with pytest.raises(OperationError, match="browse_refresh_required"):
                thumbnail_items(reader, user, [model_id])
        assert race["committed"] is True

    @pytest.mark.parametrize(
        "statement",
        [
            "UPDATE users SET is_active=0 WHERE id=:id",
            "UPDATE users SET auth_version=auth_version+1 WHERE id=:id",
            "DELETE FROM users WHERE id=:id",
        ],
    )
    def test_rejects_actor_revoked_after_authentication(
        self, browse_database, statement
    ):
        from app.modules.library.model_views.browse import thumbnail_items

        engine, user_id, model_id = browse_database
        with Session(engine) as reader:
            user = reader.get(User, user_id)
            with Session(engine) as writer:
                writer.execute(text(statement), {"id": user_id})
                writer.commit()
            with pytest.raises(OperationError, match="collection_permission_denied"):
                thumbnail_items(reader, user, [model_id])
