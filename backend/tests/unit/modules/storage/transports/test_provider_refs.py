"""Remote provider refs stay byte-identical across the transport registry.

A provider ref is persisted with every owned object and backup receipt. Each
transport now computes its own locator fields, so these values (recorded
before the registry existed) pin that the move changed no durable identity.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from app.modules.storage.remote_io_adapters import remote_io_for
from app.modules.storage.storage_providers import (
    parse_provider_config,
    resolve_transport,
)
from app.modules.storage.storage_receipts import provider_ref_for_backend

CONFIGS = {
    "s3": {
        "provider": "s3",
        "bucket": "b",
        "access_key": "a",
        "secret_key": "s",
        "region": "eu-west-1",
    },
    "cloudflare_r2": {
        "provider": "cloudflare_r2",
        "bucket": "b",
        "access_key": "a",
        "secret_key": "s",
        "account_id": "acc",
    },
    "minio": {
        "provider": "minio",
        "bucket": "b",
        "access_key": "a",
        "secret_key": "s",
        "endpoint_url": "http://minio:9000",
    },
    "nextcloud": {
        "provider": "nextcloud",
        "endpoint_url": "https://cloud.example.test/",
        "username": "ann b",
        "password": "p",
        "root": "lib",
    },
    "webdav": {
        "provider": "webdav",
        "endpoint_url": "https://dav.example.test/dav",
        "username": "u",
        "password": "p",
    },
    "sftp-password": {
        "provider": "sftp",
        "host": "nas.local",
        "username": "u",
        "password": "p",
        "host_key": "nas ssh-ed25519 AAAA",
    },
    "sftp-key": {
        "provider": "hetzner_storage_box",
        "host": "u1.your-storagebox.de",
        "username": "u1",
        "private_key_path": "/k",
        "passphrase": "x",
        "host_key": "/kh",
    },
    "gdrive": {
        "provider": "gdrive",
        "client_id": "cid",
        "client_secret": "cs",
        "refresh_token": "rt",
        "root": "PS",
    },
}

RECORDED = {
    "s3": "c8efa01b0d0b4e92f5a155900420a779c89f6220bc5d8e96601b735f8f0c8fc3",
    "cloudflare_r2": "a9b2469ea0cde0ef389d0a82b823dfc5ebc3a80dcee868aa4fee1094064d96d4",
    "minio": "16d2100d720955bd74cd0af013b5d08eecd263bc935b45175306a1ce4e27ee82",
    "nextcloud": "83a4af7a50b1f4d1b5f13d9f3af1040ff1b033fe829d16e1eb808ee327116ead",
    "webdav": "8ba29d70868c18b6d1cea4442aab42479a57aeb54e083bbe58b2046ee11c2189",
    "sftp-password": "0de09351e862af98afb472e0d337fddad871fa8030612fa6d69f2464b2b3f7af",
    "sftp-key": "8925f1af63c7e3fdd7b895143cc0abdc197127ecbe1159ecb9060ee4075e6ac4",
    "gdrive": "d2abc393f97d69c7ae3bb79d97f48133c1b70f6517eeef0f5aa270f5bfb97ef2",
}


@pytest.mark.parametrize("name", sorted(CONFIGS))
def test_keeps_the_recorded_provider_ref(name: str) -> None:
    spec = resolve_transport(parse_provider_config(CONFIGS[name]))
    operator = mock.MagicMock()
    operator.capability.return_value = SimpleNamespace(read=True, write=True, list=True)
    remote = remote_io_for(spec, operator=operator)

    assert (
        provider_ref_for_backend(remote, namespace=remote.source_namespace)
        == RECORDED[name]
    )
