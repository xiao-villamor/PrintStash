"""Full HTTP backup recovery preserves Family lifecycle and read-only source bytes."""

import hashlib
from contextlib import nullcontext

import pytest

from app.core.config import settings
from app.db.models import ModelFamily, ModelFamilyMember
from app.db.session import get_session_factory
from app.modules.storage.storage_backend.runtime import get_backend, init_backend
from tests.factories import build_external_library, build_file, build_model
from tests.factories.content import png
from tests.fakes.s3_delivery import browser_s3
from tests.paths import TESTDATA_DIR, require_fixtures

BENCHY = TESTDATA_DIR / "benchy" / "3dbenchy.stl"
require_fixtures(BENCHY)


class TestFamilyBackup:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "storage_kind", ["local", pytest.param("s3", marks=pytest.mark.s3)]
    )
    async def test_restores_family_database_backup(
        self, api, tmp_path, e2e_db, storage_kind
    ):
        context = (
            browser_s3(tmp_path) if storage_kind == "s3" else nullcontext((None, None))
        )
        with context as (s3, _tls):
            options = {
                "storage_backend": storage_kind,
                "data_dir": str(tmp_path / "files"),
                "thumb_dir": str(tmp_path / "thumbs"),
            }
            if s3 is not None:
                s3._client.put_bucket_versioning(
                    Bucket=settings.s3_bucket,
                    VersioningConfiguration={"Status": "Enabled"},
                )
                options.update(
                    {
                        "s3_bucket": settings.s3_bucket,
                        "s3_endpoint_url": settings.s3_endpoint_url,
                        "s3_region": "us-east-1",
                        "s3_access_key": settings.s3_access_key,
                        "s3_secret_key": settings.s3_secret_key,
                    }
                )
            setup = await api.post(
                "/api/v1/setup",
                json={"username": "owner", "password": "Password123", **options},
            )
            assert setup.status_code == 201, setup.text
            init_backend()
            headers = {"Authorization": f"Bearer {setup.json()['access_token']}"}
            source_root = tmp_path / "source"
            source_root.mkdir()
            source_path = source_root / "benchy.stl"
            source_data = BENCHY.read_bytes()
            source_path.write_bytes(source_data)
            library = build_external_library(
                e2e_db, source_root, writeback_enabled=False
            )
            models = [
                build_model(e2e_db, name=name)
                for name in ("Original", "Rescaled", "Archive")
            ]
            build_file(
                e2e_db,
                models[0],
                external=True,
                filename=source_path.name,
                path=str(source_path),
                external_library_id=library.id,
                source_key=source_path.name,
                size_bytes=len(source_data),
                sha256=hashlib.sha256(source_data).hexdigest(),
            )
            model_ids = [model.id for model in models]
            source_path.chmod(0o444)
            source_root.chmod(0o555)
            try:
                families = []
                cover_bytes = {}
                for name, ids in (
                    ("Vacant", model_ids[:2]),
                    ("Trashed", model_ids[2:]),
                ):
                    created = await api.post(
                        "/api/v1/families",
                        headers=headers,
                        json={
                            "name": name,
                            "canonical_model_id": ids[0],
                            "members": [{"model_id": model_id} for model_id in ids],
                        },
                    )
                    assert created.status_code == 201, created.text
                    family = created.json()
                    uploaded = await api.put(
                        f"/api/v1/families/{family['id']}/cover?version={family['version']}",
                        headers=headers,
                        files={
                            "file": ("cover.png", png(width=8, height=8), "image/png")
                        },
                    )
                    assert uploaded.status_code == 200, uploaded.text
                    family = uploaded.json()
                    image = await api.get(
                        family["cover_thumbnail_url"], headers=headers
                    )
                    assert image.status_code == 200, image.text
                    cover_bytes[family["id"]] = image.content
                    families.append(family)
                vacant, archived = families
                trashed_model = await api.delete(
                    f"/api/v1/models/{model_ids[0]}", headers=headers
                )
                assert trashed_model.status_code == 204, trashed_model.text
                trashed_family = await api.delete(
                    f"/api/v1/families/{archived['id']}?version={archived['version']}",
                    headers=headers,
                )
                assert trashed_family.status_code == 204, trashed_family.text
                created = await api.post("/api/v1/backups", headers=headers)
                assert created.status_code == 202, created.text
                backup_id = created.json()["backup_id"]
                assert created.json()["file_count"] >= 2

                # Simulate accidental Family purge through the real API. Source
                # Models remain independent, and their read-only bytes survive.
                for family in families:
                    if family is vacant:
                        trashed = await api.delete(
                            f"/api/v1/families/{family['id']}?version={family['version']}",
                            headers=headers,
                        )
                        assert trashed.status_code == 204, trashed.text
                    purged = await api.delete(
                        f"/api/v1/families/{family['id']}/purge?version={family['version'] + 1}",
                        headers=headers,
                    )
                    assert purged.status_code == 204, purged.text
                e2e_db.close()
                restored = await api.post(
                    f"/api/v1/backups/{backup_id}/restore", headers=headers
                )
                assert restored.status_code == 200, restored.text
                assert restored.json()["restored_files"] >= 2

                restored_vacant = await api.get(
                    f"/api/v1/families/{vacant['id']}", headers=headers
                )
                assert restored_vacant.status_code == 200, restored_vacant.text
                assert restored_vacant.json()["canonical_model_id"] is None
                assert restored_vacant.json()["member_count"] == 1
                with get_session_factory().session() as session:
                    archived_row = session.get(ModelFamily, archived["id"])
                    assert archived_row.deleted_at is not None
                    archived_version = archived_row.version
                    canonical = session.get(
                        ModelFamilyMember, archived_row.canonical_member_id
                    )
                    assert canonical.model_id == model_ids[2]
                    assert canonical.detach_reason == "family_trashed"
                reactivated = await api.post(
                    f"/api/v1/families/{archived['id']}/restore",
                    headers=headers,
                    json={"version": archived_version},
                )
                assert reactivated.status_code == 200, reactivated.text
                for family in families:
                    detail = await api.get(
                        f"/api/v1/families/{family['id']}", headers=headers
                    )
                    assert detail.status_code == 200, detail.text
                    image = await api.get(
                        detail.json()["cover_thumbnail_url"], headers=headers
                    )
                    assert image.status_code == 200, image.text
                    assert image.content == cover_bytes[family["id"]]
                assert source_path.read_bytes() == source_data
                assert get_backend().backend_name == storage_kind
            finally:
                source_root.chmod(0o755)
                source_path.chmod(0o644)
