"""Mixed order, revisions and atomic edit claims hold against real PostgreSQL."""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier

import pytest
from sqlalchemy import text
from sqlmodel import Session, create_engine

from alembic import command
from app.core.errors import OperationError
from app.db.migrate import _alembic_config, run_migrations
from app.db.models import CollectionRole, FileType, Model, PrintJobState, User
from app.db.url import normalize_database_url
from app.modules.library import multipart_models
from app.modules.library.edit_preconditions import EditPrecondition, claim
from app.modules.library.model_views.browse import page_items
from app.schemas.library_browse import BrowseQuery
from app.schemas.multipart_models import MultipartChoiceWrite, MultipartPartWrite
from tests.containers import fresh_postgres_database
from tests.factories import (
    build_collection,
    build_file,
    build_model,
    build_multipart_model,
    build_print_job,
    build_user,
    grant_collection_role,
)


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
    def test_excludes_trashed_candidates_before_pagination(self, pg_library):
        with Session(pg_library) as session:
            user = build_user(session, superuser=True)
            discarded = build_collection(session, "Discarded", trashed=True)
            build_model(session, "0 Trashed", trashed=True)
            build_model(session, "0 In discarded folder", collection=discarded)
            build_multipart_model(session, "0 Discarded set", collection=discarded)
            model = build_model(session, "A Live")
            group = build_multipart_model(session, "B Live set")

            first = page_items(session, user, BrowseQuery(sort="name-asc", limit=1))

            assert first.total == 2
            assert len(first.items) == 1
            assert first.items[0].kind == "model"
            assert first.items[0].model.id == model.id
            assert first.next_cursor is not None
            second = page_items(
                session,
                user,
                BrowseQuery(sort="name-asc", limit=1, cursor=first.next_cursor),
            )
            assert second.total == 2
            assert len(second.items) == 1
            assert second.items[0].kind == "multipart"
            assert second.items[0].multipart.id == group.id
            assert second.next_cursor is None

    @pytest.mark.parametrize(
        "sort", ["name-asc", "name-desc"], ids=["ascending", "descending"]
    )
    def test_pages_equal_names_by_kind_then_identity(self, pg_library, sort):
        with Session(pg_library) as session:
            user = build_user(session, superuser=True)
            first = build_model(session, "Same")
            second = build_model(session, "SAME")
            group = build_multipart_model(session, "same")
            expected = [
                ("model", first.id),
                ("model", second.id),
                ("multipart", group.id),
            ]
            actual = []
            cursor = None
            for _ in expected:
                page = page_items(
                    session, user, BrowseQuery(sort=sort, limit=1, cursor=cursor)
                )
                assert len(page.items) == 1
                entry = page.items[0]
                actual.append(
                    (
                        entry.kind,
                        entry.model.id if entry.kind == "model" else entry.multipart.id,
                    )
                )
                cursor = page.next_cursor
            assert actual == expected
            assert cursor is None

    @pytest.mark.parametrize(
        "sort",
        ["success-desc", "printed-desc", "duration-asc", "filament-asc", "cost-asc"],
        ids=["success", "printed", "duration", "filament", "cost"],
    )
    def test_places_missing_metrics_after_measured_models(self, pg_library, sort):
        with Session(pg_library) as session:
            user = build_user(session, superuser=True)
            model = build_model(session, "Measured")
            artifact = build_file(
                session,
                model,
                file_type=FileType.GCODE,
                metadata={"estimated_time_s": 100, "filament_weight_g": 10},
            )
            build_print_job(
                session,
                artifact,
                state=PrintJobState.COMPLETED,
                actual_duration_s=100,
                finished_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                cost=5,
            )
            group = build_multipart_model(session, "No metric")
            first = page_items(session, user, BrowseQuery(sort=sort, limit=1))
            assert first.items[0].kind == "model"
            assert first.items[0].model.id == model.id
            assert first.next_cursor is not None
            second = page_items(
                session, user, BrowseQuery(sort=sort, limit=1, cursor=first.next_cursor)
            )
            assert second.items[0].kind == "multipart"
            assert second.items[0].multipart.id == group.id
            assert second.next_cursor is None

    def test_filters_readable_members_before_pagination(self, pg_library):
        with Session(pg_library) as session:
            admin = build_user(session, superuser=True)
            viewer = build_user(session, superuser=False)
            visible = build_collection(session, "Visible")
            private = build_collection(session, "Private")
            grant_collection_role(session, viewer, visible, CollectionRole.VIEW)
            hidden = build_model(session, "0 Hidden", collection=private)
            permitted = build_model(session, "1 Visible", collection=visible)
            build_file(session, hidden, file_type=FileType.STL)
            build_file(session, permitted, file_type=FileType.STL)
            hidden_match = build_multipart_model(
                session, "0 Hidden member", collection=visible
            )
            valid_match = build_multipart_model(
                session, "2 Visible member", collection=visible
            )
            for group, model in [(hidden_match, hidden), (valid_match, permitted)]:
                multipart_models.replace_parts(
                    session,
                    admin,
                    group,
                    [
                        MultipartPartWrite(
                            name="Body",
                            choices=[MultipartChoiceWrite(model_id=model.id)],
                        )
                    ],
                )
            first = page_items(
                session,
                viewer,
                BrowseQuery(file_type=["stl"], sort="name-asc", limit=1),
            )
            assert first.total == 2
            assert first.items[0].kind == "model"
            assert first.items[0].model.id == permitted.id
            assert first.next_cursor is not None
            second = page_items(
                session,
                viewer,
                BrowseQuery(
                    file_type=["stl"],
                    sort="name-asc",
                    limit=1,
                    cursor=first.next_cursor,
                ),
            )
            assert second.total == 2
            assert second.items[0].kind == "multipart"
            assert second.items[0].multipart.id == valid_match.id
            assert second.next_cursor is None

    @pytest.mark.parametrize(
        "sort, names",
        [
            ("date-asc", ["Old", "Middle", "New"]),
            ("date-desc", ["New", "Middle", "Old"]),
        ],
        ids=["ascending", "descending"],
    )
    def test_pages_mixed_dates_in_global_order(self, pg_library, sort, names):
        with Session(pg_library) as session:
            user = build_user(session, superuser=True)
            build_model(session, "Old", updated_at=datetime(2025, 1, 1))
            build_multipart_model(session, "Middle", updated_at=datetime(2025, 6, 1))
            build_model(session, "New", updated_at=datetime(2026, 1, 1))
            actual = []
            cursor = None
            for _ in names:
                page = page_items(
                    session, user, BrowseQuery(sort=sort, limit=1, cursor=cursor)
                )
                assert len(page.items) == 1
                entry = page.items[0]
                actual.append(
                    entry.model.name if entry.kind == "model" else entry.multipart.name
                )
                cursor = page.next_cursor
            assert actual == names
            assert cursor is None

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
            epoch = model.edit_epoch
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
                            if_match=f'"{kind}-{model_id}-e{epoch}-v1"',
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
            epoch = model.edit_epoch
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
                    EditPrecondition(if_match=f'"model-{model_id}-e{epoch}-v1"'),
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


