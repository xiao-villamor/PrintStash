"""Serving any mesh file as STL, so the browser viewer only ever speaks one format.

An STL is passed through untouched; a 3MF, OBJ or STEP is converted once and the result
cached under a content-addressed key, because converting a large mesh on every page view
is the difference between a viewer that opens and one that times out. The cache is
create-only: two requests racing to publish the same conversion is normal, and the loser
serves its own in-memory result rather than failing or overwriting.
"""

from __future__ import annotations

import io

import numpy as np
import pytest
import trimesh
from fastapi.testclient import TestClient

from app.services.storage_backend import get_backend
from tests.fixtures.three_mf_projects import build_3d_builder_component_project

CONVERTED = b"converted-stl-bytes"


class TestFileAsStl:
    def test_serves_an_stl_untouched(
        self, client: TestClient, auth_headers, make_model, make_file
    ) -> None:
        model = make_model("stl-direct")
        key = "already.stl"
        get_backend().write_bytes(b"solid x endsolid", key)
        row = make_file(model, filename="already.stl", path=key)

        response = client.get(f"/api/v1/files/{row.id}/stl", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.content == b"solid x endsolid"

    def test_answers_a_matching_etag_with_304(
        self, client: TestClient, auth_headers, make_model, make_file
    ) -> None:
        model = make_model("stl-etag")
        key = "etag.stl"
        get_backend().write_bytes(b"solid y endsolid", key)
        row = make_file(model, filename="etag.stl", path=key)

        response = client.get(
            f"/api/v1/files/{row.id}/stl",
            headers={**auth_headers, "if-none-match": f'"{row.sha256}"'},
        )

        assert response.status_code == 304

    def test_reports_a_missing_blob_as_gone(
        self, client: TestClient, auth_headers, make_model, make_file
    ) -> None:
        row = make_file(make_model("stl-missing"))  # path points nowhere

        response = client.get(f"/api/v1/files/{row.id}/stl", headers=auth_headers)

        assert response.status_code == 410, response.text
        assert response.json()["detail"] == "file_blob_missing"

    def test_converts_a_mesh_that_is_not_stl(
        self,
        client: TestClient,
        auth_headers,
        monkeypatch: pytest.MonkeyPatch,
        make_model,
        make_file,
        remove_blob,
    ) -> None:
        model = make_model("stl-3mf")
        key = "model.3mf"
        get_backend().write_bytes(b"fake-3mf-bytes", key)
        sha = "c1" * 32
        row = make_file(model, filename="model.3mf", ftype="3mf", path=key, sha256=sha)
        remove_blob(get_backend().stl_cache_key(sha))
        monkeypatch.setattr(
            "app.services.mesh_processing.to_stl_bytes", lambda _path: CONVERTED
        )

        response = client.get(f"/api/v1/files/{row.id}/stl", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.content == CONVERTED

    def test_converts_only_once(
        self,
        client: TestClient,
        auth_headers,
        monkeypatch: pytest.MonkeyPatch,
        make_model,
        make_file,
        remove_blob,
    ) -> None:
        model = make_model("stl-3mf-cached")
        key = "cached.3mf"
        get_backend().write_bytes(b"fake-3mf-bytes", key)
        sha = "c3" * 32
        row = make_file(model, filename="cached.3mf", ftype="3mf", path=key, sha256=sha)
        # The cache is content-addressed and lives on the storage backend, which
        # survives the per-test database wipe.
        remove_blob(get_backend().stl_cache_key(sha))
        conversions = {"n": 0}

        def counted(_path):
            conversions["n"] += 1
            return CONVERTED

        monkeypatch.setattr("app.services.mesh_processing.to_stl_bytes", counted)

        client.get(f"/api/v1/files/{row.id}/stl", headers=auth_headers)
        second = client.get(f"/api/v1/files/{row.id}/stl", headers=auth_headers)

        assert second.content == CONVERTED
        assert conversions["n"] == 1

    def test_serves_its_own_result_when_another_request_wins_the_cache_race(
        self,
        client: TestClient,
        auth_headers,
        monkeypatch: pytest.MonkeyPatch,
        make_model,
        make_file,
        remove_blob,
    ) -> None:
        from app.api.v1 import files as files_api
        from app.services.storage_backend import StorageCollisionError

        model = make_model("stl-race")
        key = "race.3mf"
        get_backend().write_bytes(b"fake-3mf-bytes", key)
        sha = "c4" * 32
        row = make_file(model, filename="race.3mf", ftype="3mf", path=key, sha256=sha)
        remove_blob(get_backend().stl_cache_key(sha))
        monkeypatch.setattr(
            "app.services.mesh_processing.to_stl_bytes", lambda _path: CONVERTED
        )

        def already_published(*_args: object, **_kwargs: object):
            raise StorageCollisionError("another request published it first")

        monkeypatch.setattr(files_api.get_backend(), "create_bytes", already_published)

        response = client.get(f"/api/v1/files/{row.id}/stl", headers=auth_headers)

        # Losing the race is normal, not an error: the bytes are already in memory.
        assert response.status_code == 200, response.text
        assert response.content == CONVERTED

    def test_still_serves_the_mesh_when_the_cache_write_fails(
        self,
        client: TestClient,
        auth_headers,
        monkeypatch: pytest.MonkeyPatch,
        make_model,
        make_file,
        remove_blob,
    ) -> None:
        from app.api.v1 import files as files_api

        model = make_model("stl-cache-fails")
        key = "cache-fails.3mf"
        get_backend().write_bytes(b"fake-3mf-bytes", key)
        sha = "c5" * 32
        row = make_file(
            model, filename="cache-fails.3mf", ftype="3mf", path=key, sha256=sha
        )
        remove_blob(get_backend().stl_cache_key(sha))
        monkeypatch.setattr(
            "app.services.mesh_processing.to_stl_bytes", lambda _path: CONVERTED
        )

        def failing_receipt(*_args: object, **_kwargs: object):
            raise RuntimeError("ownership ledger unavailable")

        monkeypatch.setattr(files_api, "publish_bytes", failing_receipt)

        response = client.get(f"/api/v1/files/{row.id}/stl", headers=auth_headers)

        # A cache that cannot be written is a slow viewer, not a broken one.
        assert response.status_code == 200, response.text
        assert response.content == CONVERTED

    def test_reports_a_conversion_that_produces_nothing(
        self,
        client: TestClient,
        auth_headers,
        monkeypatch: pytest.MonkeyPatch,
        make_model,
        make_file,
    ) -> None:
        model = make_model("stl-fail")
        key = "broken.obj"
        get_backend().write_bytes(b"not-really-obj", key)
        row = make_file(
            model, filename="broken.obj", ftype="obj", path=key, sha256="c2" * 32
        )
        monkeypatch.setattr(
            "app.services.mesh_processing.to_stl_bytes", lambda _path: None
        )

        response = client.get(f"/api/v1/files/{row.id}/stl", headers=auth_headers)

        assert response.status_code == 500, response.text
        assert response.json()["detail"] == "stl_conversion_failed"

    def test_converts_a_real_3mf_whose_parts_are_placed_by_transform(
        self,
        client: TestClient,
        auth_headers,
        make_model,
        make_file,
        remove_blob,
    ) -> None:
        """The one conversion case that runs the real converter end to end.

        Every other row here stubs `to_stl_bytes`, which is right for testing the
        caching and the error shape but proves nothing about the conversion. A 3MF
        written by 3D Builder — and by most CAD exporters — stores one mesh at the
        origin and positions it through a nested build/component graph, so a
        converter that ignores the graph serves an STL of a part at 0,0,0. The
        viewer then shows something the user did not model, with no error to
        explain it, which is why this route needs one unstubbed pass.
        """
        model = make_model("stl-3d-builder")
        key = "3d-builder-component.3mf"
        sha = "c3" * 32
        get_backend().write_bytes(build_3d_builder_component_project(), key)
        remove_blob(get_backend().stl_cache_key(sha))
        row = make_file(model, filename=key, ftype="3mf", path=key, sha256=sha)

        response = client.get(f"/api/v1/files/{row.id}/stl", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("application/sla")
        mesh = trimesh.load_mesh(
            io.BytesIO(response.content), file_type="stl", process=False
        )
        # The build/component matrices, baked in — not a part sitting at the origin.
        np.testing.assert_allclose(
            mesh.bounds,
            np.asarray([[110.0, 220.0, 330.0], [112.0, 223.0, 334.0]]),
            atol=1e-5,
        )
