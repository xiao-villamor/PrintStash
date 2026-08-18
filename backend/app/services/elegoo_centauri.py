"""Backward-compatible application facade for the core Centauri client."""

from __future__ import annotations

from printstash_core.printers.elegoo_centauri import (
    Connector,
)
from printstash_core.printers.elegoo_centauri import (
    ElegooCentauriClient as _CoreElegooCentauriClient,
)
from printstash_core.printers.elegoo_centauri import (
    ElegooCentauriError as ElegooCentauriError,
)
from printstash_core.printers.models import ElegooCentauriConfig
from pycentauri.cc2 import CC2Printer as CC2Printer
from pycentauri.client import Printer as Printer
from pycentauri.client import PrinterError as PrinterError
from pycentauri.models import PrintStatus as PrintStatus
from pycentauri.models import Status as Status

_CC1_MODEL = "elegoo_centauri_carbon"
_CC2_MODEL = "elegoo_centauri_carbon_2"
_LEGACY_PLACEHOLDER = "legacy-constructor-placeholder"


def _compat_config(
    host: str,
    model: str,
    access_code: str | None,
    mainboard_id: str | None,
) -> ElegooCentauriConfig:
    """Build a valid core config without making the legacy constructor eager."""

    config_model = model if model in {_CC1_MODEL, _CC2_MODEL} else _CC1_MODEL
    config_access_code = access_code
    if config_model == _CC2_MODEL and not config_access_code:
        config_access_code = _LEGACY_PLACEHOLDER
    return ElegooCentauriConfig(
        host or _LEGACY_PLACEHOLDER,
        config_model,
        access_code=config_access_code,
        mainboard_id=mainboard_id,
    )


class ElegooCentauriClient(_CoreElegooCentauriClient):
    """Legacy constructor over the framework-neutral core implementation."""

    def __init__(
        self,
        host: str,
        *,
        model: str,
        access_code: str | None = None,
        mainboard_id: str | None = None,
        connector: Connector | None = None,
    ) -> None:
        super().__init__(
            _compat_config(host, model, access_code, mainboard_id),
            connector=connector,
        )
        # These attributes were public before extraction. Restore the exact
        # caller values; inherited connection code therefore keeps lazy errors.
        self.host = host
        self.model = model
        self.access_code = access_code
        self.mainboard_id = mainboard_id
