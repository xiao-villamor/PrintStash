"""Benchmark inspection uses isolated real databases for both supported dialects."""

from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import OperationalError

from app.db.url import normalize_database_url
from scripts.bench_database import (
    database_record,
    disposable_database,
    pending_enrichment,
)
from tests.containers import postgres_url


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)])
def database(request, tmp_path):
    with disposable_database(tmp_path, request.param) as engine:
        yield engine


class TestEnrichmentInspection:
    def test_counts_only_background_analysis(self, database):
        with database.begin() as db:
            db.exec_driver_sql(
                "CREATE TABLE artifact_analysis_generations "
                "(state TEXT, processing_policy TEXT)"
            )
            db.exec_driver_sql(
                "INSERT INTO artifact_analysis_generations VALUES "
                "('pending', 'background'), ('running', 'foreground'), "
                "('failed', 'background'), ('failed', 'foreground')"
            )
        with database.connect() as db:
            assert pending_enrichment(db) == (1, {"artifact_analysis_generations": 1})

    @pytest.mark.parametrize(
        ("file_type", "reason", "source_hash", "failures"),
        [
            ("GCODE", "no_embedded_thumbnail", "source", 0),
            ("GCODE", "no_embedded_thumbnail", "replaced-source", 1),
            ("STL", "no_embedded_thumbnail", "source", 1),
            ("GCODE", "enrichment_failed", "source", 1),
            ("GCODE", None, "source", 1),
        ],
        ids=[
            "gcode-without-preview",
            "stale-source",
            "mesh-failure",
            "unexpected-failure",
            "unknown-failure",
        ],
    )
    def test_classifies_missing_embedded_preview(
        self, database, file_type, reason, source_hash, failures
    ):
        with database.begin() as db:
            db.exec_driver_sql(
                "CREATE TABLE files (id INTEGER, file_type TEXT, sha256 TEXT)"
            )
            db.exec_driver_sql(
                "CREATE TABLE thumbnail_generations "
                "(file_id INTEGER, source_sha256 TEXT, state TEXT, processing_policy TEXT, failure_reason TEXT)"
            )
            db.execute(
                text("INSERT INTO files VALUES (1, :type, :hash)"),
                {"type": file_type, "hash": source_hash},
            )
            db.execute(
                text(
                    "INSERT INTO thumbnail_generations VALUES (1, 'source', 'failed', 'background', :reason)"
                ),
                {"reason": reason},
            )

        with database.connect() as db:
            assert pending_enrichment(db) == (0, {"thumbnail_generations": failures})

    def test_includes_similarity_only_when_requested(self, database):
        with database.begin() as db:
            db.exec_driver_sql("CREATE TABLE similarity_runs (state TEXT)")
            db.exec_driver_sql("INSERT INTO similarity_runs VALUES ('queued')")
        with database.connect() as db:
            assert pending_enrichment(db) == (0, {})
            assert pending_enrichment(db, similarity=True) == (
                1,
                {"similarity_runs": 0},
            )

    def test_counts_projection_requests_without_a_state(self, database):
        with database.begin() as db:
            db.exec_driver_sql("CREATE TABLE search_projection_requests (id INTEGER)")
            db.exec_driver_sql("INSERT INTO search_projection_requests VALUES (1), (2)")
        with database.connect() as db:
            assert pending_enrichment(db) == (2, {})

    def test_records_database_version_without_connection_details(self, database):
        with database.connect() as db:
            record = database_record(db)
        assert record["backend"] == database.dialect.name
        assert record["server_version"]
        assert set(record) == {"backend", "server_version"}


