"""S3 listing precision must not reject unchanged remote library content.

S3 XML lists may retain fractional seconds while HTTP HEAD dates do not.
Content identity and concurrent-replacement checks must remain effective.
"""

from datetime import datetime, timedelta, timezone
from io import BytesIO
from types import SimpleNamespace

import pytest

from app.modules.sources.contracts import LibrarySourceError, SourceEntry
from app.modules.sources.library_source import RemoteLibrarySource
from app.modules.storage.remote_io_adapters import OpenDALRemoteIO
from app.modules.storage.storage_providers import TransportKind, TransportSpec

MODIFIED = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


class _Operator:
    def __init__(self, modified):
        self.listed = SimpleNamespace(
            content_length=3,
            is_dir=False,
            last_modified=modified,
            etag='"original"',
            version="version-one",
        )
        self.head = SimpleNamespace(
            content_length=3,
            last_modified=MODIFIED,
            etag='"original"',
            version="version-one",
        )
        self.change_during_read = False

    def list(self, directory):
        return iter([SimpleNamespace(path="model.stl", metadata=self.listed)])

    def exists(self, key):
        return True

    def stat(self, key):
        return self.head

    def capability(self):
        return SimpleNamespace(read_with_if_match=True)

    def open(self, key, mode, **options):
        assert options == {"if_match": '"original"'}
        if self.change_during_read:
            self.head.etag = '"replacement"'
        return BytesIO(b"abc")


@pytest.fixture
def source():
    operator = _Operator(MODIFIED.replace(microsecond=877000))
    backend = OpenDALRemoteIO(
        TransportSpec(
            kind=TransportKind.S3,
            provider="s3",
            namespace="bucket/library",
            options={},
        ),
        operator=operator,
    )
    return RemoteLibrarySource(backend), operator


def _observation(source):
    with source.backend.iter_directory("") as entries:
        entry = next(entries)
    return SourceEntry(
        entry.key, entry.size, entry.modified_at, entry.etag, entry.version_id
    )


class TestS3ListingPrecision:
    @pytest.mark.parametrize(
        "microsecond", [0, 877000, 999999], ids=["whole", "millisecond", "microsecond"]
    )
    def test_materializes_unchanged_listed_content(self, source, microsecond):
        remote, operator = source
        operator.listed.last_modified = MODIFIED.replace(microsecond=microsecond)
        observation = _observation(remote)

        with remote.materialize(observation.key, expected=observation) as content:
            assert content.path.read_bytes() == b"abc"

    def test_preserves_absent_listing_timestamp(self, source):
        remote, operator = source
        operator.listed.last_modified = None

        assert _observation(remote).modified_at is None

    @pytest.mark.parametrize(
        ("attribute", "value"),
        [
            pytest.param(
                "last_modified", MODIFIED + timedelta(seconds=1), id="timestamp"
            ),
            pytest.param("etag", '"replacement"', id="etag"),
            pytest.param("version", "version-two", id="version"),
            pytest.param("content_length", 4, id="size"),
        ],
    )
    def test_rejects_replaced_listed_content(self, source, attribute, value):
        remote, operator = source
        observation = _observation(remote)
        setattr(operator.head, attribute, value)

        with pytest.raises(LibrarySourceError, match="library_source_changed"):
            with remote.materialize(observation.key, expected=observation):
                pytest.fail("replaced content was accepted")

    def test_rejects_replacement_during_download(self, source):
        remote, operator = source
        observation = _observation(remote)
        operator.change_during_read = True

        with pytest.raises(LibrarySourceError, match="library_source_changed"):
            with remote.materialize(observation.key, expected=observation):
                pytest.fail("replacement during download was accepted")


class TestOtherListingPrecision:
    def test_preserves_google_drive_fractional_timestamp(self):
        modified = MODIFIED.replace(microsecond=877000)
        backend = OpenDALRemoteIO(
            TransportSpec(
                kind=TransportKind.GDRIVE,
                provider="gdrive",
                namespace="drive/library",
                options={},
            ),
            operator=_Operator(modified),
        )

        with backend.iter_directory("") as entries:
            assert next(entries).modified_at == modified
