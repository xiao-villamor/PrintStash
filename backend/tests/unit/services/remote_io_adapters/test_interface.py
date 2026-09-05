"""The remote interface owns stream lifetimes without inventing Vault guarantees."""

from contextlib import contextmanager
from io import BytesIO
from types import SimpleNamespace

import pytest

from app.services.remote_io_adapters import remote_io_for
from app.services.storage_backend import StorageBackend, StorageConfigurationError
from app.services.storage_opendal import OpenDALStorageBackend
from app.services.storage_ownership import provider_ref_for_backend
from app.services.storage_providers import TransportKind, TransportSpec


class Operator:
    def __init__(self, *, conditional=True):
        self.conditional = conditional
        self.objects = {}
        self.reader = None
        self.observed = 0
        self.list_closed = False

    def capability(self):
        return SimpleNamespace(
            read=True,
            write=True,
            list=True,
            write_with_if_not_exists=self.conditional,
            delete_with_version=False,
        )

    def exists(self, key):
        return key in self.objects

    def write(self, key, data):
        self.objects[key] = bytes(data)

    def stat(self, key):
        return SimpleNamespace(content_length=len(self.objects[key]))

    @contextmanager
    def open(self, key, mode, **kwargs):
        self.reader = BytesIO(self.objects[key])
        with self.reader:
            yield self.reader

    def list(self, relative):
        try:
            for index in range(100_000):
                self.observed += 1
                yield SimpleNamespace(
                    path=f"{relative}{index}.gcode",
                    metadata=SimpleNamespace(content_length=1, is_dir=False),
                )
        finally:
            self.list_closed = True


def _spec(kind):
    return TransportSpec(
        kind=kind,
        provider=kind.value,
        namespace="existing-namespace",
        options={
            "root": "existing-root",
            "endpoint_url": "https://dav.example.test",
            "bucket": "backups",
        },
    )


class TestRemoteInterface:
    @pytest.mark.parametrize(
        "kind,conditional,managed,atomic",
        [
            (TransportKind.GDRIVE, True, False, False),
            (TransportKind.SFTP, True, True, False),
            (TransportKind.S3, True, True, True),
            (TransportKind.S3, False, False, False),
            (TransportKind.WEBDAV, True, True, True),
        ],
    )
    def test_managed_creation_requires_an_explicit_transport_extension(
        self, kind, conditional, managed, atomic
    ):
        remote = remote_io_for(_spec(kind), operator=Operator(conditional=conditional))
        assert not isinstance(remote, StorageBackend)
        assert (remote.managed_creation is not None) is managed
        assert remote.operations.atomic_visibility is atomic
        assert remote.exact_deletion is None

    def test_drive_replica_publication_does_not_grant_managed_creation(self):
        operator = Operator()
        remote = remote_io_for(_spec(TransportKind.GDRIVE), operator=operator)
        receipt = remote.publish_replica(
            BytesIO(b"archive"), remote.source_key("archive.tar.gz")
        )
        assert operator.objects == {"archive.tar.gz": b"archive"}
        assert receipt.size == 7
        assert remote.managed_creation is None
        vault = OpenDALStorageBackend(_spec(TransportKind.GDRIVE), operator=operator)
        with pytest.raises(
            StorageConfigurationError, match="atomic_create_not_supported"
        ):
            vault.create_stream(BytesIO(b"unsafe"), vault.source_key("managed.stl"))
        assert "managed.stl" not in operator.objects

    def test_abandoned_directory_does_not_drain_the_transport(self):
        operator = Operator()
        remote = remote_io_for(_spec(TransportKind.S3), operator=operator)
        with remote.iter_directory("models") as entries:
            assert next(entries).key == "models/0.gcode"
        assert operator.observed == 1
        assert operator.list_closed

    def test_reader_cleanup_preserves_the_consumers_exception(self):
        operator = Operator()
        operator.objects["archive"] = b"abc"
        remote = remote_io_for(_spec(TransportKind.S3), operator=operator)
        expected = ValueError("consumer failed")
        with pytest.raises(ValueError) as failure:
            with remote.open_reader(remote.source_key("archive")) as reader:
                assert reader.read(1) == b"a"
                raise expected
        assert failure.value is expected
        assert operator.reader.closed

    @pytest.mark.parametrize(
        "kind",
        [
            TransportKind.S3,
            TransportKind.WEBDAV,
            TransportKind.SFTP,
            TransportKind.GDRIVE,
        ],
        ids=["s3-identity", "webdav-identity", "sftp-identity", "drive-identity"],
    )
    def test_transport_extraction_preserves_locator_identity(self, kind):
        operator = Operator()
        remote = remote_io_for(_spec(kind), operator=operator)
        vault = OpenDALStorageBackend(_spec(kind), operator=operator)
        assert remote.source_namespace == vault.source_namespace
        assert remote.source_key("archive") == vault.source_key("archive")
        assert provider_ref_for_backend(
            remote, namespace=remote.source_namespace
        ) == provider_ref_for_backend(vault, namespace=vault.source_namespace)