class TestProvenanceSnapshot:
    def test_reads_versioned_source(self, pg_library):
        from app.modules.library.model_views.detail import provenance_detail
        from tests.factories.provenance import build_cover, build_provenance_source

        with Session(pg_library) as session:
            model = build_model(session)
            source = build_provenance_source(session, model)
            cover = build_cover(session, source)
            session.refresh(model)

            result = provenance_detail(session, model.id)

            assert result.edit_version == model.edit_version
            assert [entry.id for entry in result.sources] == [source.id]
            assert result.sources[0].cover is not None
            assert result.sources[0].cover.id == cover.id

    def test_rejects_concurrent_edit_during_composition(self, pg_library):
        from sqlalchemy import event

        from app.modules.library.model_views.detail import provenance_detail
        from tests.factories.provenance import build_provenance_source

        with Session(pg_library) as setup:
            model = build_model(setup)
            build_provenance_source(setup, model)
            model_id = model.id
        changed = False
        with Session(pg_library) as reader:
            connection = reader.connection()

            def write_during_read(_conn, _cursor, statement, _params, _context, _many):
                nonlocal changed
                if not changed and "FROM model_provenance_sources" in statement:
                    changed = True
                    with Session(pg_library) as writer:
                        writer.execute(
                            text(
                                "UPDATE models SET name='Concurrent title' WHERE id=:id"
                            ),
                            {"id": model_id},
                        )
                        writer.commit()

            event.listen(connection, "after_cursor_execute", write_during_read)
            try:
                with pytest.raises(OperationError, match="edit_snapshot_changed"):
                    provenance_detail(reader, model_id)
            finally:
                event.remove(connection, "after_cursor_execute", write_during_read)
            assert changed
