"""The frontend's closed sets match the backend's enums.

The client switches over a Job's kind, a lane, a derivative kind and a Library
source kind. Each is a TypeScript union spelling out the backend enum by hand;
if one drifts, the client either rejects a real value at the type level or
keeps a branch for one that no longer exists.
"""

from __future__ import annotations

import re
from enum import Enum

import pytest

from app.db.models import DerivativeKind, JobKind, LaneName, LibrarySourceKind
from tests.paths import REPO_ROOT

MODELS_TS = REPO_ROOT / "frontend/src/types/models.ts"
CONFIG_TS = REPO_ROOT / "frontend/src/types/config.ts"


def _union(name: str, source_file=MODELS_TS) -> set[str]:
    source = source_file.read_text()
    match = re.search(rf"export type {name} =(.*?);", source, re.S)
    assert match is not None, f"{name} is not declared in {source_file.name}"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


class TestUnions:
    @pytest.mark.parametrize("enum", [JobKind, LaneName, DerivativeKind], ids=str)
    def test_lists_exactly_the_enums_values(self, enum: type[Enum]) -> None:
        assert _union(enum.__name__) == {member.value for member in enum}

    def test_library_source_kind_lists_exactly_the_enums_values(self) -> None:
        # ``frontend/src/lib/storage-transports.ts`` is exhaustive over this
        # union, so a new transport also needs its picker label there.
        assert _union("LibrarySourceKind", CONFIG_TS) == {
            member.value for member in LibrarySourceKind
        }
