"""Exchanging one pairing code for one browser credential, exactly once.

The claim path is reachable without a login, so its concurrency properties *are* its
security properties. Three of them are enforced in SQL rather than in Python, because a
read-then-write would let two racing claims both pass the check:

* a **per-user write lock** taken before the device count is read, so two distinct codes
  for the same owner cannot both take the last slot under the ten-device cap;
* a **conditional one-time reservation**, so two claims of the same code produce at most
  one credential;
* a **compare-and-increment** on the attempt counter, so concurrent failures cannot lose
  a count and leave a code guessable past its five-attempt lock.

Only a *live* code's failures are counted. An unknown value is indistinguishable from an
expired or replayed one, so guessing cannot burn somebody else's code.
"""

from __future__ import annotations

import hashlib
import threading
from datetime import timedelta

import pytest
from sqlalchemy.dialects import postgresql, sqlite
from sqlmodel import Session, select

from app.core.time import ensure_utc, utcnow
from app.db.models import BrowserDevice, BrowserPairingCode, User
from app.services import provider_connections as service
from tests.factories import build_user

DEVICE_CAP = 10
MAX_ATTEMPTS = 5


@pytest.fixture
def user(db_session: Session) -> User:
    row = build_user(db_session, "pairing-service")
    db_session.refresh(row)
    assert row.id is not None
    return row


def _fill_devices(session: Session, user_id: int, count: int) -> None:
    for index in range(count):
        session.add(
            BrowserDevice(
                user_id=user_id,
                name=f"existing-{index}",
                credential_hash=hashlib.sha256(
                    f"existing-{index}".encode()
                ).hexdigest(),
            )
        )
    session.commit()


class TestCredentialMatches:
    def test_accepts_the_value_the_hash_was_made_from(self) -> None:
        digest = hashlib.sha256(b"a-credential").hexdigest()

        assert service.credential_matches("a-credential", digest) is True

    def test_rejects_any_other_value(self) -> None:
        digest = hashlib.sha256(b"a-credential").hexdigest()

        assert service.credential_matches("another-credential", digest) is False


class TestCreatePairingCode:
    def test_stores_only_a_hash_of_the_code(
        self, db_session: Session, user: User
    ) -> None:
        assert user.id is not None

        raw, row = service.create_pairing_code(db_session, user.id)

        assert row.code_hash == hashlib.sha256(raw.encode()).hexdigest()
        assert raw not in repr(row)

    def test_gives_the_code_five_minutes_to_live(
        self, db_session: Session, user: User
    ) -> None:
        assert user.id is not None

        _, row = service.create_pairing_code(db_session, user.id)

        assert abs(ensure_utc(row.expires_at) - utcnow() - timedelta(minutes=5)) < (
            timedelta(seconds=5)
        )


class TestPairingUserLockStatement:
    def test_locks_the_owner_of_the_code_on_both_dialects(self) -> None:
        statement = service._pairing_user_lock_statement("code-hash")

        rendered = [
            str(statement.compile(dialect=sqlite.dialect())),
            str(statement.compile(dialect=postgresql.dialect())),
        ]

        # A no-op write to the owner's row: the point is the lock, not the value.
        assert all(
            "UPDATE users SET updated_at=users.updated_at" in s for s in rendered
        )
        assert all("SELECT browser_pairing_codes.user_id" in s for s in rendered)
        assert all("browser_pairing_codes.code_hash" in s for s in rendered)


