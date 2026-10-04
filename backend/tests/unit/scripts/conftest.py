"""Deterministic v2 source fixtures shared by the corpus owner tests."""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.etree import ElementTree

import pytest

from scripts.mesh_benchmark_corpus_v2 import build_contract_corpus


@pytest.fixture
def corpus_v2(tmp_path: Path):
    return tmp_path, build_contract_corpus(tmp_path)


@pytest.fixture
def read_3mf_model():
    def read(path: Path, name: str = "3D/3dmodel.model"):
        with zipfile.ZipFile(path) as package:
            return ElementTree.fromstring(package.read(name))

    return read
