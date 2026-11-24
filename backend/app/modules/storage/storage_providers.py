"""Typed storage-provider catalogue and transport resolution.

Transports declare themselves in ``app.modules.storage.transports``; this module
is the facade that parses provider configurations, resolves them to a
``TransportSpec`` and builds the catalogue, forms and documentation from those
declarations.
"""

from __future__ import annotations

from typing import Any, Literal, get_args

from pydantic import BaseModel, Field, TypeAdapter

from app.modules.storage.storage_operations import UseAvailability, use_availability
from app.modules.storage.transports import (
    TRANSPORTS,
    StorageProviderConfig,
    config_models,
    transport_for_config,
)
from app.modules.storage.transports import transport as registered_transport
from app.modules.storage.transports.base import (
    AsyncSSHRuntime,
    BuiltinRuntime,
    ProviderCategory,
    ProviderConfig,
    StorageUse,
    TransportKind,
    TransportSpec,
    normalize_root,
)
from app.modules.storage.transports.gdrive import GoogleDriveProviderConfig
from app.modules.storage.transports.local import LocalProviderConfig
from app.modules.storage.transports.presets import PRESETS
from app.modules.storage.transports.s3 import S3ProviderConfig
from app.modules.storage.transports.sftp import SFTPProviderConfig
from app.modules.storage.transports.webdav import WebDAVProviderConfig

__all__ = [
    "PRESETS",
    "GoogleDriveProviderConfig",
    "LocalProviderConfig",
    "ProviderCategory",
    "ProviderDelivery",
    "S3ProviderConfig",
    "SFTPProviderConfig",
    "StorageFieldDescriptor",
    "StorageProvider",
    "StorageProviderConfig",
    "TransportKind",
    "TransportSpec",
    "WebDAVProviderConfig",
    "merge_provider_secrets",
    "normalize_root",
    "parse_provider_config",
    "provider_catalogue",
    "provider_fields",
    "provider_secret_fields",
    "provider_transport",
    "render_storage_provider_docs",
    "resolve_transport",
    "sanitized_provider_config",
    "split_provider_config",
]


class ProviderDelivery(BaseModel):
    # Potential adapter features. Actual requests must obtain the #101
    # BrowserDownload contract; this metadata never authorizes a redirect.
    signed_get: bool = False
    requires_endpoint_proof: bool = True
    requires_cors_proof: bool = True
    same_origin_fallback: bool = True


class _FieldMetadata(BaseModel):
    secret: bool = False
    input_type: Literal["text", "password", "url", "number", "path"] = "text"
    visible_for: list[str] = Field(default_factory=list)
    required_for: list[str] = Field(default_factory=list)


class StorageFieldDescriptor(BaseModel):
    name: str
    label: str
    help: str
    input_type: Literal["text", "password", "url", "number", "path"] = "text"
    required: bool = True
    secret: bool = False
    default: str | int | None = None
    options: list[str] = Field(default_factory=list)


class StorageProvider(BaseModel):
    id: str
    label: str
    category: ProviderCategory
    description: str
    expected_tier: Literal["verified", "guarded", "unguarded"]
    expected_tier_note: str
    consequences: list[str]
    documentation_url: str
    available: bool
    selectable: bool
    # Product support is independent from the measured storage safety tier.
    # Stable providers have the broadest lifecycle coverage; beta transports
    # remain selectable only when their optional dependency is installed.
    support_level: Literal["stable", "beta"] = "stable"
    disabled_reason: str | None = None
    fields: list[StorageFieldDescriptor]
    fields_by_use: dict[str, list[StorageFieldDescriptor]] = Field(default_factory=dict)
    requirements: list[dict[str, object]] = Field(default_factory=list)
    transport: str = ""
    uses: dict[str, UseAvailability] = Field(default_factory=dict)
    setup_guidance: str = ""
    provider_documentation_url: str = ""
    evidence: Literal["transport_tested", "endpoint_required"] = "endpoint_required"
    delivery: ProviderDelivery = Field(default_factory=ProviderDelivery)


