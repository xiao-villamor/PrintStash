"""Defends revision uniqueness migration repairs existing duplicates at the db migrate integration boundary.

A regression could strand or silently rewrite data during an existing installation upgrade.
"""

from __future__ import annotations

from ._migrate_shared import (
    _PRE_0_8_0,
    BACKEND_ROOT,
    Config,
    IntegrityError,
    Path,
    ScriptDirectory,
    Session,
    SQLModel,
    User,
    _current,
    _head_revision,
    _is_alembic_managed,
    _Spy,
    _upgrade_to,
    _url,
    command,
    create_engine,
    init_db,
    inspect,
    migrate_mod,
    pytest,
    text,
)


def test_revision_uniqueness_migration_repairs_existing_duplicates(
    tmp_path: Path,
) -> None:
    url = _url(tmp_path, "duplicate-revisions.sqlite")
    cfg = migrate_mod._alembic_config(url)
    command.upgrade(cfg, "a2f7c9d4e6b1")

    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO models (name, slug, hash, created_at, updated_at)
                    VALUES ('Duplicate', 'duplicate', :hash, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """
                ),
                {"hash": "d" * 64},
            )
            model_id = connection.execute(
                text("SELECT id FROM models WHERE slug = 'duplicate'")
            ).scalar_one()
            for index in range(2):
                connection.execute(
                    text(
                        """
                        INSERT INTO files (
                            model_id, path, original_filename, file_type, version,
                            size_bytes, sha256, uploaded_at, is_recommended, is_external
                        ) VALUES (
                            :model_id, :path, :name, 'GCODE', 1,
                            1, :sha, CURRENT_TIMESTAMP, 1, 0
                        )
                        """
                    ),
                    {
                        "model_id": model_id,
                        "path": f"/tmp/duplicate-{index}.gcode",
                        "name": f"duplicate-{index}.gcode",
                        "sha": str(index) * 64,
                    },
                )
    finally:
        engine.dispose()

    command.upgrade(cfg, "head")

    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            versions = (
                connection.execute(text("SELECT version FROM files ORDER BY version"))
                .scalars()
                .all()
            )
            recommended = connection.execute(
                text("SELECT COUNT(*) FROM files WHERE is_recommended = 1")
            ).scalar_one()
            next_version = connection.execute(
                text("SELECT next_file_version FROM models WHERE id = :id"),
                {"id": model_id},
            ).scalar_one()
        assert versions == [1, 2]
        assert recommended == 1
        assert next_version == 3
    finally:
        engine.dispose()


def test_default_printer_migration_repairs_duplicates_and_enforces_uniqueness(
    tmp_path: Path,
) -> None:
    url = _url(tmp_path, "duplicate-default-printers.sqlite")
    cfg = migrate_mod._alembic_config(url)
    command.upgrade(cfg, "c7e4a1b9d2f6")
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            for name in ("First", "Second"):
                connection.execute(
                    text(
                        """
                        INSERT INTO printers (
                            name, provider, moonraker_url, is_default,
                            drain_mode, status, created_at, updated_at
                        ) VALUES (
                            :name, 'MOONRAKER', '', 1, 0, 'UNKNOWN',
                            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                        )
                        """
                    ),
                    {"name": name},
                )
    finally:
        engine.dispose()

    command.upgrade(cfg, "head")

    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            defaults = (
                connection.execute(
                    text("SELECT id FROM printers WHERE is_default = 1 ORDER BY id")
                )
                .scalars()
                .all()
            )
        assert defaults == [1]
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE printers SET is_default = 1 WHERE id = 2")
                )
    finally:
        engine.dispose()


def test_runner_orphan_rescue_preserves_existing_data(tmp_path: Path) -> None:
    url = _url(tmp_path)
    engine = create_engine(url)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            User(
                username="keepme",
                hashed_password="x",
                is_active=True,
                is_superuser=True,
            )
        )
        session.commit()
    engine.dispose()

    migrate_mod.run_migrations(url)

    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            names = [r[0] for r in conn.execute(text("SELECT username FROM users"))]
    finally:
        engine.dispose()
    assert "keepme" in names  # rescue never dropped or rebuilt the data


def test_has_application_tables_ignores_alembic_only(tmp_path: Path) -> None:
    url = _url(tmp_path)
    engine = create_engine(url)
    try:
        assert migrate_mod._has_application_tables(engine) is False
    finally:
        engine.dispose()


def test_init_db_builds_schema_on_unmanaged_db(tmp_path: Path) -> None:
    url = _url(tmp_path, "init.sqlite")
    engine = create_engine(url)
    try:
        assert "users" not in set(inspect(engine).get_table_names())
        init_db(engine)
        assert "users" in set(inspect(engine).get_table_names())
        assert _is_alembic_managed(engine) is False  # create_all leaves it un-stamped
    finally:
        engine.dispose()


def test_init_db_is_strict_noop_on_alembic_managed_db(tmp_path: Path) -> None:
    url = _url(tmp_path, "managed.sqlite")
    migrate_mod.run_migrations(url)

    engine = create_engine(url)
    try:
        assert _is_alembic_managed(engine) is True
        before = set(inspect(engine).get_table_names())
        init_db(engine)  # must NOT call create_all
        assert set(inspect(engine).get_table_names()) == before
        assert migrate_mod._current_revision(engine) == _head_revision()
    finally:
        engine.dispose()


def test_fresh_db_bootstraps_via_create_all_not_baseline(tmp_path, monkeypatch) -> None:
    spy = _Spy()
    url = spy.install(monkeypatch, tmp_path)  # empty DB → fresh
    migrate_mod.run_migrations(url)
    assert spy.create_all == [url]  # schema built from models
    assert len(spy.stamp) == 1  # stamped head
    assert spy.upgrade == []  # baseline chain NOT replayed (Postgres-safe)


def test_orphan_db_stamps_then_upgrades(tmp_path, monkeypatch) -> None:
    # Real orphan: tables but no alembic_version.
    url = _url(tmp_path, "dispatch.sqlite")
    engine = create_engine(url)
    SQLModel.metadata.create_all(engine)
    engine.dispose()

    spy = _Spy()
    # Re-point spies at the SAME url (already has tables).
    monkeypatch.setattr(
        migrate_mod.command, "upgrade", lambda *a, **k: spy.upgrade.append(a)
    )
    monkeypatch.setattr(
        migrate_mod.command, "stamp", lambda *a, **k: spy.stamp.append(a)
    )
    monkeypatch.setattr(migrate_mod, "_create_all", lambda u: spy.create_all.append(u))

    migrate_mod.run_migrations(url)
    assert len(spy.stamp) == 1 and len(spy.upgrade) == 1  # adopt then upgrade
    assert spy.create_all == []  # never rebuilds an existing schema


def test_managed_db_only_upgrades(tmp_path, monkeypatch) -> None:
    url = _url(tmp_path, "dispatch.sqlite")
    migrate_mod.run_migrations(url)  # make it managed (real run)

    spy = _Spy()
    monkeypatch.setattr(
        migrate_mod.command, "upgrade", lambda *a, **k: spy.upgrade.append(a)
    )
    monkeypatch.setattr(
        migrate_mod.command, "stamp", lambda *a, **k: spy.stamp.append(a)
    )
    monkeypatch.setattr(migrate_mod, "_create_all", lambda u: spy.create_all.append(u))

    migrate_mod.run_migrations(url)
    assert spy.upgrade and not spy.stamp and not spy.create_all


def test_single_head_and_revisions_all_resolve(tmp_path: Path) -> None:
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(cfg)
    assert len(script.get_heads()) == 1, (
        "multiple alembic heads — `upgrade head` is ambiguous"
    )
    # Walking every revision resolves each down_revision; a deleted/renamed file
    # raises here — i.e. the "Can't locate revision X" startup crash, in CI.
    assert len(list(script.walk_revisions())) > 1


def test_upgrade_from_pre_0_8_0_release_preserves_data(tmp_path: Path) -> None:
    url = _url(tmp_path, "old.sqlite")
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)

    # Stand up an old (~0.7.2) schema and seed representative rows.
    command.upgrade(cfg, _PRE_0_8_0)
    engine = create_engine(url)
    with engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO collections (name, slug, path, created_at) "
                "VALUES ('Functional','functional','functional','2026-01-01')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO models (name, slug, hash, collection_id, created_at, updated_at) "
                "VALUES ('Bracket','bracket',:h,1,'2026-01-01','2026-01-01')"
            ),
            {"h": "a" * 64},
        )
        conn.commit()
    engine.dispose()

    # The upgrade an existing user actually runs.
    command.upgrade(cfg, "head")

    engine = create_engine(url)
    try:
        inspector = inspect(engine)
        assert "documents" in inspector.get_table_names()  # new 0.8.0 table
        assert "printer_permissions" in inspector.get_table_names()
        assert "readme" in {c["name"] for c in inspector.get_columns("collections")}
        printer_columns = {c["name"] for c in inspector.get_columns("printers")}
        assert {
            "provider_variant",
            "prusalink_url",
            "prusalink_auth_mode",
            "prusalink_username",
            "prusalink_password",
            "prusalink_api_key",
            "elegoo_centauri_host",
            "elegoo_centauri_access_code",
            "elegoo_centauri_mainboard_id",
        } <= printer_columns
        with engine.connect() as conn:
            # Existing data survived the ALTER TABLE / CREATE TABLE migrations.
            assert conn.execute(text("SELECT count(*) FROM collections")).scalar() == 1
            assert conn.execute(text("SELECT count(*) FROM models")).scalar() == 1
    finally:
        engine.dispose()
    assert _current(url) == _head_revision()


def test_fk_repair_clears_dangling_nullable_reference(tmp_path: Path) -> None:
    """A document whose creator was purged keeps the document, loses the attribution.

    ``documents.created_by`` is one of the columns the migration chain actually
    constrains — the ORM declares more foreign keys than the shipped schema has.
    """
    db_path = tmp_path / "dirty.sqlite"
    cfg = _upgrade_to(db_path, "a1c7e4f9b23d")  # the revision before the repair

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO documents (name, kind, created_by, created_at,"
                " updated_at) VALUES ('orphaned', 'markdown', 999,"
                " '2026-01-01', '2026-01-01')"
            )
        )
    engine.dispose()

    command.upgrade(cfg, "head")

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT name, created_by FROM documents WHERE name = 'orphaned'")
        ).one()
        assert row.name == "orphaned", "the document itself must survive"
        assert row.created_by is None
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
    engine.dispose()


def test_fk_repair_deletes_row_with_dangling_required_reference(
    tmp_path: Path,
) -> None:
    """A permission grant for a purged user cannot be loaded — drop it."""
    db_path = tmp_path / "dirty.sqlite"
    cfg = _upgrade_to(db_path, "a1c7e4f9b23d")

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO collections (name, slug, path, created_at)"
                " VALUES ('c', 'c', 'c', '2026-01-01')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO collection_permissions (user_id, collection_id, role,"
                " created_at, updated_at)"
                " VALUES (999, 1, 'view', '2026-01-01', '2026-01-01')"
            )
        )
    engine.dispose()

    command.upgrade(cfg, "head")

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM collection_permissions")
        ).scalar()
        assert count == 0
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
    engine.dispose()


def test_fk_repair_leaves_clean_database_untouched(tmp_path: Path) -> None:
    db_path = tmp_path / "clean.sqlite"
    cfg = _upgrade_to(db_path, "a1c7e4f9b23d")

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO models (name, slug, hash, created_at, updated_at)"
                " VALUES ('kept', 'kept', 'h1', '2026-01-01', '2026-01-01')"
            )
        )
    engine.dispose()

    command.upgrade(cfg, "head")

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM models")).scalar() == 1
    engine.dispose()


def test_sqlite_pragma_enforces_foreign_keys(tmp_path: Path) -> None:
    """The pragma is what makes the repair worth doing: without it SQLite happily
    writes a child row pointing at a parent that was never there."""
    import pytest
    from sqlalchemy import event
    from sqlalchemy.exc import IntegrityError

    from app.db.session import _set_sqlite_pragmas

    db_path = tmp_path / "vault.sqlite"
    _upgrade_to(db_path, "head")

    engine = create_engine(f"sqlite:///{db_path}")
    event.listen(engine, "connect", _set_sqlite_pragmas)
    try:
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO api_keys (user_id, name, prefix, key_hash,"
                        " created_at) VALUES (999, 'k', 'p', 'h', '2026-01-01')"
                    )
                )
    finally:
        engine.dispose()


def test_backfill_populates_existing_jobs(tmp_path: Path) -> None:
    """A job completed before the migration gets its cost resolved from its
    metadata/profile, exactly like a freshly-completed job would today."""
    db_path = tmp_path / "precost.sqlite"
    cfg = _upgrade_to(db_path, "b2d8f6a1c94e")  # the revision before this one

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO models (id, name, slug, hash, created_at, updated_at)"
                " VALUES (1, 'M', 'm', 'h', '2026-01-01', '2026-01-01')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO files (id, model_id, path, original_filename,"
                " file_type, version, size_bytes, sha256, is_recommended,"
                " is_external, uploaded_at)"
                " VALUES (1, 1, '/f', 'f.gcode', 'gcode', 1, 1, 'sha',"
                " 0, 0, '2026-01-01')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO metadata (id, file_id, material_type, material_brand,"
                " created_at)"
                " VALUES (1, 1, 'PLA', 'Hatchbox', '2026-01-01')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO filament_profiles (id, name, material_type,"
                " material_brand, cost_per_kg, created_at, updated_at)"
                " VALUES (1, 'Hatchbox PLA', 'PLA', 'Hatchbox', 20.0,"
                " '2026-01-01', '2026-01-01')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO print_jobs (id, file_id, model_id, remote_filename,"
                " state, progress, source, filament_used_g, created_at, updated_at)"
                " VALUES (1, 1, 1, 'f.gcode', 'completed', 1.0, 'vault', 100.0,"
                " '2026-01-01', '2026-01-01')"
            )
        )
    engine.dispose()

    command.upgrade(cfg, "head")

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT cost, filament_g_effective FROM print_jobs WHERE id = 1")
        ).one()
        assert row.filament_g_effective == 100.0
        # 100g @ 20/kg => 2.00.
        assert row.cost == 2.0
    engine.dispose()
