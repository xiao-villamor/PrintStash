"""Toolpaths require original-download access and never mutate Artifact bytes.

An ASCII G-code Artifact is its own toolpath. A binary one is served from its
toolpath derivative: while that is still being derived the answer is 202 with
the derivative's state (never a converted body produced inside the request),
a failed conversion is 422 with its recorded reason, and a derivative larger
than the configured limit is refused rather than streamed.
"""

import pytest

from app.core.config import _overlay
from app.db.models import DerivativeKind, DerivativeState, FileType
from app.modules.storage.storage_backend.runtime import get_backend


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

    def test_a_mesh_has_no_toolpath(self, client, auth_headers, make_model, make_file):
        row = make_file(make_model("mesh-toolpath"), filename="part.stl")

        response = client.get(f"/api/v1/files/{row.id}/toolpath", headers=auth_headers)

        assert (response.status_code, response.json()["detail"]) == (
            404,
            "toolpath_not_gcode",
        )

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


@pytest.fixture
def bgcode(make_model, make_file):
    return make_file(
        make_model("binary-toolpath"), filename="plate.bgcode", ftype=FileType.GCODE
    )


class TestBinaryToolpath:
    def test_serves_the_derived_toolpath(
        self, client, auth_headers, bgcode, make_derivative
    ) -> None:
        get_backend().write_bytes(b"G1 X1 E1\n", "_derivatives/plate-toolpath.gcode")
        make_derivative(
            bgcode,
            DerivativeKind.TOOLPATH,
            storage_key="_derivatives/plate-toolpath.gcode",
        )

        response = client.get(
            f"/api/v1/files/{bgcode.id}/toolpath", headers=auth_headers
        )

        assert (response.status_code, response.content) == (200, b"G1 X1 E1\n")
        assert response.headers["cache-control"] == "private, no-store"

    def test_a_toolpath_never_derived_is_pending(
        self, client, auth_headers, bgcode
    ) -> None:
        response = client.get(
            f"/api/v1/files/{bgcode.id}/toolpath", headers=auth_headers
        )

        assert (response.status_code, response.json()) == (202, {"state": "pending"})

    def test_a_toolpath_being_derived_reports_its_state(
        self, client, auth_headers, bgcode, make_derivative
    ) -> None:
        make_derivative(bgcode, DerivativeKind.TOOLPATH, state=DerivativeState.RUNNING)

        response = client.get(
            f"/api/v1/files/{bgcode.id}/toolpath", headers=auth_headers
        )

        assert (response.status_code, response.json()) == (202, {"state": "running"})

    def test_a_failed_conversion_reports_its_reason(
        self, client, auth_headers, bgcode, make_derivative
    ) -> None:
        make_derivative(
            bgcode,
            DerivativeKind.TOOLPATH,
            state=DerivativeState.FAILED,
            failure_reason="toolpath_invalid_bgcode",
            exhausted=True,
        )

        response = client.get(
            f"/api/v1/files/{bgcode.id}/toolpath", headers=auth_headers
        )

        assert (response.status_code, response.json()["detail"]) == (
            422,
            "toolpath_invalid_bgcode",
        )

    def test_a_derived_toolpath_over_the_limit_is_refused(
        self, client, auth_headers, bgcode, make_derivative
    ) -> None:
        _overlay["toolpath_output_max_mb"] = 1
        get_backend().write_bytes(b"G1\n" * 400_000, "_derivatives/huge.gcode")
        make_derivative(
            bgcode, DerivativeKind.TOOLPATH, storage_key="_derivatives/huge.gcode"
        )

        response = client.get(
            f"/api/v1/files/{bgcode.id}/toolpath", headers=auth_headers
        )

        assert response.status_code == 413

    def test_a_download_share_serves_the_pending_state(
        self, client, auth_headers, bgcode
    ) -> None:
        shared = client.post(
            f"/api/v1/models/{bgcode.model_id}/shares",
            headers=auth_headers,
            json={"allow_download": True},
        )

        response = client.get(
            f"/api/v1/share/{shared.json()['token']}/files/{bgcode.id}/toolpath"
        )

        assert (response.status_code, response.json()) == (202, {"state": "pending"})


class TestDisabledDelivery:
    def test_missing_binary_toolpath_returns_policy_conflict(
        self, client, auth_headers, bgcode, make_system_config
    ):
        make_system_config(derivatives_toolpath_enabled=False)
        response = client.get(
            f"/api/v1/files/{bgcode.id}/toolpath", headers=auth_headers
        )
        assert (response.status_code, response.json()["detail"]) == (
            409,
            "derivative_group_disabled",
        )

    def test_serves_a_ready_prior_recipe_while_disabled(
        self, client, auth_headers, bgcode, make_system_config, make_derivative
    ):
        make_system_config(derivatives_toolpath_enabled=False)
        data = b"G1 X42 E1\n"
        key = "_derivatives/prior-recipe.gcode"
        get_backend().write_bytes(data, key)
        make_derivative(
            bgcode, DerivativeKind.TOOLPATH, recipe_version=0, storage_key=key
        )
        response = client.get(
            f"/api/v1/files/{bgcode.id}/toolpath", headers=auth_headers
        )
        assert (response.status_code, response.content) == (200, data)

    def test_ascii_delivery_ignores_binary_processing_policy(
        self, client, auth_headers, make_model, make_file, make_system_config
    ):
        make_system_config(derivatives_toolpath_enabled=False)
        data = b"G90\nG1 X10 E1\n"
        key = "ascii-disabled.gcode"
        get_backend().write_bytes(data, key)
        file = make_file(
            make_model("ascii-disabled"),
            filename="ascii.gcode",
            ftype=FileType.GCODE,
            path=key,
            size_bytes=len(data),
        )
        response = client.get(f"/api/v1/files/{file.id}/toolpath", headers=auth_headers)
        assert (response.status_code, response.content) == (200, data)


class TestDisabledRetry:
    def test_artifact_retry_is_rejected_before_reset_while_disabled(
        self,
        client,
        auth_headers,
        make_model,
        make_file,
        make_system_config,
        make_derivative,
        db_session,
    ):
        make_system_config(derivatives_mesh_enabled=False)
        file = make_file(make_model("disabled-retry"), filename="retry.stl")
        row = make_derivative(
            file, DerivativeKind.THUMBNAIL, state=DerivativeState.FAILED, exhausted=True
        )
        response = client.post(
            f"/api/v1/files/{file.id}/derivatives/thumbnail/retry", headers=auth_headers
        )
        assert (response.status_code, response.json()["detail"]) == (
            409,
            "derivative_group_disabled",
        )
        db_session.refresh(row)
        assert row.state is DerivativeState.FAILED
        assert row.next_attempt_at is None
