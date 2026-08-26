"""Defends file types at the models API integration boundary.

A regression could expose the wrong artifact or corrupt revision and thumbnail state.
"""

from __future__ import annotations

from ._cross_unit_shared import (
    SUFFIX_TO_FILE_TYPE,
    FileType,
)


def test_step_suffixes_map_to_step_filetype():
    assert SUFFIX_TO_FILE_TYPE[".step"] == FileType.STEP
    assert SUFFIX_TO_FILE_TYPE[".stp"] == FileType.STEP
