"""Pinned-server evidence for guarded WebDAV cleanup decisions."""

from __future__ import annotations

import time

import httpx
import pytest

from app.services.storage_opendal import OpenDALStorageBackend
from app.services.storage_providers import TransportKind, TransportSpec

pytestmark = [pytest.mark.contract, pytest.mark.remote_storage]


class TestWebDAVCleanupEvidence:
    def test_stale_conditional_delete_preserves_replacement(self, cleanup_endpoint):
        provider, root, auth, set_mtime = cleanup_endpoint
        with httpx.Client(auth=auth, timeout=30) as client:
            key = root + "/object"
            assert client.put(key, content=b"original").is_success
            set_mtime("object", 1234)
            etag = client.head(key).headers["etag"]
            assert client.put(key, content=b"replaced").is_success
            set_mtime("object", 1235)
            assert client.head(key).headers["etag"] != etag
            response = client.delete(key, headers={"If-Match": etag})
            print(provider, "stale-delete", response.status_code)
            assert response.status_code == 412
            assert client.get(key).content == b"replaced"

    def test_conditional_delete_retry_is_missing(self, cleanup_endpoint):
        provider, root, auth, set_mtime = cleanup_endpoint
        with httpx.Client(auth=auth, timeout=30) as client:
            key = root + "/object"
            assert client.put(key, content=b"original").is_success
            etag = client.head(key).headers["etag"]
            first = client.delete(key, headers={"If-Match": etag})
            second = client.delete(key, headers={"If-Match": etag})
            print(provider, "delete-retry", first.status_code, second.status_code)
            assert first.is_success
            assert second.status_code in (404, 412)
            assert client.get(key).status_code == 404

    def test_stale_quarantine_move_preserves_replacement(self, cleanup_endpoint):
        provider, root, auth, set_mtime = cleanup_endpoint
        with httpx.Client(auth=auth, timeout=30) as client:
            key, quarantine = root + "/object", root + "/quarantine"
            assert client.put(key, content=b"original").is_success
            set_mtime("object", 1234)
            etag = client.head(key).headers["etag"]
            assert client.put(key, content=b"replaced").is_success
            set_mtime("object", 1235)
            assert client.head(key).headers["etag"] != etag
            response = client.request(
                "MOVE",
                key,
                headers={"Destination": quarantine, "Overwrite": "F", "If-Match": etag},
            )
            print(provider, "stale-move", response.status_code)
            assert response.status_code == 412
            assert client.get(key).content == b"replaced"
            assert client.get(quarantine).status_code == 404

    def test_quarantine_survives_client_interruption(self, cleanup_endpoint):
        provider, root, auth, set_mtime = cleanup_endpoint
        key, quarantine = root + "/object", root + "/quarantine"
        with httpx.Client(auth=auth, timeout=30) as client:
            assert client.put(key, content=b"original").is_success
            etag = client.head(key).headers["etag"]
            moved = client.request(
                "MOVE",
                key,
                headers={"Destination": quarantine, "Overwrite": "F", "If-Match": etag},
            )
            assert moved.is_success
        # A new client models losing the first response before recording progress.
        with httpx.Client(auth=auth, timeout=30) as client:
            assert client.get(quarantine).content == b"original"
            assert client.put(key, content=b"replaced").is_success
            replay = client.request(
                "MOVE",
                key,
                headers={"Destination": quarantine, "Overwrite": "F", "If-Match": etag},
            )
            print(provider, "move-replay", replay.status_code)
            assert not replay.is_success
            assert client.get(quarantine).content == b"original"
            assert client.get(key).content == b"replaced"

    def test_quarantine_collision_preserves_winner(self, cleanup_endpoint):
        _provider, root, auth, set_mtime = cleanup_endpoint
        with httpx.Client(auth=auth, timeout=30) as client:
            key, quarantine = root + "/object", root + "/quarantine"
            assert client.put(key, content=b"original").is_success
            assert client.put(quarantine, content=b"winner").is_success
            response = client.request(
                "MOVE", key, headers={"Destination": quarantine, "Overwrite": "F"}
            )
            assert response.status_code == 412
            assert client.get(quarantine).content == b"winner"
            assert client.get(key).content == b"original"

    def test_lock_requires_its_owner_token(self, cleanup_endpoint):
        provider, root, auth, set_mtime = cleanup_endpoint
        with httpx.Client(auth=auth, timeout=30) as client:
            key = root + "/object"
            assert client.put(key, content=b"original").is_success
            lock = client.request(
                "LOCK",
                key,
                headers={"Timeout": "Second-60", "Content-Type": "application/xml"},
                content=b'<d:lockinfo xmlns:d="DAV:"><d:lockscope><d:exclusive/></d:lockscope><d:locktype><d:write/></d:locktype><d:owner>PrintStash evidence</d:owner></d:lockinfo>',
            )
            print(provider, "lock", lock.status_code, lock.headers.get("lock-token"))
            if provider == "nextcloud":
                assert lock.status_code in (405, 501)
                assert client.put(key, content=b"replaced").is_success
                return
            assert lock.is_success
            token = lock.headers["lock-token"]
            try:
                assert client.put(key, content=b"replaced").status_code == 423
                assert client.delete(key).status_code == 423
                assert client.get(key).content == b"original"
                wrong = client.request(
                    "UNLOCK", key, headers={"Lock-Token": "<opaquelocktoken:wrong>"}
                )
                assert not wrong.is_success
            finally:
                assert client.request(
                    "UNLOCK", key, headers={"Lock-Token": token}
                ).is_success
            assert client.put(key, content=b"replaced").is_success

    def test_same_content_replacement_follows_validator_equality(
        self, cleanup_endpoint
    ):
        provider, root, auth, set_mtime = cleanup_endpoint
        with httpx.Client(auth=auth, timeout=30) as client:
            key = root + "/object"
            assert client.put(key, content=b"original").is_success
            set_mtime("object", 1234)
            original = client.head(key).headers["etag"]
            assert client.put(key, content=b"original").is_success
            set_mtime("object", 1234)
            replacement = client.head(key).headers["etag"]
            print(provider, "identity-reuse", original, replacement)
            assert client.get(key).content == b"original"
            if provider == "wsgidav":
                assert original == replacement
            # Nextcloud may reuse an ETag for identical content. The actual
            # precondition protects the validator, not publication ownership.
            deleted = client.delete(key, headers={"If-Match": original})
            if original == replacement:
                assert deleted.status_code == 204
                assert client.get(key).status_code == 404
            else:
                assert deleted.status_code == 412
                assert client.get(key).content == b"original"

    def test_production_cleanup_retains_replacement(self, cleanup_endpoint):
        provider, root, auth, set_mtime = cleanup_endpoint
        backend = OpenDALStorageBackend(
            TransportSpec(
                kind=TransportKind.WEBDAV,
                provider=provider if provider == "nextcloud" else "webdav",
                namespace="vault",
                options={
                    "endpoint_url": root,
                    "root": "vault",
                    "username": auth[0],
                    "password": auth[1],
                },
            )
        )
        backend.ensure_setup()
        key = backend.thumbnail_key(1)
        receipt = backend.create_bytes(b"original", key)
        with httpx.Client(auth=auth, timeout=30) as client:
            assert client.put(root + "/" + key, content=b"replaced").is_success
        assert backend.rollback_create(receipt) is False
        assert (
            backend.reclaim_unverified(key, expected_size=8, expected_etag=receipt.etag)
            is False
        )
        assert backend.read_bytes(key) == b"replaced"
        assert backend.capabilities.verified_delete is False

    @pytest.mark.parametrize("cleanup_endpoint", ["wsgidav"], indirect=True)
    @pytest.mark.parametrize("operation", ["DELETE", "MOVE"])
    def test_colliding_etag_cannot_guard_replacement(self, cleanup_endpoint, operation):
        _provider, root, auth, set_mtime = cleanup_endpoint
        with httpx.Client(auth=auth, timeout=30) as client:
            key, quarantine = root + "/object", root + "/quarantine"
            assert client.put(key, content=b"original").is_success
            set_mtime("object", 1234)
            etag = client.head(key).headers["etag"]
            assert client.put(key, content=b"replaced").is_success
            set_mtime("object", 1234)
            assert client.get(key).content == b"replaced"
            assert client.head(key).headers["etag"] == etag
            headers = {"If-Match": etag}
            if operation == "MOVE":
                headers.update({"Destination": quarantine, "Overwrite": "F"})
            response = client.request(operation, key, headers=headers)
            assert response.status_code == (204 if operation == "DELETE" else 201)
            assert client.get(key).status_code == 404
            if operation == "MOVE":
                assert client.get(quarantine).content == b"replaced"

    @pytest.mark.parametrize("cleanup_endpoint", ["wsgidav"], indirect=True)
    def test_expired_lock_cannot_authorize_cleanup(self, cleanup_endpoint):
        _provider, root, auth, _set_mtime = cleanup_endpoint
        key = root + "/object"
        with httpx.Client(auth=auth, timeout=30) as client:
            assert client.put(key, content=b"original").is_success
            lock = client.request(
                "LOCK",
                key,
                headers={"Timeout": "Second-1", "Content-Type": "application/xml"},
                content=b'<d:lockinfo xmlns:d="DAV:"><d:lockscope><d:exclusive/></d:lockscope><d:locktype><d:write/></d:locktype><d:owner>PrintStash evidence</d:owner></d:lockinfo>',
            )
            assert lock.is_success
            from xml.etree import ElementTree

            assert ElementTree.fromstring(lock.content).findtext(
                ".//{DAV:}timeout"
            ) in ("Second-0", "Second-1")
            token = lock.headers["lock-token"].strip("<>")
        # The owner disappears without UNLOCK; a later recovery cannot use the
        # expired lease to authorize removal of a new writer's object.
        time.sleep(1.2)
        with httpx.Client(auth=auth, timeout=30) as client:
            assert client.put(key, content=b"replaced").is_success
            assert client.delete(key, headers={"If": f"(<{token}>)"}).status_code == 412
            assert client.get(key).content == b"replaced"