class TestDisposableDatabase:
    @pytest.mark.postgres
    def test_rejects_public_test_password(self, tmp_path):
        with disposable_database(tmp_path, "postgres") as engine:
            untrusted = create_engine(engine.url.set(password="printstash"))
            try:
                with pytest.raises(
                    OperationalError, match="password authentication failed"
                ):
                    with untrusted.connect() as db:
                        db.exec_driver_sql("SELECT 1")
            finally:
                untrusted.dispose()

    @pytest.mark.parametrize(
        "dialect", ["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)]
    )
    def test_isolates_each_run(self, tmp_path, dialect):
        first_root, second_root = tmp_path / "first", tmp_path / "second"
        first_root.mkdir()
        second_root.mkdir()
        with disposable_database(first_root, dialect) as first:
            with first.begin() as db:
                db.exec_driver_sql("CREATE TABLE sentinel (value INTEGER)")
            with disposable_database(second_root, dialect) as second:
                assert "sentinel" not in inspect(second).get_table_names()
            assert "sentinel" in inspect(first).get_table_names()

    def test_refuses_an_existing_sqlite_file(self, tmp_path):
        path = tmp_path / "vault.sqlite"
        path.write_bytes(b"existing vault")
        with pytest.raises(FileExistsError):
            with disposable_database(tmp_path, "sqlite"):
                pytest.fail("existing file was accepted")
        assert path.read_bytes() == b"existing vault"

    @pytest.mark.postgres
    def test_removes_its_postgres_database_after_failure(self, tmp_path):
        with pytest.raises(RuntimeError, match="interrupted benchmark"):
            with disposable_database(tmp_path, "postgres") as engine:
                name = engine.url.database
                raise RuntimeError("interrupted benchmark")
        admin = create_engine(normalize_database_url(postgres_url()))
        try:
            with admin.connect() as db:
                assert (
                    db.execute(
                        text("SELECT COUNT(*) FROM pg_database WHERE datname = :name"),
                        {"name": name},
                    ).scalar_one()
                    == 0
                )
        finally:
            admin.dispose()

    def test_rejects_an_unsupported_database(self, tmp_path: Path):
        with pytest.raises(ValueError, match="Unsupported benchmark database"):
            with disposable_database(tmp_path, "mysql"):
                pytest.fail("unsupported dialect was accepted")


class TestExternalPostgresServer:
    @pytest.mark.parametrize(
        "url",
        [
            "postgresql://test:secret@127.0.0.1/existing-vault",
            "sqlite:///postgres",
            "postgresql://test:secret@127.0.0.1/postgres?dbname=existing-vault",
        ],
    )
    def test_rejects_application_databases_before_connecting(self, tmp_path, url):
        with pytest.raises(
            ValueError, match="maintenance database|query overrides"
        ) as error:
            with disposable_database(tmp_path, "postgres", postgres_admin_url=url):
                pytest.fail("invalid connection was accepted")
        assert "secret" not in str(error.value)
        assert not list(tmp_path.iterdir())

    def test_rejects_server_configuration_for_sqlite(self, tmp_path):
        with pytest.raises(ValueError, match="requires --database postgres"):
            with disposable_database(
                tmp_path, "sqlite", postgres_admin_url="postgresql:///postgres"
            ):
                pytest.fail("SQLite accepted a server configuration")
        assert not list(tmp_path.iterdir())

    @pytest.mark.postgres
    def test_uses_only_a_fresh_database_on_the_supplied_server(self, tmp_path):
        from sqlalchemy.engine import make_url

        url = make_url(normalize_database_url(postgres_url())).set(database="postgres")
        admin = create_engine(url)
        try:
            with admin.connect() as db:
                before = set(
                    db.exec_driver_sql("SELECT datname FROM pg_database").scalars()
                )
            with disposable_database(
                tmp_path,
                "postgres",
                postgres_admin_url=url.render_as_string(hide_password=False),
            ) as engine:
                assert engine.url.database not in before
                with engine.begin() as db:
                    db.exec_driver_sql("CREATE TABLE sentinel (value INTEGER)")
                    db.exec_driver_sql("INSERT INTO sentinel VALUES (42)")
                with engine.connect() as db:
                    assert (
                        db.exec_driver_sql("SELECT value FROM sentinel").scalar_one()
                        == 42
                    )
            with admin.connect() as db:
                assert (
                    set(db.exec_driver_sql("SELECT datname FROM pg_database").scalars())
                    == before
                )
        finally:
            admin.dispose()
