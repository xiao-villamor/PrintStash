"""Toolpaths require original-download access and never mutate Artifact bytes."""

from app.services.storage_backend import get_backend


class TestToolpath:
    def test_ascii_toolpath_retains_original_bytes(
        self, client, auth_headers, make_model, make_file
    ):
        content = b"G90\nG1 X10 Y10 Z0.2\nG1 X20 E1\n"
        key = "toolpath-reference.gcode"
        get_backend().write_bytes(content, key)
        row = make_file(
            make_model("toolpath"), path=key, ftype="gcode", size_bytes=len(content)
        )
        response = client.get(f"/api/v1/files/{row.id}/toolpath", headers=auth_headers)
        assert response.status_code == 200
        assert response.content == content
        assert get_backend().read_bytes(key) == content

    def test_toolpath_requires_authentication(self, client, make_model, make_file):
        row = make_file(make_model("private-toolpath"), ftype="gcode")
        response = client.get(f"/api/v1/files/{row.id}/toolpath")
        assert response.status_code == 401

    def test_view_only_share_refuses_toolpath(
        self, client, auth_headers, make_model, make_file
    ):
        model = make_model("view-only-toolpath")
        file = make_file(model, ftype="gcode")
        shared = client.post(
            f"/api/v1/models/{model.id}/shares",
            headers=auth_headers,
            json={"allow_download": False},
        )
        assert shared.status_code == 200
        response = client.get(
            f"/api/v1/share/{shared.json()['token']}/files/{file.id}/toolpath"
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "download_disabled"

    def test_download_share_cannot_read_another_models_toolpath(
        self, client, auth_headers, make_model, make_file
    ):
        model = make_model("shared-toolpath")
        other = make_file(make_model("unshared-toolpath"), ftype="gcode")
        shared = client.post(
            f"/api/v1/models/{model.id}/shares",
            headers=auth_headers,
            json={"allow_download": True},
        )
        assert shared.status_code == 200
        response = client.get(
            f"/api/v1/share/{shared.json()['token']}/files/{other.id}/toolpath"
        )
        assert response.status_code == 404

    def test_authorized_share_serves_ascii_toolpath(
        self, client, auth_headers, make_model, make_file
    ):
        model = make_model("download-share-toolpath")
        content = b"G1 X10 E1\n"
        get_backend().write_bytes(content, "shared-toolpath.gcode")
        file = make_file(
            model, ftype="gcode", path="shared-toolpath.gcode", size_bytes=len(content)
        )
        shared = client.post(
            f"/api/v1/models/{model.id}/shares",
            headers=auth_headers,
            json={"allow_download": True},
        )
        assert shared.status_code == 200
        response = client.get(
            f"/api/v1/share/{shared.json()['token']}/files/{file.id}/toolpath"
        )
        assert response.status_code == 200
        assert response.content == content
