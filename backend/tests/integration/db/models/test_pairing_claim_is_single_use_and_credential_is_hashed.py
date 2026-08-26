"""Defends pairing claim is single use and credential is hashed at the db models integration boundary.

A regression could commit partial, unauthenticated, or internally inconsistent database state.
"""

from __future__ import annotations

from ._browser_pairing_api_shared import (
    BrowserDevice,
    BrowserPairingCode,
    HTTPException,
    Session,
    SQLModel,
    TestClient,
    User,
    _claim_limit,
    _headers,
    _set_sqlite_pragmas,
    create_engine,
    event,
    hash_password,
    hashlib,
    postgresql,
    provider_service,
    pytest,
    require_browser_import_user,
    select,
    sqlite,
    threading,
    timedelta,
    utcnow,
)


def test_pairing_claim_is_single_use_and_credential_is_hashed(
    client: TestClient, db_session: Session
) -> None:
    headers = _headers(db_session, "pair-user")
    created = client.post("/api/v1/browser-pairings", headers=headers)
    assert created.status_code == 201
    code = created.json()["code"]
    claimed = client.post(
        "/api/v1/browser-pairings/claim", json={"code": code, "name": "Firefox"}
    )
    assert claimed.status_code == 200
    credential = claimed.json()["credential"]
    assert (
        credential not in client.get("/api/v1/browser-pairings", headers=headers).text
    )
    device = db_session.exec(
        select(BrowserDevice).where(BrowserDevice.name == "Firefox")
    ).one()
    assert device.credential_hash != credential
    assert (
        client.post(
            "/api/v1/browser-pairings/claim", json={"code": code, "name": "Replay"}
        ).json()["detail"]
        == "invalid_or_expired_pairing_code"
    )
    assert (
        db_session.exec(
            select(BrowserPairingCode).where(
                BrowserPairingCode.code_hash
                == hashlib.sha256(code.encode()).hexdigest()
            )
        )
        .one()
        .used_at
        is not None
    )


def test_revoked_device_can_be_repaired_with_the_same_name(
    client: TestClient, db_session: Session
) -> None:
    headers = _headers(db_session, "re-pair-user")
    db_session.rollback()
    first_code = client.post("/api/v1/browser-pairings", headers=headers).json()["code"]
    first = client.post(
        "/api/v1/browser-pairings/claim",
        json={"code": first_code, "name": "Default browser"},
    )
    assert first.status_code == 200
    old_credential = first.json()["credential"]
    old_device = first.json()["device"]

    assert (
        client.delete(
            f"/api/v1/browser-pairings/{old_device['id']}", headers=headers
        ).status_code
        == 204
    )
    second_code = client.post("/api/v1/browser-pairings", headers=headers).json()[
        "code"
    ]
    second = client.post(
        "/api/v1/browser-pairings/claim",
        json={"code": second_code, "name": "Default browser"},
    )

    assert second.status_code == 200
    new_credential = second.json()["credential"]
    assert new_credential != old_credential
    assert second.json()["device"]["id"] == old_device["id"]
    assert second.json()["device"]["revoked_at"] is None

    with pytest.raises(HTTPException) as exc_info:
        require_browser_import_user(old_credential, db_session)
    assert exc_info.value.status_code == 401
    assert (
        require_browser_import_user(new_credential, db_session).username
        == "re-pair-user"
    )


def test_active_duplicate_name_rejects_without_spending_code(
    client: TestClient, db_session: Session
) -> None:
    headers = _headers(db_session, "active-duplicate-user")
    db_session.rollback()
    first_code = client.post("/api/v1/browser-pairings", headers=headers).json()["code"]
    assert (
        client.post(
            "/api/v1/browser-pairings/claim",
            json={"code": first_code, "name": "Default browser"},
        ).status_code
        == 200
    )
    second_code = client.post("/api/v1/browser-pairings", headers=headers).json()[
        "code"
    ]
    conflict = client.post(
        "/api/v1/browser-pairings/claim",
        json={"code": second_code, "name": "Default browser"},
    )

    assert conflict.status_code == 409
    assert conflict.json()["detail"] == "browser_device_name_in_use"
    second_row = db_session.exec(
        select(BrowserPairingCode).where(
            BrowserPairingCode.code_hash
            == hashlib.sha256(second_code.encode()).hexdigest()
        )
    ).one()
    assert second_row.used_at is None
    assert (
        client.post(
            "/api/v1/browser-pairings/claim",
            json={"code": second_code, "name": "Different browser"},
        ).status_code
        == 200
    )


def test_pairing_code_ttl_is_five_minutes(
    client: TestClient, db_session: Session
) -> None:
    headers = _headers(db_session, "pair-ttl-user")
    created = client.post("/api/v1/browser-pairings", headers=headers)
    assert created.status_code == 201
    expires_at = created.json()["expires_at"]

    row = db_session.exec(select(BrowserPairingCode)).one()
    assert (
        abs(
            (row.expires_at - row.created_at).total_seconds()
            - timedelta(minutes=5).total_seconds()
        )
        < 1
    )
    assert expires_at.startswith(row.expires_at.isoformat()[:19])


