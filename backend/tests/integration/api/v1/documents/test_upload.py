"""Defends upload at the documents API integration boundary.

A regression could expose, corrupt, or permanently remove the wrong document.
"""

from __future__ import annotations

from ._router_shared import (
    Session,
    TestClient,
    _headers,
    _overlay,
    _user,
    pytest,
)


class TestUploadDocument:
    def test_binary_upload_rejects_declared_bytes_over_limit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        db_session: Session,
        client: TestClient,
    ) -> None:
        admin = _user(db_session, "binary-size-admin", superuser=True)
        monkeypatch.setitem(_overlay, "max_upload_mb", 1)

        response = client.post(
            "/api/v1/documents/upload",
            files={
                "file": (
                    "manual.pdf",
                    b"x" * (1024 * 1024 + 1),
                    "application/pdf",
                )
            },
            headers=_headers(admin),
        )

        assert response.status_code == 413, response.text

    def test_markdown_upload_rejects_streamed_bytes_over_limit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        db_session: Session,
        client: TestClient,
    ) -> None:
        admin = _user(db_session, "markdown-size-admin", superuser=True)
        monkeypatch.setitem(_overlay, "max_upload_mb", 1)

        response = client.post(
            "/api/v1/documents/upload",
            files={
                "file": (
                    "notes.md",
                    b"x" * (1024 * 1024 + 1),
                    "text/markdown",
                )
            },
            headers=_headers(admin),
        )

        assert response.status_code == 413, response.text
