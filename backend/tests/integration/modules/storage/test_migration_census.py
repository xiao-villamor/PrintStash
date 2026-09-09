"""The final census rejects invalid ownership paths and missing in-flight bytes."""

import hashlib
import json
from dataclasses import asdict

import pytest

from app.core.secrets import encrypt_secret
from app.modules.storage.migration_census import census, remap_owned_key
from app.modules.storage.migration_identity import namespace_ref
from app.modules.storage.storage_backend.local import LocalStorageBackend
from app.modules.storage.storage_backend.runtime import get_backend


class TestRemapOwnedKey:
    @pytest.mark.parametrize(
        "suffix", ["../escape.stl", "./invalid.stl", ""], ids=["parent", "dot", "empty"]
    )
    def test_invalid_owned_key_cannot_escape_namespace(self, tmp_path, suffix):
        source = LocalStorageBackend(
            data_dir=tmp_path / "source", thumb_dir=tmp_path / "thumbs"
        )
        destination = LocalStorageBackend(
            data_dir=tmp_path / "destination", thumb_dir=tmp_path / "new-thumbs"
        )
        key = str(source.data_dir) + "/" + suffix

        with pytest.raises(ValueError, match="migration_key_invalid"):
            remap_owned_key(source, destination, key)

    def test_external_path_is_not_adopted(self, tmp_path):
        source = LocalStorageBackend(
            data_dir=tmp_path / "source", thumb_dir=tmp_path / "thumbs"
        )
        destination = LocalStorageBackend(
            data_dir=tmp_path / "destination", thumb_dir=tmp_path / "new-thumbs"
        )

        with pytest.raises(ValueError, match="migration_owned_key_outside_namespace"):
            remap_owned_key(source, destination, str(tmp_path / "linked.stl"))


class TestCensus:
    def test_model_cover_without_file_thumbnail_is_owned(self, db_session, make_model):
        source = get_backend()
        model = make_model()
        key = source.thumbnail_key(model.id)
        source.create_bytes(b"model-cover", key)
        model.thumbnail_path = key
        db_session.add(model)
        db_session.commit()

        result = census(db_session, source, source)

        assert [
            (blob.source_key, blob.resource_type, blob.resource_id) for blob in result
        ] == [(key, "model_thumbnail", str(model.id))]

    def test_missing_derived_thumbnail_is_regenerable(
        self, db_session, make_model, make_file
    ):
        source = get_backend()
        key = source.blob_key("model", 1, "part.stl")
        source.create_bytes(b"data", key)
        make_file(
            make_model(),
            path=key,
            size_bytes=4,
            sha256=hashlib.sha256(b"data").hexdigest(),
            thumbnail_path=source.thumbnail_key(999),
        )

        result = census(db_session, source, source)

        assert [blob.source_key for blob in result] == [key]

    def test_missing_native_staging_blocks_census(
        self, db_session, tmp_path, make_artifact_upload, make_user
    ):
        source = get_backend()
        receipt = source.create_bytes(
            b"data", source.capture_upload_slot_key("missing-native")
        )
        make_artifact_upload(
            make_user(),
            state="verifying",
            adapter_id="native_parts",
            destination_ref=namespace_ref(source),
            protected_native_id=encrypt_secret(
                json.dumps(
                    {
                        "key": receipt.key,
                        "upload_id": "fake-native-id",
                        "ownership_token": "fake-owner",
                        "completion_receipt": asdict(receipt),
                    }
                )
            ),
        )
        assert source.rollback_create(receipt)
        destination = LocalStorageBackend(
            data_dir=tmp_path / "destination", thumb_dir=tmp_path / "new-thumbs"
        )

        with pytest.raises(ValueError, match="migration_source_object_missing"):
            census(db_session, source, destination)

    def test_missing_terminal_native_staging_is_not_recreated(
        self, db_session, make_artifact_upload, make_user
    ):
        source = get_backend()
        receipt = source.create_bytes(
            b"data", source.capture_upload_slot_key("completed-native")
        )
        make_artifact_upload(
            make_user(),
            state="completed",
            adapter_id="native_parts",
            destination_ref=namespace_ref(source),
            protected_native_id=encrypt_secret(
                json.dumps(
                    {
                        "key": receipt.key,
                        "upload_id": "fake-native-id",
                        "ownership_token": "fake-owner",
                        "completion_receipt": asdict(receipt),
                    }
                )
            ),
        )
        assert source.rollback_create(receipt)

        assert census(db_session, source, source) == []
