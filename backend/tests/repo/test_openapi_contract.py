"""A snapshot of the public API, so a breaking change has to be deliberate.

The OpenAPI document is what the frontend, the browser extension and any
self-hoster's script are written against. A renamed field or a changed status code
is invisible in the backend's own tests and breaks all three.

So the whole document is snapshotted and compared. Release metadata is stripped
(a version bump is not an API change) and test-only routes are filtered out, since
`pytest-randomly` would otherwise make the snapshot depend on which test ran
first.

When this fails the diff is the review: regenerate with
`UPDATE_OPENAPI_CONTRACT=1` only once the change is confirmed intended.
"""

from __future__ import annotations

import copy
import json
import os

from app.main import app
from tests.paths import FIXTURES_DIR

CONTRACT_PATH = FIXTURES_DIR / "openapi_contract.json"


def _canonical_contract() -> str:
    document = copy.deepcopy(app.openapi())

    # Some tests install deliberately failing, test-only routes on the shared
    # FastAPI instance. They are not part of the product contract and pytest's
    # randomized order must not make the snapshot depend on when they ran.
    paths = document.get("paths")
    if isinstance(paths, dict):
        document["paths"] = {
            path: operations
            for path, operations in paths.items()
            if not path.startswith("/__test__/")
        }

    # Release metadata changes independently of endpoint compatibility.
    info = document.get("info")
    if isinstance(info, dict):
        info.pop("version", None)

    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


class TestOpenapi:
    def test_openapi_contract(self) -> None:
        current_contract = _canonical_contract()

        if os.environ.get("UPDATE_OPENAPI_CONTRACT") == "1":
            CONTRACT_PATH.write_text(current_contract, encoding="utf-8")

        expected_contract = CONTRACT_PATH.read_text(encoding="utf-8")
        assert current_contract == expected_contract, (
            "OpenAPI contract changed. Review the diff; if the change is intentional, "
            "regenerate the fixture with:\n"
            "UPDATE_OPENAPI_CONTRACT=1 uv run pytest tests/test_openapi_contract.py -v"
        )
