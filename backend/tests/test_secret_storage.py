import pytest
from sqlalchemy import text
from sqlmodel import Session

from app.core import secrets as secrets_mod
from app.core.config import _overlay
from app.db.models import NotificationChannel, NotificationTarget, Printer, SystemConfig


def test_credentials_are_encrypted_in_database(db_session: Session) -> None:
    printer = Printer(name="Encrypted printer", api_key="moonraker-secret")
    config = SystemConfig(
        id=1,
        s3_access_key="storage-user",
        s3_secret_key="storage-secret",
        makerworld_token="makerworld-secret",
    )
    channel = NotificationChannel(
        name="Encrypted webhook",
        target=NotificationTarget.WEBHOOK,
        config_json='{"url":"https://hooks.example/secret-token"}',
    )
    db_session.add(printer)
    db_session.add(config)
    db_session.add(channel)
    db_session.commit()

    raw_printer = db_session.exec(
        text("SELECT api_key FROM printers WHERE id = :id").bindparams(id=printer.id)
    ).one()[0]
    raw_config = db_session.exec(
        text("SELECT s3_secret_key, makerworld_token FROM system_config WHERE id = 1")
    ).one()
    raw_channel = db_session.exec(
        text("SELECT config_json FROM notification_channels WHERE id = :id").bindparams(
            id=channel.id
        )
    ).one()[0]

    assert raw_printer.startswith("enc:v1:")
    assert "moonraker-secret" not in raw_printer
    assert all(value.startswith("enc:v1:") for value in raw_config)
    assert "secret-token" not in raw_channel

    db_session.expire_all()
    assert db_session.get(Printer, printer.id).api_key == "moonraker-secret"
    assert db_session.get(SystemConfig, 1).s3_secret_key == "storage-secret"
    assert "secret-token" in db_session.get(NotificationChannel, channel.id).config_json


def test_legacy_plaintext_credentials_remain_readable(db_session: Session) -> None:
    db_session.exec(
        text(
            "INSERT INTO printers (name, provider, moonraker_url, status, api_key, "
            "created_at, updated_at) VALUES "
            "('Legacy', 'MOONRAKER', '', 'UNKNOWN', 'legacy-secret', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
    )
    db_session.commit()
    printer = db_session.exec(
        text("SELECT id FROM printers WHERE name = 'Legacy'")
    ).one()

    assert db_session.get(Printer, printer[0]).api_key == "legacy-secret"


# --------------------------------------------------------------------------- #
# app.core.secrets — key material sourcing, encrypt/decrypt round trips, and
# the tamper/corruption error path. Direct unit coverage of the real
# functions (no mocking of encrypt/decrypt themselves).
# --------------------------------------------------------------------------- #


def test_encrypt_and_decrypt_secret_passthrough_for_none() -> None:
    assert secrets_mod.encrypt_secret(None) is None
    assert secrets_mod.decrypt_secret(None) is None


def test_encrypt_secret_is_idempotent_on_already_encrypted_value() -> None:
    token = secrets_mod.encrypt_secret("hunter2")
    assert token is not None and token.startswith("enc:v1:")
    # Re-encrypting an already-wrapped value must not double-wrap it.
    assert secrets_mod.encrypt_secret(token) == token


def test_decrypt_secret_passthrough_for_legacy_plaintext() -> None:
    # A value with no "enc:v1:" prefix is legacy plaintext, returned as-is.
    assert secrets_mod.decrypt_secret("plain-old-value") == "plain-old-value"


def test_encrypt_decrypt_round_trip_via_configured_key() -> None:
    token = secrets_mod.encrypt_secret("round-trip-me")
    assert token != "round-trip-me"
    assert secrets_mod.decrypt_secret(token) == "round-trip-me"


def test_decrypt_secret_raises_value_error_on_corrupted_token() -> None:
    token = secrets_mod.encrypt_secret("hunter2")
    corrupted = token[:-4] + "xxxx"  # mangle the Fernet payload -> InvalidToken
    with pytest.raises(ValueError, match="cannot be decrypted"):
        secrets_mod.decrypt_secret(corrupted)


def test_decrypt_secret_raises_when_key_material_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(_overlay, "secrets_key", "first-key-0123456789")
    token = secrets_mod.encrypt_secret("hunter2")

    monkeypatch.setitem(_overlay, "secrets_key", "a-totally-different-key")
    with pytest.raises(ValueError, match="cannot be decrypted"):
        secrets_mod.decrypt_secret(token)


def test_key_material_reads_from_file_when_no_configured_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    key_file = tmp_path / "secrets-key"
    key_file.write_bytes(b"file-backed-key-material\n")
    monkeypatch.setitem(_overlay, "secrets_key", "")
    monkeypatch.setitem(_overlay, "secrets_key_file", key_file)

    assert secrets_mod._key_material() == b"file-backed-key-material"


def test_key_material_generates_and_persists_a_new_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    key_file = tmp_path / "nested" / "secrets-key"
    monkeypatch.setitem(_overlay, "secrets_key", "")
    monkeypatch.setitem(_overlay, "secrets_key_file", key_file)
    assert not key_file.exists()

    material = secrets_mod._key_material()

    assert key_file.exists()
    assert key_file.read_bytes().strip() == material
    # A second call reads the now-persisted file back, unchanged.
    assert secrets_mod._key_material() == material


def test_key_material_handles_concurrent_create_race(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Two processes racing to generate the key file: the loser's exclusive
    ``os.open`` fails with FileExistsError and it must fall back to reading
    whatever the winner wrote, not crash or overwrite it."""
    key_file = tmp_path / "race-key"
    monkeypatch.setitem(_overlay, "secrets_key", "")
    monkeypatch.setitem(_overlay, "secrets_key_file", key_file)

    real_open = secrets_mod.os.open

    def _racing_open(path, flags, mode=0o777):
        # Simulate the winner writing the file an instant before our exclusive
        # create, then let our create fail like the real OS would.
        key_file.write_bytes(b"winner-key-material\n")
        raise FileExistsError()

    monkeypatch.setattr(secrets_mod.os, "open", _racing_open)
    try:
        material = secrets_mod._key_material()
    finally:
        monkeypatch.setattr(secrets_mod.os, "open", real_open)

    assert material == b"winner-key-material"
