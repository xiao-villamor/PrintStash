"""Immutable, framework-neutral printer models."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, ClassVar, TypeAlias, cast


class ProviderId(StrEnum):
    """Stable identifiers for supported printer protocols."""

    MOONRAKER = "moonraker"
    BAMBU_LAN = "bambu_lan"
    PRUSALINK = "prusalink"
    ELEGOO_CENTAURI = "elegoo_centauri"
    OCTOPRINT = "octoprint"


class Capability(StrEnum):
    """Provider action vocabulary shared by all transports."""

    START = "start"
    PAUSE = "pause"
    RESUME = "resume"
    CANCEL = "cancel"
    LIVE_STATUS = "live_status"
    UPLOAD = "upload"
    LIST_FILES = "list_files"
    SEND_GCODE = "send_gcode"
    MEASURED_CONSUMPTION = "measured_consumption"
    DELETE_FILE = "delete_file"
    EMERGENCY_STOP = "emergency_stop"
    SERVER_INFO = "server_info"
    SERVER_CONFIG = "server_config"
    PRINTER_CONFIG = "printer_config"
    MATERIAL_STATE = "material_state"


class ProviderError(RuntimeError):
    """Exception boundary shared by provider implementations."""

    def __init__(self, detail: str, *, code: str = "provider_error") -> None:
        super().__init__(detail)
        self.detail = detail
        self.code = code


_UNSUPPORTED_ACTION_ORDER: tuple[Capability, ...] = (
    Capability.UPLOAD,
    Capability.LIST_FILES,
    Capability.DELETE_FILE,
    Capability.SEND_GCODE,
    Capability.EMERGENCY_STOP,
    Capability.MEASURED_CONSUMPTION,
)


@dataclass(frozen=True)
class ProviderCapabilities:
    """Capabilities and support metadata declared by a provider factory."""

    supported: frozenset[Capability]
    support_level: str = "stable"
    support_notes: tuple[str, ...] = ()
    requires_ready_before_send: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "supported", frozenset(self.supported))
        object.__setattr__(self, "support_notes", tuple(self.support_notes))

    def supports(self, capability: Capability) -> bool:
        return capability in self.supported

    @property
    def can_start(self) -> bool:
        return self.supports(Capability.START)

    @property
    def can_pause(self) -> bool:
        return self.supports(Capability.PAUSE)

    @property
    def can_resume(self) -> bool:
        return self.supports(Capability.RESUME)

    @property
    def can_cancel(self) -> bool:
        return self.supports(Capability.CANCEL)

    @property
    def can_live_status(self) -> bool:
        return self.supports(Capability.LIVE_STATUS)

    @property
    def can_upload(self) -> bool:
        return self.supports(Capability.UPLOAD)

    @property
    def can_list_files(self) -> bool:
        return self.supports(Capability.LIST_FILES)

    @property
    def can_send_gcode(self) -> bool:
        return self.supports(Capability.SEND_GCODE)

    @property
    def can_measure_consumption(self) -> bool:
        return self.supports(Capability.MEASURED_CONSUMPTION)

    @property
    def can_report_material_state(self) -> bool:
        return self.supports(Capability.MATERIAL_STATE)

    @property
    def unsupported_actions(self) -> tuple[str, ...]:
        return tuple(
            capability.value
            for capability in _UNSUPPORTED_ACTION_ORDER
            if not self.supports(capability)
        )

    def action_flags(self) -> dict[str, bool]:
        return {
            "can_start": self.can_start,
            "can_pause": self.can_pause,
            "can_resume": self.can_resume,
            "can_cancel": self.can_cancel,
            "can_live_status": self.can_live_status,
            "can_upload": self.can_upload,
            "can_list_files": self.can_list_files,
            "can_send_gcode": self.can_send_gcode,
            "can_measure_consumption": self.can_measure_consumption,
            "can_report_material_state": self.can_report_material_state,
        }

    def as_api_dict(self) -> dict[str, object]:
        return {
            **self.action_flags(),
            "support_level": self.support_level,
            "support_notes": list(self.support_notes),
            "unsupported_actions": list(self.unsupported_actions),
        }


def _credentials_missing() -> None:
    raise ProviderError(
        "provider_credentials_missing", code="provider_credentials_missing"
    )


def _require_values(*values: str | None) -> None:
    if any(value is None or not value.strip() for value in values):
        _credentials_missing()


@dataclass(frozen=True)
class MoonrakerConfig:
    base_url: str
    api_key: str | None = None
    variant: str | None = None

    provider_id: ClassVar[ProviderId] = ProviderId.MOONRAKER

    def __post_init__(self) -> None:
        _require_values(self.base_url)


@dataclass(frozen=True)
class BambuConfig:
    host: str
    serial: str
    access_code: str

    provider_id: ClassVar[ProviderId] = ProviderId.BAMBU_LAN

    def __post_init__(self) -> None:
        _require_values(self.host, self.serial, self.access_code)


@dataclass(frozen=True)
class PrusaLinkConfig:
    base_url: str
    auth_mode: str
    username: str | None = None
    password: str | None = None
    api_key: str | None = None

    provider_id: ClassVar[ProviderId] = ProviderId.PRUSALINK

    def __post_init__(self) -> None:
        _require_values(self.base_url, self.auth_mode)
        if self.auth_mode == "digest":
            _require_values(self.username, self.password)
        elif self.auth_mode == "api_key":
            _require_values(self.api_key)
        else:
            _credentials_missing()


@dataclass(frozen=True)
class OctoPrintConfig:
    base_url: str
    api_key: str

    provider_id: ClassVar[ProviderId] = ProviderId.OCTOPRINT

    def __post_init__(self) -> None:
        _require_values(self.base_url, self.api_key)


@dataclass(frozen=True)
class ElegooCentauriConfig:
    host: str
    model: str
    access_code: str | None = None
    mainboard_id: str | None = None

    provider_id: ClassVar[ProviderId] = ProviderId.ELEGOO_CENTAURI
    _MODELS: ClassVar[frozenset[str]] = frozenset(
        {"elegoo_centauri_carbon", "elegoo_centauri_carbon_2"}
    )

    def __post_init__(self) -> None:
        _require_values(self.host, self.model)
        if self.model not in self._MODELS:
            _credentials_missing()
        if self.model == "elegoo_centauri_carbon_2":
            _require_values(self.access_code)


PrinterConfig: TypeAlias = (
    MoonrakerConfig
    | BambuConfig
    | PrusaLinkConfig
    | OctoPrintConfig
    | ElegooCentauriConfig
)


JsonScalar: TypeAlias = str | int | float | bool | None
FrozenValue: TypeAlias = (
    JsonScalar | tuple["FrozenValue", ...] | Mapping[str, "FrozenValue"]
)


def _freeze(value: Any) -> FrozenValue:
    """Take a recursively immutable defensive copy of JSON-like data."""

    if isinstance(value, Mapping):
        frozen = {str(key): _freeze(item) for key, item in value.items()}
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    # Legacy payloads are JSON-shaped in production. Keeping an unexpected
    # scalar by value would expose mutable provider objects, so retain a stable
    # representation instead.
    return repr(value)


def _freeze_mapping(value: Mapping[str, Any] | None) -> Mapping[str, FrozenValue]:
    if value is None:
        return MappingProxyType({})
    frozen = _freeze(value)
    return cast(Mapping[str, FrozenValue], frozen)


def _thaw(value: FrozenValue) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _extra(
    source: Mapping[str, Any], known: frozenset[str]
) -> Mapping[str, FrozenValue]:
    return _freeze_mapping(
        {key: value for key, value in source.items() if key not in known}
    )


_PRINT_FIELDS = frozenset(
    {
        "state",
        "filename",
        "print_duration",
        "total_duration",
        "filament_used",
        "message",
        "external_display_name",
        "external_task_id",
        "external_subtask_id",
        "external_project_id",
        "external_profile_id",
        "external_gcode_file",
        "external_artifact_path",
        "external_plate_index",
        "external_current_layer",
        "external_total_layers",
        "external_nozzle_diameter",
    }
)
_STORAGE_FIELDS = frozenset({"progress", "file_position", "file_size"})
_TEMPERATURE_FIELDS = frozenset({"temperature", "target"})
_TOOLHEAD_FIELDS = frozenset({"position", "homed_axes"})
_WEBHOOK_FIELDS = frozenset({"state", "state_message"})


@dataclass(frozen=True)
class TemperatureSnapshot:
    """One temperature sensor reading."""

    temperature: int | float | None = None
    target: int | float | None = None
    extra: Mapping[str, FrozenValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "extra", _freeze_mapping(self.extra))


@dataclass(frozen=True)
class PrintSnapshot:
    """Current print lifecycle, progress, and reported source metadata."""

    state: str | None = None
    filename: str | None = None
    progress: int | float | None = None
    print_duration: int | float | None = None
    total_duration: int | float | None = None
    filament_used: int | float | None = None
    message: str | None = None
    file_position: int | float | None = None
    file_size: int | float | None = None
    external_display_name: str | None = None
    external_task_id: str | None = None
    external_subtask_id: str | None = None
    external_project_id: str | None = None
    external_profile_id: str | None = None
    external_gcode_file: str | None = None
    external_artifact_path: str | None = None
    external_plate_index: int | None = None
    external_current_layer: int | None = None
    external_total_layers: int | None = None
    external_nozzle_diameter: int | float | None = None
    extra: Mapping[str, FrozenValue] = field(default_factory=dict)
    storage_extra: Mapping[str, FrozenValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "extra", _freeze_mapping(self.extra))
        object.__setattr__(self, "storage_extra", _freeze_mapping(self.storage_extra))


@dataclass(frozen=True)
class MaterialSlotSnapshot:
    """One provider-reported material feed or tray."""

    slot_key: str
    label: str
    state: str
    material_type: str | None = None
    material_brand: str | None = None
    color_hex: str | None = None
    external_spool_id: int | None = None
    tool_key: str | None = None


@dataclass(frozen=True)
class ToolSnapshot:
    """One provider-reported tool and its currently installed nozzle."""

    tool_key: str
    label: str
    nozzle_diameter_mm: int | float | None = None


@dataclass(frozen=True)
class PrinterSnapshot:
    """Immutable printer state independent of a provider wire protocol."""

    print: PrintSnapshot = field(default_factory=PrintSnapshot)
    temperatures: Mapping[str, TemperatureSnapshot] = field(default_factory=dict)
    position: tuple[int | float, ...] | None = None
    homed_axes: str | None = None
    webhook_state: str | None = None
    webhook_message: str | None = None
    material_slots: tuple[MaterialSlotSnapshot, ...] = ()
    material_tools: tuple[ToolSnapshot, ...] = ()
    extra: Mapping[str, FrozenValue] = field(default_factory=dict)
    toolhead_extra: Mapping[str, FrozenValue] = field(default_factory=dict)
    webhook_extra: Mapping[str, FrozenValue] = field(default_factory=dict)
    _legacy_payload: Mapping[str, FrozenValue] | None = field(
        default=None, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        frozen_temperatures = MappingProxyType(dict(self.temperatures))
        object.__setattr__(self, "temperatures", frozen_temperatures)
        object.__setattr__(self, "material_slots", tuple(self.material_slots))
        object.__setattr__(self, "material_tools", tuple(self.material_tools))
        if self.position is not None:
            object.__setattr__(self, "position", tuple(self.position))
        object.__setattr__(self, "extra", _freeze_mapping(self.extra))
        object.__setattr__(self, "toolhead_extra", _freeze_mapping(self.toolhead_extra))
        object.__setattr__(self, "webhook_extra", _freeze_mapping(self.webhook_extra))
        if self._legacy_payload is not None:
            object.__setattr__(
                self, "_legacy_payload", _freeze_mapping(self._legacy_payload)
            )

    @property
    def state(self) -> str | None:
        return self.print.state

    @property
    def filename(self) -> str | None:
        return self.print.filename

    @property
    def progress(self) -> int | float | None:
        return self.print.progress

    @property
    def bed(self) -> TemperatureSnapshot | None:
        return self.temperatures.get("heater_bed")

    @property
    def extruder(self) -> TemperatureSnapshot | None:
        return self.temperatures.get("extruder")

    @property
    def raw_payload(self) -> Mapping[str, FrozenValue]:
        """Immutable legacy representation, including unknown fields."""

        if self._legacy_payload is not None:
            return self._legacy_payload
        return _freeze_mapping(self.to_legacy_payload())

    @classmethod
    def from_legacy_payload(cls, payload: dict[str, Any]) -> PrinterSnapshot:
        """Parse either a status object or the legacy ``result.status`` envelope."""

        status: Mapping[str, Any] = payload
        result = _mapping(payload.get("result"))
        wrapped_status = result.get("status")
        if isinstance(wrapped_status, Mapping):
            status = wrapped_status

        print_stats = _mapping(status.get("print_stats"))
        storage = _mapping(status.get("virtual_sdcard"))
        toolhead = _mapping(status.get("toolhead"))
        webhooks = _mapping(status.get("webhooks"))
        raw_material_slots = status.get("material_slots")
        material_slots: list[MaterialSlotSnapshot] = []
        if isinstance(raw_material_slots, (list, tuple)):
            for item in raw_material_slots:
                source = _mapping(item)
                slot_key = _optional_str(source.get("slot_key"))
                label = _optional_str(source.get("label"))
                state = _optional_str(source.get("state"))
                if slot_key and label and state:
                    material_slots.append(
                        MaterialSlotSnapshot(
                            slot_key=slot_key,
                            label=label,
                            state=state,
                            material_type=_optional_str(source.get("material_type")),
                            material_brand=_optional_str(source.get("material_brand")),
                            color_hex=_optional_str(source.get("color_hex")),
                            external_spool_id=_optional_int(
                                source.get("external_spool_id")
                            ),
                            tool_key=_optional_str(source.get("tool_key")),
                        )
                    )
        raw_material_tools = status.get("material_tools")
        material_tools: list[ToolSnapshot] = []
        if isinstance(raw_material_tools, (list, tuple)):
            for item in raw_material_tools:
                source = _mapping(item)
                tool_key = _optional_str(source.get("tool_key"))
                label = _optional_str(source.get("label"))
                if tool_key and label:
                    material_tools.append(
                        ToolSnapshot(
                            tool_key=tool_key,
                            label=label,
                            nozzle_diameter_mm=_optional_number(
                                source.get("nozzle_diameter_mm")
                            ),
                        )
                    )

        print_snapshot = PrintSnapshot(
            state=_optional_str(print_stats.get("state")),
            filename=_optional_str(print_stats.get("filename")),
            progress=_optional_number(storage.get("progress")),
            print_duration=_optional_number(print_stats.get("print_duration")),
            total_duration=_optional_number(print_stats.get("total_duration")),
            filament_used=_optional_number(print_stats.get("filament_used")),
            message=_optional_str(print_stats.get("message")),
            file_position=_optional_number(storage.get("file_position")),
            file_size=_optional_number(storage.get("file_size")),
            external_display_name=_optional_str(
                print_stats.get("external_display_name")
            ),
            external_task_id=_optional_str(print_stats.get("external_task_id")),
            external_subtask_id=_optional_str(print_stats.get("external_subtask_id")),
            external_project_id=_optional_str(print_stats.get("external_project_id")),
            external_profile_id=_optional_str(print_stats.get("external_profile_id")),
            external_gcode_file=_optional_str(print_stats.get("external_gcode_file")),
            external_artifact_path=_optional_str(
                print_stats.get("external_artifact_path")
            ),
            external_plate_index=_optional_int(print_stats.get("external_plate_index")),
            external_current_layer=_optional_int(
                print_stats.get("external_current_layer")
            ),
            external_total_layers=_optional_int(
                print_stats.get("external_total_layers")
            ),
            external_nozzle_diameter=_optional_number(
                print_stats.get("external_nozzle_diameter")
            ),
            extra=_extra(print_stats, _PRINT_FIELDS),
            storage_extra=_extra(storage, _STORAGE_FIELDS),
        )

        temperatures: dict[str, TemperatureSnapshot] = {}
        temperature_keys = {
            key
            for key in status
            if key in {"heater_bed", "extruder"}
            or key.startswith("temperature_sensor ")
        }
        for key in temperature_keys:
            source = _mapping(status.get(key))
            temperatures[key] = TemperatureSnapshot(
                temperature=_optional_number(source.get("temperature")),
                target=_optional_number(source.get("target")),
                extra=_extra(source, _TEMPERATURE_FIELDS),
            )

        known_top_level = frozenset(
            {
                "print_stats",
                "virtual_sdcard",
                "toolhead",
                "webhooks",
                "material_slots",
                "material_tools",
                *temperature_keys,
            }
        )
        return cls(
            print=print_snapshot,
            temperatures=temperatures,
            position=_optional_position(toolhead.get("position")),
            homed_axes=_optional_str(toolhead.get("homed_axes")),
            webhook_state=_optional_str(webhooks.get("state")),
            webhook_message=_optional_str(webhooks.get("state_message")),
            material_slots=tuple(material_slots),
            material_tools=tuple(material_tools),
            extra=_extra(status, known_top_level),
            toolhead_extra=_extra(toolhead, _TOOLHEAD_FIELDS),
            webhook_extra=_extra(webhooks, _WEBHOOK_FIELDS),
            _legacy_payload=_freeze_mapping(payload),
        )

    def to_legacy_payload(self) -> dict[str, Any]:
        """Return a defensive mutable copy in the original legacy shape."""

        if self._legacy_payload is not None:
            return cast(dict[str, Any], _thaw(self._legacy_payload))
        return self._build_legacy_status()

    def _build_legacy_status(self) -> dict[str, Any]:
        status = cast(dict[str, Any], _thaw(self.extra))
        print_stats = cast(dict[str, Any], _thaw(self.print.extra))
        _put_not_none(
            print_stats,
            state=self.print.state,
            filename=self.print.filename,
            print_duration=self.print.print_duration,
            total_duration=self.print.total_duration,
            filament_used=self.print.filament_used,
            message=self.print.message,
            external_display_name=self.print.external_display_name,
            external_task_id=self.print.external_task_id,
            external_subtask_id=self.print.external_subtask_id,
            external_project_id=self.print.external_project_id,
            external_profile_id=self.print.external_profile_id,
            external_gcode_file=self.print.external_gcode_file,
            external_artifact_path=self.print.external_artifact_path,
            external_plate_index=self.print.external_plate_index,
            external_current_layer=self.print.external_current_layer,
            external_total_layers=self.print.external_total_layers,
            external_nozzle_diameter=self.print.external_nozzle_diameter,
        )
        if print_stats:
            status["print_stats"] = print_stats

        storage = cast(dict[str, Any], _thaw(self.print.storage_extra))
        _put_not_none(
            storage,
            progress=self.print.progress,
            file_position=self.print.file_position,
            file_size=self.print.file_size,
        )
        if storage:
            status["virtual_sdcard"] = storage

        for key, snapshot in self.temperatures.items():
            temperature = cast(dict[str, Any], _thaw(snapshot.extra))
            _put_not_none(
                temperature,
                temperature=snapshot.temperature,
                target=snapshot.target,
            )
            status[key] = temperature

        toolhead = cast(dict[str, Any], _thaw(self.toolhead_extra))
        if self.position is not None:
            toolhead["position"] = list(self.position)
        if self.homed_axes is not None:
            toolhead["homed_axes"] = self.homed_axes
        if toolhead:
            status["toolhead"] = toolhead

        webhooks = cast(dict[str, Any], _thaw(self.webhook_extra))
        _put_not_none(
            webhooks,
            state=self.webhook_state,
            state_message=self.webhook_message,
        )
        if webhooks:
            status["webhooks"] = webhooks
        if self.material_slots:
            status["material_slots"] = [
                {
                    key: value
                    for key, value in {
                        "slot_key": row.slot_key,
                        "label": row.label,
                        "state": row.state,
                        "material_type": row.material_type,
                        "material_brand": row.material_brand,
                        "color_hex": row.color_hex,
                        "external_spool_id": row.external_spool_id,
                        "tool_key": row.tool_key,
                    }.items()
                    if value is not None
                }
                for row in self.material_slots
            ]
        if self.material_tools:
            status["material_tools"] = [
                {
                    key: value
                    for key, value in {
                        "tool_key": row.tool_key,
                        "label": row.label,
                        "nozzle_diameter_mm": row.nozzle_diameter_mm,
                    }.items()
                    if value is not None
                }
                for row in self.material_tools
            ]
        return status


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _optional_number(value: object) -> int | float | None:
    if isinstance(value, bool):
        return None
    return value if isinstance(value, (int, float)) else None


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) else None


def _optional_position(value: object) -> tuple[int | float, ...] | None:
    if not isinstance(value, (list, tuple)):
        return None
    if any(
        isinstance(item, bool) or not isinstance(item, (int, float)) for item in value
    ):
        return None
    return tuple(value)


def _put_not_none(destination: dict[str, Any], **values: object) -> None:
    destination.update(
        {key: value for key, value in values.items() if value is not None}
    )
