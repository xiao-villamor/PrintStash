"""SSH File Transfer Protocol through AsyncSSH.

OpenDAL's SFTP service accepts only a host-key strategy backed by the process
OpenSSH catalogue; AsyncSSH verifies an explicitly pinned host key and maps
create-only publication to an exclusive open.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import model_validator

from app.modules.storage.target_identity import (
    StorageTargetIdentity,
    normalized_endpoint,
)
from app.modules.storage.transports.base import (
    AsyncSSHRuntime,
    CatalogueRequirement,
    CreateStrategy,
    ProviderCategory,
    ProviderConfig,
    ProviderPresentation,
    RemoteTransport,
    TransportKind,
    TransportSpec,
    config_field,
    normalize_root,
)


class SFTPProviderConfig(ProviderConfig):
    provider: Literal["sftp", "hetzner_storage_box"]
    host: str = config_field("Host", "SFTP hostname", min_length=1)
    port: int = config_field(
        "Port", "SFTP port", default=22, input_type="number", ge=1, le=65535
    )
    username: str = config_field("Username", "SFTP account username", min_length=1)
    # Either a mounted known_hosts file path or one OpenSSH known-host entry.
    # Empty is accepted only so pre-host-key rows remain readable and can be
    # edited. ``resolve`` is the activation boundary and rejects it, so no SFTP
    # connection can fall back to trust-on-first-use.
    host_key: str = config_field(
        "Host key",
        "OpenSSH known-hosts path or entry",
        default="",
        input_type="path",
        required_for=("sftp", "hetzner_storage_box"),
    )
    password: str = config_field(
        "Password",
        "Use either a password or a mounted private-key path",
        default="",
        secret=True,
    )
    private_key_path: str = config_field(
        "Private key path",
        "Mounted service-key path; inline key material is forbidden",
        default="",
        input_type="path",
    )
    passphrase: str = config_field(
        "Key passphrase",
        "Optional passphrase for the mounted private key",
        default="",
        secret=True,
    )

    @model_validator(mode="after")
    def validate_auth(self):
        password = self.password.strip()
        key_path = self.private_key_path.strip()
        self.host_key = self.host_key.strip()
        if bool(password) == bool(key_path):
            raise ValueError("sftp_exactly_one_authentication_required")
        if self.passphrase and not key_path:
            raise ValueError("sftp_passphrase_requires_private_key")
        if "BEGIN " in key_path:
            raise ValueError("sftp_inline_private_key_forbidden")
        self.password = password
        self.private_key_path = key_path
        return self


_AUTH_REQUIREMENTS = (
    CatalogueRequirement(
        kind="exactly_one",
        fields=("password", "private_key_path"),
        message="Use either a password or a private key path.",
    ),
    CatalogueRequirement(
        kind="requires",
        fields=("passphrase", "private_key_path"),
        message="A key passphrase requires a private key path.",
    ),
)


class SFTPTransport(RemoteTransport[SFTPProviderConfig]):
    kind = TransportKind.SFTP
    config_model = SFTPProviderConfig
    default_provider = "sftp"
    providers = (
        ProviderPresentation(
            id="sftp",
            label="SFTP",
            category=ProviderCategory.SFTP,
            description="NAS storage over SSH File Transfer Protocol.",
            expected_tier="guarded",
            expected_tier_note="Publish uses SSH exclusive create (`x` mode); "
            "`host_key` is required and confirmed catalog purge retains stored "
            "bytes.",
            consequences=(
                "Confirmed catalog removal retains stored bytes.",
                "Automatic physical deletion is unavailable.",
            ),
            support_level="beta",
        ),
    )
    preset_category = ProviderCategory.SFTP
    preset_description = "Remote storage over SFTP."
    vault_runtime = AsyncSSHRuntime()
    remote_runtime = AsyncSSHRuntime()
    create_strategy = CreateStrategy.EXCLUSIVE_OPEN
    provisions_root = True
    large_object_note = (
        "bounded streaming/materialization with proxy delivery; server limits apply."
    )
    requirements = {
        "sftp": _AUTH_REQUIREMENTS,
        "hetzner_storage_box": _AUTH_REQUIREMENTS,
    }

    def resolve(self, config: SFTPProviderConfig) -> TransportSpec:
        root = normalize_root(config.root)
        options: dict[str, str | int | bool] = {
            "host": config.host,
            "port": config.port,
            "username": config.username,
            "root": root,
            "host_key": config.host_key,
        }
        if not config.host_key:
            raise ValueError("sftp_host_key_required")
        if config.password:
            options["password"] = config.password
        else:
            options["private_key_path"] = config.private_key_path
            if config.passphrase:
                options["passphrase"] = config.passphrase
        return TransportSpec(
            kind=self.kind,
            provider=config.provider,
            namespace=f"sftp/{root}",
            options=options,
        )

    def target(self, options: Mapping[str, object]) -> StorageTargetIdentity:
        host = str(options["host"])
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        endpoint = normalized_endpoint(f"sftp://{host}:{options.get('port', 22)}")
        return StorageTargetIdentity(transport=self.kind.value, endpoint=endpoint)

    def provider_ref_fields(
        self,
        *,
        options: Mapping[str, object],
        backend: object,
        namespace: str,
    ) -> dict[str, object]:
        # SFTP has no URL endpoint. Pin the network destination and managed
        # root, excluding password/private-key/passphrase material.
        host = str(options.get("host", getattr(backend, "_host", ""))).strip()
        port = options.get("port", getattr(backend, "_port", 22))
        return {
            "sftp": {
                "host": host.lower().rstrip("."),
                "port": int(str(port or 22)),
                "username": str(
                    options.get("username", getattr(backend, "_username", ""))
                ).strip(),
                "root": str(options.get("root") or namespace).strip().strip("/"),
            }
        }
