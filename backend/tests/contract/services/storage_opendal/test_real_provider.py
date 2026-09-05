"""Behavioral contracts for the pinned Nextcloud and OpenSSH adapters.

These cases instantiate the production ``OpenDALStorageBackend`` and exercise
publication, collision, readback, and cleanup semantics.  Container startup is
lazy and fails the selected contract lane when Docker or a provider prerequisite
is unavailable; no endpoint-only smoke assertion can make a contract green.
"""

from __future__ import annotations

import pytest

from app.services.library_source import OpenDalLibrarySource
from app.services.storage_backend import (
    StorageCollisionError,
    StorageConfigurationError,
)
from app.services.storage_opendal import OpenDALStorageBackend
from app.services.storage_providers import TransportKind, TransportSpec
from tests.containers import nextcloud_endpoint, openssh_endpoint

pytestmark = [pytest.mark.contract, pytest.mark.remote_storage]


class TestOpenDALStorageBackend:
    def test_nextcloud_preserves_the_original_on_duplicate_publication(self) -> None:
        endpoint = nextcloud_endpoint()
        backend = OpenDALStorageBackend(
            TransportSpec(
                kind=TransportKind.WEBDAV,
                provider="nextcloud",
                namespace="contract-nextcloud",
                options={
                    "endpoint_url": f"{endpoint}/remote.php/dav/files/admin",
                    "root": "contract-nextcloud",
                    "username": "admin",
                    "password": "contract-only",
                },
            )
        )
        backend.ensure_setup()
        key = "contract-nextcloud/nested/unicode-✓.stl"
        first = b"first bytes"
        backend.create_bytes(first, key)

        with pytest.raises(StorageCollisionError):
            backend.create_bytes(b"replacement bytes", key)

        assert backend.read_bytes(key) == first
        assert backend.stat_size(key) == len(first)
        assert key in list(backend.list_prefix("contract-nextcloud/nested"))

    def test_openssh_rejects_duplicate_publication(self) -> None:
        host, port, host_key = openssh_endpoint()
        backend = OpenDALStorageBackend(
            TransportSpec(
                kind=TransportKind.SFTP,
                provider="sftp",
                namespace="contract-sftp",
                options={
                    "host": host,
                    "port": port,
                    "username": "contract",
                    "password": "contract-only",
                    "host_key": host_key,
                    "root": "contract-sftp",
                },
            )
        )
        backend.provision_root()
        backend.ensure_setup()
        key = "contract-sftp/nested/unicode-✓.stl"
        first = b"first sftp bytes"
        backend.create_bytes(first, key)

        with pytest.raises(StorageCollisionError):
            backend.create_bytes(b"replacement bytes", key)

        assert b"".join(backend.stream_chunks(key)) == first
        assert backend.stat_size(key) == len(first)
        with pytest.raises(
            StorageConfigurationError, match="atomic_move_not_supported"
        ):
            backend.move(key, "contract-sftp/nested/moved.stl")


class TestOpenDalLibrarySource:
    @pytest.mark.critical
    @pytest.mark.parametrize("provider", ["nextcloud", "sftp"])
    def test_real_provider_content_is_materializable(self, provider: str) -> None:
        if provider == "nextcloud":
            endpoint = nextcloud_endpoint()
            spec = TransportSpec(
                kind=TransportKind.WEBDAV,
                provider="nextcloud",
                namespace="source-contract-nextcloud",
                options={
                    "endpoint_url": f"{endpoint}/remote.php/dav/files/admin",
                    "root": "source-contract-nextcloud",
                    "username": "admin",
                    "password": "contract-only",
                },
            )
        else:
            host, port, host_key = openssh_endpoint()
            spec = TransportSpec(
                kind=TransportKind.SFTP,
                provider="sftp",
                namespace="source-contract-sftp",
                options={
                    "host": host,
                    "port": port,
                    "username": "contract",
                    "password": "contract-only",
                    "host_key": host_key,
                    "root": "source-contract-sftp",
                },
            )
        backend = OpenDALStorageBackend(spec)
        if provider == "sftp":
            backend.provision_root()
        backend.ensure_setup()
        key = f"source-contract-{provider}/models/part.gcode"
        payload = b"G1 X20 Y20\n"
        backend.create_bytes(payload, key)
        source = OpenDalLibrarySource(backend)

        page = source.list_page("models", cursor=None, limit=1000)

        assert [entry.key for entry in page.entries] == ["models/part.gcode"]
        with source.materialize("models/part.gcode") as content:
            local_path = content.path
            assert local_path.read_bytes() == payload