_CONFIG_ADAPTER = TypeAdapter(StorageProviderConfig)


def _secret_field_names(
    value: ProviderConfig | type[ProviderConfig],
) -> set[str]:
    """Derive secret splitting from Pydantic field metadata.

    A field becomes secret by declaring ``json_schema_extra={"secret": True}``,
    so business logic need not be updated in a second location when a provider
    grows a credential.
    """
    model = value if isinstance(value, type) else type(value)
    names: set[str] = set()
    for name, field in model.model_fields.items():
        extra = field.json_schema_extra
        if isinstance(extra, dict) and extra.get("secret") is True:
            names.add(name)
    return names


def provider_secret_fields(provider: str) -> set[str]:
    model = _provider_model(provider)
    return _secret_field_names(model) if model is not None else set()


def _provider_model(provider: object) -> type[ProviderConfig] | None:
    for model in config_models():
        if provider in get_args(model.model_fields["provider"].annotation):
            return model
    return None


def parse_provider_config(value: object) -> StorageProviderConfig:
    return _CONFIG_ADAPTER.validate_python(value)


def split_provider_config(
    value: ProviderConfig,
) -> tuple[dict[str, object], dict[str, str]]:
    raw = value.model_dump(mode="json")
    secret_fields = _secret_field_names(value)
    secrets = {}
    for name in secret_fields:
        secret = raw.pop(name, None)
        if secret not in (None, ""):
            secrets[name] = str(secret)
    return raw, secrets


def merge_provider_secrets(
    config: dict[str, object], secrets: dict[str, str]
) -> StorageProviderConfig:
    return parse_provider_config({**config, **secrets})


def sanitized_provider_config(
    config: dict[str, object], secrets: dict[str, str]
) -> dict[str, object]:
    model = _provider_model(config.get("provider"))
    secret_fields = _secret_field_names(model) if model is not None else set()
    configured_secrets = {
        name
        for name in secret_fields
        if config.get(name) not in (None, "") or secrets.get(name) not in (None, "")
    }
    return {
        **{name: value for name, value in config.items() if name not in secret_fields},
        "secret_fields_set": sorted(configured_secrets),
    }


def resolve_transport(config: ProviderConfig) -> TransportSpec:
    return transport_for_config(config).resolve_config(config)


def _presentations() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = [
        provider.as_catalogue_entry()
        for registered in TRANSPORTS
        for provider in registered.providers
    ]
    for preset_id, preset in PRESETS.items():
        registered = registered_transport(TransportKind(preset["transport"]))
        entries.append(
            {
                "id": preset_id,
                "label": preset["label"],
                "category": registered.preset_category.value,
                "description": registered.preset_description,
                "expected_tier": "guarded",
                "expected_tier_note": "The endpoint probe determines safety. A preset does not certify hardware or authorize deletion.",
                "consequences": [
                    "Automatic physical deletion requires a Verified probe."
                ],
                "documentation_url": f"/docs/storage-providers.md#{preset_id}",
                "support_level": "beta",
                "setup_guidance": preset["guide"],
                "provider_documentation_url": preset["url"],
                "evidence": "transport_tested",
            }
        )
    return entries


def _provider_transport_kind(provider: str) -> TransportKind:
    model = _provider_model(provider)
    for registered in TRANSPORTS:
        if registered.config_model is model:
            return registered.kind
    raise ValueError("storage_provider_unknown")


def provider_transport(provider: str) -> str:
    return _provider_transport_kind(provider).value