def test_expired_pairing_code_is_stable_and_does_not_increment_attempts(
    client: TestClient, db_session: Session
) -> None:
    headers = _headers(db_session, "expired-pair-user")
    code = client.post("/api/v1/browser-pairings", headers=headers).json()["code"]
    row = db_session.exec(select(BrowserPairingCode)).one()
    row.expires_at = utcnow() - timedelta(seconds=1)
    db_session.commit()

    response = client.post(
        "/api/v1/browser-pairings/claim",
        json={"code": code, "name": "Expired"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "invalid_or_expired_pairing_code"
    db_session.refresh(row)
    assert row.attempts == 0


def test_pairing_code_locks_after_five_failed_exchange_attempts(
    client: TestClient, db_session: Session
) -> None:
    headers = _headers(db_session, "locked-pair-user")
    user = db_session.exec(
        select(User).where(User.username == "locked-pair-user")
    ).one()
    assert user.id is not None
    for index in range(10):
        db_session.add(
            BrowserDevice(
                user_id=user.id,
                name=f"existing-{index}",
                credential_hash=hashlib.sha256(
                    f"existing-{index}".encode()
                ).hexdigest(),
            )
        )
    db_session.commit()
    code = client.post("/api/v1/browser-pairings", headers=headers).json()["code"]
    row = db_session.exec(select(BrowserPairingCode)).one()

    for _ in range(5):
        assert provider_service.claim_pairing_code(db_session, code, "Locked") is None
        db_session.commit()
    db_session.refresh(row)
    assert row.attempts == 5

    for device in db_session.exec(
        select(BrowserDevice).where(BrowserDevice.user_id == user.id)
    ):
        db_session.delete(device)
    db_session.commit()
    assert provider_service.claim_pairing_code(db_session, code, "After unlock") is None
    db_session.commit()
    db_session.refresh(row)
    assert row.attempts == 5


def test_public_pairing_claim_persists_live_failures_and_locks_code(
    client: TestClient, db_session: Session
) -> None:
    _claim_limit.limiter.reset()
    headers = _headers(db_session, "public-locked-pair-user")
    user = db_session.exec(
        select(User).where(User.username == "public-locked-pair-user")
    ).one()
    assert user.id is not None
    for index in range(10):
        db_session.add(
            BrowserDevice(
                user_id=user.id,
                name=f"public-existing-{index}",
                credential_hash=hashlib.sha256(
                    f"public-existing-{index}".encode()
                ).hexdigest(),
            )
        )
    db_session.commit()
    code = client.post("/api/v1/browser-pairings", headers=headers).json()["code"]

    for _ in range(5):
        response = client.post(
            "/api/v1/browser-pairings/claim",
            json={"code": code, "name": "blocked-browser"},
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "invalid_or_expired_pairing_code"
    row = db_session.exec(select(BrowserPairingCode)).one()
    db_session.refresh(row)
    assert row.attempts == 5

    for device in db_session.exec(
        select(BrowserDevice).where(BrowserDevice.user_id == user.id)
    ):
        db_session.delete(device)
    db_session.commit()
    response = client.post(
        "/api/v1/browser-pairings/claim",
        json={"code": code, "name": "after-cap-is-cleared"},
    )
    assert response.status_code == 400
    db_session.refresh(row)
    assert row.attempts == 5
    _claim_limit.limiter.reset()


def test_public_invalid_secret_does_not_spend_pairing_attempt(
    client: TestClient, db_session: Session
) -> None:
    _claim_limit.limiter.reset()
    headers = _headers(db_session, "public-invalid-secret-user")
    code = client.post("/api/v1/browser-pairings", headers=headers).json()["code"]
    row = db_session.exec(select(BrowserPairingCode)).one()

    response = client.post(
        "/api/v1/browser-pairings/claim",
        json={"code": f"wrong-{code}", "name": "invalid-secret"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "invalid_or_expired_pairing_code"
    db_session.refresh(row)
    assert row.attempts == 0
    assert code not in repr(row)
    _claim_limit.limiter.reset()


def test_concurrent_pairing_exchange_consumes_code_once(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'pairing-race.sqlite'}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    event.listen(engine, "connect", _set_sqlite_pragmas)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as seed:
        user = User(
            username="pair-race-user", hashed_password=hash_password("Password123")
        )
        seed.add(user)
        seed.commit()
        seed.refresh(user)
        assert user.id is not None
        code, _ = provider_service.create_pairing_code(seed, user.id)
        seed.commit()

    start = threading.Barrier(3)
    outcomes: list[bool] = []

    def exchange(name: str) -> None:
        with Session(engine) as session:
            start.wait(timeout=5)
            claimed = provider_service.claim_pairing_code(session, code, name)
            if claimed is not None:
                session.commit()
                outcomes.append(True)
            else:
                session.rollback()
                outcomes.append(False)

    threads = [
        threading.Thread(target=exchange, args=("Race A",)),
        threading.Thread(target=exchange, args=("Race B",)),
    ]
    for thread in threads:
        thread.start()
    start.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=10)

    assert sorted(outcomes) == [False, True]
    with Session(engine) as session:
        assert len(session.exec(select(BrowserDevice)).all()) == 1
        assert session.exec(select(BrowserPairingCode)).one().used_at is not None
    engine.dispose()


def test_pairing_user_lock_statement_renders_for_sqlite_and_postgres() -> None:
    statement = provider_service._pairing_user_lock_statement("code-hash")

    sqlite_sql = str(statement.compile(dialect=sqlite.dialect()))
    postgres_sql = str(statement.compile(dialect=postgresql.dialect()))

    for rendered in (sqlite_sql, postgres_sql):
        assert "UPDATE users SET updated_at=users.updated_at" in rendered
        assert "SELECT browser_pairing_codes.user_id" in rendered
        assert "browser_pairing_codes.code_hash" in rendered


def test_concurrent_distinct_pairing_codes_never_exceed_device_cap(tmp_path) -> None:
    """The ten-device invariant holds when two codes race for the last slot."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'pairing-cap-race.sqlite'}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    event.listen(engine, "connect", _set_sqlite_pragmas)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as seed:
        user = User(
            username="pair-cap-race-user", hashed_password=hash_password("Password123")
        )
        seed.add(user)
        seed.commit()
        seed.refresh(user)
        assert user.id is not None
        user_id = user.id
        for index in range(9):
            seed.add(
                BrowserDevice(
                    user_id=user_id,
                    name=f"existing-{index}",
                    credential_hash=hashlib.sha256(
                        f"existing-{index}".encode()
                    ).hexdigest(),
                )
            )
        first_code, _ = provider_service.create_pairing_code(seed, user_id)
        second_code, _ = provider_service.create_pairing_code(seed, user_id)
        seed.commit()

    start = threading.Barrier(3)
    outcomes: list[bool] = []
    failures: list[BaseException] = []

    def exchange(code: str, name: str) -> None:
        try:
            with Session(engine) as session:
                start.wait(timeout=5)
                claimed = provider_service.claim_pairing_code(session, code, name)
                if claimed is not None:
                    session.commit()
                    outcomes.append(True)
                else:
                    session.rollback()
                    outcomes.append(False)
        except BaseException as exc:  # pragma: no cover - surfaced below
            failures.append(exc)

    threads = [
        threading.Thread(target=exchange, args=(first_code, "Race A")),
        threading.Thread(target=exchange, args=(second_code, "Race B")),
    ]
    for thread in threads:
        thread.start()
    start.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=10)

    assert not failures
    assert sorted(outcomes) == [False, True]
    with Session(engine) as session:
        active = session.exec(
            select(BrowserDevice).where(
                BrowserDevice.user_id == user_id,
                BrowserDevice.revoked_at.is_(None),  # type: ignore[union-attr]
            )
        ).all()
        assert len(active) == 10
    engine.dispose()


def test_concurrent_distinct_pairing_codes_same_name_have_one_stable_conflict(
    tmp_path,
) -> None:
    """Per-user serialization makes a same-name race deterministic and safe."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'pairing-same-name-race.sqlite'}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    event.listen(engine, "connect", _set_sqlite_pragmas)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as seed:
        user = User(
            username="pair-same-name-race-user",
            hashed_password=hash_password("Password123"),
        )
        seed.add(user)
        seed.commit()
        seed.refresh(user)
        assert user.id is not None
        user_id = user.id
        first_code, _ = provider_service.create_pairing_code(seed, user_id)
        second_code, _ = provider_service.create_pairing_code(seed, user_id)
        seed.commit()

    start = threading.Barrier(3)
    outcomes: dict[str, str] = {}
    failures: list[BaseException] = []

    def exchange(code: str) -> None:
        try:
            with Session(engine) as session:
                start.wait(timeout=5)
                try:
                    claimed = provider_service.claim_pairing_code(
                        session, code, "Same browser"
                    )
                except provider_service.BrowserDeviceNameInUseError:
                    session.rollback()
                    outcomes[code] = "name_in_use"
                else:
                    assert claimed is not None
                    session.commit()
                    outcomes[code] = "success"
        except BaseException as exc:  # pragma: no cover - surfaced below
            failures.append(exc)

    threads = [
        threading.Thread(target=exchange, args=(first_code,)),
        threading.Thread(target=exchange, args=(second_code,)),
    ]
    for thread in threads:
        thread.start()
    start.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=10)

    assert not failures
    assert sorted(outcomes.values()) == ["name_in_use", "success"]
    rejected_code = next(
        code for code, result in outcomes.items() if result == "name_in_use"
    )
    with Session(engine) as session:
        active = session.exec(
            select(BrowserDevice).where(
                BrowserDevice.user_id == user_id,
                BrowserDevice.name == "Same browser",
                BrowserDevice.revoked_at.is_(None),  # type: ignore[union-attr]
            )
        ).all()
        assert len(active) == 1
        assert (
            provider_service.claim_pairing_code(
                session, rejected_code, "Different browser"
            )
            is not None
        )
        session.commit()
    engine.dispose()
