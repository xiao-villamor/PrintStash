"""Large remote directories are observed once and resumed from durable inventory."""

from types import SimpleNamespace

from app.services.library_source import RemoteLibrarySource
from app.services.remote_io_adapters import remote_io_for
from app.services.storage_providers import TransportKind, TransportSpec


class _DirectoryOperator:
    def __init__(self, count):
        self.count = count
        self.requests = 0
        self.observations = 0
        self.closed = 0

    def list(self, directory):
        self.requests += 1
        try:
            for index in range(self.count):
                self.observations += 1
                yield SimpleNamespace(
                    path=f"{directory}{index:06}.gcode",
                    metadata=SimpleNamespace(content_length=10, is_dir=False),
                )
        finally:
            self.closed += 1


def _source(operator):
    return RemoteLibrarySource(
        remote_io_for(
            TransportSpec(
                kind=TransportKind.S3,
                provider="s3",
                namespace="s3/discovery",
                options={
                    "endpoint_url": "https://unit.test",
                    "root": "discovery",
                    "bucket": "library",
                },
            ),
            operator=operator,
        )
    )


class TestDurableDirectoryInventory:
    def test_large_directory_is_processed_in_bounded_pages(self, db_session):
        operator = _DirectoryOperator(10_005)
        source = _source(operator)
        keys = []
        cursor = None
        for _ in range(20):
            page = source.list_page("models", cursor=cursor, limit=1000)
            assert len(page.entries) <= 1000
            keys.extend(entry.key for entry in page.entries)
            cursor = page.next_cursor
            if page.complete:
                break
        assert page.complete is True
        assert keys == [f"models/{index:06}.gcode" for index in range(10_005)]
        assert operator.requests == 1
        assert operator.observations == 10_005
        assert operator.closed == 1

    def test_new_source_resumes_without_rereading_a_completed_directory(
        self, db_session
    ):
        operator = _DirectoryOperator(2001)
        first = _source(operator).list_page("models", cursor=None, limit=1000)
        second = _source(operator).list_page(
            "models", cursor=first.next_cursor, limit=1000
        )
        assert first.entries[-1].key == "models/000999.gcode"
        assert second.entries[0].key == "models/001000.gcode"
        assert operator.requests == 1

    def test_interrupted_directory_restarts_without_stale_partial_entries(
        self, db_session
    ):
        import pytest

        from app.services.library_source import LibrarySourceError

        class Interrupted(_DirectoryOperator):
            fail = True

            def list(self, directory):
                for index, entry in enumerate(super().list(directory)):
                    if self.fail and index == 1100:
                        raise OSError("listing interrupted")
                    yield entry

        operator = Interrupted(2000)
        with pytest.raises(LibrarySourceError) as failure:
            _source(operator).list_page("models", cursor=None, limit=1000)
        cursor = failure.value.discovery_cursor
        operator.fail = False
        operator.count = 999
        page = _source(operator).list_page("models", cursor=cursor, limit=1000)
        assert page.complete is True
        assert len(page.entries) == 999
        assert page.entries[-1].key == "models/000998.gcode"
        assert operator.requests == 2

    def test_cursor_cannot_move_to_another_prefix(self, db_session):
        import pytest

        from app.services.library_source import LibrarySourceError

        operator = _DirectoryOperator(1001)
        first = _source(operator).list_page("models", cursor=None, limit=1000)
        with pytest.raises(
            LibrarySourceError, match="library_source_cursor_target_changed"
        ):
            _source(operator).list_page("other", cursor=first.next_cursor, limit=1000)
        assert operator.requests == 1

    def test_inventory_rejects_a_recursive_entry_disguised_as_a_child(self, db_session):
        import pytest

        from app.services.library_source import LibrarySourceError

        class Recursive(_DirectoryOperator):
            def list(self, directory):
                yield SimpleNamespace(
                    path="models/../models/",
                    metadata=SimpleNamespace(content_length=0, is_dir=True),
                )

        with pytest.raises(
            LibrarySourceError, match="library_source_directory_entry_invalid"
        ):
            _source(Recursive(0)).list_page("models", cursor=None, limit=1000)

    def test_retiring_completed_inventory_releases_all_observations(self, db_session):
        from sqlmodel import select

        from app.db.models import (
            RemoteDiscoveryDirectory,
            RemoteDiscoveryEntry,
            RemoteDiscoveryInventory,
        )
        from app.services.remote_discovery import retire_inventory

        page = _source(_DirectoryOperator(10)).list_page(
            "models", cursor=None, limit=1000
        )
        retire_inventory(page.inventory_id)
        assert db_session.exec(select(RemoteDiscoveryEntry)).all() == []
        assert db_session.exec(select(RemoteDiscoveryDirectory)).all() == []
        assert db_session.exec(select(RemoteDiscoveryInventory)).all() == []

    def test_expired_inventory_cannot_be_resumed(self, db_session):
        from datetime import timedelta

        import pytest

        from app.core.time import utcnow
        from app.db.models import RemoteDiscoveryInventory
        from app.services.library_source import LibrarySourceError

        source = _source(_DirectoryOperator(1001))
        first = source.list_page("models", cursor=None, limit=1000)
        inventory = db_session.get(RemoteDiscoveryInventory, first.inventory_id)
        inventory.updated_at = utcnow() - timedelta(days=31)
        db_session.add(inventory)
        db_session.commit()
        source.list_page("models", cursor=None, limit=1000)
        with pytest.raises(LibrarySourceError, match="cursor_target_changed"):
            source.list_page("models", cursor=first.next_cursor, limit=1000)

    def test_completed_parent_survives_an_interrupted_child(self, db_session):
        from contextlib import contextmanager

        import pytest

        from app.services.library_source import LibrarySourceError
        from app.services.remote_io import RemoteEntry

        calls = []
        failed = False

        @contextmanager
        def listing(directory):
            nonlocal failed
            calls.append(directory)

            def entries():
                nonlocal failed
                if directory == "models":
                    yield RemoteEntry("models/child", 0, True)
                    yield RemoteEntry("models/root.gcode", 6, False)
                elif not failed:
                    failed = True
                    raise OSError("interrupted child")
                else:
                    yield RemoteEntry("models/child/a.gcode", 6, False)

            yield entries()

        backend = SimpleNamespace(backend_name="tree-inventory", iter_directory=listing)
        source = RemoteLibrarySource(backend)
        with pytest.raises(LibrarySourceError) as error:
            source.list_page("models", cursor=None, limit=1000)
        page = source.list_page(
            "models", cursor=error.value.discovery_cursor, limit=1000
        )
        assert {entry.key for entry in page.entries} == {
            "models/root.gcode",
            "models/child/a.gcode",
        }
        assert calls == ["models", "models/child", "models/child"]
        assert page.complete is True

    def test_cursor_cannot_adopt_another_target(self, db_session):
        import pytest

        from app.services.library_source import LibrarySourceError

        operator = _DirectoryOperator(1001)
        first = _source(operator).list_page("models", cursor=None, limit=1000)
        other = RemoteLibrarySource(SimpleNamespace(backend_name="different-target"))
        with pytest.raises(LibrarySourceError, match="cursor_target_changed"):
            other.list_page("models", cursor=first.next_cursor, limit=1000)
        assert operator.requests == 1

    def test_malformed_cursor_offsets_fail_before_listing(self, db_session):
        import json

        import pytest

        from app.services.library_source import LibrarySourceError

        operator = _DirectoryOperator(1)
        for offset in (-1, True, "0", None):
            cursor = json.dumps({"v": 1, "inventory": "missing", "after": offset})
            with pytest.raises(LibrarySourceError, match="cursor_invalid"):
                _source(operator).list_page("models", cursor=cursor, limit=1000)
        assert operator.requests == 0
