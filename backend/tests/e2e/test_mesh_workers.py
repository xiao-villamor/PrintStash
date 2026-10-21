"""An administrator changes how many mesh jobs run at once, without a restart."""

from __future__ import annotations

import pytest

from app.core.config import _overlay
from app.db.models import LaneName

_URL = "/api/v1/config/mesh-workers"


@pytest.fixture
def restored_overlay():
    previous = dict(_overlay)
    yield
    _overlay.clear()
    _overlay.update(previous)


async def _native_lane(api, headers) -> int:
    overview = (await api.get("/api/v1/admin/work", headers=headers)).json()
    (lane,) = [
        row for row in overview["lanes"] if row["name"] == LaneName.DERIVE_NATIVE
    ]
    return lane["concurrency"]


class TestMeshWorkersE2E:
    @pytest.mark.asyncio
    async def test_a_chosen_count_resizes_the_native_lane_until_reset(
        self, api, superuser_headers, restored_overlay
    ):
        headers = superuser_headers
        current = await api.get(_URL, headers=headers)
        assert current.status_code == 200, current.text
        automatic = current.json()["automatic"]
        assert await _native_lane(api, headers) == automatic
        chosen = 1 if automatic > 1 else current.json()["maximum"]

        saved = await api.put(
            _URL,
            headers={**headers, "If-Match": current.headers["ETag"]},
            json={"jobs": chosen},
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["source"] == "database"
        assert await _native_lane(api, headers) == chosen

        reset = await api.delete(
            _URL, headers={**headers, "If-Match": saved.headers["ETag"]}
        )
        assert reset.status_code == 200, reset.text
        assert reset.json()["source"] == "automatic"
        assert await _native_lane(api, headers) == automatic
