"""Framework-neutral contracts shared by PrintStash components."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .printers import (
        PROVIDER_DEFINITIONS,
        SETUP_OPTIONS,
        ArtifactCaptureClient,
        BambuConfig,
        Capability,
        ConfigField,
        ConfigValueKind,
        ElegooCentauriConfig,
        MaterialSlotSnapshot,
        MoonrakerConfig,
        OctoPrintConfig,
        PrinterClient,
        PrinterConfig,
        PrinterSnapshot,
        PrintSnapshot,
        ProviderCapabilities,
        ProviderDefinition,
        ProviderError,
        ProviderFactory,
        ProviderId,
        ProviderRegistry,
        PrusaLinkConfig,
        SetupOption,
        SnapshotCallback,
        TemperatureSnapshot,
        ToolSnapshot,
        catalog_document,
    )


__all__ = [
    "ArtifactCaptureClient",
    "BambuConfig",
    "Capability",
    "ConfigField",
    "ConfigValueKind",
    "ElegooCentauriConfig",
    "MoonrakerConfig",
    "MaterialSlotSnapshot",
    "OctoPrintConfig",
    "PROVIDER_DEFINITIONS",
    "PrinterClient",
    "PrinterConfig",
    "PrinterSnapshot",
    "PrintSnapshot",
    "ProviderCapabilities",
    "ProviderDefinition",
    "ProviderError",
    "ProviderFactory",
    "ProviderId",
    "ProviderRegistry",
    "PrusaLinkConfig",
    "SETUP_OPTIONS",
    "SetupOption",
    "SnapshotCallback",
    "TemperatureSnapshot",
    "ToolSnapshot",
    "catalog_document",
]


def __getattr__(name: str) -> Any:
    # Geometry and inference workers do not need provider catalogs or async
    # printer contracts. Keep the historical root imports available on demand.
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from . import printers

    value = getattr(printers, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
