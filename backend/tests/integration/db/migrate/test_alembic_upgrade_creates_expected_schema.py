"""Defends alembic upgrade creates expected schema at the db migrate integration boundary.

A regression could strand or silently rewrite data during an existing installation upgrade.
"""

from __future__ import annotations

from ._migrate_shared import (
    BACKEND_ROOT,
    Config,
    Path,
    SQLModel,
    _current,
    _head_revision,
    _rewrite_sqlite_table_definition,
    _table_names,
    _url,
    command,
    create_engine,
    inspect,
    migrate_mod,
    module_from_spec,
    pytest,
    spec_from_file_location,
    text,
)


def test_alembic_upgrade_creates_expected_schema(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "vault.sqlite"
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

    command.upgrade(cfg, "head")

    engine = create_engine(f"sqlite:///{db_path}")
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    assert "alembic_version" in tables
    assert "models" in tables
    assert "files" in tables
    assert "print_jobs" in tables
    assert "refresh_tokens" in tables
    assert "printer_profiles" in tables
    assert "share_links" in tables
    assert {
        "model_provenance_sources",
        "model_provenance_fields",
        "provenance_captures",
        "artifact_provenance_links",
        "inbox_item_results",
    } <= tables
    print_job_columns = {
        col["name"]: col for col in inspector.get_columns("print_jobs")
    }
    assert "artifact_capture_error_code" in print_job_columns
    assert "artifact_capture_error_message" in print_job_columns
    assert "dedupe_absorbed_at" in print_job_columns
    assert "dedupe_survivor_id" in print_job_columns

    files_columns = {col["name"]: col for col in inspector.get_columns("files")}
    assert "revision_label" in files_columns
    assert "revision_status" in files_columns
    assert "revision_notes" in files_columns
    assert "is_recommended" in files_columns
    assert files_columns["is_recommended"]["nullable"] is False
    assert files_columns["is_recommended"]["default"] is not None
    model_columns = {col["name"]: col for col in inspector.get_columns("models")}
    assert "next_file_version" in model_columns
    file_indexes = {index["name"]: index for index in inspector.get_indexes("files")}
    assert bool(file_indexes["uq_files_model_version"]["unique"]) is True
    assert bool(file_indexes["uq_files_live_recommended_gcode"]["unique"]) is True
    assert file_indexes["ix_files_model_deleted_type"]["column_names"] == [
        "model_id",
        "deleted_at",
        "file_type",
    ]
    model_indexes = {index["name"]: index for index in inspector.get_indexes("models")}
    assert model_indexes["ix_models_deleted_updated_id"]["column_names"] == [
        "deleted_at",
        "updated_at",
        "id",
    ]
    background_job_indexes = {
        index["name"]: index for index in inspector.get_indexes("background_jobs")
    }
    assert background_job_indexes["ix_background_jobs_visible_state_owner_updated"][
        "column_names"
    ] == ["visible", "state", "owner_user_id", "updated_at"]
    printer_indexes = {
        index["name"]: index for index in inspector.get_indexes("printers")
    }
    assert bool(printer_indexes["uq_printers_live_default"]["unique"]) is True

    share_columns = {col["name"]: col for col in inspector.get_columns("share_links")}
    assert "model_id" in share_columns
    assert "token_hash" in share_columns
    assert "expires_at" in share_columns
    assert "allow_download" in share_columns
    assert "selected_file_ids_json" in share_columns

    user_columns = {col["name"]: col for col in inspector.get_columns("users")}
    assert "oidc_issuer" in user_columns
    assert "oidc_subject" in user_columns
    assert user_columns["oidc_managed"]["nullable"] is False
    config_columns = {
        col["name"]: col for col in inspector.get_columns("system_config")
    }
    assert "oidc_enabled" in config_columns
    assert "oidc_client_secret" in config_columns
    assert "oidc_admin_groups" in config_columns

    inbox_columns = {col["name"]: col for col in inspector.get_columns("inbox_items")}
    assert "completion" in inbox_columns
    inbox_fks = {
        fk["constrained_columns"][0]: (fk.get("options") or {}).get("ondelete")
        for fk in inspector.get_foreign_keys("inbox_items")
    }
    assert inbox_fks["background_job_id"] == "SET NULL"
    provenance_source_indexes = {
        index["name"]: index
        for index in inspector.get_indexes("model_provenance_sources")
    }
    assert provenance_source_indexes["ix_provenance_source_provider_item"][
        "column_names"
    ] == [
        "provider",
        "source_item_id",
    ]
    provenance_link_columns = {
        col["name"]: col for col in inspector.get_columns("artifact_provenance_links")
    }
    assert "import_key" in provenance_link_columns
    provenance_link_fks = {
        fk["constrained_columns"][0]: (fk.get("options") or {}).get("ondelete")
        for fk in inspector.get_foreign_keys("artifact_provenance_links")
    }
    assert provenance_link_fks == {
        "file_id": "CASCADE",
        "provenance_source_id": "CASCADE",
        "capture_id": "SET NULL",
    }


def test_bambu_identity_migration_absorbs_duplicate_pair_without_delete(
    tmp_path: Path,
) -> None:
    """A project-only/task-only pair keeps the early row and rich evidence."""

    db_path = tmp_path / "bambu-repair.sqlite"
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "e7b4c1d9a6f2")
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as connection:
            for job_id, created_at, project_id, task_id, evidence in (
                (1, "2026-08-24 00:00:00", "project-1", None, "metadata_only"),
                (2, "2026-08-24 00:00:10", None, "task-1", "gcode_archived"),
            ):
                connection.execute(
                    text(
                        "INSERT INTO print_jobs "
                        "(id, printer_id, file_id, model_id, remote_filename, state, "
                        "progress, source, external_project_id, external_task_id, "
                        "artifact_evidence, started_at, created_at, updated_at) "
                        "VALUES (:id, 1, 1, 1, 'plate.gcode', 'PRINTING', 0.5, "
                        "'external', :project_id, :task_id, :evidence, "
                        "'2026-08-24 00:00:00', :created_at, :created_at)"
                    ),
                    {
                        "id": job_id,
                        "project_id": project_id,
                        "task_id": task_id,
                        "evidence": evidence,
                        "created_at": created_at,
                    },
                )
        command.upgrade(cfg, "head")
        with engine.connect() as connection:
            rows = (
                connection.execute(
                    text(
                        "SELECT id, dedupe_absorbed_at, dedupe_survivor_id, "
                        "external_project_id, external_task_id, artifact_evidence "
                        "FROM print_jobs ORDER BY id"
                    )
                )
                .mappings()
                .all()
            )
        assert rows[0]["dedupe_absorbed_at"] is None
        assert rows[0]["external_project_id"] == "project-1"
        assert rows[0]["external_task_id"] == "task-1"
        assert rows[0]["artifact_evidence"] == "gcode_archived"
        assert rows[1]["dedupe_absorbed_at"] is not None
        assert rows[1]["dedupe_survivor_id"] == 1
        with pytest.raises(
            RuntimeError, match="cannot downgrade Bambu identity repair"
        ):
            command.downgrade(cfg, "e7b4c1d9a6f2")
        assert {
            column["name"] for column in inspect(engine).get_columns("print_jobs")
        } >= {"dedupe_absorbed_at", "dedupe_survivor_id"}
    finally:
        engine.dispose()


