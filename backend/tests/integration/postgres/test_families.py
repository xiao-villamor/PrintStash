"""Family migrations and concurrent edits on SQLite and supported PostgreSQL.

Each case owns a private schema or SQLite file, with independent connections.
The partial indexes and canonical/member cycle must protect real committed data.
"""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import event, inspect, make_url
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import Session, SQLModel, col, create_engine, select

from alembic import command
from app.core.errors import OperationError
from app.db.models import File, FileType, Model, ModelFamily, ModelFamilyMember
from app.db.session import _set_sqlite_pragmas
from app.db.url import normalize_database_url
from app.modules.library.families import canonical, members
from app.schemas.families import FamilyCanonicalChange, FamilyMemberAdd
from app.schemas.models import ModelSort
from tests import factories as f
from tests.containers import postgres_url
from tests.paths import ALEMBIC_INI

FAMILY_TABLES = {
    "model_families",
    "model_family_members",
    "model_family_stars",
    "model_family_tags",
}
BEFORE_FAMILIES = "f3736257acd2"
FAMILY_REVISION = "0118bda3e719"


@pytest.fixture(params=["sqlite", "postgresql"])
def family_engine(request, tmp_path):
    """The pre-Family schema; data comes from the repository factories."""
    base = None
    schema = "families_" + uuid4().hex
    if request.param == "postgresql":
        url = make_url(normalize_database_url(postgres_url()))
        base = create_engine(url)
        with base.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        url = url.update_query_dict({"options": f"-csearch_path={schema}"})
    else:
        url = make_url(f"sqlite:///{tmp_path / 'families.sqlite'}")
    engine = create_engine(url)
    if request.param == "sqlite":
        event.listen(engine, "connect", _set_sqlite_pragmas)
    config = Config(str(ALEMBIC_INI))
    config.set_main_option(
        "sqlalchemy.url", url.render_as_string(hide_password=False).replace("%", "%%")
    )
    try:
        SQLModel.metadata.create_all(
            engine,
            tables=[
                table
                for name, table in SQLModel.metadata.tables.items()
                if name not in FAMILY_TABLES
            ],
        )
        command.stamp(config, BEFORE_FAMILIES)
        yield engine, config
    finally:
        engine.dispose()
        if base is not None:
            with base.begin() as connection:
                connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
            base.dispose()


def _attempt(engine, barrier, actor_id, operation, family_id, payload):
    from app.db.models import User

    with Session(engine) as session:
        actor = session.get(User, actor_id)
        barrier.wait(timeout=10)
        try:
            operation(session, actor, family_id, payload)
            session.commit()
            return "committed"
        except (OperationError, IntegrityError, OperationalError):
            session.rollback()
            return "conflict"


class TestFamilySchema:
    def test_roundtrips_family_schema(self, family_engine):
        engine, config = family_engine
        with Session(engine) as session:
            model = f.build_model(session)
            artifact = f.build_file(
                session, model, file_type=FileType.GCODE, recommended=True
            )
            model_id, file_id, digest = model.id, artifact.id, artifact.sha256
        command.upgrade(config, FAMILY_REVISION)
        with Session(engine) as session:
            family = f.build_family(session)
            f.build_family_member(
                session, family, session.get(Model, model_id), canonical=True
            )

        command.downgrade(config, BEFORE_FAMILIES)
        command.upgrade(config, FAMILY_REVISION)

        foreign_keys = inspect(engine).get_foreign_keys("model_families")
        assert any(
            key["constrained_columns"] == ["canonical_member_id"]
            and key["referred_table"] == "model_family_members"
            for key in foreign_keys
        )
        with Session(engine) as session:
            assert session.get(Model, model_id) is not None
            assert session.get(File, file_id).sha256 == digest
            assert session.get(File, file_id).is_recommended is True
            assert session.exec(select(ModelFamily)).all() == []


