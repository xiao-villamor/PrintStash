"""Preset edit claims serialize competing writers on both supported databases."""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import Engine, event, text
from sqlmodel import Session, create_engine

from app.core.errors import ErrorKind, OperationError
from app.db.migrate import run_migrations
from app.db.models import FilamentProfile, PrinterProfile, User
from app.db.session import _set_sqlite_pragmas
from app.db.url import normalize_database_url
from app.modules.printing.profile_edits import ProfileKind, editing_base
from app.schemas.editing import EditingBase, EditPrecondition
from tests.containers import fresh_postgres_database
from tests.factories import build_filament_profile, build_printer_profile, build_user


@dataclass(frozen=True)
class EditingDatabase:
    engine: Engine
    actor_id: int
    profile_id: int
    kind: ProfileKind
    base: EditingBase


@pytest.fixture(scope="module")
def pg_edit_engine() -> Iterator[Engine]:
    url = fresh_postgres_database("profile_edit_claim")
    run_migrations(url)
    engine = create_engine(normalize_database_url(url))
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def sqlite_edit_engine(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Engine]:
    url = f"sqlite:///{tmp_path_factory.mktemp('profile-claims') / 'profiles.sqlite'}"
    run_migrations(url)
    engine = create_engine(url, connect_args={"check_same_thread": False})
    event.listen(engine, "connect", _set_sqlite_pragmas)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(params=list(ProfileKind))
def profile_kind(request: pytest.FixtureRequest) -> ProfileKind:
    return request.param


@pytest.fixture(
    params=[
        pytest.param("sqlite", id="sqlite"),
        pytest.param("postgresql", id="postgres", marks=pytest.mark.postgres),
    ]
)
def editing_db(
    request: pytest.FixtureRequest, profile_kind: ProfileKind
) -> Iterator[EditingDatabase]:
    engine = request.getfixturevalue(
        "pg_edit_engine" if request.param == "postgresql" else "sqlite_edit_engine"
    )
    with Session(engine) as session:
        build = (
            build_filament_profile
            if profile_kind is ProfileKind.FILAMENT
            else build_printer_profile
        )
        row = build(session, notes="Original")
        assert row.id is not None
        actor = build_user(session, superuser=True)
        assert actor.id is not None
        base = editing_base(row)
        environment = EditingDatabase(engine, actor.id, row.id, profile_kind, base)
    try:
        yield environment
    finally:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM filament_profiles"))
            connection.execute(text("DELETE FROM printer_profiles"))
            connection.execute(text("DELETE FROM users"))


def _precondition(db: EditingDatabase) -> EditPrecondition:
    return EditPrecondition(
        contract="conditional-v1",
        if_match=f'"{db.kind.value}-{db.profile_id}-e{db.base.edit_epoch}-v{db.base.edit_version}"',
    )


