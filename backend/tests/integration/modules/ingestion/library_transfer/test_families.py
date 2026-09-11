"""Portable Family identity is stable across imports; membership conflicts are atomic."""

import hashlib
import json
import zipfile
from contextlib import nullcontext

import pytest
from sqlmodel import Session, select

from app.core.errors import ErrorKind, OperationError
from app.db.models import (
    CollectionRole,
    ModelFamily,
    ModelFamilyMember,
    ModelFamilyTagLink,
    SavedView,
    Tag,
    User,
)
from app.modules.ingestion import library_transfer
from app.modules.library.families import covers, lifecycle, metadata, mutations
from app.modules.media.source_cover_processing import process_source_cover_upload
from app.modules.storage.storage_backend.runtime import bind_backend, get_backend
from app.modules.storage.storage_deletion import process_storage_delete_intents
from app.modules.storage.storage_utils import ownership_snapshot
from app.schemas.families import FamilyCreate, FamilyUpdate
from tests.factories.content import png
from tests.factories.storage import store_owned_bytes
from tests.fakes.s3_delivery import browser_s3
from tests.paths import TESTDATA_DIR, require_fixtures

MESHES = [
    TESTDATA_DIR / "benchy" / "3dbenchy.stl",
    TESTDATA_DIR / "Spatula_Printables_IS.3mf",
]
require_fixtures(*MESHES)


def _archive(tmp_path, models, **family_changes):
    family = {
        "export_id": "682c8cc0-12a7-41cd-a55e-2a73d92723fa",
        "name": "Portable variants",
        "slug": "portable-variants",
        "canonical_model_hash": models[0].hash,
        "members": [
            {
                "model_hash": model.hash,
                "role": "canonical" if index == 0 else "rescaled",
                "scale_factor": 1.0 if index == 0 else 0.5,
                "relative_to_model_hash": models[0].hash,
                "transformation_note": "Original" if index == 0 else "Half size",
                "sort_order": index,
            }
            for index, model in enumerate(models)
        ],
        **family_changes,
    }
    manifest = {
        "format": "printstash-library-v2",
        "models": [
            {"source_id": model.id, "name": model.name, "hash": model.hash}
            for model in models
        ],
        "families": [family],
    }
    path = tmp_path / "families.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
    return path


