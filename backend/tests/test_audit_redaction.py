"""The ORM after_flush audit listener must never leak secrets into diff_json.

GET /admin/audit exposes every audited row, so a printer API key or runtime
config secret written into a diff is readable by any admin. See
reports/17-0.8.5-closeout-addenda.md item 1.
"""

from __future__ import annotations

import json

from sqlmodel import Session, select

from app.db.models import AuditLog, Printer
from app.services.audit import install_audit_listeners


def test_secret_field_update_is_redacted(db_session: Session) -> None:
    install_audit_listeners()

    printer = Printer(
        name="Ender 3",
        api_key="super-secret-key",
        prusalink_password="old-prusa-password",
        prusalink_api_key="old-prusa-key",
        elegoo_centauri_access_code="old-centauri-code",
        octoprint_api_key="old-octoprint-key",
    )
    db_session.add(printer)
    db_session.commit()

    printer.api_key = "rotated-secret-key"
    printer.prusalink_password = "new-prusa-password"
    printer.prusalink_api_key = "new-prusa-key"
    printer.elegoo_centauri_access_code = "new-centauri-code"
    printer.octoprint_api_key = "new-octoprint-key"
    db_session.add(printer)
    db_session.commit()

    rows = db_session.exec(
        select(AuditLog).where(
            AuditLog.resource_type == "printers", AuditLog.action == "update"
        )
    ).all()
    assert rows, "expected an audit row for the printer update"

    raw = "\n".join(r.diff_json for r in rows)
    assert "super-secret-key" not in raw
    assert "rotated-secret-key" not in raw
    assert "old-prusa-password" not in raw
    assert "new-prusa-password" not in raw
    assert "old-prusa-key" not in raw
    assert "new-prusa-key" not in raw
    assert "old-centauri-code" not in raw
    assert "new-centauri-code" not in raw
    assert "old-octoprint-key" not in raw
    assert "new-octoprint-key" not in raw

    diff = json.loads(rows[-1].diff_json)
    assert diff["api_key"] == {"before": "[redacted]", "after": "[redacted]"}
    assert diff["prusalink_password"] == {
        "before": "[redacted]",
        "after": "[redacted]",
    }
    assert diff["prusalink_api_key"] == {
        "before": "[redacted]",
        "after": "[redacted]",
    }
    assert diff["elegoo_centauri_access_code"] == {
        "before": "[redacted]",
        "after": "[redacted]",
    }
    assert diff["octoprint_api_key"] == {
        "before": "[redacted]",
        "after": "[redacted]",
    }


def test_non_secret_field_update_is_not_redacted(db_session: Session) -> None:
    install_audit_listeners()

    printer = Printer(name="Ender 3")
    db_session.add(printer)
    db_session.commit()

    printer.name = "Ender 3 Pro"
    db_session.add(printer)
    db_session.commit()

    rows = db_session.exec(
        select(AuditLog).where(
            AuditLog.resource_type == "printers", AuditLog.action == "update"
        )
    ).all()
    assert rows, "expected an audit row for the printer update"

    diff = json.loads(rows[-1].diff_json)
    assert diff["name"]["after"] == "Ender 3 Pro"
    assert diff["name"]["before"] != "[redacted]"