def test_inbox_job_set_null_migration_preserves_completed_history(
    tmp_path: Path,
) -> None:
    """Upgrade changes only the FK action and keeps terminal inbox rows."""
    url = f"sqlite:///{tmp_path / 'inbox-job-fk.sqlite'}"
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "f9a7c3e5b1d2")

    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users "
                    "(username, hashed_password, is_superuser, is_active, "
                    "created_at, updated_at) VALUES "
                    "('history-owner', 'hash', 0, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            owner_id = connection.execute(
                text("SELECT id FROM users WHERE username = 'history-owner'")
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO background_jobs "
                    "(id, visible, kind, state, status_json, replay_safe, "
                    "created_at, updated_at, finished_at) VALUES "
                    "('history-job', 1, 'pending_import', 'completed', '{}', 1, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO inbox_items "
                    "(owner_user_id, source_kind, state, manifest_json, "
                    "requested_tags_json, background_job_id, retryable, "
                    "attempt_count, created_at, updated_at) VALUES "
                    "(:owner_id, 'BROWSER', 'COMPLETED', '{}', '[]', "
                    "'history-job', 0, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"owner_id": owner_id},
            )
    finally:
        engine.dispose()

    command.upgrade(cfg, "head")
    engine = create_engine(url)
    try:
        inbox_fks = {
            fk["constrained_columns"][0]: (fk.get("options") or {}).get("ondelete")
            for fk in inspect(engine).get_foreign_keys("inbox_items")
        }
        assert inbox_fks["background_job_id"] == "SET NULL"
        with engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.execute(
                text("DELETE FROM background_jobs WHERE id = 'history-job'")
            )
            assert connection.execute(
                text(
                    "SELECT state, background_job_id FROM inbox_items "
                    "WHERE owner_user_id = :owner_id"
                ),
                {"owner_id": owner_id},
            ).one() == ("COMPLETED", None)
    finally:
        engine.dispose()


