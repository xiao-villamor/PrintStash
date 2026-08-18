"""Pure printer-provider catalog shared by product adapters and code generation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping

from .models import Capability, ProviderCapabilities, ProviderId


class ConfigValueKind(StrEnum):
    """UI-neutral input kinds for provider configuration."""

    URL = "url"
    HOST = "host"
    TEXT = "text"
    SECRET = "secret"
    CHOICE = "choice"


@dataclass(frozen=True)
class ConfigField:
    """One neutral provider configuration field."""

    name: str
    value_kind: ConfigValueKind
    required: bool = True
    choices: tuple[str, ...] = ()


@dataclass(frozen=True)
class SetupOption:
    """A user-facing setup option mapped to a transport and optional variant."""

    kind: str
    provider_id: ProviderId
    label: str
    description: str
    variant: str | None = None


@dataclass(frozen=True)
class ProviderDefinition:
    """Capabilities and configuration vocabulary for one shared provider."""

    provider_id: ProviderId
    capabilities: ProviderCapabilities
    config_fields: tuple[ConfigField, ...]


def _capabilities(
    *supported: Capability,
    support_level: str = "stable",
    support_notes: tuple[str, ...] = (),
    requires_ready_before_send: bool = False,
) -> ProviderCapabilities:
    return ProviderCapabilities(
        supported=frozenset(supported),
        support_level=support_level,
        support_notes=support_notes,
        requires_ready_before_send=requires_ready_before_send,
    )


_CONTROL_AND_STATUS = (
    Capability.START,
    Capability.PAUSE,
    Capability.RESUME,
    Capability.CANCEL,
    Capability.LIVE_STATUS,
)


PROVIDER_DEFINITIONS: Mapping[ProviderId, ProviderDefinition] = MappingProxyType(
    {
        ProviderId.MOONRAKER: ProviderDefinition(
            provider_id=ProviderId.MOONRAKER,
            capabilities=_capabilities(*Capability),
            config_fields=(
                ConfigField("base_url", ConfigValueKind.URL),
                ConfigField("api_key", ConfigValueKind.SECRET, required=False),
                ConfigField("variant", ConfigValueKind.TEXT, required=False),
            ),
        ),
        ProviderId.BAMBU_LAN: ProviderDefinition(
            provider_id=ProviderId.BAMBU_LAN,
            capabilities=_capabilities(
                *_CONTROL_AND_STATUS,
                Capability.UPLOAD,
                support_level="beta",
                support_notes=(
                    "Bambu LAN upload and explicit start are beta features.",
                    "Printer file inventory, deletion, raw G-code controls, and measured filament consumption are unavailable.",
                ),
                requires_ready_before_send=True,
            ),
            config_fields=(
                ConfigField("host", ConfigValueKind.HOST),
                ConfigField("serial", ConfigValueKind.TEXT),
                ConfigField("access_code", ConfigValueKind.SECRET),
            ),
        ),
        ProviderId.PRUSALINK: ProviderDefinition(
            provider_id=ProviderId.PRUSALINK,
            capabilities=_capabilities(
                *_CONTROL_AND_STATUS,
                Capability.UPLOAD,
                Capability.LIST_FILES,
                Capability.DELETE_FILE,
                Capability.SERVER_INFO,
                support_level="beta",
                support_notes=(
                    "PrusaLink local FDM support is beta pending broader hardware validation.",
                    "Raw G-code controls and measured filament consumption are unavailable.",
                ),
            ),
            config_fields=(
                ConfigField("base_url", ConfigValueKind.URL),
                ConfigField(
                    "auth_mode",
                    ConfigValueKind.CHOICE,
                    choices=("digest", "api_key"),
                ),
                ConfigField("username", ConfigValueKind.TEXT, required=False),
                ConfigField("password", ConfigValueKind.SECRET, required=False),
                ConfigField("api_key", ConfigValueKind.SECRET, required=False),
            ),
        ),
        ProviderId.OCTOPRINT: ProviderDefinition(
            provider_id=ProviderId.OCTOPRINT,
            capabilities=_capabilities(
                *_CONTROL_AND_STATUS,
                Capability.UPLOAD,
                Capability.LIST_FILES,
                Capability.DELETE_FILE,
                Capability.SERVER_INFO,
                support_level="beta",
                support_notes=(
                    "OctoPrint support is beta pending broader hardware validation.",
                    "Raw G-code controls and measured filament consumption are unavailable.",
                ),
            ),
            config_fields=(
                ConfigField("base_url", ConfigValueKind.URL),
                ConfigField("api_key", ConfigValueKind.SECRET),
            ),
        ),
        ProviderId.ELEGOO_CENTAURI: ProviderDefinition(
            provider_id=ProviderId.ELEGOO_CENTAURI,
            capabilities=_capabilities(
                *_CONTROL_AND_STATUS,
                Capability.SERVER_INFO,
                Capability.UPLOAD,
                support_level="beta",
                support_notes=(
                    "Centauri Carbon uses local SDCP; Carbon 2 uses local authenticated MQTT.",
                    "Upload runs over plain HTTP, independent of the SDCP/MQTT control channel.",
                    "File inventory, deletion, and print-history import remain unavailable.",
                ),
            ),
            config_fields=(
                ConfigField("host", ConfigValueKind.HOST),
                ConfigField(
                    "model",
                    ConfigValueKind.CHOICE,
                    choices=(
                        "elegoo_centauri_carbon",
                        "elegoo_centauri_carbon_2",
                    ),
                ),
                ConfigField("access_code", ConfigValueKind.SECRET, required=False),
                ConfigField("mainboard_id", ConfigValueKind.TEXT, required=False),
            ),
        ),
    }
)


SETUP_OPTIONS: tuple[SetupOption, ...] = (
    SetupOption(
        kind="moonraker",
        provider_id=ProviderId.MOONRAKER,
        label="Moonraker / Klipper",
        description="Generic Klipper printer using Moonraker.",
    ),
    SetupOption(
        kind="elegoo_neptune4",
        provider_id=ProviderId.MOONRAKER,
        variant="elegoo_neptune4",
        label="Elegoo Neptune 4 family",
        description="Neptune 4, Pro, Plus, or Max using its Moonraker service.",
    ),
    SetupOption(
        kind="prusalink",
        provider_id=ProviderId.PRUSALINK,
        label="PrusaLink (beta)",
        description="Local Prusa FDM connection.",
    ),
    SetupOption(
        kind="octoprint",
        provider_id=ProviderId.OCTOPRINT,
        label="OctoPrint (beta)",
        description="Local OctoPrint/OctoPi instance using an API key.",
    ),
    SetupOption(
        kind="elegoo_centauri_carbon",
        provider_id=ProviderId.ELEGOO_CENTAURI,
        variant="elegoo_centauri_carbon",
        label="Elegoo Centauri Carbon (beta)",
        description="Local SDCP monitoring and controls; file upload is not available.",
    ),
    SetupOption(
        kind="elegoo_centauri_carbon_2",
        provider_id=ProviderId.ELEGOO_CENTAURI,
        variant="elegoo_centauri_carbon_2",
        label="Elegoo Centauri Carbon 2 (beta)",
        description="Local MQTT monitoring and controls; enable LAN Only on printer first.",
    ),
    SetupOption(
        kind="bambu_lan",
        provider_id=ProviderId.BAMBU_LAN,
        label="Bambu LAN (beta)",
        description="Local-network connection using serial and access code.",
    ),
)


def catalog_document() -> dict[str, Any]:
    """Return a deterministic JSON-compatible representation of the catalog."""

    providers: dict[str, Any] = {}
    for provider_id in ProviderId:
        definition = PROVIDER_DEFINITIONS[provider_id]
        providers[provider_id.value] = {
            "capabilities": [
                capability.value
                for capability in Capability
                if definition.capabilities.supports(capability)
            ],
            "supportLevel": definition.capabilities.support_level,
            "supportNotes": list(definition.capabilities.support_notes),
            "requiresReadyBeforeSend": (
                definition.capabilities.requires_ready_before_send
            ),
            "configFields": [
                {
                    "name": config.name,
                    "kind": config.value_kind.value,
                    "required": config.required,
                    "choices": list(config.choices),
                }
                for config in definition.config_fields
            ],
        }

    return {
        "schemaVersion": 1,
        "capabilities": [capability.value for capability in Capability],
        "providers": providers,
        "setupOptions": [
            {
                "value": option.kind,
                "provider": option.provider_id.value,
                "variant": option.variant,
                "label": option.label,
                "description": option.description,
            }
            for option in SETUP_OPTIONS
        ],
    }
