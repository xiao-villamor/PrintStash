"""Storage independence cannot be manufactured by changing a locator or role."""

import pytest

from app.services.storage_identity import (
    StorageTargetIdentity,
    normalized_endpoint,
    s3_target,
    shares_storage,
    target_for_transport,
)


class TestTargetIdentity:
    @pytest.mark.parametrize(
        "endpoint",
        [
            "https://s3.amazonaws.com",
            "https://s3.us-east-1.amazonaws.com",
            "https://bucket.s3.us-east-1.amazonaws.com",
        ],
    )
    def test_aws_addressing_aliases_identify_the_same_bucket(
        self, endpoint: str
    ) -> None:
        assert (
            s3_target(endpoint=endpoint, bucket="bucket").target_ref
            == s3_target(endpoint="", bucket="bucket").target_ref
        )

    @pytest.mark.parametrize(
        "changes",
        [
            {"root": "elsewhere"},
            {"access_key": "other", "secret_key": "replacement"},
            {"addressing_style": "virtual"},
            {"region": "different"},
        ],
    )
    def test_profile_options_do_not_establish_independence(self, changes: dict) -> None:
        options = {
            "endpoint_url": "https://store.example.test",
            "bucket": "shared",
            "root": "vault",
            "access_key": "key",
            "secret_key": "secret",
            "addressing_style": "path",
        }
        first = target_for_transport("s3", options)
        second = target_for_transport("s3", options | changes)

        assert first is not None and second is not None
        assert first.target_ref == second.target_ref
        assert shares_storage(first, second)
        assert "secret" not in first.model_dump_json()

    @pytest.mark.parametrize(
        "endpoint,domain",
        [
            ("https://s3.us-west-004.backblazeb2.com", "provider:backblaze"),
            ("https://s3.eu-central-1.wasabisys.com", "provider:wasabi"),
            ("https://account.r2.cloudflarestorage.com", "provider:cloudflare"),
            ("https://s3.amazonaws.com.evil.test", None),
            ("https://custom.example.test", None),
        ],
    )
    def test_provider_domain_requires_a_recognized_endpoint(
        self, endpoint: str, domain: str | None
    ) -> None:
        assert s3_target(endpoint=endpoint, bucket="backup").provider_domain == domain

    def test_different_buckets_on_one_server_share_storage(self) -> None:
        first = s3_target(endpoint="https://nas.example.test:9000", bucket="vault")
        second = s3_target(endpoint="http://nas.example.test:9001", bucket="backup")

        assert first.target_ref != second.target_ref
        assert shares_storage(first, second)

    def test_different_provider_accounts_share_a_conservative_domain(self) -> None:
        first = s3_target(
            endpoint="https://one.r2.cloudflarestorage.com", bucket="vault"
        )
        second = s3_target(
            endpoint="https://two.r2.cloudflarestorage.com", bucket="backup"
        )

        assert shares_storage(first, second)

    @pytest.mark.parametrize(
        "host",
        [
            "localhost",
            "127.0.0.1",
            "127.0.0.2",
            "[::1]",
            "[::ffff:127.0.0.1]",
            "backup.localhost",
        ],
    )
    def test_loopback_storage_shares_the_local_installation(self, host: str) -> None:
        local = StorageTargetIdentity(transport="local", endpoint="installation-id")
        remote = s3_target(endpoint=f"http://{host}:9000", bucket="backup")

        assert shares_storage(local, remote)
        assert shares_storage(remote, local)

    @pytest.mark.parametrize(
        "endpoint",
        [
            "https://name:secret@host.test",
            "https://host.test?token=secret",
            "https://host.test#secret",
            "file:///path",
            "missing-scheme",
        ],
    )
    def test_rejects_ambiguous_or_secret_bearing_endpoints(self, endpoint: str) -> None:
        with pytest.raises(ValueError, match="storage_target_endpoint_invalid"):
            normalized_endpoint(endpoint)

    def test_normalizes_transport_endpoint_spelling(self) -> None:
        assert (
            normalized_endpoint("HTTPS://STORE.EXAMPLE.TEST.:443/")
            == "https://store.example.test"
        )
        target = target_for_transport("sftp", {"host": "::1", "port": 22})
        assert target is not None and target.endpoint == "sftp://[::1]"

    def test_keeps_unsupported_identity_unknown(self) -> None:
        assert target_for_transport("other", {}) is None

    @pytest.mark.parametrize(
        "kind,options,domain",
        [
            ("gdrive", {"root": "archive"}, "provider:google"),
            ("webdav", {"endpoint_url": "https://dav.example.test/account"}, None),
            (
                "sftp",
                {"host": "ssh.example.test", "port": 2222, "root": "archive"},
                None,
            ),
        ],
    )
    def test_remote_transports_preserve_conservative_identity(
        self, kind: str, options: dict, domain: str | None
    ) -> None:
        target = target_for_transport(kind, options)

        assert target is not None and target.transport == kind
        assert target.provider_domain == domain
        assert "archive" not in target.model_dump_json()
