"""Bounded native batches across representative source formats."""

import json
import time

import pytest
import trimesh
from sqlmodel import select

from app.db.models import ArtifactDerivative, DerivativeKind, DerivativeState
from app.modules.derivatives import previews
from app.modules.media.preview_contracts import PreviewManifest
from tests.factories.geometry import three_mf
from tests.paths import BACKEND_DIR


@pytest.fixture(params=["stl-20480", "stl-81920", "stl-327680", "step", "3mf"])
def workload(request):
    if request.param == "step":
        return "representative.step", (
            BACKEND_DIR / "tests/fixtures/cascadio_material.stp"
        ).read_bytes()
    if request.param == "3mf":
        mesh = trimesh.creation.icosphere(subdivisions=5)
        return "assembly.3mf", three_mf(
            meshes={1: mesh},
            build=((1, "1 0 0 0 1 0 0 0 1 0 0 0"), (1, "1 0 0 0 1 0 0 0 1 4 0 0")),
        )
    level = {"stl-20480": 5, "stl-81920": 6, "stl-327680": 7}[request.param]
    return request.param + ".stl", trimesh.creation.icosphere(
        subdivisions=level
    ).export(file_type="stl")


class TestPreviewWorkloads:
    def test_publishes_bounded_source_formats(
        self, db_session, stored, workload, local_storage
    ):
        name, content = workload
        file = stored(name, content)
        start = time.monotonic()
        outcome = previews.derive(file.id)
        row = db_session.exec(
            select(ArtifactDerivative).where(
                ArtifactDerivative.file_id == file.id,
                ArtifactDerivative.kind == DerivativeKind.PREVIEWS,
            )
        ).one()
        assert outcome.kinds[DerivativeKind.PREVIEWS] is DerivativeState.READY, (
            row.failure_reason
        )
        manifest = PreviewManifest.model_validate_json(row.output_json)
        assert len(manifest.views) >= 5
        print(
            "PREVIEW_COST="
            + json.dumps(
                {
                    "source": name,
                    "source_bytes": len(content),
                    "seconds": round(time.monotonic() - start, 3),
                    "stored_bytes": manifest.size,
                    "views": len(manifest.views),
                }
            )
        )
