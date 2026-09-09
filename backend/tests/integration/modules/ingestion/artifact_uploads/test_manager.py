import pytest
from sqlalchemy import update
from sqlmodel import Session

from app.db.models import ArtifactUploadSession, ArtifactUploadState
from app.modules.ingestion.artifact_uploads import (
    ArtifactUploadError,
    SqlArtifactUploadManager,
)


def test_compare_and_set_rejects_a_stale_state_version(
    db_session: Session,
    make_user,
    make_artifact_upload,
) -> None:
    upload = make_artifact_upload(make_user("cas-owner"))
    db_session.expunge(upload)
    stale = upload
    db_session.execute(
        update(ArtifactUploadSession)
        .where(ArtifactUploadSession.id == upload.id)
        .values(state=ArtifactUploadState.UPLOADING.value, version=1)
    )
    db_session.commit()

    with pytest.raises(ArtifactUploadError, match="state_conflict"):
        SqlArtifactUploadManager(db_session).transition(
            stale, ArtifactUploadState.ABORTED
        )

    current = db_session.get(ArtifactUploadSession, stale.id)
    assert current is not None
    assert current.state == ArtifactUploadState.UPLOADING
    assert current.version == 1
