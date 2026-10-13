"""The library page answers in time at the size PrintStash supports.

PrintStash commits to libraries of 25,000 collections and 100,000 Models
(docs/known-limitations.md). Issue #295 was a library a third that size whose
sidebar took over a minute; nothing measured it because no test had a library
that large. These tests seed one, then time each registered read.

They run in the ``scale`` lane (``./scripts/test.sh scale``) in Deep CI, never
in a PR run: seeding takes seconds per case and wall-clock depends on the
machine. The deterministic half of the same guarantee, which does run on every
PR, is ``test_read_scaling.py``.

Budgets are about three times what a developer machine measured, to absorb a
slower CI runner; a regression to quadratic work misses them by minutes, not
milliseconds. The growth test does not depend on the machine at all: the library
grows eight times, and a read may take at most sixteen times as long.
"""

from __future__ import annotations

import base64
import statistics
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db.models import Collection
from app.modules.library.model_views.browse import Cursor, _encode
from tests._library_reads import LIBRARY_READS, read_library
from tests.factories import build_tag, tag_collection
from tests.factories.library_scale import build_library_at_scale

pytestmark = pytest.mark.scale

SUPPORTED = {"collections": 25_000, "models": 100_000}
AN_EIGHTH = {"collections": 3_125, "models": 12_500}
THE_REST = {"collections": 21_875, "models": 87_500}

BUDGET_SECONDS = {
    "/api/v1/outliner/restore": 0.5,
    "/api/v1/outliner/collections": 0.5,
    "/api/v1/outliner/entries": 0.5,
    "/api/v1/outliner/search": 0.5,
    # The whole tree, 25,000 rows; measured 1.0s. A lazy tree replaces it (#295).
    "/api/v1/collections": 3.0,
    "/api/v1/collections/children": 0.5,
    "/api/v1/collections/lookup": 0.5,
    "/api/v1/collections/search": 0.5,
    "/api/v1/tags": 1.0,
    "/api/v1/models/page": 1.5,
    "/api/v1/models/browse": 1.5,
    "/api/v1/models/outliner": 0.5,
    "/api/v1/models/facets": 0.5,
    "/api/v1/models/stats": 0.5,
    "/api/v1/multipart-models": 0.5,
    "/api/v1/documents": 0.5,
    "/api/v1/documents/trash": 0.5,
    "/api/v1/multipart-builds": 0.5,
}
# Linear work grows eight times with an eight-times library; quadratic, 64.
MAX_GROWTH = 16
# Below this a read is fast at any size, and timer noise dominates the ratio.
NOISE_FLOOR_SECONDS = 0.05


def _median_seconds(
    client: TestClient,
    path: str,
    params: dict[str, Any],
    headers: dict[str, str],
    *,
    expected_tag_count: int | None = None,
) -> float:
    """Median of three timed reads, after one that warms per-process caches."""
    warm = read_library(client, path, params, headers)
    assert warm.status_code == 200, warm.text
    if expected_tag_count is not None:
        assert warm.json()[0]["model_count"] == expected_tag_count
    samples = []
    for _ in range(3):
        started = time.perf_counter()
        response = read_library(client, path, params, headers)
        samples.append(time.perf_counter() - started)
        assert response.status_code == 200, response.text
    return statistics.median(samples)


