"""Set up an isolated backup-recovery app through its public API."""

from app.services.setup_token import current_setup_token


async def setup_and_login(api, tmp_path) -> dict[str, str]:
    r = await api.post(
        "/api/v1/setup",
        json={
            "setup_token": current_setup_token(),
            "username": "owner",
            "password": "Password123",
            "storage_backend": "local",
            "data_dir": str(tmp_path / "files"),
            "thumb_dir": str(tmp_path / "thumbs"),
        },
    )
    assert r.status_code == 201, r.text
    from app.services.storage_backend import init_backend

    init_backend()
    return {"Authorization": f"Bearer {r.json()['access_token']}"}
