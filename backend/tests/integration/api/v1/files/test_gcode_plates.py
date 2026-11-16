"""Only authorized readers can discover the bounded sliced plates of a live 3MF.

Discovery uses ZIP metadata, so a mesh-free sliced export is just as browsable
as a project. Unsafe or unavailable originals never become an empty success.
"""

from __future__ import annotations

import pytest

from app.core.config import _overlay
from app.db.models import FileType
from app.modules.media.three_mf_preview import preview_capacity
from app.modules.storage.storage_backend.runtime import get_backend
from tests.fixtures.three_mf_projects import build_sliced_project


@pytest.fixture
def sliced_file(make_model, make_file):
    data = build_sliced_project(plate_numbers=(10, 2))
    key = "sliced/project.gcode.3mf"
    get_backend().write_bytes(data, key)
    return make_file(
        make_model("sliced"),
        filename="project.gcode.3mf",
        ftype="3mf",
        path=key,
        size_bytes=len(data),
    )


class TestGcodePlates:
    def test_lists_sliced_plates_without_mesh_metadata(
        self, client, auth_headers, sliced_file
    ):
        response = client.get(
            f"/api/v1/files/{sliced_file.id}/gcode-plates", headers=auth_headers
        )

        assert response.status_code == 200, response.text
        assert response.json() == {"plates": [2, 10]}

    def test_refuses_unauthenticated_access(self, client, sliced_file):
        response = client.get(f"/api/v1/files/{sliced_file.id}/gcode-plates")

        assert response.status_code == 401, response.text

    def test_refuses_an_inaccessible_collection(
        self, client, user_headers, make_collection, make_model, make_file
    ):
        collection = make_collection("private")
        artifact = make_file(
            make_model("secret", collection=collection),
            filename="secret.3mf",
            ftype="3mf",
        )

        response = client.get(
            f"/api/v1/files/{artifact.id}/gcode-plates", headers=user_headers()
        )

        assert response.status_code == 403, response.text

    @pytest.mark.parametrize("trashed", ["model", "file"], ids=["model", "file"])
    def test_hides_trashed_artifacts(
        self, client, auth_headers, make_model, make_file, trashed
    ):
        artifact = make_file(
            make_model("trashed", trashed=trashed == "model"),
            filename="project.3mf",
            ftype="3mf",
            trashed=trashed == "file",
        )

        response = client.get(
            f"/api/v1/files/{artifact.id}/gcode-plates", headers=auth_headers
        )

        assert response.status_code == 404, response.text

    def test_refuses_other_file_types(
        self, client, auth_headers, make_model, make_file
    ):
        artifact = make_file(make_model("plain"), ftype=FileType.GCODE.value)

        response = client.get(
            f"/api/v1/files/{artifact.id}/gcode-plates", headers=auth_headers
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "embedded_gcode_not_supported"

    def test_reports_missing_original(
        self, client, auth_headers, make_model, make_file
    ):
        artifact = make_file(make_model("missing"), filename="missing.3mf", ftype="3mf")

        response = client.get(
            f"/api/v1/files/{artifact.id}/gcode-plates", headers=auth_headers
        )

        assert response.status_code == 410, response.text

    def test_refuses_malformed_archive(self, client, auth_headers, sliced_file):
        path = get_backend().direct_path(sliced_file.path)
        assert path is not None
        path.write_bytes(b"not a zip archive")

        response = client.get(
            f"/api/v1/files/{sliced_file.id}/gcode-plates", headers=auth_headers
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "embedded_gcode_malformed"

    @pytest.mark.parametrize(
        ("setting", "limit", "code"),
        [
            pytest.param(
                "three_mf_preview_max_archive_mb",
                0.000001,
                "embedded_gcode_archive_too_large",
                id="archive",
            ),
            pytest.param(
                "three_mf_preview_max_entries",
                1,
                "embedded_gcode_too_many_entries",
                id="entries",
            ),
            pytest.param(
                "three_mf_preview_max_central_directory_mb",
                0.000001,
                "embedded_gcode_central_directory_too_large",
                id="directory",
            ),
        ],
    )
    def test_bounds_archive_metadata(
        self, client, auth_headers, sliced_file, monkeypatch, setting, limit, code
    ):
        monkeypatch.setitem(_overlay, setting, limit)

        response = client.get(
            f"/api/v1/files/{sliced_file.id}/gcode-plates", headers=auth_headers
        )

        assert response.status_code == 413, response.text
        assert response.json()["detail"] == code

    def test_reports_exhausted_preview_capacity(
        self, client, auth_headers, sliced_file, monkeypatch
    ):
        monkeypatch.setitem(_overlay, "three_mf_preview_max_concurrent", 1)

        with preview_capacity():
            response = client.get(
                f"/api/v1/files/{sliced_file.id}/gcode-plates", headers=auth_headers
            )

        assert response.status_code == 429, response.text
        assert response.json()["detail"] == "embedded_gcode_busy"
