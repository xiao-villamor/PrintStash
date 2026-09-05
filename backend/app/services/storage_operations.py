"""Operation-specific storage guarantees, separate from provider availability."""

from __future__ import annotations

from importlib.util import find_spec
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from app.services.storage_backend import StorageCapabilities


class OperationResult(BaseModel):
    allowed: bool
    reason: str
    confirmation_required: bool = False


class UseAvailability(BaseModel):
    dependency_installed: bool
    service_compiled: bool
    supported: bool
    available: bool
    endpoint_proven: bool = False
    reason: str


def _compiled_service(transport: str) -> bool:
    import opendal

    options = {
        "s3": {
            "bucket": "availability",
            "region": "us-east-1",
            "disable_config_load": "true",
            "disable_ec2_metadata": "true",
        },
        "webdav": {"endpoint": "https://availability.invalid"},
        "gdrive": {"access_token": "availability-only"},
    }
    try:
        # Construction tests the compiled service; it performs no endpoint IO.
        opendal.Operator(transport, **options[transport])
    except opendal.exceptions.Unsupported:
        return False
    return True


def use_availability(transport: str, use: str) -> UseAvailability:
    supported = not (transport == "gdrive" and use == "vault")
    native = transport == "local" or (transport == "s3" and use == "vault")
    dependency = (
        native
        or find_spec("asyncssh" if transport == "sftp" else "opendal") is not None
    )
    compiled = bool(
        dependency and (native or transport == "sftp" or _compiled_service(transport))
    )
    reason = (
        "storage_use_unsupported"
        if not supported
        else "storage_dependency_missing"
        if not dependency
        else "storage_service_not_compiled"
        if not compiled
        else "storage_endpoint_probe_required"
    )
    return UseAvailability(
        dependency_installed=dependency,
        service_compiled=compiled,
        supported=supported,
        available=supported and compiled,
        reason=reason,
    )


def vault_operations(capabilities: StorageCapabilities) -> dict[str, OperationResult]:
    verified = capabilities.tier.value == "verified"
    delete = verified and capabilities.namespace_ownership
    return {
        "catalog_purge": OperationResult(
            allowed=True,
            reason="storage_owned_objects_verified"
            if verified
            else "storage_catalog_only_bytes_retained",
            confirmation_required=not verified,
        ),
        "physical_delete": OperationResult(
            allowed=delete,
            reason="storage_owned_objects_verified"
            if delete
            else "storage_exact_delete_unavailable",
        ),
        "automatic_retention": OperationResult(
            allowed=delete,
            reason="storage_owned_objects_verified"
            if delete
            else "storage_retention_unsupported",
        ),
        "gc_witness": OperationResult(
            allowed=False, reason="storage_independent_backup_required"
        ),
    }


def source_operations() -> dict[str, OperationResult]:
    return {
        "catalog_purge": OperationResult(
            allowed=True, reason="storage_source_originals_retained"
        ),
        **{
            operation: OperationResult(allowed=False, reason="storage_source_read_only")
            for operation in ("physical_delete", "automatic_retention", "gc_witness")
        },
    }


def replica_operations(
    *, exact_delete: bool, gc_reason: str = "storage_gc_witness_unsupported"
) -> dict[str, OperationResult]:
    return {
        "catalog_purge": OperationResult(
            allowed=False, reason="storage_backup_ownership_required"
        ),
        "physical_delete": OperationResult(
            allowed=exact_delete,
            reason="storage_owned_objects_verified"
            if exact_delete
            else "storage_exact_delete_unavailable",
        ),
        "automatic_retention": OperationResult(
            allowed=exact_delete,
            reason="storage_owned_objects_verified"
            if exact_delete
            else "storage_retention_unsupported",
        ),
        "gc_witness": OperationResult(allowed=False, reason=gc_reason),
    }


def serialize_operations(operations: dict[str, OperationResult]) -> dict[str, dict]:
    return {name: result.model_dump() for name, result in operations.items()}
