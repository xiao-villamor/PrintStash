"""E2E: public share links — create, anonymous read, revoke.

A share link must let an unauthenticated visitor read a model, and revoking it
must immediately deny further access.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e


def _seed_model(session) -> int:
    from app.db.models import Model

    model = Model(name="Shared Bracket", slug="shared-bracket", hash="c" * 64)
    session.add(model)
    session.commit()
    session.refresh(model)
    return model.id


@pytest.mark.asyncio
async def test_share_link_grants_then_revokes_anonymous_access(api, superuser_headers, e2e_db):
    model_id = _seed_model(e2e_db)

    created = await api.post(
        f"/api/v1/models/{model_id}/shares",
        json={"expires_in_days": 7, "allow_download": False},
        headers=superuser_headers,
    )
    assert created.status_code == 200, created.text
    payload = created.json()
    token = payload["token"]
    share_id = payload["id"]

    # Anonymous visitor (no auth header) can read the shared model.
    public = await api.get(f"/api/v1/share/{token}")
    assert public.status_code == 200, public.text
    assert public.json()["name"] == "Shared Bracket"

    # A bogus token is rejected.
    assert (await api.get("/api/v1/share/not-a-real-token")).status_code in (403, 404)

    # Revoke -> the same token no longer resolves.
    revoked = await api.delete(f"/api/v1/shares/{share_id}", headers=superuser_headers)
    assert revoked.status_code == 200, revoked.text
    after = await api.get(f"/api/v1/share/{token}")
    assert after.status_code in (403, 404, 410), after.text

    # Revoked links drop out of the owner's management list (the row is kept so
    # the token above stays permanently dead).
    listed = await api.get(f"/api/v1/models/{model_id}/shares", headers=superuser_headers)
    assert listed.status_code == 200, listed.text
    assert all(link["id"] != share_id for link in listed.json())
