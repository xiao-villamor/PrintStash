"""Builders for the fleet: printers, their remote files, and print jobs.

`Printer` is the most-built row in the suite and the easiest to build *wrongly*.
One table carries the credentials for all five providers, and every field is
nullable, so a printer with `provider="bambu_lan"` and no `bambu_host` inserts
happily and then fails somewhere far away — inside a dispatch, or as a
`provider_config_mismatch` from a factory three layers down. That is a whole
class of confusing test failure, and it is what `build_printer(provider=...)`
exists to prevent: name the provider and the credential set that provider needs
is filled in.

The values are obviously-fake placeholders. A printer access code is a real
credential — nothing here may resemble one.
"""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from app.db.models import (
    ArtifactMaterialRequirement,
    File,
    MaterialSource,
    Printer,
    PrinterFile,
    PrinterMaterialSlot,
    PrinterProvider,
    PrinterStatus,
    PrinterTool,
    PrintJob,
    PrintJobState,
)
from tests.factories._support import nth, reject_aliases, save

# Per-provider connection details. Keyed by the same enum the app dispatches on,
# so adding a provider to `PrinterProvider` without adding it here is a KeyError
# in the builder rather than a printer row that silently cannot connect.
_PROVIDER_FIELDS: dict[PrinterProvider, dict[str, Any]] = {
    PrinterProvider.MOONRAKER: {
        "moonraker_url": "http://printer.invalid:7125",
        "api_key": "not-a-real-api-key",
    },
    PrinterProvider.BAMBU_LAN: {
        "bambu_host": "printer.invalid",
        "bambu_serial": "FAKESERIAL0001",
        "bambu_access_code": "00000000",
    },
    PrinterProvider.PRUSALINK: {
        "prusalink_url": "http://printer.invalid",
        "prusalink_auth_mode": "digest",
        "prusalink_username": "maker",
        "prusalink_password": "not-a-real-password",
    },
    # The variant is part of the credential set here, not cosmetic: the client
    # factory only accepts a variant it has an implementation for, and the
    # second-generation one additionally requires an access code.
    PrinterProvider.ELEGOO_CENTAURI: {
        "elegoo_centauri_host": "printer.invalid",
        "provider_variant": "elegoo_centauri_carbon",
    },
    PrinterProvider.OCTOPRINT: {
        "octoprint_url": "http://printer.invalid",
        "octoprint_api_key": "not-a-real-api-key",
    },
}


def printer_config(
    name: str | None = None,
    *,
    provider: PrinterProvider = PrinterProvider.MOONRAKER,
    status: PrinterStatus = PrinterStatus.READY,
    credentials: bool = True,
    **overrides: Any,
) -> Printer:
    """A configured `Printer` that is deliberately *not* saved.

    The contract tier has no database session — it builds a printer only to hand
    to a provider client — and a printer under test for "this row is gone" must
    never reach a session either. Both still need the per-provider credential set
    filled in, which is the whole reason `build_printer` exists, so the
    configuration and the persistence are separate here and `build_printer` is
    this plus `save`.

    Pass a credential field as `None` to build a deliberately misconfigured
    printer, the same way `build_printer` accepts it. `credentials=False` omits
    the whole set at once, which is the shape the conformance pack asserts on:
    every provider must refuse to build a client from a row that carries none of
    its own credentials, and naming each field as `None` per provider would put
    the credential list in two places.
    """
    fields = dict(_PROVIDER_FIELDS[provider]) if credentials else {}
    fields.update(overrides)
    return Printer(
        name=name or f"Printer {nth('printer')}",
        provider=provider,
        status=status,
        **fields,
    )


def build_printer(
    session: Session,
    name: str | None = None,
    *,
    provider: PrinterProvider = PrinterProvider.MOONRAKER,
    status: PrinterStatus = PrinterStatus.READY,
    trashed: bool = False,
    **overrides: Any,
) -> Printer:
    """A configured printer of *provider*, ready to accept a job.

    The credentials that provider requires are filled in unless the test names
    them. To test a *mis*configured printer, pass the field explicitly as `None`
    — that reads as the deliberate omission it is:

        build_printer(session, provider=PrinterProvider.BAMBU_LAN,
                      bambu_access_code=None)

    `status` defaults to `READY` because an offline printer is skipped by
    dispatch, so a test that forgets it ends up asserting against a fleet with
    nothing in it.
    """
    reject_aliases(overrides, {"deleted_at": "trashed"} if trashed else {})
    if trashed:
        from app.core.time import utcnow

        overrides.setdefault("deleted_at", utcnow())
    return save(
        session,
        printer_config(name, provider=provider, status=status, **overrides),
    )


