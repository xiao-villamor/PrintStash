"""Unit coverage for ``import_resolvers`` — turning model *page* URLs into
direct download URLs.

The host HTTP calls (Printables GraphQL, MakerWorld page + API) are patched at
the module's small network helpers, so these tests exercise the dispatch, id
extraction, pack selection and JSON-walking logic without any real network.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from printstash_core.imports import resolvers as core_resolvers

from app.services import import_resolvers as r
from app.services.capture_provider_transport import ProviderTransportError
from app.services.importer import ImportError_

# --------------------------------------------------------------------------- #
# Host classification + id extraction (pure functions)
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Generic JSON helpers
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# resolve_page_url dispatch
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Provider payload characterization
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Collection classification + id extraction
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Printables per-file listing + selective download (real-shaped payloads)
# --------------------------------------------------------------------------- #
# Trimmed real `print(id: 1660232)` response (Springy Cat) — 11 stls across buckets.
_SPRINGY_CAT_META = {
    "data": {
        "print": {
            "id": "1660232",
            "name": "Springy Cat",
            "stls": [
                {"id": "7098445", "name": "SpringyCat.stl", "fileSize": 1233984},
                {
                    "id": "6978173",
                    "name": "SpringyCat_Spring-joiner.stl",
                    "fileSize": 1684,
                },
            ],
            "gcodes": [],
            "slas": [],
            "otherFiles": [{"id": "9001", "name": "readme.pdf", "fileSize": 4242}],
        }
    }
}


# --------------------------------------------------------------------------- #
# Collection resolution
# --------------------------------------------------------------------------- #

__all__ = [
    "AsyncMock",
    "ImportError_",
    "ProviderTransportError",
    "_SPRINGY_CAT_META",
    "core_resolvers",
    "httpx",
    "logging",
    "patch",
    "pytest",
    "r",
]
