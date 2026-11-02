"""What an Artifact's derivatives are, and asking for a failed one again.

Metadata, thumbnails and toolpaths are derived in the background after an
Artifact commits, so the route that lists them is how a client knows whether a
missing thumbnail is still coming (``pending``/``queued``/``running``) or will
not come without help (``failed``). ``pending`` is reported for a kind with no
attempt at the current recipe; a client must never read it as "no thumbnail
exists".

Retry is the per-Artifact repair: it forgets a failed or cancelled attempt and
nudges the producer, so the reconciler picks the Artifact up again. It needs
edit rights on the Model's collection, because it spends shared render capacity.
"""

from __future__ import annotations

import hashlib

import pytest
import trimesh
from fastapi.testclient import TestClient
from sqlmodel import Session

import app.modules.work as work
from app.core.time import utcnow
from app.db.models import (
    CollectionRole,
    DerivativeKind,
    DerivativeRegeneration,
    DerivativeState,
    File,
    JobKind,
    User,
)
from app.modules.identity.auth import create_access_token
from app.modules.storage.storage_backend.runtime import get_backend
from tests.factories import (
    bearer,
    build_collection,
    build_derivative,
    build_file,
    build_model,
    build_user,
    grant_collection_role,
)
from tests.integration.api.v1._ingest_assertions import drain_work


def _headers(user: User) -> dict[str, str]:
    token = create_access_token(user.id, user.username, scope="write")
    return {"Authorization": f"Bearer {token}"}


def _states(response) -> dict[str, str]:
    assert response.status_code in (200, 202), response.text
    return {row["kind"]: row["state"] for row in response.json()}


