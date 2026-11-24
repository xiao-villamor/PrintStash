"""Local filesystem directories, including NAS shares mounted on the host."""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar, Literal

from app.modules.storage.target_identity import StorageTargetIdentity
from app.modules.storage.transports.base import (
    BuiltinRuntime,
    ProviderCategory,
    ProviderConfig,
    ProviderPresentation,
    Transport,
    TransportKind,
    TransportSpec,
    config_field,
    normalize_root,
)


class LocalProviderConfig(ProviderConfig):
    provider: Literal["local", "synology", "truenas", "qnap", "unraid"]
    data_dir: str = config_field(
        "Your files",
        "Where uploaded models are saved. With Docker, enter the path inside the container.",
        input_type="path",
    )
    thumb_dir: str = config_field(
        "Previews",
        "Where generated thumbnails and previews are saved.",
        input_type="path",
    )


class LocalTransport(Transport[LocalProviderConfig]):
    kind = TransportKind.LOCAL
    config_model = LocalProviderConfig
    default_provider = "local"
    providers = (
        ProviderPresentation(
            id="local",
            label="This machine",
            category=ProviderCategory.THIS_MACHINE,
            description="Local filesystem directories.",
            expected_tier="verified",
            expected_tier_note="Verified on local filesystems with working hardlinks.",
            consequences=(),
            support_level="stable",
        ),
    )
    preset_category = ProviderCategory.THIS_MACHINE
    preset_description = "NAS folder mounted on this host."
    vault_runtime = BuiltinRuntime()
    remote_runtime = BuiltinRuntime()
    large_object_note: ClassVar[str] = "bounded filesystem streaming and range reads."

    def resolve(self, config: LocalProviderConfig) -> TransportSpec:
        root = normalize_root(config.root)
        return TransportSpec(
            kind=self.kind,
            provider=config.provider,
            namespace=f"local/{root}",
            options={
                "data_dir": config.data_dir,
                "thumb_dir": config.thumb_dir,
                "root": root,
            },
        )

    def target(self, options: Mapping[str, object]) -> StorageTargetIdentity | None:
        # The local backend derives its installation-bound identity itself.
        del options
        return None

    def provider_domain(self, identity: StorageTargetIdentity) -> str | None:
        return f"installation:{identity.endpoint}"
