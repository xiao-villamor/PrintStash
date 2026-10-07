"""Printer settings edits cannot overwrite another editor's accepted draft."""

import pytest
from sqlalchemy import text


@pytest.fixture(autouse=True)
def _use_threaded_db(threaded_hub_db: None) -> None:
    """Hub restarts use independent connections, as in the CRUD contract."""


def _headers(auth_headers, printer_id, base):
    return {
        **auth_headers,
        "X-PrintStash-Edit-Contract": "conditional-v1",
        "If-Match": f'"printer-{printer_id}-e{base["edit_epoch"]}-v{base["edit_version"]}"',
    }


def test_rejects_a_competing_settings_edit(client, auth_headers, make_printer):
    printer = make_printer()
    url = f"/api/v1/printers/{printer.id}"
    base = client.get(url, headers=auth_headers).json()
    headers = _headers(auth_headers, printer.id, base)
    first = client.patch(url, headers=headers, json={"name": "First editor"})
    assert first.status_code == 200
    assert first.json()["edit_version"] > base["edit_version"]
    second = client.patch(url, headers=headers, json={"name": "Stale editor"})
    assert second.status_code == 412
    assert client.get(url, headers=auth_headers).json()["name"] == "First editor"


def test_requires_the_advertised_precondition(client, auth_headers, make_printer):
    printer = make_printer()
    url = f"/api/v1/printers/{printer.id}"
    response = client.patch(
        url,
        headers={**auth_headers, "X-PrintStash-Edit-Contract": "conditional-v1"},
        json={"name": "Unreviewed"},
    )
    assert response.status_code == 428
    assert client.get(url, headers=auth_headers).json()["name"] == printer.name


@pytest.mark.parametrize("legacy", [True, False], ids=["legacy-api", "direct-writer"])
def test_invalidates_a_base_after_an_external_settings_write(
    client, auth_headers, make_printer, db_session, legacy
):
    printer = make_printer()
    url = f"/api/v1/printers/{printer.id}"
    base = client.get(url, headers=auth_headers).json()
    if legacy:
        accepted = client.patch(url, headers=auth_headers, json={"notes": "Other edit"})
        assert accepted.status_code == 200
    else:
        db_session.execute(
            text("UPDATE printers SET notes = :notes WHERE id = :id"),
            {"notes": "Other edit", "id": printer.id},
        )
        db_session.commit()
    response = client.patch(
        url,
        headers=_headers(auth_headers, printer.id, base),
        json={"name": "Stale editor"},
    )
    assert response.status_code == 412
    assert client.get(url, headers=auth_headers).json()["notes"] == "Other edit"


def test_preserves_the_base_during_telemetry_updates(
    client, auth_headers, make_printer, db_session
):
    printer = make_printer()
    url = f"/api/v1/printers/{printer.id}"
    base = client.get(url, headers=auth_headers).json()
    db_session.execute(
        text(
            "UPDATE printers SET last_error = :error, updated_at = :now WHERE id = :id"
        ),
        {"error": "Printer offline", "now": "2026-10-07 12:00:00", "id": printer.id},
    )
    db_session.commit()
    refreshed = client.get(url, headers=auth_headers).json()
    assert refreshed["edit_version"] == base["edit_version"]
    response = client.patch(
        url,
        headers=_headers(auth_headers, printer.id, base),
        json={"notes": "Reviewed edit"},
    )
    assert response.status_code == 200
    assert response.json()["notes"] == "Reviewed edit"


def test_rejects_a_prior_database_history(
    client, auth_headers, make_printer, db_session
):
    printer = make_printer()
    url = f"/api/v1/printers/{printer.id}"
    base = client.get(url, headers=auth_headers).json()
    db_session.execute(
        text("UPDATE library_revision SET epoch = :epoch WHERE id = 1"),
        {"epoch": "f" * 32},
    )
    db_session.commit()
    response = client.patch(
        url,
        headers=_headers(auth_headers, printer.id, base),
        json={"name": "Old history"},
    )
    assert response.status_code == 412
    assert client.get(url, headers=auth_headers).json()["name"] == printer.name


def test_rolls_back_an_invalid_settings_edit(client, auth_headers, make_printer):
    printer = make_printer()
    url = f"/api/v1/printers/{printer.id}"
    base = client.get(url, headers=auth_headers).json()
    response = client.patch(
        url,
        headers=_headers(auth_headers, printer.id, base),
        json={"moonraker_url": ""},
    )
    assert response.status_code == 400
    refreshed = client.get(url, headers=auth_headers).json()
    assert refreshed["edit_version"] == base["edit_version"]
    assert refreshed["moonraker_url"] == base["moonraker_url"]


@pytest.mark.parametrize(
    ("tag", "contract", "status"),
    [
        pytest.param(
            '"printer-999-e' + "a" * 32 + '-v1"',
            "conditional-v1",
            412,
            id="wrong-printer",
        ),
        pytest.param(
            '"model-1-e' + "a" * 32 + '-v1"', "conditional-v1", 412, id="wrong-kind"
        ),
        pytest.param("*", "conditional-v1", 412, id="wildcard"),
        pytest.param(
            '"printer-1-e' + "a" * 32 + '-v0"', "conditional-v1", 412, id="zero"
        ),
        pytest.param(
            '"printer-1-e' + "a" * 32 + '-v9223372036854775808"',
            "conditional-v1",
            412,
            id="overflow",
        ),
        pytest.param(
            '"printer-1-e' + "a" * 32 + "-v" + "9" * 5000 + '"',
            "conditional-v1",
            412,
            id="oversized",
        ),
        pytest.param("invalid", "future-contract", 400, id="unknown-contract"),
    ],
)
def test_rejects_malformed_editing_bases(
    client, auth_headers, make_printer, tag, contract, status
):
    printer = make_printer()
    url = f"/api/v1/printers/{printer.id}"
    response = client.patch(
        url,
        headers={
            **auth_headers,
            "If-Match": tag,
            "X-PrintStash-Edit-Contract": contract,
        },
        json={"name": "Unreviewed"},
    )
    assert response.status_code == status
    assert client.get(url, headers=auth_headers).json()["name"] == printer.name