def _states_of(client: TestClient, user: User, file: File) -> list[dict]:
    response = client.get(
        f"/api/v1/files/{file.id}/derivatives", headers=_headers(user)
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def shelf(db_session: Session):
    return build_collection(db_session, "Derivative shelf")


@pytest.fixture
def viewer(db_session: Session, shelf) -> User:
    user = build_user(db_session, "derivative-viewer")
    grant_collection_role(db_session, user, shelf, CollectionRole.VIEW)
    return user


@pytest.fixture
def editor(db_session: Session, shelf) -> User:
    user = build_user(db_session, "derivative-editor")
    grant_collection_role(db_session, user, shelf, CollectionRole.EDIT)
    return user


@pytest.fixture
def mesh(db_session: Session, shelf) -> File:
    """A real STL Artifact whose bytes are on the backend."""
    content = trimesh.creation.box((10, 10, 10)).export(file_type="stl")
    model = build_model(db_session, "Box", collection=shelf)
    row = build_file(
        db_session,
        model,
        filename="box.stl",
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )
    get_backend().write_bytes(content, row.path)
    return row


class TestListFileDerivatives:
    def test_reports_every_applicable_kind_as_pending_before_any_attempt(
        self, client: TestClient, viewer: User, mesh: File
    ) -> None:
        response = client.get(
            f"/api/v1/files/{mesh.id}/derivatives", headers=_headers(viewer)
        )

        assert _states(response) == {
            DerivativeKind.METADATA: "pending",
            DerivativeKind.PREVIEWS: "pending",
            DerivativeKind.THUMBNAIL: "pending",
        }

    def test_a_binary_gcode_artifact_also_has_a_toolpath(
        self, client: TestClient, db_session: Session, viewer: User, shelf
    ) -> None:
        model = build_model(db_session, "Plate", collection=shelf)
        artifact = build_file(db_session, model, filename="plate.bgcode")

        response = client.get(
            f"/api/v1/files/{artifact.id}/derivatives", headers=_headers(viewer)
        )

        assert set(_states(response)) == {
            DerivativeKind.METADATA,
            DerivativeKind.THUMBNAIL,
            DerivativeKind.TOOLPATH,
        }

    def test_reports_a_failed_attempt_with_its_reason(
        self, client: TestClient, db_session: Session, viewer: User, mesh: File
    ) -> None:
        build_derivative(
            db_session,
            mesh,
            DerivativeKind.THUMBNAIL,
            state=DerivativeState.FAILED,
            failure_reason="render_failed",
            exhausted=True,
        )
        build_derivative(db_session, mesh, DerivativeKind.METADATA)

        response = client.get(
            f"/api/v1/files/{mesh.id}/derivatives", headers=_headers(viewer)
        )

        rows = {row["kind"]: row for row in response.json()}
        assert (
            rows[DerivativeKind.THUMBNAIL]["state"],
            rows[DerivativeKind.THUMBNAIL]["failure_reason"],
        ) == (
            "failed",
            "render_failed",
        )
        assert rows[DerivativeKind.THUMBNAIL]["retryable"] is True
        assert (
            rows[DerivativeKind.METADATA]["state"],
            rows[DerivativeKind.METADATA]["retryable"],
        ) == (
            "ready",
            False,
        )

    def test_an_output_older_than_a_regenerate_all_reads_as_pending(
        self, client: TestClient, db_session: Session, viewer: User, mesh: File
    ) -> None:
        build_derivative(db_session, mesh, DerivativeKind.THUMBNAIL)
        db_session.add(
            DerivativeRegeneration(kind=DerivativeKind.THUMBNAIL, requested_at=utcnow())
        )
        db_session.commit()

        response = client.get(
            f"/api/v1/files/{mesh.id}/derivatives", headers=_headers(viewer)
        )

        assert _states(response)[DerivativeKind.THUMBNAIL] == "pending"

    def test_an_artifact_the_user_cannot_view_is_not_found(
        self, client: TestClient, db_session: Session, mesh: File
    ) -> None:
        outsider = build_user(db_session, "derivative-outsider")

        response = client.get(
            f"/api/v1/files/{mesh.id}/derivatives", headers=_headers(outsider)
        )

        assert response.status_code in (403, 404), response.text


class TestRetryFileDerivative:
    def test_a_retried_thumbnail_is_derived_again(
        self, client: TestClient, db_session: Session, editor: User, mesh: File
    ) -> None:
        # The headline repair: an exhausted failure is never picked up again on
        # its own, and one retry brings the thumbnail back.
        build_derivative(db_session, mesh, DerivativeKind.METADATA)
        build_derivative(
            db_session,
            mesh,
            DerivativeKind.THUMBNAIL,
            state=DerivativeState.FAILED,
            exhausted=True,
        )

        response = client.post(
            f"/api/v1/files/{mesh.id}/derivatives/{DerivativeKind.THUMBNAIL}/retry",
            headers=_headers(editor),
        )

        assert response.status_code == 202, response.text
        assert _states(response)[DerivativeKind.THUMBNAIL] == "pending"
        drain_work()
        settled = client.get(
            f"/api/v1/files/{mesh.id}/derivatives", headers=_headers(editor)
        )
        assert _states(settled) == {
            DerivativeKind.METADATA: "ready",
            DerivativeKind.PREVIEWS: "ready",
            DerivativeKind.THUMBNAIL: "ready",
        }
        db_session.expire_all()
        published = db_session.get(File, mesh.id)
        assert published is not None and published.thumbnail_path
        assert get_backend().exists(published.thumbnail_path)

    def test_nudges_the_producer_of_the_kind(
        self,
        client: TestClient,
        db_session: Session,
        editor: User,
        mesh: File,
        monkeypatch,
    ) -> None:
        build_derivative(
            db_session, mesh, DerivativeKind.THUMBNAIL, state=DerivativeState.CANCELLED
        )
        nudged: list[str] = []
        monkeypatch.setattr(work, "nudge", lambda name, **_: nudged.append(name))

        client.post(
            f"/api/v1/files/{mesh.id}/derivatives/{DerivativeKind.THUMBNAIL}/retry",
            headers=_headers(editor),
        )

        assert JobKind.DERIVATIVES_MESH in nudged

    def test_a_ready_derivative_is_not_retryable(
        self, client: TestClient, db_session: Session, editor: User, mesh: File
    ) -> None:
        build_derivative(db_session, mesh, DerivativeKind.THUMBNAIL)

        response = client.post(
            f"/api/v1/files/{mesh.id}/derivatives/{DerivativeKind.THUMBNAIL}/retry",
            headers=_headers(editor),
        )

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "derivative_not_retryable"

    def test_a_kind_that_does_not_apply_is_not_found(
        self, client: TestClient, editor: User, mesh: File
    ) -> None:
        response = client.post(
            f"/api/v1/files/{mesh.id}/derivatives/{DerivativeKind.TOOLPATH}/retry",
            headers=_headers(editor),
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "derivative_kind_not_found"

    def test_a_viewer_cannot_spend_render_capacity(
        self, client: TestClient, db_session: Session, viewer: User, mesh: File
    ) -> None:
        build_derivative(
            db_session,
            mesh,
            DerivativeKind.THUMBNAIL,
            state=DerivativeState.FAILED,
            exhausted=True,
        )

        response = client.post(
            f"/api/v1/files/{mesh.id}/derivatives/{DerivativeKind.THUMBNAIL}/retry",
            headers=_headers(viewer),
        )

        assert response.status_code == 403, response.text

    def test_a_read_only_token_cannot_retry(
        self, client: TestClient, db_session: Session, editor: User, mesh: File
    ) -> None:
        # The editor may retry, but not through a token scoped to reading.
        build_derivative(
            db_session,
            mesh,
            DerivativeKind.THUMBNAIL,
            state=DerivativeState.FAILED,
            exhausted=True,
        )

        response = client.post(
            f"/api/v1/files/{mesh.id}/derivatives/{DerivativeKind.THUMBNAIL}/retry",
            headers=bearer(editor, scope="read"),
        )

        assert (response.status_code, response.json()["detail"]) == (
            401,
            "insufficient_scope",
        )
        db_session.expire_all()
        rows = {row["kind"]: row["state"] for row in _states_of(client, editor, mesh)}
        assert rows[DerivativeKind.THUMBNAIL] == "failed"
