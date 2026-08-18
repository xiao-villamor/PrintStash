from __future__ import annotations

import copy
import json
import os
from pathlib import Path

from app.main import app

CONTRACT_PATH = Path(__file__).parent / "fixtures" / "openapi_contract.json"


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


def test_openapi_contract() -> None:
    current_contract = _canonical_contract()

    if os.environ.get("UPDATE_OPENAPI_CONTRACT") == "1":
        CONTRACT_PATH.write_text(current_contract, encoding="utf-8")

    expected_contract = CONTRACT_PATH.read_text(encoding="utf-8")
    assert current_contract == expected_contract, (
        "OpenAPI contract changed. Review the diff; if the change is intentional, "
        "regenerate the fixture with:\n"
        "UPDATE_OPENAPI_CONTRACT=1 uv run pytest tests/test_openapi_contract.py -v"
    )