class TestClaimPairingCode:
    def test_hands_back_a_credential_with_the_device_it_made(
        self, db_session: Session, user: User
    ) -> None:
        assert user.id is not None
        raw, _ = service.create_pairing_code(db_session, user.id)
        db_session.commit()

        claimed = service.claim_pairing_code(db_session, raw, "Firefox")

        assert claimed is not None
        credential, device = claimed
        assert device.name == "Firefox"
        assert service.credential_matches(credential, device.credential_hash)

    def test_reuses_a_revoked_devices_row(
        self, db_session: Session, user: User
    ) -> None:
        assert user.id is not None
        first_raw, _ = service.create_pairing_code(db_session, user.id)
        second_raw, _ = service.create_pairing_code(db_session, user.id)
        db_session.commit()
        claimed = service.claim_pairing_code(db_session, first_raw, "Same browser")
        assert claimed is not None
        claimed[1].revoked_at = utcnow()
        db_session.commit()

        repaired = service.claim_pairing_code(db_session, second_raw, "Same browser")

        assert repaired is not None
        assert repaired[1].id == claimed[1].id
        assert repaired[1].revoked_at is None

    def test_refuses_a_code_that_was_already_claimed(
        self, db_session: Session, user: User
    ) -> None:
        assert user.id is not None
        raw, _ = service.create_pairing_code(db_session, user.id)
        db_session.commit()
        service.claim_pairing_code(db_session, raw, "First")
        db_session.commit()

        assert service.claim_pairing_code(db_session, raw, "Replay") is None

    def test_refuses_a_code_that_expired(self, db_session: Session, user: User) -> None:
        assert user.id is not None
        raw, row = service.create_pairing_code(db_session, user.id)
        row.expires_at = utcnow().replace(tzinfo=None) - timedelta(seconds=1)
        db_session.commit()

        assert service.claim_pairing_code(db_session, raw, "Expired") is None

    def test_refuses_a_code_this_deployment_never_issued(
        self, db_session: Session
    ) -> None:
        assert service.claim_pairing_code(db_session, "forged", "Guess") is None

    def test_raises_when_an_active_device_already_uses_the_name(
        self, db_session: Session, user: User
    ) -> None:
        assert user.id is not None
        first_raw, _ = service.create_pairing_code(db_session, user.id)
        second_raw, _ = service.create_pairing_code(db_session, user.id)
        db_session.commit()
        service.claim_pairing_code(db_session, first_raw, "Same browser")
        db_session.commit()

        with pytest.raises(service.BrowserDeviceNameInUseError):
            service.claim_pairing_code(db_session, second_raw, "Same browser")

    def test_refuses_a_claim_that_would_pass_the_device_cap(
        self, db_session: Session, user: User
    ) -> None:
        assert user.id is not None
        _fill_devices(db_session, user.id, DEVICE_CAP)
        raw, _ = service.create_pairing_code(db_session, user.id)
        db_session.commit()

        assert service.claim_pairing_code(db_session, raw, "One too many") is None

    def test_spends_an_attempt_on_a_live_code_refused_at_the_cap(
        self, db_session: Session, user: User
    ) -> None:
        assert user.id is not None
        _fill_devices(db_session, user.id, DEVICE_CAP)
        raw, row = service.create_pairing_code(db_session, user.id)
        db_session.commit()

        service.claim_pairing_code(db_session, raw, "One too many")
        db_session.commit()

        db_session.refresh(row)
        assert row.attempts == 1

    def test_locks_a_code_after_five_live_failures(
        self, db_session: Session, user: User
    ) -> None:
        assert user.id is not None
        _fill_devices(db_session, user.id, DEVICE_CAP)
        raw, row = service.create_pairing_code(db_session, user.id)
        db_session.commit()
        for _ in range(MAX_ATTEMPTS):
            service.claim_pairing_code(db_session, raw, "Locked")
            db_session.commit()
        for device in db_session.exec(select(BrowserDevice)):
            db_session.delete(device)
        db_session.commit()

        assert service.claim_pairing_code(db_session, raw, "After unlock") is None

    def test_stops_counting_attempts_once_a_code_is_locked(
        self, db_session: Session, user: User
    ) -> None:
        assert user.id is not None
        _fill_devices(db_session, user.id, DEVICE_CAP)
        raw, row = service.create_pairing_code(db_session, user.id)
        db_session.commit()
        for _ in range(MAX_ATTEMPTS + 3):
            service.claim_pairing_code(db_session, raw, "Locked")
            db_session.commit()

        db_session.refresh(row)
        assert row.attempts == MAX_ATTEMPTS

    def test_spends_no_attempt_on_a_value_that_matches_no_code(
        self, db_session: Session, user: User
    ) -> None:
        assert user.id is not None
        _, row = service.create_pairing_code(db_session, user.id)
        db_session.commit()

        service.claim_pairing_code(db_session, "not-the-code", "Guess")
        db_session.commit()

        # Otherwise anyone could burn somebody else's code by guessing at it.
        db_session.refresh(row)
        assert row.attempts == 0

    def test_consumes_a_code_once_when_two_claims_race(self, file_engine) -> None:
        engine = file_engine("pairing-race")
        with Session(engine) as seed:
            owner = build_user(seed, "pair-race")
            assert owner.id is not None
            raw, _ = service.create_pairing_code(seed, owner.id)
            seed.commit()
        outcomes = _race(engine, [(raw, "Race A"), (raw, "Race B")])

        assert sorted(outcomes) == [False, True]
        with Session(engine) as check:
            assert len(check.exec(select(BrowserDevice)).all()) == 1
            assert check.exec(select(BrowserPairingCode)).one().used_at is not None

    def test_holds_the_device_cap_when_two_distinct_codes_race(
        self, file_engine
    ) -> None:
        engine = file_engine("pairing-cap-race")
        with Session(engine) as seed:
            owner = build_user(seed, "pair-cap-race")
            assert owner.id is not None
            user_id = owner.id
            _fill_devices(seed, user_id, DEVICE_CAP - 1)
            first, _ = service.create_pairing_code(seed, user_id)
            second, _ = service.create_pairing_code(seed, user_id)
            seed.commit()

        outcomes = _race(engine, [(first, "Race A"), (second, "Race B")])

        assert sorted(outcomes) == [False, True]
        with Session(engine) as check:
            active = check.exec(
                select(BrowserDevice).where(
                    BrowserDevice.user_id == user_id,
                    BrowserDevice.revoked_at.is_(None),  # type: ignore[union-attr]
                )
            ).all()
            assert len(active) == DEVICE_CAP

    def test_gives_one_stable_conflict_when_two_codes_race_for_one_name(
        self, file_engine
    ) -> None:
        engine = file_engine("pairing-same-name-race")
        with Session(engine) as seed:
            owner = build_user(seed, "pair-same-name-race")
            assert owner.id is not None
            user_id = owner.id
            first, _ = service.create_pairing_code(seed, user_id)
            second, _ = service.create_pairing_code(seed, user_id)
            seed.commit()

        outcomes = _race(engine, [(first, "Same browser"), (second, "Same browser")])

        # Serialization makes the loser a *deterministic* conflict, not a crash.
        assert sorted(outcomes, key=str) == [True, "name_in_use"]
        with Session(engine) as check:
            active = check.exec(
                select(BrowserDevice).where(
                    BrowserDevice.name == "Same browser",
                    BrowserDevice.revoked_at.is_(None),  # type: ignore[union-attr]
                )
            ).all()
            assert len(active) == 1


def _race(engine, claims: list[tuple[str, str]]) -> list[object]:
    """Run every claim concurrently on its own connection; report each outcome.

    An outcome is ``True`` (a credential was issued), ``False`` (the code was refused),
    or ``"name_in_use"``.
    """
    start = threading.Barrier(len(claims) + 1)
    outcomes: list[object] = []
    failures: list[BaseException] = []

    def run(code: str, name: str) -> None:
        try:
            with Session(engine) as session:
                start.wait(timeout=5)
                try:
                    claimed = service.claim_pairing_code(session, code, name)
                except service.BrowserDeviceNameInUseError:
                    session.rollback()
                    outcomes.append("name_in_use")
                    return
                if claimed is None:
                    session.rollback()
                    outcomes.append(False)
                else:
                    session.commit()
                    outcomes.append(True)
        except BaseException as exc:  # pragma: no cover - surfaced by the caller
            failures.append(exc)

    threads = [threading.Thread(target=run, args=claim) for claim in claims]
    for thread in threads:
        thread.start()
    start.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=10)

    assert not failures, failures
    return outcomes