class TestLibraryReadsAtScale:
    @pytest.mark.parametrize("sort", ["name-asc", "date-desc"], ids=["name", "date"])
    def test_answers_a_late_mixed_browse_page_within_budget(
        self,
        client: TestClient,
        db_session: Session,
        reader: tuple[dict[str, str], Collection],
        sort: str,
    ) -> None:
        headers, root = reader
        build_library_at_scale(
            db_session, under=root, **SUPPORTED, multipart_models=10_000
        )
        path = "/api/v1/models/browse"
        params = {"view": "all", "limit": 60, "sort": sort}
        first = read_library(client, path, params, headers)
        assert first.status_code == 200, first.text
        body = first.json()
        assert body["total"] == 110_000
        encoded = body["next_cursor"].split(".")[0]
        cursor = Cursor.model_validate_json(
            base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        )
        # The public cursor remains opaque. This test-only setup retains the
        # server's real caller/filter/revision binding while placing the benchmark
        # near the tail, without timing a thousand unrelated earlier requests.
        cursor = Cursor.model_validate(
            cursor.model_dump() | {"offset": body["total"] - 120}
        )
        tail_params = params | {"cursor": _encode(cursor)}
        warm = client.get(path, params=tail_params, headers=headers)
        assert warm.status_code == 200, warm.text
        samples = []
        for _ in range(3):
            started = time.perf_counter()
            response = client.get(path, params=tail_params, headers=headers)
            samples.append(time.perf_counter() - started)
            assert response.status_code == 200, response.text
            page = response.json()
            assert len(page["items"]) == 60
            assert page["next_cursor"] is not None
            assert page["total"] == 110_000
        print(f"late mixed browse sort={sort} samples_seconds={samples}")
        assert statistics.median(samples) <= BUDGET_SECONDS[path], samples

    def test_answers_within_budget_at_the_supported_scale_without_tags(
        self,
        client: TestClient,
        db_session: Session,
        reader: tuple[dict[str, str], Collection],
    ) -> None:
        headers, root = reader
        build_library_at_scale(db_session, under=root, **SUPPORTED)
        response = client.get("/api/v1/tags", headers=headers)

        assert response.status_code == 200
        assert response.json() == []
        seconds = _median_seconds(client, "/api/v1/tags", {}, headers)
        assert seconds <= BUDGET_SECONDS["/api/v1/tags"]

    @pytest.mark.parametrize(("path", "params"), LIBRARY_READS)
    def test_answers_within_budget_at_the_supported_scale(
        self,
        client: TestClient,
        db_session: Session,
        reader: tuple[dict[str, str], Collection],
        path: str,
        params: dict[str, Any],
    ) -> None:
        headers, root = reader
        if path == "/api/v1/tags":
            tag_collection(db_session, root, build_tag(db_session, "Scale tag"))
        seeded = build_library_at_scale(db_session, under=root, **SUPPORTED)
        if path == "/api/v1/outliner/entries":
            params = params | {"collection_id": seeded.collection_ids[0]}

        seconds = _median_seconds(
            client,
            path,
            params,
            headers,
            expected_tag_count=SUPPORTED["models"] if path == "/api/v1/tags" else None,
        )

        assert seconds <= BUDGET_SECONDS[path], f"{path} took {seconds:.2f}s"

    @pytest.mark.parametrize(("path", "params"), LIBRARY_READS)
    def test_grows_no_faster_than_the_library(
        self,
        client: TestClient,
        db_session: Session,
        reader: tuple[dict[str, str], Collection],
        path: str,
        params: dict[str, Any],
    ) -> None:
        headers, root = reader
        if path == "/api/v1/tags":
            tag_collection(db_session, root, build_tag(db_session, "Scale tag"))
        seeded = build_library_at_scale(db_session, under=root, **AN_EIGHTH)
        if path == "/api/v1/outliner/entries":
            params = params | {"collection_id": seeded.collection_ids[0]}
        small = max(
            _median_seconds(
                client,
                path,
                params,
                headers,
                expected_tag_count=AN_EIGHTH["models"]
                if path == "/api/v1/tags"
                else None,
            ),
            NOISE_FLOOR_SECONDS,
        )
        build_library_at_scale(db_session, under=root, **THE_REST)

        large = _median_seconds(
            client,
            path,
            params,
            headers,
            expected_tag_count=SUPPORTED["models"] if path == "/api/v1/tags" else None,
        )

        assert large / small <= MAX_GROWTH, f"{path}: {small:.3f}s -> {large:.3f}s"
