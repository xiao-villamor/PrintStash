"""Mixed order, revisions and atomic edit claims hold against real PostgreSQL."""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import text
from sqlmodel import Session, create_engine

from alembic import command
from app.core.errors import OperationError
from app.db.migrate import _alembic_config, run_migrations
from app.db.models import CollectionRole, Model, User
from app.db.url import normalize_database_url
from app.modules.library.edit_preconditions import EditPrecondition, claim
from app.modules.library.model_views.browse import page_items
from app.schemas.library_browse import BrowseQuery
from tests.containers import fresh_postgres_database
from tests.factories import build_model, build_multipart_model, build_user


@pytest.fixture
def pg_library() -> Iterator:
    url = fresh_postgres_database("library_browse")
    run_migrations(url)
    engine = create_engine(normalize_database_url(url))
    try:
        yield engine
    finally:
        engine.dispose()


class TestLibraryBrowse:
    def test_preserves_unicode_name_order(self, pg_library):
        with Session(pg_library) as session:
            user = build_user(session, superuser=True)
            build_model(session, "Éclair")
            build_model(session, "éclair")
            build_multipart_model(session, "Zebra")

            page = page_items(session, user, BrowseQuery(sort="name-asc"))

            assert [
                row.model.name if row.kind == "model" else row.multipart.name
                for row in page.items
            ] == ["Zebra", "Éclair", "éclair"]

    def test_requires_refresh_after_committed_write(self, pg_library):
        with Session(pg_library) as session:
            user = build_user(session, superuser=True)
            model = build_model(session, "First")
            build_model(session, "Second")
            page = page_items(session, user, BrowseQuery(limit=1))
            session.execute(
                text("UPDATE models SET name='Changed' WHERE id=:id"), {"id": model.id}
            )
            session.commit()

            with pytest.raises(OperationError, match="browse_refresh_required"):
                page_items(session, user, BrowseQuery(limit=1, cursor=page.next_cursor))

    @pytest.mark.parametrize(
        "kind, entity, factory",
        [
            ("model", "Model", "build_model"),
            ("multipart", "MultipartModel", "build_multipart_model"),
            ("document", "Document", "build_document"),
        ],
        ids=["model", "multipart", "document"],
    )
    def test_permits_only_one_atomic_editor(self, pg_library, kind, entity, factory):
        from app.db import models
        from tests import factories

        entity = getattr(models, entity)
        factory = getattr(factories, factory)
        with Session(pg_library) as session:
            user = build_user(session, superuser=True)
            model = factory(session)
            user_id, model_id = user.id, model.id
        start = Barrier(2)

        def editor(name):
            with Session(pg_library) as session:
                user = session.get(User, user_id)
                row = session.get(entity, model_id)
                start.wait(timeout=10)
                try:
                    claim(
                        session,
                        user,
                        row,
                        EditPrecondition(
                            if_match=f'"{kind}-{model_id}-v1"',
                            contract="conditional-v1",
                        ),
                    )
                    row.name = name
                    session.add(row)
                    session.commit()
                    return "saved"
                except OperationError as exc:
                    session.rollback()
                    return exc.detail

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(editor, "First")
            second = executor.submit(editor, "Second")
            results = [first.result(timeout=20), second.result(timeout=20)]

        assert sorted(results) == ["edit_conflict", "saved"]

    def test_rejects_revoked_actor(self, pg_library):
        with Session(pg_library) as setup:
            user = build_user(setup, superuser=True)
            model = build_model(setup)
            user_id, model_id = user.id, model.id
        with Session(pg_library) as session:
            actor = session.get(User, user_id)
            model = session.get(Model, model_id)
            with Session(pg_library) as other:
                revoked = other.get(User, user_id)
                revoked.is_active = False
                other.add(revoked)
                other.commit()

            with pytest.raises(OperationError, match="collection_permission_denied"):
                claim(
                    session,
                    actor,
                    model,
                    EditPrecondition(if_match=f'"model-{model_id}-v1"'),
                )
            session.rollback()

        with Session(pg_library) as session:
            assert session.get(Model, model_id).edit_version == 1

    def test_upgrades_existing_rows(self, pg_library):
        with Session(pg_library) as session:
            build_user(session, superuser=True)
            model = build_model(session, "Preserved")
            model_id = model.id
        config = _alembic_config(pg_library.url.render_as_string(hide_password=False))
        command.downgrade(config, "8298455ff341")

        command.upgrade(config, "head")

        with Session(pg_library) as session:
            model = session.get(Model, model_id)
            assert model.name == "Preserved"
            assert model.edit_version == 1
            before = session.execute(
                text("SELECT revision FROM library_revision WHERE id=1")
            ).scalar_one()
            session.execute(
                text("UPDATE models SET name='After upgrade' WHERE id=:id"),
                {"id": model_id},
            )
            session.commit()
            assert (
                session.execute(
                    text("SELECT revision FROM library_revision WHERE id=1")
                ).scalar_one()
                > before
            )
            session.refresh(model)
            assert model.edit_version > 1

    def test_detects_authorization_write(self, pg_library):
        from app.modules.library.model_views.browse import revision
        from tests.factories import build_collection, grant_collection_role

        with Session(pg_library) as session:
            folder = build_collection(session, "Inherited Root")
            user = build_user(session)
            before = revision(session)
            grant_collection_role(session, user, folder, CollectionRole.VIEW)

            checked = revision(session)

            assert checked.authorization_revision != before.authorization_revision
            assert checked.browse_revision != before.browse_revision

    def test_keeps_revision_in_writer_transaction(self, pg_library):
        from app.modules.library.model_views.browse import revision

        with Session(pg_library) as session:
            build_user(session, superuser=True)
            model = build_model(session)
            before = revision(session)
            session.execute(
                text("UPDATE models SET name='Rolled back' WHERE id=:id"),
                {"id": model.id},
            )
            session.rollback()

            checked = revision(session)

            assert checked == before


