"""Defends delete file revision clears stale thumbnail pointer at the models API integration boundary.

A regression could expose the wrong artifact or corrupt revision and thumbnail state.
"""

from __future__ import annotations

from ._model_revisions_api_shared import (
    Session,
    TestClient,
    _file,
    _model,
)


def test_delete_file_revision_clears_stale_thumbnail_pointer(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    model = _model(db_session)
    file_row = _file(db_session, model)
    model.thumbnail_file_id = file_row.id
    model.thumbnail_path = "thumbs/stale.webp"
    db_session.add(model)
    db_session.commit()

    resp = client.delete(
        f"/api/v1/models/{model.id}/files/{file_row.id}/revision",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    db_session.refresh(model)
    assert model.thumbnail_file_id is None
    assert model.thumbnail_path is None
