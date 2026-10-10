"""The reads a library page makes, registered once for every scaling test.

``tests/repo/test_read_scaling.py`` asserts each keeps the same shape as the
library grows; ``tests/repo/test_read_scale_budgets.py`` times each at the
supported scale. A new collection-scoped listing belongs here in the same PR,
so both apply to it (#295).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

# Pages are requested smaller than the smallest seeded library, so every size
# fills one page and only the library behind it grows.
LIBRARY_READS = [
    # The smallest corpus has eight direct siblings; fill each page before comparing scale.
    pytest.param(
        "/api/v1/outliner/restore",
        {"limit": 5, "expanded_paths": ["shared"]},
        id="outliner-restore",
    ),
    pytest.param("/api/v1/models/browse", {"limit": 10}, id="mixed-browse"),
    pytest.param(
        "/api/v1/outliner/collections", {"limit": 10}, id="outliner-collections"
    ),
    pytest.param("/api/v1/outliner/entries", {"limit": 10}, id="outliner-entries"),
    pytest.param(
        "/api/v1/outliner/search", {"limit": 10, "q": "model"}, id="outliner-search"
    ),
    pytest.param("/api/v1/collections", {}, id="collections"),
    pytest.param("/api/v1/collections/children", {"limit": 10}, id="collection-roots"),
    pytest.param(
        "/api/v1/collections/lookup", {"path": "shared"}, id="collection-lookup"
    ),
    pytest.param("/api/v1/collections/search", {"limit": 10}, id="collection-search"),
    pytest.param("/api/v1/tags", {}, id="tags"),
    pytest.param("/api/v1/models/page", {"limit": 10}, id="models-page"),
    pytest.param("/api/v1/models/outliner", {"limit": 10}, id="outliner"),
    pytest.param("/api/v1/models/facets", {}, id="facets"),
    pytest.param("/api/v1/models/stats", {}, id="vault-stats"),
    pytest.param("/api/v1/multipart-models", {"limit": 10}, id="multipart-models"),
    pytest.param("/api/v1/documents", {"limit": 10}, id="documents"),
    pytest.param("/api/v1/documents/trash", {}, id="document-trash"),
    pytest.param("/api/v1/multipart-builds", {"limit": 10}, id="multipart-builds"),
]


def read_library(
    client: TestClient, path: str, params: dict[str, Any], headers: dict[str, str]
):
    """The restoration read uses a body because persisted paths can be long."""
    if path == "/api/v1/outliner/restore":
        return client.post(path, json=params, headers=headers)
    return client.get(path, params=params, headers=headers)
