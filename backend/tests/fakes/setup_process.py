"""Independent API workers for first-owner concurrency contracts."""

from pathlib import Path


def claim_installation(url, directory, username, barrier, outcomes):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlmodel import Session, create_engine

    from app.api.v1.setup import router
    from app.core.config import _overlay
    from app.db.session import get_session

    _overlay.update(
        setup_mode="trusted_network",
        setup_allowed_hosts="localhost",
        jwt_secret="setup-process-contract-secret-0123456789",
        secrets_key="setup-process-secrets-0123456789",
    )
    for name in ("data_dir", "thumb_dir", "staging_dir", "backup_dir"):
        _overlay[name] = Path(directory) / name
    engine = create_engine(url)

    def sessions():
        with Session(engine) as session:
            yield session

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_session] = sessions
    try:
        with TestClient(
            app, base_url="http://localhost", headers={"Origin": "http://localhost"}
        ) as client:
            preparation = client.post("/api/v1/setup/session")
            if preparation.status_code != 200:
                outcomes.put((preparation.status_code, preparation.text))
                return
            client.headers["X-PrintStash-Setup-CSRF"] = preparation.json()["csrf"]
            barrier.wait(timeout=30)
            response = client.post(
                "/api/v1/setup",
                json={
                    "username": username,
                    "password": "SetupContractPassword123",
                    "storage_backend": "local",
                },
            )
            outcomes.put(
                (response.status_code, response.json().get("detail", "created"))
            )
    except Exception as error:
        outcomes.put((500, type(error).__name__))
    finally:
        engine.dispose()


def race_setup_workers(url, directory):
    """Run two browser registrations after both workers have obtained sessions."""
    import multiprocessing

    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    outcomes = context.Queue()
    processes = [
        context.Process(
            target=claim_installation,
            args=(url, str(directory), name, barrier, outcomes),
        )
        for name in ("first", "second")
    ]
    try:
        for process in processes:
            process.start()
        results = [outcomes.get(timeout=60) for _ in processes]
        for process in processes:
            process.join(timeout=10)
        return results
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(timeout=10)
        outcomes.close()
