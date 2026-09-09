"""Epoch fencing never resurrects terminal uploads or guesses a retained provider."""

import json

import pytest

from app.core.time import utcnow
from app.modules.storage.migration_identity import namespace_ref
from app.modules.storage.migration_uploads import (
    activate_uploads,
    retained_upload_backend,
)
from app.modules.storage.storage_backend.local import LocalStorageBackend


class TestActivateUploads:
    @pytest.mark.parametrize(
        "state", ["completed", "aborted", "expired", "failed"], ids=str
    )
    def test_terminal_native_session_is_not_rewritten(
        self, db_session, tmp_path, make_artifact_upload, make_user, state
    ):
        source = LocalStorageBackend(
            data_dir=tmp_path / "old", thumb_dir=tmp_path / "old-thumbs"
        )
        destination = LocalStorageBackend(
            data_dir=tmp_path / "new", thumb_dir=tmp_path / "new-thumbs"
        )
        upload = make_artifact_upload(
            make_user(),
            state=state,
            adapter_id="native_parts",
            destination_ref=namespace_ref(source),
        )
        version = upload.version

        activate_uploads(db_session, [], source=source, destination=destination)
        db_session.commit()
        db_session.refresh(upload)

        assert upload.state == state
        assert upload.version == version
        assert upload.destination_ref == namespace_ref(source)


class TestRetainedUploadBackend:
    @pytest.mark.parametrize(
        "source_config",
        ["{}", "local"],
        ids=["credentials-removed", "different-source"],
    )
    def test_missing_retained_provider_never_uses_active_backend(
        self, db_session, tmp_path, make_vault_migration, source_config
    ):
        configs = {
            "{}": "{}",
            "local": json.dumps(
                {
                    "provider": "local",
                    "data_dir": str(tmp_path / "retained"),
                    "thumb_dir": str(tmp_path / "retained-thumbs"),
                }
            ),
        }
        make_vault_migration(
            state="active", activated_at=utcnow(), source_config=configs[source_config]
        )

        with pytest.raises(
            ValueError, match="artifact_upload_retained_provider_unavailable"
        ):
            retained_upload_backend(db_session, "unknown-source")
