"""S3-compatible object storage.

Managed Vault storage uses the native boto3 adapter; Library sources and backup
replicas use OpenDAL's S3 service.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from app.modules.storage.target_identity import (
    StorageTargetIdentity,
    locator_endpoint,
    normalized_endpoint,
)
from app.modules.storage.transports.base import (
    BuiltinRuntime,
    CatalogueRequirement,
    CreateStrategy,
    FormDefaults,
    OpenDALService,
    ProviderCategory,
    ProviderConfig,
    ProviderPresentation,
    RemoteTransport,
    TransportKind,
    TransportSpec,
    config_field,
    normalize_root,
    validate_endpoint,
)

# Self-hosted endpoints rarely provide wildcard bucket DNS, so ``auto``
# addressing resolves to path style for them.
_PATH_STYLE_PROVIDERS = frozenset(
    {"s3_self_hosted", "minio", "garage", "seaweedfs", "hetzner_object_storage"}
)
_ENDPOINT_REQUIRED = frozenset({"s3_self_hosted", "minio", "garage", "seaweedfs"})


def _s3_provider_domain(host: str) -> str | None:
    if host in {"s3.amazonaws.com", "s3.amazonaws.com.cn"}:
        return "provider:aws"
    if re.fullmatch(r"s3[.-][a-z0-9.-]+\.amazonaws\.com(?:\.cn)?", host):
        return "provider:aws"
    if re.fullmatch(r"s3\.[a-z0-9-]+\.backblazeb2\.com", host):
        return "provider:backblaze"
    if re.fullmatch(r"s3(?:\.[a-z0-9-]+)?\.wasabisys\.com", host):
        return "provider:wasabi"
    if re.fullmatch(r"[a-z0-9-]+\.r2\.cloudflarestorage\.com", host):
        return "provider:cloudflare"
    return None


def s3_target(*, endpoint: str, bucket: str) -> StorageTargetIdentity:
    endpoint = normalized_endpoint(endpoint or "https://s3.amazonaws.com")
    parts = urlsplit(endpoint)
    host = parts.hostname or ""
    # Only recognize AWS's documented virtual-host endpoint. A custom server
    # named "bucket.example.test" must retain its host identity when another
    # bucket on that same server is considered as a backup witness.
    if (
        bucket
        and host.startswith(f"{bucket}.")
        and (_s3_provider_domain(host[len(bucket) + 1 :]) == "provider:aws")
    ):
        host = host[len(bucket) + 1 :]
        endpoint = urlunsplit(
            (
                parts.scheme,
                host + (f":{parts.port}" if parts.port else ""),
                parts.path,
                "",
                "",
            )
        )
    target = StorageTargetIdentity(transport="s3", endpoint=endpoint, container=bucket)
    if _s3_provider_domain(target.host) == "provider:aws":
        endpoint = (
            "https://s3.amazonaws.com.cn"
            if host.endswith(".cn")
            else "https://s3.amazonaws.com"
        )
        return target.model_copy(update={"endpoint": endpoint})
    return target


class S3ProviderConfig(ProviderConfig):
    provider: Literal[
        "s3",
        "cloudflare_r2",
        "backblaze_b2",
        "wasabi",
        "s3_self_hosted",
        "minio",
        "garage",
        "seaweedfs",
        "hetzner_object_storage",
    ]
    bucket: str = config_field("Bucket", "Existing bucket name", min_length=1)
    region: str = config_field(
        "Region",
        "Provider region; required for Backblaze B2 and Wasabi",
        default="auto",
    )
    addressing_style: Literal["auto", "path", "virtual"] = config_field(
        "Addressing style", "Auto uses the provider preset", default="auto"
    )
    endpoint_url: str = config_field(
        "Endpoint",
        "S3-compatible server URL",
        default="",
        input_type="url",
        visible_for=(
            "s3",
            "s3_self_hosted",
            "minio",
            "garage",
            "seaweedfs",
            "hetzner_object_storage",
        ),
        required_for=("s3_self_hosted", "minio", "garage", "seaweedfs"),
    )
    account_id: str = config_field(
        "Account ID",
        "Cloudflare account ID",
        default="",
        visible_for=("cloudflare_r2",),
        required_for=("cloudflare_r2",),
    )
    access_key: str = config_field(
        "Access key", "Object-storage access key", secret=True, min_length=1
    )
    secret_key: str = config_field(
        "Secret key", "Object-storage secret key", secret=True, min_length=1
    )


def _s3_provider(id: str, label: str, support_level: Literal["stable", "beta"]):
    return ProviderPresentation(
        id=id,
        label=label,
        category=ProviderCategory.S3_COMPATIBLE,
        description="Native S3-compatible object storage.",
        expected_tier="guarded",
        expected_tier_note="Verified when bucket versioning is enabled; otherwise "
        "Guarded.",
        consequences=("Automated purge requires a Verified probe.",),
        support_level=support_level,
    )


_REGION_REQUIRED = (
    CatalogueRequirement(
        kind="not_value",
        fields=("region",),
        value="auto",
        message="Enter the provider region.",
    ),
)


class S3Transport(RemoteTransport[S3ProviderConfig]):
    kind = TransportKind.S3
    config_model = S3ProviderConfig
    default_provider = "s3"
    providers = (
        _s3_provider("s3", "Amazon S3 or compatible", "stable"),
        _s3_provider("cloudflare_r2", "Cloudflare R2", "beta"),
        _s3_provider("backblaze_b2", "Backblaze B2", "beta"),
        _s3_provider("wasabi", "Wasabi", "beta"),
        _s3_provider("s3_self_hosted", "Self-hosted S3", "beta"),
    )
    preset_category = ProviderCategory.S3_COMPATIBLE
    preset_description = "S3-compatible object storage."
    vault_runtime = BuiltinRuntime()
    remote_runtime = OpenDALService(
        scheme="s3",
        label="S3",
        probe_options={
            "bucket": "availability",
            "region": "us-east-1",
            "disable_config_load": "true",
            "disable_ec2_metadata": "true",
        },
    )
    create_strategy = CreateStrategy.CONDITIONAL_WRITE
    atomic_publication = True
    listed_mtime_whole_seconds = True
    signed_get = True
    large_object_note = "multipart or bounded streaming writes and range reads."
    requirements = {"backblaze_b2": _REGION_REQUIRED, "wasabi": _REGION_REQUIRED}
    # Preserve the ordinary S3 form's default across all three uses. R2 still
    # resolves to its required literal "auto" signing region.
    form_defaults = FormDefaults({"s3": {"region": "us-east-1"}})

    def resolve(self, config: S3ProviderConfig) -> TransportSpec:
        root = normalize_root(config.root)
        endpoint = config.endpoint_url.strip()
        region = config.region.strip() or "auto"
        addressing_style = config.addressing_style
        if config.provider in _ENDPOINT_REQUIRED and not endpoint:
            raise ValueError("s3_endpoint_required")
        if addressing_style == "auto" and config.provider in _PATH_STYLE_PROVIDERS:
            addressing_style = "path"
        path_style = addressing_style == "path"
        if config.provider == "cloudflare_r2":
            if not config.account_id.strip():
                raise ValueError("r2_account_id_required")
            endpoint = f"https://{config.account_id.strip()}.r2.cloudflarestorage.com"
            region = "auto"
        elif config.provider == "backblaze_b2":
            if region == "auto":
                raise ValueError("b2_region_required")
            endpoint = f"https://s3.{region}.backblazeb2.com"
        elif config.provider == "wasabi":
            if region == "auto":
                raise ValueError("wasabi_region_required")
            endpoint = f"https://s3.{region}.wasabisys.com"
        elif config.provider == "hetzner_object_storage":
            if region == "auto" or not region.replace("-", "").isalnum():
                raise ValueError("s3_region_required")
            endpoint = endpoint or f"https://{region}.your-objectstorage.com"
        if endpoint:
            validate_endpoint(endpoint)
        return TransportSpec(
            kind=self.kind,
            provider=config.provider,
            namespace=f"s3/{config.bucket}/{root}",
            options={
                "bucket": config.bucket,
                "endpoint_url": endpoint,
                "region": region,
                "root": root,
                "path_style": path_style,
                "addressing_style": addressing_style,
                "access_key": config.access_key,
                "secret_key": config.secret_key,
            },
        )

    def operator_options(self, spec: TransportSpec) -> dict[str, str]:
        options = spec.options
        kwargs = {
            "bucket": str(options["bucket"]),
            "root": str(options["root"]),
            "access_key_id": str(options["access_key"]),
            "secret_access_key": str(options["secret_key"]),
            "disable_config_load": "true",
            "disable_ec2_metadata": "true",
        }
        addressing_style = str(options.get("addressing_style") or "auto")
        if addressing_style != "auto":
            kwargs["enable_virtual_host_style"] = str(
                addressing_style == "virtual"
            ).lower()
        endpoint = str(options.get("endpoint_url") or "")
        if endpoint:
            kwargs["endpoint"] = endpoint
        # The pinned Python binding requires an explicit signing region. R2's
        # literal "auto" is a valid region, not a request for network discovery.
        kwargs["region"] = str(options.get("region") or "auto")
        return kwargs

    def target(self, options: Mapping[str, object]) -> StorageTargetIdentity:
        return s3_target(
            endpoint=str(options.get("endpoint_url") or ""),
            bucket=str(options["bucket"]),
        )

    def provider_domain(self, identity: StorageTargetIdentity) -> str | None:
        return _s3_provider_domain(identity.host)

    def provider_ref_fields(
        self,
        *,
        options: Mapping[str, object],
        backend: object,
        namespace: str,
    ) -> dict[str, object]:
        del backend
        fields: dict[str, object] = {}
        if options.get("endpoint_url") is not None:
            fields["endpoint"] = locator_endpoint(options.get("endpoint_url"))
        if options.get("region") is not None:
            fields["region"] = str(options.get("region") or "").strip().lower()
        if options.get("addressing_style") is not None:
            fields["addressing_style"] = (
                str(options.get("addressing_style")).strip().lower()
            )
        fields["namespace"] = str(namespace)
        return fields
