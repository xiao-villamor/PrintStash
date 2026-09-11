"""Family covers are independent owned bytes with atomic replacement and purge."""

import pytest
from sqlmodel import Session, select

from app.core.errors import ErrorKind, OperationError
from app.db.models import (
    AuditLog,
    CollectionPermission,
    CollectionRole,
    ModelFamily,
    ModelFamilyMember,
)
from app.modules.library.families import covers
from app.modules.storage.storage_backend.runtime import get_backend
from app.modules.storage.storage_utils import ownership_snapshot
from tests.factories.content import png


class TestFamilyCovers:
    @pytest.mark.parametrize("replacement", ["url", "member"])
    def test_metadata_cover_replaces_owned_upload(
        self,
        client,
        auth_headers,
        db_session,
        make_family,
        make_model,
        make_family_member,
        replacement,
    ):
        family = make_family()
        model = make_model()
        make_family_member(family, model, canonical=True)
        uploaded = client.put(
            f"/api/v1/families/{family.id}/cover?version={family.version}",
            headers=auth_headers,
            files={"file": ("cover.png", png(), "image/png")},
        )
        assert uploaded.status_code == 200, uploaded.text
        db_session.refresh(family)
        key = get_backend().model_family_cover_key(
            family.export_id, family.cover_filename
        )
        field = "cover_image_url" if replacement == "url" else "cover_model_id"
        value = "https://example.test/cover.webp" if replacement == "url" else model.id

        changed = client.patch(
            f"/api/v1/families/{family.id}",
            headers=auth_headers,
            json={"version": family.version, field: value},
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["cover_image_uploaded"] is False
        assert changed.json()[field] == value
        assert not get_backend().exists(key)

    @pytest.mark.parametrize("change", ["permission", "version"])
    def test_compensates_upload_after_concurrent_edit(
        self,
        client,
        db_session,
        make_family,
        make_model,
        make_family_member,
        make_user,
        make_collection,
        grant_role,
        headers_for,
        monkeypatch,
        change,
    ):
        user, collection = make_user(), make_collection()
        grant = grant_role(user, collection, CollectionRole.EDIT)
        family = make_family(created_by=user.id)
        make_family_member(family, make_model(collection=collection), canonical=True)
        family_id, permission_id, version = family.id, grant.id, family.version
        headers = headers_for(user)
        db_session.commit()
        real_publish = covers.publish_bytes
        published = []

        def publish_after_concurrent_edit(session, backend, key, data, **kwargs):
            with Session(session.get_bind()) as concurrent:
                if change == "permission":
                    row = concurrent.get(CollectionPermission, permission_id)
                    row.role = CollectionRole.VIEW
                else:
                    row = concurrent.get(ModelFamily, family_id)
                    row.version += 1
                    row.name = "Concurrent edit"
                concurrent.add(row)
                concurrent.commit()
            receipt = real_publish(session, backend, key, data, **kwargs)
            published.append(receipt.key)
            return receipt

        monkeypatch.setattr(covers, "publish_bytes", publish_after_concurrent_edit)
        response = client.put(
            f"/api/v1/families/{family_id}/cover?version={version}",
            headers=headers,
            files={"file": ("cover.png", png(), "image/png")},
        )
        assert response.status_code == (403 if change == "permission" else 409), (
            response.text
        )
        assert len(published) == 1
        assert not get_backend().exists(published[0])
        db_session.refresh(family)
        assert family.cover_filename is None
        if change == "version":
            assert family.name == "Concurrent edit"
            assert family.version == version + 1

    def test_rolls_back_published_cover_when_audit_fails(
        self, client, auth_headers, db_session, make_family, monkeypatch
    ):
        family = make_family()
        before = ownership_snapshot(db_session).discovered_keys

        def unavailable(*args, **kwargs):
            raise OperationError("audit_unavailable", kind=ErrorKind.UNAVAILABLE)

        monkeypatch.setattr(covers, "record", unavailable)
        response = client.put(
            f"/api/v1/families/{family.id}/cover?version={family.version}",
            headers=auth_headers,
            files={"file": ("cover.png", png(), "image/png")},
        )
        assert response.status_code == 503, response.text
        db_session.refresh(family)
        assert family.cover_filename is None
        assert family.version == 1
        assert ownership_snapshot(db_session).discovered_keys == before

    @pytest.mark.parametrize("case", ["hidden", "missing", "unavailable"])
    def test_reports_authorized_cover_availability(
        self, client, auth_headers, make_family, make_user, headers_for, case
    ):
        family = make_family(
            cover_filename="missing.webp" if case == "unavailable" else None
        )
        headers = headers_for(make_user()) if case == "hidden" else auth_headers
        response = client.get(f"/api/v1/families/{family.id}/cover", headers=headers)
        assert response.status_code == (410 if case == "unavailable" else 404), (
            response.text
        )

    def test_rejects_oversized_cover_before_publication(
        self, client, auth_headers, make_family, db_session
    ):
        from app.modules.media.source_cover_processing import MAX_SOURCE_COVER_BYTES

        family = make_family()
        before = ownership_snapshot(db_session).discovered_keys
        response = client.put(
            f"/api/v1/families/{family.id}/cover?version={family.version}",
            headers=auth_headers,
            files={
                "file": ("cover.png", b"x" * (MAX_SOURCE_COVER_BYTES + 1), "image/png")
            },
        )
        assert response.status_code == 422, response.text
        assert ownership_snapshot(db_session).discovered_keys == before

    @pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///etc/passwd"])
    def test_refuses_non_http_cover_urls(self, client, auth_headers, make_family, url):
        family = make_family()
        response = client.patch(
            f"/api/v1/families/{family.id}",
            headers=auth_headers,
            json={"version": family.version, "cover_image_url": url},
        )
        assert response.status_code == 422, response.text

    def test_manages_owned_cover_lifecycle(
        self, client, auth_headers, db_session, make_family
    ):
        family = make_family()
        backend = get_backend()
        previous_key = None
        for size in (8, 9):
            response = client.put(
                f"/api/v1/families/{family.id}/cover?version={family.version}",
                headers=auth_headers,
                files={
                    "file": ("cover.png", png(width=size, height=size), "image/png")
                },
            )
            assert response.status_code == 200, response.text
            db_session.refresh(family)
            body = response.json()
            assert body["cover_image_uploaded"] is True
            content = client.get(body["cover_thumbnail_url"], headers=auth_headers)
            assert content.status_code == 200, content.text
            assert content.headers["content-type"] == "image/webp"
            assert content.headers["x-content-type-options"] == "nosniff"
            blobs = [
                blob
                for blob in ownership_snapshot(db_session, discover=False).primary
                if blob.resource_type == "model_family_cover"
            ]
            assert len(blobs) == 1
            assert blobs[0].resource_id == family.id
            assert blobs[0].expected_size == len(content.content)
            assert backend.read_bytes(blobs[0].key) == content.content
            if previous_key is not None:
                assert not backend.exists(previous_key)
            previous_key = blobs[0].key

        removed = client.delete(
            f"/api/v1/families/{family.id}/cover?version={family.version}",
            headers=auth_headers,
        )
        assert removed.status_code == 200, removed.text
        assert removed.json()["cover_thumbnail_url"] is None
        assert removed.json()["cover_image_uploaded"] is False
        assert not backend.exists(previous_key)

    @pytest.mark.parametrize("failure", ["invalid", "stale"])
    def test_rejects_invalid_or_stale_cover_without_publishing(
        self, client, auth_headers, db_session, make_family, failure
    ):
        family = make_family(version=2)
        before = ownership_snapshot(db_session).claimed_keys
        response = client.put(
            f"/api/v1/families/{family.id}/cover?version={1 if failure == 'stale' else 2}",
            headers=auth_headers,
            files={
                "file": (
                    "cover.png",
                    b"invalid" if failure == "invalid" else png(),
                    "image/png",
                )
            },
        )
        assert response.status_code == (422 if failure == "invalid" else 409), (
            response.text
        )
        db_session.refresh(family)
        assert family.cover_filename is None
        assert family.version == 2
        assert ownership_snapshot(db_session).claimed_keys == before

    def test_purges_family_without_changing_models(
        self,
        client,
        auth_headers,
        db_session,
        make_family,
        make_family_member,
        make_model,
        make_file,
    ):
        family = make_family()
        model = make_model()
        artifact = make_file(model)
        member = make_family_member(family, model, canonical=True)
        family_id, member_id = family.id, member.id
        model_before = db_session.get(type(model), model.id).model_dump()
        artifact_before = db_session.get(type(artifact), artifact.id).model_dump()
        uploaded = client.put(
            f"/api/v1/families/{family.id}/cover?version={family.version}",
            headers=auth_headers,
            files={"file": ("cover.png", png(), "image/png")},
        )
        assert uploaded.status_code == 200, uploaded.text
        db_session.refresh(family)
        key = get_backend().model_family_cover_key(
            family.export_id, family.cover_filename
        )
        trashed = client.delete(
            f"/api/v1/families/{family.id}?version={family.version}",
            headers=auth_headers,
        )
        assert trashed.status_code == 204, trashed.text
        db_session.refresh(family)

        response = client.delete(
            f"/api/v1/families/{family.id}/purge?version={family.version}",
            headers=auth_headers,
        )
        assert response.status_code == 204, response.text
        db_session.expire_all()
        assert db_session.get(ModelFamily, family_id) is None
        assert db_session.get(ModelFamilyMember, member_id) is None
        assert not get_backend().exists(key)
        assert db_session.get(type(model), model.id).model_dump() == model_before
        assert (
            db_session.get(type(artifact), artifact.id).model_dump() == artifact_before
        )
        audit = db_session.exec(
            select(AuditLog).where(
                AuditLog.action == "family.purge", AuditLog.resource_id == family_id
            )
        ).one()
        assert audit.actor_id is not None

    def test_refuses_purging_a_live_family(
        self, client, auth_headers, db_session, make_family
    ):
        family = make_family()
        response = client.delete(
            f"/api/v1/families/{family.id}/purge?version={family.version}",
            headers=auth_headers,
        )
        assert response.status_code == 409, response.text
        assert db_session.get(ModelFamily, family.id) is not None
