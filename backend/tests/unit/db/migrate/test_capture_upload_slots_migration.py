"""Defends ``test_fe17_sqlite_upgrade_drops_old_job_unique_and_downgrade_restores_it`` behavior for the ``migrate`` production unit.

A failure means this boundary no longer preserves its observable contract.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from alembic import command
from tests.paths import BACKEND_ROOT

_MIGRATION_SPEC = importlib.util.spec_from_file_location(
    "fe17c8d1a0f2_capture_upload_slots",
    BACKEND_ROOT / "alembic/versions/fe17c8d1a0f2_capture_upload_slots.py",
)
assert _MIGRATION_SPEC is not None and _MIGRATION_SPEC.loader is not None
_MIGRATION = importlib.util.module_from_spec(_MIGRATION_SPEC)
_MIGRATION_SPEC.loader.exec_module(_MIGRATION)


def _config(url: str) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def test_fe17_sqlite_upgrade_drops_old_job_unique_and_downgrade_restores_it(
    tmp_path: Path,
) -> None:
    url = f"sqlite:///{tmp_path / 'capture-upload-slots.sqlite'}"
    config = _config(url)
    command.upgrade(config, "fd16b7f0c9e5")

    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO background_jobs "
                    "(id, visible, kind, state, status_json, replay_safe, attempts, "
                    "created_at, updated_at) VALUES "
                    "('old-job', 1, 'ingest', 'pending', '{}', 0, 0, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO staging_leases "
                    "(id, path, background_job_id, size_bytes, sha256, expires_at, "
                    "created_at) VALUES "
                    "('old-lease', '/tmp/old', 'old-job', 1, :sha, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"sha": "a" * 64},
            )
    finally:
        engine.dispose()

    command.upgrade(config, "fe17c8d1a0f2")
    engine = create_engine(url)
    try:
        unique_constraints = inspect(engine).get_unique_constraints("staging_leases")
        assert {
            tuple(constraint["column_names"]) for constraint in unique_constraints
        } == {
            ("path",),
            ("inbox_item_id",),
            ("model_source_cover_id",),
            ("capture_upload_slot_id",),
        }
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT background_job_id FROM staging_leases WHERE id = 'old-lease'"
                    )
                ).scalar_one()
                == "old-job"
            )
    finally:
        engine.dispose()

    command.downgrade(config, "fd16b7f0c9e5")
    engine = create_engine(url)
    try:
        unique_constraints = inspect(engine).get_unique_constraints("staging_leases")
        assert ("background_job_id",) in {
            tuple(constraint["column_names"]) for constraint in unique_constraints
        }
        assert "capture_upload_slot_id" not in {
            column["name"] for column in inspect(engine).get_columns("staging_leases")
        }
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT background_job_id FROM staging_leases WHERE id = 'old-lease'"
                    )
                ).scalar_one()
                == "old-job"
            )
    finally:
        engine.dispose()


def test_fe17_sqlite_downgrade_drops_transferred_multifile_capture_leases(
    tmp_path: Path,
) -> None:
    """Downgrade removes every capture lease before restoring job uniqueness."""
    url = f"sqlite:///{tmp_path / 'capture-upload-slots-transferred.sqlite'}"
    config = _config(url)
    command.upgrade(config, "fd16b7f0c9e5")

    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            # This is a pre-fe17 ordinary lease.  It must survive the
            # downgrade even though the same job also owns capture slots.
            connection.execute(
                text(
                    "INSERT INTO background_jobs "
                    "(id, visible, kind, state, status_json, replay_safe, attempts, "
                    "created_at, updated_at) VALUES "
                    "('ordinary-job', 1, 'ingest', 'pending', '{}', 0, 0, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO staging_leases "
                    "(id, path, background_job_id, size_bytes, sha256, expires_at, "
                    "created_at) VALUES "
                    "('ordinary-lease', '/tmp/ordinary', 'ordinary-job', 1, :sha, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"sha": "b" * 64},
            )

    finally:
        engine.dispose()

    command.upgrade(config, "fe17c8d1a0f2")
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users "
                    "(username, hashed_password, is_superuser, is_active, "
                    "created_at, updated_at) VALUES "
                    "('capture-owner', 'hash', 0, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            owner_id = connection.execute(
                text("SELECT id FROM users WHERE username = 'capture-owner'")
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO inbox_items "
                    "(owner_user_id, source_kind, state, manifest_json, "
                    "requested_tags_json, retryable, attempt_count, created_at, "
                    "updated_at) VALUES "
                    "(:owner_id, 'URL', 'CAPTURED', '{}', '[]', 0, 0, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"owner_id": owner_id},
            )
            inbox_item_id = connection.execute(
                text("SELECT id FROM inbox_items ORDER BY id DESC LIMIT 1")
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO background_jobs "
                    "(id, visible, kind, state, status_json, replay_safe, attempts, "
                    "created_at, updated_at) VALUES "
                    "('capture-job', 1, 'ingest', 'pending', '{}', 0, 0, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            for slot_id, lease_id in (
                ("slot-a", "capture-lease-a"),
                ("slot-b", "capture-lease-b"),
            ):
                connection.execute(
                    text(
                        "INSERT INTO capture_upload_slots "
                        "(id, inbox_item_id, role, filename, media_type, size_bytes, "
                        "sha256, state, created_at, updated_at) VALUES "
                        "(:slot_id, :inbox_item_id, 'model', :filename, "
                        "'application/octet-stream', 1, :sha, 'PENDING', "
                        "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                    ),
                    {
                        "slot_id": slot_id,
                        "inbox_item_id": inbox_item_id,
                        "filename": f"{slot_id}.stl",
                        "sha": "c" * 64,
                    },
                )
                # transfer_capture_slots_to_job() leaves the slot id empty,
                # retaining the slot id as the capture-origin marker.
                connection.execute(
                    text(
                        "INSERT INTO staging_leases "
                        "(id, path, background_job_id, capture_upload_slot_origin_id, "
                        "size_bytes, sha256, expires_at, created_at) VALUES "
                        "(:lease_id, :path, 'capture-job', :slot_id, 1, :sha, "
                        "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                    ),
                    {
                        "lease_id": lease_id,
                        "path": f"capture-slot:{slot_id}",
                        "slot_id": slot_id,
                        "sha": "d" * 64,
                    },
                )
    finally:
        engine.dispose()

    command.downgrade(config, "fd16b7f0c9e5")
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT count(*) FROM staging_leases")
                ).scalar_one()
                == 1
            )
            assert connection.execute(
                text(
                    "SELECT background_job_id, path FROM staging_leases "
                    "WHERE id = 'ordinary-lease'"
                )
            ).one() == ("ordinary-job", "/tmp/ordinary")
            assert "capture_upload_slots" not in inspect(engine).get_table_names()
        unique_constraints = inspect(engine).get_unique_constraints("staging_leases")
        assert ("background_job_id",) in {
            tuple(constraint["column_names"]) for constraint in unique_constraints
        }
    finally:
        engine.dispose()


def test_fe17_resolves_postgresql_generated_job_unique_name(monkeypatch) -> None:
    inspector = SimpleNamespace(
        get_unique_constraints=lambda _table: [
            {
                "name": "staging_leases_background_job_id_key",
                "column_names": ["background_job_id"],
            }
        ]
    )
    monkeypatch.setattr(_MIGRATION.sa, "inspect", lambda _bind: inspector)
    bind = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

    assert (
        _MIGRATION._staging_background_job_unique_name(bind)
        == "staging_leases_background_job_id_key"
    )


def test_fe17_resolves_postgresql_name_when_rendering_offline(monkeypatch) -> None:
    monkeypatch.setattr(
        _MIGRATION.sa,
        "inspect",
        lambda _bind: (_ for _ in ()).throw(_MIGRATION.sa.exc.NoInspectionAvailable()),
    )
    bind = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

    assert (
        _MIGRATION._staging_background_job_unique_name(bind)
        == "staging_leases_background_job_id_key"
    )


def test_fe17_postgresql_offline_sql_drops_generated_job_unique(capsys) -> None:
    config = _config("postgresql+psycopg://user:pass@localhost/printstash")
    command.upgrade(config, "fd16b7f0c9e5:fe17c8d1a0f2", sql=True)

    rendered = capsys.readouterr().out
    assert (
        "ALTER TABLE staging_leases DROP CONSTRAINT "
        "staging_leases_background_job_id_key;"
    ) in rendered


def test_fe17_postgresql_offline_sql_downgrade_removes_all_capture_origins(
    capsys,
) -> None:
    config = _config("postgresql+psycopg://user:pass@localhost/printstash")
    command.downgrade(config, "fe17c8d1a0f2:fd16b7f0c9e5", sql=True)

    rendered = capsys.readouterr().out
    delete = (
        "DELETE FROM staging_leases WHERE capture_upload_slot_id IS NOT NULL "
        "OR capture_upload_slot_origin_id IS NOT NULL;"
    )
    add_unique = (
        "ALTER TABLE staging_leases ADD CONSTRAINT "
        "uq_staging_leases_background_job_id UNIQUE (background_job_id);"
    )
    assert delete in rendered
    assert rendered.index(delete) < rendered.index(add_unique)


def test_fe17_uses_batch_naming_convention_for_unnamed_sqlite_job_unique(
    monkeypatch,
) -> None:
    inspector = SimpleNamespace(
        get_unique_constraints=lambda _table: [
            {"name": None, "column_names": ["background_job_id"]}
        ]
    )
    monkeypatch.setattr(_MIGRATION.sa, "inspect", lambda _bind: inspector)
    bind = SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))

    assert (
        _MIGRATION._staging_background_job_unique_name(bind)
        == "uq_staging_leases_background_job_id"
    )


def test_sqlite_head_allows_multiple_staging_leases_for_one_import_job(
    tmp_path: Path,
) -> None:
    """Repair a stamped database that retained the legacy unique job index."""
    url = f"sqlite:///{tmp_path / 'multifile-capture-leases.sqlite'}"
    config = _config(url)

    command.upgrade(config, "fe17c8d1a0f2")
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP INDEX ix_staging_leases_background_job_id"))
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX ix_staging_leases_background_job_id "
                    "ON staging_leases (background_job_id)"
                )
            )
    finally:
        engine.dispose()

    command.upgrade(config, "head")

    engine = create_engine(url)
    try:
        background_job_indexes = [
            index
            for index in inspect(engine).get_indexes("staging_leases")
            if index["column_names"] == ["background_job_id"]
        ]
        assert background_job_indexes == [
            {
                "name": "ix_staging_leases_background_job_id",
                "column_names": ["background_job_id"],
                "unique": 0,
                "dialect_options": {},
            }
        ]
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO background_jobs "
                    "(id, visible, kind, state, status_json, replay_safe, attempts, "
                    "created_at, updated_at) VALUES "
                    "('multifile-job', 1, 'pending_import', 'pending', '{}', 1, 0, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            for lease_id in ("lease-a", "lease-b"):
                connection.execute(
                    text(
                        "INSERT INTO staging_leases "
                        "(id, path, background_job_id, capture_upload_slot_origin_id, "
                        "size_bytes, sha256, expires_at, created_at) VALUES "
                        "(:lease_id, :path, 'multifile-job', :origin_id, 1, :sha, "
                        "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                    ),
                    {
                        "lease_id": lease_id,
                        "path": f"capture-slot:{lease_id}",
                        "origin_id": f"slot-{lease_id}",
                        "sha": "e" * 64,
                    },
                )
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM staging_leases "
                        "WHERE background_job_id = 'multifile-job'"
                    )
                ).scalar_one()
                == 2
            )
    finally:
        engine.dispose()