def provider_fields(
    provider: str, *, use: str = "vault"
) -> list[StorageFieldDescriptor]:
    model = _provider_model(provider)
    if model is None or use not in {member.value for member in StorageUse}:
        raise ValueError("storage_provider_unknown")
    registered = registered_transport(_provider_transport_kind(provider))
    fields = []
    for name, field in model.model_fields.items():
        if name == "provider":
            continue
        extra = _FieldMetadata.model_validate(field.json_schema_extra or {})
        visible = extra.visible_for
        if visible and provider not in visible:
            continue
        default = (
            PRESETS.get(provider, {})
            .get("defaults", {})
            .get(name, None if field.is_required() else field.default)
        )
        required = field.is_required() or provider in extra.required_for
        label = field.title or name
        form_default = registered.form_defaults.get(provider, name)
        if form_default is not None:
            default = form_default
        if name == "root":
            required = True
            if use != StorageUse.VAULT.value:
                label, default = "Base folder", "PrintStash"
            elif registered.vault_runtime is None:
                label, default = "Folder", "PrintStash"
        fields.append(
            StorageFieldDescriptor(
                name=name,
                label=label,
                help=field.description or "",
                input_type=extra.input_type,
                required=required,
                secret=extra.secret,
                default=default,
                options=list(get_args(field.annotation))
                if getattr(field.annotation, "__origin__", None) is Literal
                else [],
            )
        )
    return fields


def provider_catalogue() -> list[StorageProvider]:
    entries = []
    for presentation in _presentations():
        provider_id = presentation["id"]
        registered = registered_transport(_provider_transport_kind(provider_id))
        transport = registered.kind.value
        uses = {use.value: use_availability(transport, use.value) for use in StorageUse}
        vault = uses[StorageUse.VAULT.value]
        entries.append(
            StorageProvider(
                **presentation,
                transport=transport,
                delivery=ProviderDelivery(signed_get=registered.signed_get),
                available=vault.dependency_installed and vault.service_compiled,
                selectable=vault.available,
                disabled_reason=None if vault.available else vault.reason,
                uses=uses,
                fields=provider_fields(provider_id),
                fields_by_use={
                    use: provider_fields(provider_id, use=use) for use in uses
                },
                requirements=[
                    requirement.as_dict()
                    for requirement in registered.requirements.get(provider_id, ())
                ],
            )
        )
    return entries


