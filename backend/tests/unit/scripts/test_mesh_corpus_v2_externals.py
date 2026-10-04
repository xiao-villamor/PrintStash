"""Pinned egress retains only verified content and preserves destinations on refusal."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from scripts.mesh_corpus_v2_contracts import Profile
from scripts.mesh_corpus_v2_externals import (
    FETCH_CHUNK_BYTES,
    external_references,
    materialize_external,
)
from scripts.mesh_corpus_v2_scenes import SceneCase, scene
from tests.paths import BACKEND_DIR


class TestExternalFixtures:
    def test_reuses_pinned_pilot_references(self) -> None:
        refs = external_references()
        pilot = json.loads(
            (
                BACKEND_DIR.parent / "docs/adr/0009-3mf-pilot/external-inputs.json"
            ).read_text()
        )
        assert [{key: asdict(row)[key] for key in pilot[0]} for row in refs] == pilot
        assert {row.case for row in refs} == {
            "PrusaSlicer",
            "BambuStudio",
            "OrcaSlicer",
        }

    def test_requires_explicit_download(self, corpus_v2) -> None:
        root, manifest = corpus_v2
        assert not any(
            ((root / row.filename).exists() for row in manifest.external_references)
        )
        assert not any((row.profile is Profile.DOWNLOAD for row in manifest.fixtures))

    def test_verifies_external_hash(self, tmp_path: Path) -> None:
        payload = scene(SceneCase.STANDARD)
        reference = replace(
            external_references()[0],
            input_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
        )
        actual = materialize_external(tmp_path, reference, fetch=lambda _: (payload,))
        assert actual.read_bytes() == payload
        assert sorted((p.name for p in tmp_path.iterdir())) == [reference.filename]

    @pytest.mark.parametrize("oversize", [1, FETCH_CHUNK_BYTES])
    def test_rejects_external_size_limit(self, tmp_path: Path, oversize) -> None:
        reference = replace(external_references()[0], input_bytes=1)
        with pytest.raises(ValueError, match="exceeds"):
            materialize_external(
                tmp_path, reference, fetch=lambda _: (b"x" * (1 + oversize),)
            )
        assert list(tmp_path.iterdir()) == []

    def test_preserves_existing_file_on_download_failure(self, tmp_path: Path) -> None:
        reference = replace(external_references()[0], input_bytes=3)
        target = tmp_path / reference.filename
        target.write_bytes(b"old")
        with pytest.raises(ValueError, match="identity differs"):
            materialize_external(tmp_path, reference, fetch=lambda _: (b"bad",))
        assert target.read_bytes() == b"old"
        assert sorted((p.name for p in tmp_path.iterdir())) == [reference.filename]