class TestConcurrentFamilyEdits:
    def test_serializes_concurrent_membership(self, family_engine):
        engine, config = family_engine
        command.upgrade(config, FAMILY_REVISION)
        with Session(engine) as session:
            actor = f.build_user(session, superuser=True)
            model = f.build_model(session)
            first, second = f.build_family(session), f.build_family(session)
            actor_id, model_id, first_id, second_id = (
                actor.id,
                model.id,
                first.id,
                second.id,
            )
        barrier = Barrier(2)
        payload = FamilyMemberAdd(model_id=model_id, version=1)

        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(
                _attempt, engine, barrier, actor_id, members.add, first_id, payload
            )
            b = pool.submit(
                _attempt, engine, barrier, actor_id, members.add, second_id, payload
            )
            results = sorted([a.result(timeout=45), b.result(timeout=45)])

        assert results == ["committed", "conflict"]
        with Session(engine) as session:
            assert session.exec(
                select(ModelFamilyMember.model_id).where(
                    col(ModelFamilyMember.detached_at).is_(None)
                )
            ).all() == [model_id]

    def test_serializes_canonical_edits(self, family_engine):
        engine, config = family_engine
        command.upgrade(config, FAMILY_REVISION)
        with Session(engine) as session:
            actor = f.build_user(session, superuser=True)
            family = f.build_family(session)
            f.build_family_member(
                session, family, f.build_model(session), canonical=True
            )
            first = f.build_family_member(session, family, f.build_model(session))
            second = f.build_family_member(session, family, f.build_model(session))
            actor_id, family_id, first_id, second_id = (
                actor.id,
                family.id,
                first.id,
                second.id,
            )
        barrier = Barrier(2)

        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(
                _attempt,
                engine,
                barrier,
                actor_id,
                canonical.choose,
                family_id,
                FamilyCanonicalChange(
                    member_id=first_id, previous_role="identical", version=1
                ),
            )
            b = pool.submit(
                _attempt,
                engine,
                barrier,
                actor_id,
                canonical.choose,
                family_id,
                FamilyCanonicalChange(
                    member_id=second_id, previous_role="identical", version=1
                ),
            )
            results = sorted([a.result(timeout=45), b.result(timeout=45)])

        assert results == ["committed", "conflict"]
        with Session(engine) as session:
            family = session.get(ModelFamily, family_id)
            assert family.version == 2
            assert family.canonical_member_id in [first_id, second_id]
            assert session.exec(
                select(ModelFamilyMember.id).where(
                    ModelFamilyMember.role == "canonical",
                    col(ModelFamilyMember.detached_at).is_(None),
                )
            ).all() == [family.canonical_member_id]


class TestFamilyBrowse:
    @pytest.mark.parametrize("sort", list(ModelSort))
    def test_pages_family_union_on_supported_databases(self, family_engine, sort):
        from datetime import datetime, timezone

        from app.modules.library.model_views.family_browse import collapsed_page
        from app.schemas.models import ModelFilters

        engine, config = family_engine
        command.upgrade(config, FAMILY_REVISION)
        with Session(engine) as session:
            actor = f.build_user(session, superuser=True)
            when = datetime(2026, 1, 1, tzinfo=timezone.utc)
            family = f.build_family(session, "Same", updated_at=when)
            member = f.build_model(session, "Same", updated_at=when)
            f.build_family_member(session, family, member, canonical=True)
            standalone = f.build_model(session, "Same", updated_at=when)
            first = collapsed_page(
                session, actor, filters=ModelFilters(), sort=sort, cursor=None, limit=1
            )

            second = collapsed_page(
                session,
                actor,
                filters=ModelFilters(),
                sort=sort,
                cursor=first.next_cursor,
                limit=1,
            )

            assert first.total == second.total == 2
            assert first.items[0].kind == "family"
            assert first.items[0].family.id == family.id
            assert second.items[0].kind == "model"
            assert second.items[0].model.id == standalone.id
            assert second.next_cursor is None