class TestBrowseThumbnails:
    def test_projects_only_visible_models_in_requested_order(self, pg_library):
        from app.modules.library.model_views.browse import revision, thumbnail_items
        from tests.factories import build_collection, grant_collection_role

        with Session(pg_library) as session:
            folder = build_collection(session, "Allowed")
            private = build_collection(session, "Private")
            user = build_user(session)
            first = build_model(session, collection=folder)
            second = build_model(session, collection=folder, thumbnail_path="123.png")
            hidden = build_model(session, collection=private)
            grant_collection_role(session, user, folder, CollectionRole.VIEW)
            result = thumbnail_items(
                session, user, [second.id, hidden.id, first.id, second.id]
            )
            assert [(item.model_id, item.thumbnail_url) for item in result.items] == [
                (second.id, "/api/v1/files/123/thumbnail"),
                (first.id, None),
            ]
            assert (
                result.authorization_revision
                == revision(session).authorization_revision
            )

    def test_rejects_permission_revocation_during_projection(self, pg_library):
        from sqlalchemy import event

        from app.modules.library.model_views.browse import thumbnail_items
        from tests.factories import build_collection, grant_collection_role

        with Session(pg_library) as reader:
            folder = build_collection(reader, "Allowed")
            user = build_user(reader)
            model = build_model(reader, collection=folder)
            grant_collection_role(reader, user, folder, CollectionRole.VIEW)
            model_id, user_id = model.id, user.id
            race = {"committed": False}

            def revoke(_conn, _cursor, statement, _parameters, _context, _many):
                if "FROM models" in statement and not race["committed"]:
                    race["committed"] = True
                    with Session(pg_library) as writer:
                        writer.execute(
                            text(
                                "DELETE FROM collection_permissions WHERE user_id=:id"
                            ),
                            {"id": user_id},
                        )
                        writer.commit()

            event.listen(reader.connection(), "after_cursor_execute", revoke)
            with pytest.raises(OperationError, match="browse_refresh_required"):
                thumbnail_items(reader, user, [model_id])
            assert race["committed"] is True
