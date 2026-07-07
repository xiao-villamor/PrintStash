"""E2E: notifications, end to end against contract-enforcing fakes.

This is the keystone of the E2E layer. It drives the *real* app — enable the
master switch and create channels through the public REST API — fires a real
``print_completed`` event through the same enqueue path the printer hub uses, runs
the real dispatcher, and asserts each fake provider received a payload it would
actually accept.

Two assertions here fail against the pre-fix renderers, which is the point:
- Telegram: the filename ``benchy_v2.gcode`` (single ``_``) makes the legacy
  Markdown body unparseable → the real Bot API (and our fake) returns 400.
- ntfy: ``_title`` contains an em-dash (``—``), which is not latin-1 and cannot
  be sent as the ``Title`` HTTP header → every ntfy send fails.
"""

from __future__ import annotations

import pytest
from sqlmodel import select

from app.db.models import (
    File,
    FileType,
    Model,
    NotificationDelivery,
    NotificationDeliveryStatus,
    NotificationEventType,
    PrintJob,
    PrintJobState,
    Printer,
    PrinterProvider,
    PrinterStatus,
)
from app.services import notifications

pytestmark = pytest.mark.e2e

NOTIF_BASE = "/api/v1/notifications"
COMPLETED = NotificationEventType.PRINT_COMPLETED.value


async def _enable(api, headers) -> None:
    r = await api.put(NOTIF_BASE, json={"enabled": True}, headers=headers)
    assert r.status_code == 200, r.text


async def _create_channel(api, headers, *, name, target, config, events=(COMPLETED,)):
    r = await api.post(
        f"{NOTIF_BASE}/channels",
        json={"name": name, "target": target, "config": config, "events": list(events)},
        headers=headers,
    )
    assert r.status_code in (200, 201), r.text
    return r.json()


def _seed_completed_job(session, *, filename: str, printer_name: str) -> tuple[int, PrintJob]:
    """Insert a printer + model + file + a COMPLETED PrintJob; return (printer_id, job)."""
    printer = Printer(name=printer_name, provider=PrinterProvider.MOONRAKER, status=PrinterStatus.READY)
    session.add(printer)
    session.commit()
    session.refresh(printer)

    model = Model(name="Benchy", slug="benchy", hash="a" * 64)
    session.add(model)
    session.commit()
    session.refresh(model)
    file = File(
        model_id=model.id,
        path="/tmp/benchy.gcode",
        original_filename=filename,
        file_type=FileType.GCODE,
        version=1,
        size_bytes=1234,
        sha256="b" * 64,
    )
    session.add(file)
    session.commit()
    session.refresh(file)

    job = PrintJob(
        printer_id=printer.id,
        printer_name=printer.name,
        file_id=file.id,
        model_id=model.id,
        remote_filename=filename,
        state=PrintJobState.COMPLETED,
        progress=1.0,
        actual_duration_s=3661,
        filament_used_g=12.3,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return printer.id, job


@pytest.mark.asyncio
async def test_all_targets_deliver_a_valid_payload_for_a_real_print(
    api, fakes, superuser_headers, e2e_db
):
    """A completed print fans out to all four targets and each accepts the payload."""
    await _enable(api, superuser_headers)
    await _create_channel(api, superuser_headers, name="hook", target="webhook", config={"url": fakes.webhook_url})
    await _create_channel(api, superuser_headers, name="dc", target="discord", config={"url": fakes.discord_url})
    await _create_channel(
        api, superuser_headers, name="tg", target="telegram",
        config={"bot_token": "123:ABC", "chat_id": "42"},
    )
    await _create_channel(
        api, superuser_headers, name="nt", target="ntfy",
        config={"topic": "prints", "server_url": fakes.ntfy_server},
    )

    # Fire the event exactly as printer_hub does, with a realistic filename.
    printer_id, job = _seed_completed_job(e2e_db, filename="benchy_v2.gcode", printer_name="Voron 2.4")
    enqueued = notifications.enqueue_for_event(
        e2e_db, NotificationEventType.PRINT_COMPLETED, printer_id=printer_id, job=job
    )
    e2e_db.commit()
    assert enqueued == 4

    sent = await notifications.dispatch_due()
    assert sent == 4

    # Every delivery succeeded (no 400 / encoding failure).
    e2e_db.expire_all()
    deliveries = e2e_db.exec(select(NotificationDelivery)).all()
    statuses = {d.event_type: d.status for d in deliveries}
    assert all(d.status == NotificationDeliveryStatus.SENT for d in deliveries), [
        (d.id, d.status, d.last_error) for d in deliveries
    ]
    assert statuses  # non-empty

    # Each fake received exactly one request.
    assert len(fakes.recorder.for_target("webhook")) == 1
    assert len(fakes.recorder.for_target("discord")) == 1
    assert len(fakes.recorder.for_target("telegram")) == 1
    assert len(fakes.recorder.for_target("ntfy")) == 1

    # Payload spot-checks against each provider's contract.
    discord = fakes.recorder.for_target("discord")[0].json
    assert "Print completed" in discord["embeds"][0]["title"]

    webhook = fakes.recorder.for_target("webhook")[0].json
    assert webhook["event"] == COMPLETED
    assert webhook["data"]["filename"] == "benchy_v2.gcode"

    ntfy = fakes.recorder.for_target("ntfy")[0]
    assert b"Voron" in (ntfy.body or b"") or "Voron" in str(ntfy.headers)


@pytest.mark.asyncio
async def test_telegram_filename_with_underscore_is_accepted(
    api, fakes, superuser_headers, e2e_db
):
    """Regression: a normal filename with '_' must not break Telegram parsing."""
    await _enable(api, superuser_headers)
    await _create_channel(
        api, superuser_headers, name="tg", target="telegram",
        config={"bot_token": "123:ABC", "chat_id": "42"},
    )
    printer_id, job = _seed_completed_job(e2e_db, filename="my_part_v3.gcode", printer_name="Printer_One")
    notifications.enqueue_for_event(
        e2e_db, NotificationEventType.PRINT_COMPLETED, printer_id=printer_id, job=job
    )
    e2e_db.commit()

    await notifications.dispatch_due()

    e2e_db.expire_all()
    d = e2e_db.exec(select(NotificationDelivery)).one()
    assert d.status == NotificationDeliveryStatus.SENT, d.last_error


@pytest.mark.asyncio
async def test_ntfy_title_with_non_ascii_is_accepted(
    api, fakes, superuser_headers, e2e_db
):
    """Regression: non-latin-1 chars in the title (incl. the em-dash) must send."""
    await _enable(api, superuser_headers)
    await _create_channel(
        api, superuser_headers, name="nt", target="ntfy",
        config={"topic": "prints", "server_url": fakes.ntfy_server},
    )
    printer_id, job = _seed_completed_job(e2e_db, filename="café_ñandú.gcode", printer_name="Impresora-Ñ")
    notifications.enqueue_for_event(
        e2e_db, NotificationEventType.PRINT_COMPLETED, printer_id=printer_id, job=job
    )
    e2e_db.commit()

    await notifications.dispatch_due()

    e2e_db.expire_all()
    d = e2e_db.exec(select(NotificationDelivery)).one()
    assert d.status == NotificationDeliveryStatus.SENT, d.last_error