def render_storage_provider_docs() -> str:
    """Render the public provider reference from the authoritative registry."""
    providers = provider_catalogue()

    def role_mark(provider: StorageProvider, use: str) -> str:
        return "✓" if provider.uses[use].supported else "—"

    def runtime_packaging(transport: str) -> tuple[str, str]:
        registered = registered_transport(TransportKind(transport))
        # A Vault-capable transport documents its Vault runtime; the others
        # document the runtime their Library sources and replicas use.
        runtime = registered.vault_runtime or registered.remote_runtime
        if isinstance(runtime, BuiltinRuntime):
            return "All images", "uses a built-in transport."
        if isinstance(runtime, AsyncSSHRuntime):
            return "Full image", "requires AsyncSSH."
        return "Full image", f"requires OpenDAL with {runtime.label} support."

    def browser_delivery(provider: StorageProvider) -> str:
        return (
            "Signed GET candidate; proxy fallback"
            if provider.delivery.signed_get
            else "Proxy"
        )

    def large_object_note(transport: str) -> str:
        return registered_transport(TransportKind(transport)).large_object_note

    lines = [
        "# Storage providers",
        "",
        "This page configures **managed Vault storage**, where PrintStash owns object\ncreation and cleanup. To index files already owned by a NAS, S3 bucket, WebDAV\ncollection or SFTP directory, use a read-only\n[Library source](./library-sources.md). Reusing the same server does not merge\nthe two ownership domains.",
        "",
        "PrintStash probes the configured storage at startup. Support maturity and storage safety are separate: the expected tier below is guidance, while `/api/v1/health` and Settings report the measured active tier.",
        "",
        "| Provider | Transport | Vault | Library source | Backup destination | Runtime | Support | Expected tier | Browser delivery |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for provider in providers:
        runtime, _ = runtime_packaging(provider.transport)
        lines.append(
            f"| [{provider.label}](#{provider.id}) | {provider.transport} | "
            f"{role_mark(provider, 'vault')} | {role_mark(provider, 'library')} | "
            f"{role_mark(provider, 'backup')} | {runtime} | "
            f"{provider.support_level.title()} | {provider.expected_tier.title()} | "
            f"{browser_delivery(provider)} |"
        )
    lines.extend(
        [
            "",
            "## Delivery and role setup",
            "",
            "S3 transports can offer signed browser GETs only when the configured endpoint proves safe URLs and CORS for the requesting origin. Every transport retains a same-origin fallback. Delivery capability is independent from deletion safety; a Guarded provider can support signed GET without permitting automatic deletion.",
            "",
            "Mounted NAS presets configure Vault directories. For existing NAS files choose a mounted Library source; for backup folders choose a mounted destination. Mount SMB/NFS outside PrintStash and pass the mount into the container. No direct SMB adapter is included. WebDAV and SFTP are explicit alternative entries, not automatic protocol detection.",
            "",
            "## Safety tiers",
            "",
            "Upload staging is probed separately from the Vault provider. At startup PrintStash\nrecords its filesystem kind, hard-link support, exclusive creation and directory\nsync support in `components.storage.diagnostics.staging` on\n`GET /api/v1/health/details`. This also applies to S3, WebDAV and SFTP Vaults:\nuploads still pass through a local staging directory.",
            "",
            "Filesystems without hard links, including Unraid `/mnt/user` (SHFS/FUSE), use\nexclusive, synced copies for staging and local publication. Settings and startup\nlogs report the extra temporary space requirement. Each affected root is logged\nonce per process setup, rather than once per upload. Existing destinations are\nnever replaced. Copy publication can expose partial bytes until the operation\nreturns; failed copies retain uncertain files for reconciliation.",
            "",
            "A staging copy warning does not change the Vault's safety tier. Local Vault roots\nwithout stable inode proofs remain Guarded, while a verified remote provider can\nremain Verified with hardlinkless staging. Actual permission, capacity or I/O\nerrors still fail the operation. See [deployment layout](deployment.md#hard-linked-imports).",
            "",
            "- **Verified** storage proves conditional creation, replacement identity, and deletion identity. Automated storage-backed purge is allowed.",
            "- **Guarded** storage proves unique creation but lacks at least one destructive-operation proof. Manual permanent deletion requires one-shot confirmation; scheduled storage purge is skipped.",
            "- **Unguarded** storage cannot prove unique creation. Startup additionally requires `VAULT_STORAGE_ALLOW_UNVERIFIED=true`.",
            "",
            "Directory `fsync` support is diagnostic only. Local paths on network or unknown filesystems are capped at Guarded even when hardlinks work.",
            "",
        ]
    )
    for provider in providers:
        runtime, runtime_requirement = runtime_packaging(provider.transport)
        supported_roles = ", ".join(
            label
            for use, label in (
                ("vault", "Vault"),
                ("library", "Library source"),
                ("backup", "Backup destination"),
            )
            if provider.uses[use].supported
        )
        primary_use = "vault" if provider.uses["vault"].supported else "library"
        required_fields = ", ".join(
            f"`{field.name}`" + (" (write-only secret)" if field.secret else "")
            for field in provider.fields_by_use[primary_use]
            if field.required
        )
        lines.extend(
            [
                f"## {provider.id}",
                "",
                provider.description,
                "",
                f"Transport: **{provider.transport}**.",
                "",
                f"Supported roles: {supported_roles}.",
                "",
                f"Runtime packaging: **{runtime}**; {runtime_requirement}",
                "",
                f"Configuration prerequisites: {required_fields}.",
                "",
                f"Large objects: {large_object_note(provider.transport)}",
                "",
                f"Browser delivery: {browser_delivery(provider)}. Signed targets are never returned by catalogue metadata.",
                "",
                f"Expected tier: **{provider.expected_tier.title()}**. {provider.expected_tier_note}",
                "",
                f"Known limitations: {provider.expected_tier_note}",
                "",
            ]
        )
        if provider.setup_guidance:
            lines.extend(
                [
                    provider.setup_guidance,
                    "",
                    f"[Provider instructions]({provider.provider_documentation_url})",
                    "",
                    "Evidence: transport contracts only; this is not hardware or hosted-account certification. Validate the endpoint and each intended role before use.",
                    "",
                ]
            )
        if provider.id == "s3":
            lines.extend(
                [
                    "Use the concrete AWS region for Amazon S3. Leave `endpoint_url` empty and keep\n`addressing_style=auto` unless the account has a specific endpoint requirement.\nFor self-hosted S3, `addressing_style=auto` resolves to path style because many\nNAS and local object stores do not provide wildcard bucket DNS. Select\n`virtual` only when the endpoint, DNS and TLS certificate support virtual-host\nbucket names.",
                    "",
                    "The startup probe creates and cleans up a unique probe object. When the server\nreturns a VersionId, cleanup targets that exact version. It never deletes a\nsame-key replacement by an external writer.",
                    "",
                ]
            )
    lines.extend(
        [
            "## Credentials and upgrades",
            "",
            "Secrets are write-only: configuration reads expose only which secret fields are set. SFTP accepts exactly one authentication mode: password, or a mounted private-key path with an optional passphrase. Inline private-key material is rejected. New and updated SFTP configurations require `host_key` as either a mounted known-hosts path or an OpenSSH known-host entry; legacy rows without it remain readable but cannot activate until it is added.",
            "",
            "PrintStash never creates an S3 bucket or changes its lifecycle policy. Grant data-plane access plus read-only bucket/versioning/lifecycle inspection; remove `s3:CreateBucket` and `s3:PutLifecycleConfiguration` from older policies.",
            "",
            "Large direct browser uploads also require bucket CORS for `PUT`, the\n`content-type` and `x-amz-checksum-sha256` request headers, and the exposed\n`ETag` response header. Use only the exact configured frontend origins. See\n[Resumable Artifact uploads](./artifact-uploads.md#s3-bucket-cors) for the rule\nand fallback behavior.",
            "",
            "New deployments should select and save a provider through Setup or Settings.\nEnvironment-only deployments use scalar fields: `VAULT_STORAGE_PROVIDER` and\n`VAULT_STORAGE_ROOT`, plus `VAULT_S3_*`, `VAULT_WEBDAV_*`, or `VAULT_SFTP_*`\nfor the selected transport. `VAULT_STORAGE_PROVIDER_CONFIG` and\n`VAULT_STORAGE_PROVIDER_SECRETS` remain compatibility inputs but are deprecated.",
            "",
            "The checked-in Compose files forward the legacy/local and `VAULT_S3_*` fields,\nbut do not automatically forward `VAULT_STORAGE_PROVIDER`,\n`VAULT_STORAGE_ROOT`, `VAULT_WEBDAV_*`, `VAULT_SFTP_*`, or\n`VAULT_STORAGE_ALLOW_UNVERIFIED` from `.env`. When configuring those fields\nentirely through environment variables, add them explicitly under the API\nservice's `environment` in a Compose override. Configuration saved through the\nSetup or Settings UI does not need that override.",
            "",
            "`VAULT_STORAGE_BACKEND` and the legacy S3 variables remain supported upgrade\ninputs. Keep them unchanged for the first 0.13.0 compatibility boot.",
            "",
            "Changing from the legacy `s3` input to a typed provider does not move bytes.\nAdopt only an equivalent bucket, endpoint, region, addressing style and root.\nThere is no general provider-to-provider byte migration in 0.13.0.",
        ]
    )
    return "\n".join(lines) + "\n"
