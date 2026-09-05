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
