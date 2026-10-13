"""Every endpoint that returns a list says what keeps the list small.

Issue #295 was a list endpoint that returned the whole collection tree, all
8,985 rows of it, on every page load. Nothing flagged it when it was written,
because at the time it returned a dozen. This file makes the question
unavoidable: a GET that returns a JSON array either takes a ``limit`` with a
maximum, or is registered below with the reason its size is bounded.

``SMALL_BY_NATURE`` is for lists whose size something else caps: rows an
administrator configures, grants on one resource, a closed set of kinds.
``UNBOUNDED_DEBT`` lists endpoints that grow with the library or with use and
still return everything. It may only shrink: a new endpoint cannot be added to
it, and one that gains a bounded ``limit`` must be removed.

Endpoints are read from the OpenAPI snapshot, which ``test_openapi_contract``
keeps identical to the running app.
"""

from __future__ import annotations

import json

from tests.paths import FIXTURES_DIR

SMALL_BY_NATURE = {
    "/api/v1/admin/users": "accounts on one install",
    "/api/v1/auth/api-keys": "one user's keys",
    "/api/v1/backups": "configured backup destinations",
    "/api/v1/backups/sources": "configured backup sources",
    "/api/v1/browser-pairings": "one user's paired browsers",
    "/api/v1/collections/{collection_id}/permissions": "grants on one collection",
    "/api/v1/config/ai-search/generations": "retained index generations",
    "/api/v1/filament-profiles": "configured filament profiles",
    "/api/v1/files/{file_id}/derivatives": "one Artifact's closed set of kinds",
    "/api/v1/fleet/printers/{printer_id}/maintenance-windows": "one printer's windows",
    "/api/v1/inference/models": "the installable model catalog",
    "/api/v1/jobs": "JOBS_RETENTION_PER_USER per owner",
    "/api/v1/libraries": "configured library sources",
    "/api/v1/libraries/locations": "configured library locations",
    "/api/v1/maintenance/audit-policies": "one policy per audit kind",
    "/api/v1/models/{model_id}/shares": "one Model's share links",
    "/api/v1/notifications/channels": "configured notification channels",
    "/api/v1/printer-profiles": "configured printer profiles",
    "/api/v1/printers": "the printers on one install",
    "/api/v1/printers/{printer_id}/permissions": "grants on one printer",
    "/api/v1/provider-connections": "configured provider connections",
    "/api/v1/saved-views": "one user's saved views",
    "/api/v1/search/generations": "retained index generations",
    "/api/v1/spoolman/spools": "the spools Spoolman reports",
    "/api/v1/storage-connections": "configured storage connections",
    "/api/v1/storage/migrations": "vault migrations, a handful per install",
    "/api/v1/storage/providers": "the closed set of storage providers",
    "/api/v1/storage/targets": "configured storage targets",
}

UNBOUNDED_DEBT = {
    "/api/v1/backups/unowned-local": "orphaned local objects; grows with drift",
    "/api/v1/backups/unowned-remote": "orphaned remote objects; grows with drift",
    "/api/v1/backups/unowned-s3": "orphaned S3 objects; grows with drift",
    "/api/v1/collections": "the whole tree; lazy paged tree planned (#295)",
    "/api/v1/documents/trash": "grows with deletions",
    "/api/v1/fleet/printers/{printer_id}/maintenance-log": "grows with use",
    "/api/v1/fleet/queue": "grows with queued prints",
    "/api/v1/inbox": "grows with captures",
    "/api/v1/models/{model_id}/artifact-outcomes": "grows with prints",
    "/api/v1/models/{model_id}/print-jobs": "grows with prints",
    "/api/v1/models/{model_id}/printer-files": "grows with uploads",
    "/api/v1/notifications/deliveries": "limit has no maximum",
    "/api/v1/printers/{printer_id}/files": "the printer's own storage",
    "/api/v1/printers/{printer_id}/jobs": "limit has no maximum",
    "/api/v1/tags": "grows with the library's tags",
}
# Lower this with every entry removed; raising it is the one change this file
# exists to make visible in review.
MAX_UNBOUNDED_DEBT = 15


def _unbounded_list_endpoints() -> set[str]:
    """GET paths returning an array without a ``limit`` that has a maximum."""
    spec = json.loads((FIXTURES_DIR / "openapi_contract.json").read_text())
    unbounded = set()
    for path, operations in spec["paths"].items():
        get = operations.get("get")
        if get is None:
            continue
        schema = (
            get["responses"]
            .get("200", {})
            .get("content", {})
            .get("application/json", {})
            .get("schema", {})
        )
        if schema.get("type") != "array":
            continue
        limits = [
            parameter
            for parameter in get.get("parameters", [])
            if parameter["name"] == "limit" and parameter["in"] == "query"
        ]
        if not limits or "maximum" not in limits[0]["schema"]:
            unbounded.add(path)
    return unbounded


class TestListEndpoints:
    def test_every_unbounded_list_is_registered(self) -> None:
        unregistered = (
            _unbounded_list_endpoints() - set(SMALL_BY_NATURE) - set(UNBOUNDED_DEBT)
        )

        assert unregistered == set(), (
            "a GET returning a list needs `limit: int = Query(..., le=...)`, or an "
            "entry in SMALL_BY_NATURE naming what keeps it small"
        )

    def test_the_debt_cap_follows_the_list_down(self) -> None:
        assert MAX_UNBOUNDED_DEBT == len(UNBOUNDED_DEBT), (
            "an entry was paid off; lower MAX_UNBOUNDED_DEBT to match"
        )

    def test_no_registered_endpoint_is_already_bounded(self) -> None:
        registered = set(SMALL_BY_NATURE) | set(UNBOUNDED_DEBT)

        stale = registered - _unbounded_list_endpoints()

        assert stale == set(), "remove entries that are bounded or no longer exist"

    def test_no_endpoint_is_registered_twice(self) -> None:
        assert set(SMALL_BY_NATURE) & set(UNBOUNDED_DEBT) == set()