def test_bambu_identity_grouping_rejects_fast_reprints_and_transitive_chains() -> None:
    migration_path = (
        BACKEND_ROOT
        / "alembic"
        / "versions"
        / "f8a6c2d9e1b4_bambu_job_identity_repair.py"
    )
    spec = spec_from_file_location("bambu_identity_repair", migration_path)
    assert spec is not None and spec.loader is not None
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)

    def row(
        job_id: int,
        *,
        project: str | None = None,
        task: str | None = None,
        started: str = "2026-08-24 00:00:00",
        finished: str | None = None,
        created: str = "2026-08-24 00:00:00",
    ) -> dict[str, object]:
        return {
            "id": job_id,
            "printer_id": 1,
            "remote_filename": "plate.gcode",
            "external_project_id": project,
            "external_task_id": task,
            "external_subtask_id": None,
            "provider_job_id": None,
            "started_at": started,
            "finished_at": finished,
            "created_at": created,
        }

    # A completed print followed by a fast reprint has disjoint lifecycles,
    # despite sharing a filename and being only seconds apart.
    fast_reprints = [
        row(1, project="project-old", finished="2026-08-24 00:00:08"),
        row(
            2,
            task="task-new",
            started="2026-08-24 00:00:10",
            created="2026-08-24 00:00:10",
        ),
    ]
    assert migration._groups(fast_reprints) == []

    # A(project), B(project+task), C(task) must not collapse transitively.
    chain = [
        row(1, project="project-chain"),
        row(2, project="project-chain", task="task-chain"),
        row(3, task="task-chain"),
    ]
    groups = migration._groups(chain)
    assert [[entry["id"] for entry in group] for group in groups] == [[1, 2]]

    # A reused project id in a later, completed lifecycle is a reprint, not a
    # duplicate callback. Shared identity must be paired with time evidence.
    separated_reprints = [
        row(
            10,
            project="project-reused",
            started="2026-08-24 01:00:00",
            finished="2026-08-24 01:05:00",
        ),
        row(
            11,
            project="project-reused",
            started="2026-08-24 02:00:00",
            finished="2026-08-24 02:05:00",
            created="2026-08-24 02:00:00",
        ),
    ]
    assert migration._groups(separated_reprints) == []

    # The same token on separate printers is not evidence of one print.
    multi_printer = [
        row(20, project="shared-project", started="2026-08-24 03:00:00"),
        {
            **row(21, project="shared-project", started="2026-08-24 03:00:00"),
            "printer_id": 2,
        },
    ]
    assert migration._groups(multi_printer) == []


