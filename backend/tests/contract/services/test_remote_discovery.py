"""Production factories enumerate protocol-backed directories with durable pages."""

import json
import time

import psutil
import pytest
from sqlalchemy import event
from sqlmodel import SQLModel, create_engine

from app.db.models import LibrarySourceKind, StorageConnectionPurpose
from app.db.session import SQLiteSessionFactory, _set_sqlite_pragmas
from app.services import remote_discovery
from app.services.library_source import source_from_connection
from tests.factories import build_storage_connection
from tests.fakes.discovery_http import directory_server


@pytest.fixture
def discovery_disk(tmp_path, monkeypatch):
    path = tmp_path / "inventory.sqlite"
    engine = create_engine(f"sqlite:///{path}")
    event.listen(engine, "connect", _set_sqlite_pragmas)
    SQLModel.metadata.create_all(engine)
    factory = SQLiteSessionFactory(engine)
    monkeypatch.setattr(remote_discovery, "get_session_factory", lambda: factory)
    try:
        yield path
    finally:
        engine.dispose()


class TestProtocolDirectoryInventory:
    @pytest.mark.parametrize("count", [1000, 10_000, 100_000])
    @pytest.mark.parametrize(
        "provider", ["s3", "webdav"], ids=["s3-directory", "webdav-directory"]
    )
    def test_every_eligible_key_appears_once_in_completed_inventory(
        self, db_session, provider, count, record_property, discovery_disk
    ):
        baseline_rss = psutil.Process().memory_info().rss
        with directory_server(count, webdav=provider == "webdav") as (
            endpoint,
            metrics,
        ):
            profile = build_storage_connection(
                db_session, purpose=StorageConnectionPurpose.LIBRARY
            )
            profile.kind = LibrarySourceKind(provider)
            profile.config_json = json.dumps(
                {
                    "provider": "s3",
                    "bucket": "library",
                    "endpoint_url": endpoint,
                    "root": "library",
                    "region": "us-east-1",
                    "addressing_style": "path",
                }
                if provider == "s3"
                else {
                    "provider": "webdav",
                    "endpoint_url": endpoint,
                    "root": "library",
                    "username": "contract",
                }
            )
            profile.secret_json = json.dumps(
                {"access_key": "contract", "secret_key": "contract"}
                if provider == "s3"
                else {"password": "contract"}
            )
            db_session.add(profile)
            db_session.commit()
            cursor, total, pages = None, 0, 0
            started = time.monotonic()
            requests_after_first = None
            while True:
                source = source_from_connection(profile)
                page = source.list_page("models", cursor=cursor, limit=1000)
                assert len(page.entries) <= 1000
                assert [entry.key for entry in page.entries] == [
                    f"models/{index:06}.gcode"
                    for index in range(total, total + len(page.entries))
                ]
                total += len(page.entries)
                pages += 1
                cursor = page.next_cursor
                if requests_after_first is None:
                    requests_after_first = metrics["requests"]
                assert metrics["requests"] == requests_after_first
                if page.complete:
                    break
            assert total == count
            assert metrics["requests"] == (count // 1000 if provider == "s3" else 1)
            assert metrics["peak_rss"] - baseline_rss < 64 * 1024 * 1024
            record_property(
                "discovery_benchmark",
                json.dumps(
                    {
                        "provider": provider,
                        "entries": count,
                        "pages": pages,
                        "seconds": time.monotonic() - started,
                        **{key: metrics[key] for key in metrics},
                        "peak_rss_growth": max(0, metrics["peak_rss"] - baseline_rss),
                        "database_bytes": sum(
                            path.stat().st_size
                            for path in discovery_disk.parent.glob("inventory.sqlite*")
                        ),
                        "content_temporary_bytes": 0,
                    }
                ),
            )


class TestTransportDeadline:
    @pytest.mark.parametrize(
        "provider", ["s3", "webdav"], ids=["s3-deadline", "webdav-deadline"]
    )
    def test_stalled_directory_request_respects_the_remaining_slice(
        self, db_session, provider, discovery_disk
    ):
        from app.services.library_source import LibrarySourceError
        from app.services.remote_deadline import remote_budget

        with directory_server(1, webdav=provider == "webdav", response_delay=5) as (
            endpoint,
            metrics,
        ):
            profile = build_storage_connection(
                db_session, purpose=StorageConnectionPurpose.LIBRARY
            )
            profile.kind = LibrarySourceKind(provider)
            profile.config_json = json.dumps(
                {
                    "provider": "s3",
                    "bucket": "library",
                    "endpoint_url": endpoint,
                    "root": "library",
                    "region": "us-east-1",
                    "addressing_style": "path",
                }
                if provider == "s3"
                else {
                    "provider": "webdav",
                    "endpoint_url": endpoint,
                    "root": "library",
                    "username": "contract",
                }
            )
            profile.secret_json = json.dumps(
                {"access_key": "contract", "secret_key": "contract"}
                if provider == "s3"
                else {"password": "contract"}
            )
            source = source_from_connection(profile)
            started = time.monotonic()
            with remote_budget(deadline=started + 0.3):
                with pytest.raises(
                    LibrarySourceError, match="remote_scan_slice_deadline"
                ) as failure:
                    source.list_page("models", cursor=None, limit=1000)
            assert time.monotonic() - started < 2
            assert failure.value.discovery_cursor is not None
            assert metrics["requests"] == 1


class TestSFTPDirectoryInventory:
    @pytest.mark.parametrize("count", [1000, 10_000, 100_000])
    def test_sftp_inventory_pages_without_reopening_the_directory(
        self, db_session, count, record_property, discovery_disk
    ):
        from tests.fakes.discovery_sftp import sftp_directory_server

        baseline_rss = psutil.Process().memory_info().rss
        with sftp_directory_server(count) as (port, known_host, metrics):
            profile = build_storage_connection(
                db_session, purpose=StorageConnectionPurpose.LIBRARY
            )
            profile.kind = LibrarySourceKind.SFTP
            profile.config_json = json.dumps(
                {
                    "provider": "sftp",
                    "host": "127.0.0.1",
                    "port": port,
                    "username": "printstash",
                    "host_key": known_host,
                    "root": "library",
                }
            )
            profile.secret_json = json.dumps({"password": "contract"})
            cursor, total, pages = None, 0, 0
            started = time.monotonic()
            first_requests = None
            while True:
                page = source_from_connection(profile).list_page(
                    "models", cursor=cursor, limit=1000
                )
                assert [entry.key for entry in page.entries] == [
                    f"models/{index:06}.gcode"
                    for index in range(total, total + len(page.entries))
                ]
                assert len(page.entries) <= 1000
                total += len(page.entries)
                pages += 1
                cursor = page.next_cursor
                if first_requests is None:
                    first_requests = metrics["requests"]
                assert metrics["requests"] == first_requests
                if page.complete:
                    break
            assert total == count
            assert metrics["requests"] > 0
            assert metrics["connections"] == 1
            assert metrics["peak_rss"] - baseline_rss < 64 * 1024 * 1024
            record_property(
                "discovery_benchmark",
                json.dumps(
                    {
                        "provider": "sftp",
                        "entries": count,
                        "pages": pages,
                        "seconds": time.monotonic() - started,
                        **{key: metrics[key] for key in metrics},
                        "peak_rss_growth": max(0, metrics["peak_rss"] - baseline_rss),
                        "database_bytes": sum(
                            path.stat().st_size
                            for path in discovery_disk.parent.glob("inventory.sqlite*")
                        ),
                        "content_temporary_bytes": 0,
                    }
                ),
            )

    def test_stalled_sftp_directory_respects_the_remaining_slice(
        self, db_session, discovery_disk
    ):
        from app.services.library_source import LibrarySourceError
        from app.services.remote_deadline import remote_budget
        from tests.fakes.discovery_sftp import sftp_directory_server

        with sftp_directory_server(1, response_delay=5) as (port, known_host, metrics):
            profile = build_storage_connection(
                db_session, purpose=StorageConnectionPurpose.LIBRARY
            )
            profile.kind = LibrarySourceKind.SFTP
            profile.config_json = json.dumps(
                {
                    "provider": "sftp",
                    "host": "127.0.0.1",
                    "port": port,
                    "username": "printstash",
                    "host_key": known_host,
                    "root": "library",
                }
            )
            profile.secret_json = json.dumps({"password": "contract"})
            source = source_from_connection(profile)
            started = time.monotonic()
            with remote_budget(deadline=started + 0.5):
                with pytest.raises(
                    LibrarySourceError, match="remote_scan_slice_deadline"
                ) as failure:
                    source.list_page("models", cursor=None, limit=1000)
            assert time.monotonic() - started < 2
            assert failure.value.discovery_cursor is not None
            assert metrics["connections"] == 1