def build_printer_file(
    session: Session,
    printer: Printer,
    *,
    file: File | None = None,
    remote_filename: str | None = None,
    **overrides: Any,
) -> PrinterFile:
    """A file the printer reports having on its own storage.

    `file` links it to a library artifact; leaving it `None` is the real and
    interesting case of a file somebody put on the printer by SD card, which the
    library knows about but does not own.
    """
    index = nth("printer_file")
    if file is not None:
        overrides.setdefault("file_id", file.id)
        overrides.setdefault("sha256", file.sha256)
        overrides.setdefault("matched_by", "sha256")
    return save(
        session,
        PrinterFile(
            printer_id=printer.id,
            remote_filename=remote_filename or f"remote-{index}.gcode",
            **overrides,
        ),
    )


def print_job_config(
    file: File | None = None,
    *,
    state: PrintJobState = PrintJobState.QUEUED,
    printer: Printer | None = None,
    **overrides: Any,
) -> PrintJob:
    """A `PrintJob` that is deliberately *not* saved.

    A handful of services take a job row and return a decision about it —
    filament usage to record, a notification to render — without touching the
    database. Giving those a session would invent persistence they do not use,
    while the row still has to be shaped the way a real one is.
    """
    if printer is not None:
        overrides.setdefault("printer_id", printer.id)
        overrides.setdefault("printer_name", printer.name)
    if file is not None:
        overrides.setdefault("file_id", file.id)
        overrides.setdefault("model_id", file.model_id)
        overrides.setdefault("remote_filename", file.original_filename)
    return PrintJob(state=state, **overrides)


def build_print_job(
    session: Session,
    file: File,
    *,
    printer: Printer | None = None,
    state: PrintJobState = PrintJobState.QUEUED,
    **overrides: Any,
) -> PrintJob:
    """A print job for *file*.

    `model_id` is derived from the artifact rather than asked for: a job whose
    model does not own its file is a state the app cannot produce, and a test that
    accidentally builds one gets confusing results from every read path that
    joins the two.
    """
    if printer is not None:
        overrides.setdefault("printer_id", printer.id)
        overrides.setdefault("printer_name", printer.name)
    overrides.setdefault("remote_filename", file.original_filename)
    return save(
        session,
        PrintJob(
            file_id=file.id,
            model_id=file.model_id,
            state=state,
            **overrides,
        ),
    )


def build_printer_tool(
    session: Session,
    printer: Printer,
    *,
    tool_key: str = "tool0",
    nozzle_diameter_mm: float | None = 0.4,
    source: MaterialSource = MaterialSource.MANUAL,
    **overrides: Any,
) -> PrinterTool:
    """One extruder on *printer*, as the material state sees it.

    `observed_at` defaults to the printer's own `updated_at` because staleness is
    computed by comparing the two: a provider-reported row observed before the
    printer's last update reads as stale, and a test that left the field null
    gets "unknown" from every compatibility check without saying why.
    """
    overrides.setdefault("label", f"Tool {tool_key.removeprefix('tool')}")
    overrides.setdefault("observed_at", printer.updated_at)
    return save(
        session,
        PrinterTool(
            printer_id=printer.id,
            tool_key=tool_key,
            nozzle_diameter_mm=nozzle_diameter_mm,
            source=source,
            **overrides,
        ),
    )


def build_material_slot(
    session: Session,
    printer: Printer,
    *,
    slot_key: str = "slot0",
    material_type: str | None = "PLA",
    state: str = "loaded",
    source: MaterialSource = MaterialSource.MANUAL,
    **overrides: Any,
) -> PrinterMaterialSlot:
    """One filament position on *printer*, loaded with *material_type*.

    A slot with `state="loaded"` and no `material_type` is the "tracked but
    unresolved" shape — the spool id is known, what is on it is not — and the
    whole three-way verdict turns on it, so it is reachable by passing
    `material_type=None` rather than by omitting a field.
    """
    overrides.setdefault("label", slot_key)
    overrides.setdefault("observed_at", printer.updated_at)
    return save(
        session,
        PrinterMaterialSlot(
            printer_id=printer.id,
            slot_key=slot_key,
            material_type=material_type,
            state=state,
            source=source,
            **overrides,
        ),
    )


def build_material_requirement(
    session: Session,
    file: File,
    *,
    tool_index: int = 0,
    material_type: str = "PLA",
    color_hex: str | None = "#FF0000",
    **overrides: Any,
) -> ArtifactMaterialRequirement:
    """What *file* asks of one extruder.

    A multi-tool artifact carries one of these per `tool_index`, and the count is
    load-bearing: a printer that cannot map every required index to a loaded slot
    is `unknown`, not compatible, so a test asserting the multi-tool path needs
    two of these rather than one with two materials in it.
    """
    return save(
        session,
        ArtifactMaterialRequirement(
            file_id=file.id,
            tool_index=tool_index,
            material_type=material_type,
            color_hex=color_hex,
            **overrides,
        ),
    )