@pytest.mark.usefixtures("local_storage")
class TestFamilyPortable:
    @pytest.mark.parametrize(
        "filter_value",
        [
            {"family_id": 1},
            {"family_role": "mirrored"},
            {"in_family": False},
            {"browse": "families_collapsed"},
        ],
    )
    def test_omits_family_saved_views_from_legacy_export(
        self, db_session, auth_headers, filter_value
    ):
        user = db_session.exec(select(User).where(User.is_superuser)).first()
        db_session.add(
            SavedView(
                user_id=user.id,
                name="Family view",
                filters_json=json.dumps(filter_value),
            )
        )
        db_session.commit()
        path = library_transfer.create_archive(db_session, user, version=1)
        try:
            with zipfile.ZipFile(path) as archive:
                manifest = json.loads(archive.read("manifest.json"))
            assert manifest["saved_views"] == []
        finally:
            path.unlink(missing_ok=True)

    def test_reports_missing_saved_family_target_without_broadening_view(
        self, db_session, auth_headers, make_model, tmp_path
    ):
        user = db_session.exec(select(User).where(User.is_superuser)).first()
        path = _archive(tmp_path, [make_model()])
        with zipfile.ZipFile(path) as archive:
            manifest = json.loads(archive.read("manifest.json"))
        manifest["saved_views"] = [
            {
                "name": "Missing Family",
                "filters": {"family_role": "repaired"},
                "family_export_id": "ede112d7-f16e-420b-adb1-8d3c3e00b386",
            }
        ]
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("manifest.json", json.dumps(manifest))

        result = library_transfer.import_archive(db_session, path, user)

        assert result["family_saved_view_conflicts"] == 1
        assert db_session.exec(select(SavedView)).all() == []

    def test_remaps_saved_family_filter_by_portable_identity(
        self, db_session, auth_headers, make_family, make_model, make_family_member
    ):
        user = db_session.exec(select(User).where(User.is_superuser)).first()
        family = make_family()
        make_family_member(family, make_model(), canonical=True)
        family_id, export_id = family.id, family.export_id
        saved = SavedView(
            user_id=user.id,
            name="My variants",
            filters_json=json.dumps(
                {"family_id": family_id, "family_role": "canonical"}
            ),
        )
        db_session.add(saved)
        db_session.commit()
        path = library_transfer.create_archive(db_session, user)
        try:
            with zipfile.ZipFile(path) as archive:
                exported = json.loads(archive.read("manifest.json"))["saved_views"][0]
            assert exported["family_export_id"] == export_id
            assert "family_id" not in exported["filters"]
            db_session.delete(saved)
            lifecycle.trash_family(db_session, user, family.id, family.version)
            db_session.commit()
            lifecycle.purge_family(db_session, user, family.id, family.version)
            db_session.commit()
            make_family("Different grouping")

            library_transfer.import_archive(db_session, path, user)

            imported = db_session.exec(
                select(ModelFamily).where(ModelFamily.export_id == export_id)
            ).one()
            assert imported.id != family_id
            filters = json.loads(db_session.exec(select(SavedView)).one().filters_json)
            assert filters == {"family_id": imported.id, "family_role": "canonical"}
        finally:
            path.unlink(missing_ok=True)

    @pytest.mark.parametrize("condition", ["missing", "oversized", "changed"])
    def test_refuses_export_with_unusable_family_cover(
        self, client, auth_headers, make_family, condition
    ):
        family = make_family(
            cover_filename="cover.webp",
            cover_size_bytes=16 * 1024 * 1024 if condition == "oversized" else 1,
        )
        if condition == "changed":
            get_backend().create_bytes(b"changed", covers.uploaded_key(family))
        response = client.get("/api/v1/models/library-archive", headers=auth_headers)
        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "archive_blob_hash_mismatch"

    @pytest.mark.parametrize("failure", ["audit", "concurrent-membership"])
    def test_compensates_prepared_cover_on_import_failure(
        self, db_session, auth_headers, make_model, tmp_path, monkeypatch, failure
    ):
        models = [make_model(), make_model()]
        ids = [model.id for model in models]
        user = db_session.exec(select(User).where(User.is_superuser)).first()
        user_id = user.id
        data = process_source_cover_upload(png(width=8, height=8), "image/png").data
        entry = "family-covers/682c8cc0-12a7-41cd-a55e-2a73d92723fa.webp"
        path = _archive(
            tmp_path,
            models,
            cover={
                "entry": entry,
                "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            },
        )
        with zipfile.ZipFile(path, "a") as archive:
            archive.writestr(entry, data)
        before = ownership_snapshot(db_session).discovered_keys
        if failure == "audit":

            def failed_audit(*args, **kwargs):
                raise OperationError("audit_unavailable", kind=ErrorKind.UNAVAILABLE)

            monkeypatch.setattr(mutations, "record", failed_audit)
            with pytest.raises(OperationError, match="audit_unavailable"):
                library_transfer.import_archive(db_session, path, user)
            assert db_session.exec(select(ModelFamily)).all() == []
            assert db_session.exec(select(ModelFamilyMember)).all() == []
        else:
            real_prepare = covers.prepare_upload

            def reserve_member_before_publication(session, *args, **kwargs):
                with Session(session.get_bind()) as concurrent:
                    actor = concurrent.get(User, user_id)
                    mutations.create(
                        concurrent,
                        actor,
                        FamilyCreate.model_validate(
                            {
                                "name": "Concurrent Family",
                                "canonical_model_id": ids[1],
                                "members": [{"model_id": ids[1]}],
                            }
                        ),
                    )
                    concurrent.commit()
                return real_prepare(session, *args, **kwargs)

            monkeypatch.setattr(
                covers, "prepare_upload", reserve_member_before_publication
            )
            result = library_transfer.import_archive(db_session, path, user)
            assert result["family_conflicts"] == 1
            assert (
                db_session.exec(select(ModelFamily)).one().name == "Concurrent Family"
            )
            assert db_session.exec(select(ModelFamilyMember)).one().model_id == ids[1]
        assert ownership_snapshot(db_session).discovered_keys == before

    @pytest.mark.parametrize("private_family", [False, True])
    def test_exports_only_authorized_family_members(
        self,
        db_session,
        make_user,
        make_collection,
        grant_role,
        make_model,
        make_family,
        make_family_member,
        private_family,
    ):
        user = make_user()
        shared, private = make_collection("Shared"), make_collection("Private")
        grant_role(user, shared, CollectionRole.VIEW)
        visible = make_model("Visible", collection=shared)
        hidden = make_model("Hidden design", collection=private)
        family = make_family(
            collection=private if private_family else None, cover_model_id=hidden.id
        )
        canonical = make_family_member(family, hidden, canonical=True)
        make_family_member(
            family, visible, scale_factor=0.5, relative_to_member_id=canonical.id
        )
        path = library_transfer.create_archive(db_session, user)
        try:
            with zipfile.ZipFile(path) as archive:
                manifest = json.loads(archive.read("manifest.json"))
            if private_family:
                assert manifest["families"] == []
                assert hidden.hash not in json.dumps(manifest)
                return
            exported = manifest["families"][0]
            assert len(exported["members"]) == 1
            assert exported["members"][0]["model_hash"] == visible.hash
            assert exported["members"][0]["relative_to_model_hash"] is None
            assert exported["members"][0]["relative_review_required"] is True
            assert exported["canonical_model_hash"] is None
            assert exported["cover_model_hash"] is None
            assert exported["collection"] is None
            assert hidden.hash not in json.dumps(manifest)
            assert hidden.name not in json.dumps(manifest)
        finally:
            path.unlink(missing_ok=True)

    @pytest.mark.parametrize(
        "storage_kind", ["local", pytest.param("s3", marks=pytest.mark.s3)]
    )
    def test_roundtrips_family_portable_v2(
        self, db_session, auth_headers, make_model, make_file, tmp_path, storage_kind
    ):
        user = db_session.exec(select(User).where(User.is_superuser)).first()
        local = get_backend()
        (tmp_path / "s3").mkdir()
        context = (
            browser_s3(tmp_path / "s3")
            if storage_kind == "s3"
            else nullcontext((local, None))
        )
        with context as (backend, _tls):
            if storage_kind == "s3":
                from app.core.config import settings

                backend._client.put_bucket_versioning(
                    Bucket=settings.s3_bucket,
                    VersioningConfiguration={"Status": "Enabled"},
                )
                backend.ensure_setup()
            bind_backend(backend)
            try:
                models = []
                for fixture in MESHES:
                    data = fixture.read_bytes()
                    digest = hashlib.sha256(data).hexdigest()
                    model = make_model(fixture.stem, hash=digest)
                    key = backend.blob_key(model.slug, 1, fixture.name)
                    store_owned_bytes(db_session, backend, key, data)
                    make_file(
                        model,
                        filename=fixture.name,
                        path=key,
                        sha256=digest,
                        size_bytes=len(data),
                    )
                    models.append(model)
                family = mutations.create(
                    db_session,
                    user,
                    FamilyCreate.model_validate(
                        {
                            "name": "Portable geometry",
                            "description": "Independent source designs",
                            "canonical_model_id": models[0].id,
                            "members": [
                                {"model_id": models[0].id},
                                {
                                    "model_id": models[1].id,
                                    "role": "print_variant",
                                    "scale_factor": 0.5,
                                    "mirrored": True,
                                    "mirror_verified": True,
                                    "transformation_note": "Alternate print setup",
                                    "sort_order": 3,
                                },
                            ],
                        }
                    ),
                )
                db_session.commit()
                metadata.update_metadata(
                    db_session,
                    user,
                    family.id,
                    FamilyUpdate(version=family.version, tags=["variant"]),
                )
                db_session.commit()
                family = covers.upload(
                    db_session,
                    user,
                    family.id,
                    family.version,
                    png(width=8, height=8),
                    "image/png",
                )
                export_id = family.export_id
                path = library_transfer.create_archive(db_session, user)
                try:
                    with zipfile.ZipFile(path) as archive:
                        manifest = json.loads(archive.read("manifest.json"))
                        exported = manifest["families"][0]
                        assert manifest["format"] == "printstash-library-v2"
                        assert exported["export_id"] == export_id
                        assert "model_id" not in exported["members"][0]
                        assert exported["canonical_model_hash"] == models[0].hash
                        assert (
                            hashlib.sha256(
                                archive.read(exported["cover"]["entry"])
                            ).hexdigest()
                            == exported["cover"]["sha256"]
                        )
                    lifecycle.trash_family(db_session, user, family.id, family.version)
                    db_session.commit()
                    lifecycle.purge_family(db_session, user, family.id, family.version)
                    db_session.commit()
                    process_storage_delete_intents()

                    result = library_transfer.import_archive(db_session, path, user)

                    assert result["created_families"] == 1
                    restored = db_session.exec(select(ModelFamily)).one()
                    assert restored.export_id == export_id
                    assert restored.description == "Independent source designs"
                    members = db_session.exec(
                        select(ModelFamilyMember).order_by(ModelFamilyMember.sort_order)
                    ).all()
                    assert [member.model_id for member in members] == [
                        model.id for model in models
                    ]
                    assert members[1].role == "print_variant"
                    assert members[1].transformation_note == "Alternate print setup"
                    assert members[1].sort_order == 3
                    assert members[1].scale_factor == 0.5
                    assert members[1].mirrored is True
                    assert members[1].mirror_verified is True
                    assert members[1].relative_to_member_id == members[0].id
                    assert members[1].mirror_reference_member_id == members[0].id
                    assert db_session.exec(
                        select(Tag.name)
                        .join(ModelFamilyTagLink, Tag.id == ModelFamilyTagLink.tag_id)
                        .where(ModelFamilyTagLink.family_id == restored.id)
                    ).all() == ["variant"]
                    assert members[0].id == restored.canonical_member_id
                    assert restored.cover_filename is not None
                    assert backend.exists(covers.uploaded_key(restored))
                finally:
                    path.unlink(missing_ok=True)
            finally:
                bind_backend(local)

    def test_imports_legacy_archive_without_families(
        self,
        client,
        db_session,
        auth_headers,
        make_family,
        make_model,
        make_family_member,
        tmp_path,
    ):
        family = make_family()
        make_family_member(family, make_model(), canonical=True)
        response = client.get(
            "/api/v1/models/library-archive?version=1", headers=auth_headers
        )
        assert response.status_code == 200, response.text
        assert (
            response.headers["x-printstash-export-warning"] == "model_families_omitted"
        )
        path = tmp_path / "legacy.zip"
        path.write_bytes(response.content)
        with zipfile.ZipFile(path) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            assert manifest["format"] == "printstash-library-v1"
            assert "families" not in manifest
        user = db_session.exec(select(User).where(User.is_superuser)).first()
        result = library_transfer.import_archive(db_session, path, user)
        assert "created_families" not in result
        assert db_session.exec(select(ModelFamily)).one().id == family.id

    @pytest.mark.parametrize(
        "attack", ["traversal", "oversized", "missing", "bad-hash", "not-image"]
    )
    def test_rejects_unsafe_family_cover(
        self, db_session, auth_headers, make_model, tmp_path, attack
    ):
        model = make_model()
        data = b"invalid image"
        entry = "family-covers/682c8cc0-12a7-41cd-a55e-2a73d92723fa.webp"
        cover = {
            "entry": "../outside.webp" if attack == "traversal" else entry,
            "size_bytes": 16 * 1024 * 1024 if attack == "oversized" else len(data),
            "sha256": "0" * 64
            if attack == "bad-hash"
            else hashlib.sha256(data).hexdigest(),
        }
        path = _archive(tmp_path, [model], cover=cover)
        if attack not in {"missing", "traversal", "oversized"}:
            with zipfile.ZipFile(path, "a") as archive:
                archive.writestr(entry, data)
        user = db_session.exec(select(User).where(User.is_superuser)).first()
        before = ownership_snapshot(db_session).discovered_keys
        with pytest.raises(
            ValueError, match="portable_(manifest|family_cover)_invalid"
        ):
            library_transfer.import_archive(db_session, path, user)
        assert db_session.exec(select(ModelFamily)).all() == []
        assert ownership_snapshot(db_session).discovered_keys == before

    def test_reimports_family_idempotently(
        self, db_session, auth_headers, make_model, tmp_path
    ):
        models = [make_model("Original"), make_model("Small")]
        user = db_session.exec(select(User).where(User.is_superuser)).first()
        path = _archive(tmp_path, models)

        first = library_transfer.import_archive(db_session, path, user)
        family = db_session.exec(select(ModelFamily)).one()
        before = family.model_dump()
        members_before = [
            row.model_dump() for row in db_session.exec(select(ModelFamilyMember)).all()
        ]
        second = library_transfer.import_archive(db_session, path, user)

        assert first["created_families"] == 1
        assert second["skipped_families"] == 1
        assert db_session.exec(select(ModelFamily)).one().model_dump() == before
        assert [
            row.model_dump() for row in db_session.exec(select(ModelFamilyMember)).all()
        ] == members_before
        canonical = db_session.get(ModelFamilyMember, family.canonical_member_id)
        assert canonical.model_id == models[0].id
        smaller = next(row for row in members_before if row["model_id"] == models[1].id)
        assert smaller["scale_factor"] == 0.5
        assert smaller["relative_to_member_id"] == canonical.id
        assert smaller["transformation_note"] == "Half size"

    def test_refuses_import_membership_conflict(
        self,
        db_session,
        auth_headers,
        make_model,
        make_family,
        make_family_member,
        tmp_path,
    ):
        models = [make_model(), make_model()]
        existing = make_family()
        original = make_family_member(existing, models[1], canonical=True)
        db_session.refresh(original)
        before = original.model_dump()
        user = db_session.exec(select(User).where(User.is_superuser)).first()

        result = library_transfer.import_archive(
            db_session, _archive(tmp_path, models), user
        )

        assert result["family_conflicts"] == 1
        assert db_session.exec(select(ModelFamily)).one().id == existing.id
        assert db_session.exec(select(ModelFamilyMember)).one().model_dump() == before

    def test_preserves_existing_canonical_on_import(
        self, client, db_session, auth_headers, make_model, tmp_path
    ):
        models = [make_model(), make_model()]
        user = db_session.exec(select(User).where(User.is_superuser)).first()
        path = _archive(tmp_path, models)
        library_transfer.import_archive(db_session, path, user)
        family = db_session.exec(select(ModelFamily)).one()
        next_member = db_session.exec(
            select(ModelFamilyMember).where(ModelFamilyMember.model_id == models[1].id)
        ).one()
        response = client.post(
            f"/api/v1/families/{family.id}/canonical",
            headers=auth_headers,
            json={
                "member_id": next_member.id,
                "version": family.version,
                "previous_role": "identical",
            },
        )
        assert response.status_code == 200, response.text
        chosen_id = response.json()["canonical_member_id"]

        library_transfer.import_archive(db_session, path, user)

        db_session.refresh(family)
        assert family.canonical_member_id == chosen_id

    def test_reports_missing_canonical_as_vacancy(
        self, db_session, auth_headers, make_model, tmp_path
    ):
        model = make_model()
        user = db_session.exec(select(User).where(User.is_superuser)).first()
        path = _archive(
            tmp_path,
            [model],
            canonical_model_hash="f" * 64,
            members=[
                {"model_hash": model.hash, "role": "rescaled", "scale_factor": 0.5}
            ],
        )

        result = library_transfer.import_archive(db_session, path, user)

        family = db_session.exec(select(ModelFamily)).one()
        assert family.canonical_member_id is None
        assert result["family_canonical_vacancies"] == 1
        assert (
            db_session.exec(select(ModelFamilyMember)).one().relative_review_required
            is True
        )

    def test_allocates_slug_for_distinct_portable_identity(
        self, db_session, auth_headers, make_model, make_family, tmp_path
    ):
        original = make_family(slug="portable-variants")
        user = db_session.exec(select(User).where(User.is_superuser)).first()
        result = library_transfer.import_archive(
            db_session, _archive(tmp_path, [make_model()]), user
        )
        assert result["created_families"] == 1
        families = db_session.exec(select(ModelFamily).order_by(ModelFamily.id)).all()
        assert len(families) == 2
        assert families[0].id == original.id
        assert families[1].slug == "portable-variants-2"
