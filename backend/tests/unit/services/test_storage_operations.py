"""Availability is not evidence that a configured endpoint can delete bytes."""

import sys
from types import SimpleNamespace

import pytest

from app.services import storage_operations
from app.services.storage_backend import ObjectIdentity, StorageCapabilities
from app.services.storage_operations import (
    replica_operations,
    source_operations,
    use_availability,
    vault_operations,
)


class TestUseAvailability:
    @pytest.mark.parametrize("use", ["vault", "library", "backup"])
    def test_native_local_use_needs_no_optional_dependency(
        self, monkeypatch, use
    ) -> None:
        monkeypatch.setattr(storage_operations, "find_spec", lambda _: None)
        result = use_availability("local", use)
        assert (
            result.available and result.dependency_installed and result.service_compiled
        )
        assert not result.endpoint_proven
        assert result.reason == "storage_endpoint_probe_required"

    def test_lite_keeps_native_s3_but_disables_remote_profiles(
        self, monkeypatch
    ) -> None:
        monkeypatch.setattr(storage_operations, "find_spec", lambda _: None)
        assert use_availability("s3", "vault").available
        for transport in ("s3", "webdav", "sftp", "gdrive"):
            result = use_availability(transport, "backup")
            assert not result.available and not result.dependency_installed
            assert result.reason == "storage_dependency_missing"

    def test_installed_module_without_compiled_service_is_unavailable(
        self, monkeypatch
    ) -> None:
        monkeypatch.setattr(storage_operations, "find_spec", lambda _: object())
        monkeypatch.setattr(storage_operations, "_compiled_service", lambda _: False)
        result = use_availability("s3", "library")
        assert result.dependency_installed and result.supported
        assert (
            not result.service_compiled
            and not result.available
            and not result.endpoint_proven
        )
        assert result.reason == "storage_service_not_compiled"

    def test_sftp_uses_asyncssh_without_an_opendal_service(self, monkeypatch) -> None:
        calls = []
        monkeypatch.setattr(
            storage_operations, "find_spec", lambda name: calls.append(name) or object()
        )
        assert use_availability("sftp", "backup").available
        assert calls == ["asyncssh"]

    def test_google_drive_supports_replication_but_not_managed_vault(
        self, monkeypatch
    ) -> None:
        monkeypatch.setattr(storage_operations, "find_spec", lambda _: object())
        monkeypatch.setattr(storage_operations, "_compiled_service", lambda _: True)
        assert use_availability("gdrive", "backup").available
        vault = use_availability("gdrive", "vault")
        assert vault.dependency_installed and vault.service_compiled
        assert not vault.supported and not vault.available
        assert vault.reason == "storage_use_unsupported"

    @pytest.mark.parametrize("unsupported", [False, True])
    def test_compilation_probe_only_constructs_the_selected_service(
        self, monkeypatch, unsupported
    ) -> None:
        class Unsupported(Exception):
            pass

        calls = []

        def construct(transport, **options):
            calls.append((transport, options))
            if unsupported:
                raise Unsupported()
            return SimpleNamespace()

        monkeypatch.setitem(
            sys.modules,
            "opendal",
            SimpleNamespace(
                Operator=construct, exceptions=SimpleNamespace(Unsupported=Unsupported)
            ),
        )
        assert storage_operations._compiled_service("s3") is not unsupported  # noqa: SLF001
        assert calls == [
            (
                "s3",
                {
                    "bucket": "availability",
                    "region": "us-east-1",
                    "disable_config_load": "true",
                    "disable_ec2_metadata": "true",
                },
            )
        ]


class TestOperations:
    @pytest.mark.parametrize(
        "create,delete,replace",
        [(True, True, True), (True, False, False), (False, False, False)],
    )
    def test_vault_catalog_purge_and_physical_delete_have_separate_guarantees(
        self, create, delete, replace
    ) -> None:
        caps = StorageCapabilities(
            create, ObjectIdentity.NONE, delete, replace, True, False
        )
        result = vault_operations(caps)
        assert result["catalog_purge"].allowed
        assert result["catalog_purge"].confirmation_required is (
            not (create and delete and replace)
        )
        assert result["physical_delete"].allowed is delete
        assert result["automatic_retention"].allowed is (create and delete and replace)
        if not delete:
            assert (
                result["catalog_purge"].reason == "storage_catalog_only_bytes_retained"
            )
            assert (
                result["physical_delete"].reason == "storage_exact_delete_unavailable"
            )
        assert not result["gc_witness"].allowed

    def test_external_originals_are_always_read_only(self) -> None:
        result = source_operations()
        assert result["catalog_purge"].allowed
        assert result["catalog_purge"].reason == "storage_source_originals_retained"
        for name in ("physical_delete", "automatic_retention", "gc_witness"):
            assert not result[name].allowed
            assert result[name].reason == "storage_source_read_only"

    @pytest.mark.parametrize("exact", [True, False])
    def test_replica_retention_requires_exact_deletion(self, exact) -> None:
        result = replica_operations(exact_delete=exact)
        assert result["physical_delete"].allowed is exact
        assert result["automatic_retention"].allowed is exact
        assert not result["catalog_purge"].allowed
        assert not result["gc_witness"].allowed