class TestClaim:
    def test_only_one_editor_can_commit_from_a_shared_base(
        self, editing_db: EditingDatabase
    ) -> None:
        from app.modules.printing.profile_edits import claim

        start = Barrier(2)

        def editor(currency: str) -> str:
            with Session(editing_db.engine) as session:
                actor = session.get(User, editing_db.actor_id)
                row = session.get(
                    FilamentProfile
                    if editing_db.kind is ProfileKind.FILAMENT
                    else PrinterProfile,
                    editing_db.profile_id,
                )
                assert actor is not None and row is not None
                start.wait(timeout=10)
                try:
                    claim(session, actor, row, _precondition(editing_db))
                    row.notes = currency
                    session.add(row)
                    session.commit()
                    return "saved"
                except OperationError as error:
                    session.rollback()
                    assert error.kind is ErrorKind.PRECONDITION_FAILED
                    return error.detail

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(editor, "EUR")
            second = executor.submit(editor, "GBP")
            outcomes = [first.result(timeout=20), second.result(timeout=20)]
        assert sorted(outcomes) == ["edit_conflict", "saved"]
        with Session(editing_db.engine) as session:
            row = session.get(
                FilamentProfile
                if editing_db.kind is ProfileKind.FILAMENT
                else PrinterProfile,
                editing_db.profile_id,
            )
            assert row is not None
            assert row.notes in {"EUR", "GBP"}
            assert row.edit_version > editing_db.base.edit_version

    def test_rollback_preserves_an_unused_editing_base(
        self, editing_db: EditingDatabase
    ) -> None:
        from app.modules.printing.profile_edits import claim

        with Session(editing_db.engine) as session:
            actor = session.get(User, editing_db.actor_id)
            row = session.get(
                FilamentProfile
                if editing_db.kind is ProfileKind.FILAMENT
                else PrinterProfile,
                editing_db.profile_id,
            )
            assert actor is not None and row is not None
            claim(session, actor, row, _precondition(editing_db))
            row.notes = "EUR"
            session.add(row)
            session.flush()
            session.rollback()
        with Session(editing_db.engine) as session:
            row = session.get(
                FilamentProfile
                if editing_db.kind is ProfileKind.FILAMENT
                else PrinterProfile,
                editing_db.profile_id,
            )
            actor = session.get(User, editing_db.actor_id)
            assert row is not None and actor is not None
            assert row.notes == "Original"
            assert row.edit_version == editing_db.base.edit_version
            claim(session, actor, row, _precondition(editing_db))
            row.notes = "GBP"
            session.add(row)
            session.commit()

    def test_a_restored_database_rejects_a_prior_incarnation(
        self, editing_db: EditingDatabase
    ) -> None:
        from app.modules.printing.profile_edits import claim

        with editing_db.engine.begin() as connection:
            connection.execute(
                text("UPDATE library_revision SET epoch=:epoch WHERE id=1"),
                {"epoch": uuid4().hex},
            )
        with Session(editing_db.engine) as session:
            row = session.get(
                FilamentProfile
                if editing_db.kind is ProfileKind.FILAMENT
                else PrinterProfile,
                editing_db.profile_id,
            )
            actor = session.get(User, editing_db.actor_id)
            assert row is not None and actor is not None
            with pytest.raises(OperationError, match="edit_conflict"):
                claim(session, actor, row, _precondition(editing_db))
            session.rollback()
            assert row.notes == "Original"
            assert row.edit_version == editing_db.base.edit_version

    def test_a_legacy_writer_invalidates_an_earlier_conditional_base(
        self, editing_db: EditingDatabase
    ) -> None:
        from app.modules.printing.profile_edits import claim

        with editing_db.engine.begin() as connection:
            connection.execute(
                text(
                    f"UPDATE {'filament_profiles' if editing_db.kind is ProfileKind.FILAMENT else 'printer_profiles'} SET notes='EUR' WHERE id=:id"
                ),
                {"id": editing_db.profile_id},
            )
        with Session(editing_db.engine) as session:
            row = session.get(
                FilamentProfile
                if editing_db.kind is ProfileKind.FILAMENT
                else PrinterProfile,
                editing_db.profile_id,
            )
            actor = session.get(User, editing_db.actor_id)
            assert row is not None and actor is not None
            before = row.edit_version
            with pytest.raises(OperationError, match="edit_conflict"):
                claim(session, actor, row, _precondition(editing_db))
            session.rollback()
            assert row.notes == "EUR"
            assert row.edit_version == before

    @pytest.mark.parametrize(
        "revocation", ["disabled", "demoted", "deleted", "session"], ids=str
    )
    def test_revoked_administrator_authority_rejects_the_edit(
        self, editing_db: EditingDatabase, revocation: str
    ) -> None:
        from app.modules.printing.profile_edits import claim

        with Session(editing_db.engine) as session:
            actor = session.get(User, editing_db.actor_id)
            row = session.get(
                FilamentProfile
                if editing_db.kind is ProfileKind.FILAMENT
                else PrinterProfile,
                editing_db.profile_id,
            )
            assert actor is not None and row is not None
            with Session(editing_db.engine) as other:
                revoked = other.get(User, editing_db.actor_id)
                assert revoked is not None
                if revocation == "deleted":
                    other.delete(revoked)
                else:
                    if revocation == "disabled":
                        revoked.is_active = False
                    elif revocation == "demoted":
                        revoked.is_superuser = False
                    else:
                        revoked.auth_version += 1
                    other.add(revoked)
                other.commit()
            with pytest.raises(
                OperationError, match="preset_permission_denied"
            ) as caught:
                claim(session, actor, row, _precondition(editing_db))
            assert caught.value.kind is ErrorKind.FORBIDDEN
            session.rollback()
            assert row.notes == "Original"
            assert row.edit_version == editing_db.base.edit_version

    def test_a_current_administrator_can_commit_an_explicit_edit(
        self, editing_db: EditingDatabase
    ) -> None:
        from app.modules.printing.profile_edits import claim

        with Session(editing_db.engine) as session:
            row = session.get(
                FilamentProfile
                if editing_db.kind is ProfileKind.FILAMENT
                else PrinterProfile,
                editing_db.profile_id,
            )
            actor = session.get(User, editing_db.actor_id)
            assert row is not None and actor is not None
            claim(session, actor, row, _precondition(editing_db))
            row.notes = "EUR"
            session.add(row)
            session.commit()
        with Session(editing_db.engine) as session:
            row = session.get(
                FilamentProfile
                if editing_db.kind is ProfileKind.FILAMENT
                else PrinterProfile,
                editing_db.profile_id,
            )
            assert row is not None
            assert row.notes == "EUR"
            assert row.edit_version > editing_db.base.edit_version

    def test_explicit_legacy_commands_can_save_without_a_base(
        self, editing_db: EditingDatabase
    ) -> None:
        from app.modules.printing.profile_edits import claim

        with Session(editing_db.engine) as session:
            row = session.get(
                FilamentProfile
                if editing_db.kind is ProfileKind.FILAMENT
                else PrinterProfile,
                editing_db.profile_id,
            )
            actor = session.get(User, editing_db.actor_id)
            assert row is not None and actor is not None
            claim(session, actor, row, EditPrecondition())
            row.notes = "EUR"
            session.add(row)
            session.commit()
        with Session(editing_db.engine) as session:
            row = session.get(
                FilamentProfile
                if editing_db.kind is ProfileKind.FILAMENT
                else PrinterProfile,
                editing_db.profile_id,
            )
            assert row is not None
            assert row.notes == "EUR"
            assert row.edit_version > editing_db.base.edit_version

    def test_rejects_a_recreated_preset_identity(
        self, editing_db: EditingDatabase
    ) -> None:
        from app.modules.printing.profile_edits import claim

        with Session(editing_db.engine) as session:
            table = (
                FilamentProfile
                if editing_db.kind is ProfileKind.FILAMENT
                else PrinterProfile
            )
            row = session.get(table, editing_db.profile_id)
            assert row is not None
            session.delete(row)
            session.commit()
            build = (
                build_filament_profile
                if editing_db.kind is ProfileKind.FILAMENT
                else build_printer_profile
            )
            recreated = build(session, id=editing_db.profile_id, notes="New identity")
            actor = session.get(User, editing_db.actor_id)
            assert actor is not None
            with pytest.raises(OperationError, match="edit_conflict"):
                claim(session, actor, recreated, _precondition(editing_db))
            session.rollback()
            assert recreated.notes == "New identity"
            assert recreated.edit_version == 1

    @pytest.mark.parametrize("profile_kind", [ProfileKind.FILAMENT], indirect=True)
    def test_refuses_a_filament_adopted_by_spoolman(
        self, editing_db: EditingDatabase
    ) -> None:
        from app.modules.printing.profile_edits import claim

        with Session(editing_db.engine) as session:
            row = session.get(FilamentProfile, editing_db.profile_id)
            actor = session.get(User, editing_db.actor_id)
            assert row is not None and actor is not None
            with editing_db.engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE filament_profiles SET spoolman_filament_id=42 WHERE id=:id"
                    ),
                    {"id": row.id},
                )
            with pytest.raises(OperationError, match="filament_profile_linked"):
                claim(session, actor, row, EditPrecondition())
            session.rollback()
            assert row.notes == "Original"
            assert row.spoolman_filament_id == 42