def test_runner_fresh_db_migrates_to_head_and_stamps(tmp_path: Path) -> None:
    url = _url(tmp_path)
    migrate_mod.run_migrations(url)

    assert _current(url) == _head_revision()
    assert {"users", "models", "files", "alembic_version"} <= _table_names(url)


def test_runner_is_idempotent_noop_at_head(tmp_path: Path) -> None:
    url = _url(tmp_path)
    migrate_mod.run_migrations(url)
    head = _current(url)
    migrate_mod.run_migrations(url)  # must not raise, must not move off head
    assert _current(url) == head == _head_revision()


def test_runner_orphan_db_is_adopted_without_table_exists_error(tmp_path: Path) -> None:
    # The issue #29 state: full schema built by create_all(), no alembic_version.
    url = _url(tmp_path)
    engine = create_engine(url)
    SQLModel.metadata.create_all(engine)
    engine.dispose()

    assert _current(url) is None
    assert "users" in _table_names(url) and "alembic_version" not in _table_names(url)

    # A naive `upgrade head` would hit "table already exists"; the runner must
    # stamp first and finish cleanly at head.
    migrate_mod.run_migrations(url)
    assert _current(url) == _head_revision()


def test_runner_rejects_incomplete_orphan_schema_without_stamping(
    tmp_path: Path,
) -> None:
    url = _url(tmp_path, "incomplete-orphan.sqlite")
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("CREATE TABLE models (id INTEGER PRIMARY KEY, name TEXT)")
            )
    finally:
        engine.dispose()

    with pytest.raises(migrate_mod.OrphanSchemaError, match="does not match"):
        migrate_mod.run_migrations(url)

    assert _current(url) is None
    assert "alembic_version" not in _table_names(url)


@pytest.mark.parametrize(
    ("table_name", "old", "new"),
    [
        ("users", "username VARCHAR(128) NOT NULL", "username INTEGER NOT NULL"),
        (
            "users",
            "hashed_password VARCHAR(255) NOT NULL",
            "hashed_password VARCHAR(255)",
        ),
        (
            "users",
            "oidc_managed BOOLEAN DEFAULT '0'",
            "oidc_managed BOOLEAN DEFAULT '1'",
        ),
        (
            "printer_permissions",
            "FOREIGN KEY(user_id) REFERENCES users (id)",
            "FOREIGN KEY(user_id) REFERENCES collections (id)",
        ),
    ],
)
def test_runner_rejects_structurally_divergent_orphan_schema(
    tmp_path: Path,
    table_name: str,
    old: str,
    new: str,
) -> None:
    url = _url(tmp_path, f"divergent-{table_name}-{abs(hash(old))}.sqlite")
    engine = create_engine(url)
    SQLModel.metadata.create_all(engine)
    engine.dispose()
    _rewrite_sqlite_table_definition(url, table_name, old, new)

    with pytest.raises(migrate_mod.OrphanSchemaError, match="does not match"):
        migrate_mod.run_migrations(url)

    assert _current(url) is None


def test_runner_rejects_orphan_with_wrong_partial_index_predicate(
    tmp_path: Path,
) -> None:
    url = _url(tmp_path, "divergent-partial-index.sqlite")
    engine = create_engine(url)
    SQLModel.metadata.create_all(engine)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("DROP INDEX uq_printers_live_default")
            connection.exec_driver_sql(
                "CREATE UNIQUE INDEX uq_printers_live_default "
                "ON printers (is_default) WHERE is_default = 0"
            )
    finally:
        engine.dispose()

    with pytest.raises(migrate_mod.OrphanSchemaError, match="does not match"):
        migrate_mod.run_migrations(url)

    assert _current(url) is None
