"""A publication retains ordered private images without replacing its primary cover."""

from sqlmodel import select

from app.db.models import ModelSourceCover
from app.modules.library import source_covers


def test_retains_every_source_image(
    db_session, make_model, make_provenance_source, make_cover
):
    source = make_provenance_source(make_model())
    images = [
        make_cover(source, position=index, storage_key=f"gallery-{index}.webp")
        for index in range(3)
    ]

    stored = db_session.exec(
        select(ModelSourceCover)
        .where(ModelSourceCover.provenance_source_id == source.id)
        .order_by(ModelSourceCover.position)
    ).all()

    assert [image.id for image in stored] == [image.id for image in images]
    assert source_covers.get(db_session, source.id).id == images[0].id
    assert source_covers.get(db_session, source.id, position=2).id == images[2].id


def test_recovers_gallery_capture(db_session, make_user):
    from app.modules.ingestion import inbox
    from app.schemas.inbox import CaptureSessionCreate, CaptureUploadFileDeclaration
    from tests.factories.capture import DEFAULT_SOURCE_URL, capture_source

    owner = make_user(superuser=True)
    payload = CaptureSessionCreate.model_validate(
        {
            "request_key": "gallery-recovery",
            "source_url": DEFAULT_SOURCE_URL,
            "capture_source": capture_source(),
            "files": [{"id": "part", "name": "part.stl", "file_type": "stl"}],
            "gallery": [
                {"id": str(i) * 64, "name": f"photo-{i}.png"} for i in range(3)
            ],
        }
    )
    first = inbox.start_capture_session(db_session, owner, payload)
    replay = inbox.start_capture_session(db_session, owner, payload)
    for index, image in enumerate(payload.gallery):
        inbox.declare_capture_image(
            db_session,
            owner,
            first.id,
            index,
            CaptureUploadFileDeclaration(
                id=image.id,
                filename=image.name,
                media_type="image/png",
                size_bytes=20,
                sha256=str(index) * 64,
            ),
        )
    receipt = inbox.capture_recovery(db_session, owner, first.id)
    assert replay.id == first.id
    assert sorted(slot.image_position for slot in receipt.slots) == [0, 1, 2]
    assert {slot.filename for slot in receipt.slots} == {
        f"photo-{i}.png" for i in range(3)
    }


def test_rejects_changed_gallery_on_replay(db_session, make_user):
    import pytest

    from app.core.errors import OperationError
    from app.modules.ingestion import inbox
    from app.schemas.inbox import CaptureSessionCreate
    from tests.factories.capture import DEFAULT_SOURCE_URL, capture_source

    owner = make_user(superuser=True)
    raw = {
        "request_key": "gallery-conflict",
        "source_url": DEFAULT_SOURCE_URL,
        "capture_source": capture_source(),
        "files": [{"id": "part", "name": "part.stl", "file_type": "stl"}],
        "gallery": [{"id": "a" * 64, "name": "photo.png"}],
    }
    first = inbox.start_capture_session(
        db_session, owner, CaptureSessionCreate.model_validate(raw)
    )
    raw["gallery"][0]["id"] = "b" * 64
    with pytest.raises(OperationError, match="capture_request_conflict"):
        inbox.start_capture_session(
            db_session, owner, CaptureSessionCreate.model_validate(raw)
        )
    assert first.gallery_json == '[{"id": "' + "a" * 64 + '", "name": "photo.png"}]'


def test_publishes_every_normalized_image(
    db_session, make_model, make_provenance_source
):
    import io

    from PIL import Image

    from app.modules.storage.storage_backend.runtime import get_backend

    source = make_provenance_source(make_model())
    backend = get_backend()
    keys = []
    for position, color in enumerate(("red", "green", "blue")):
        image = io.BytesIO()
        Image.new("RGB", (16, 16), color).save(image, "PNG")
        result = source_covers.put(
            db_session,
            backend,
            provenance_source_id=source.id,
            actor_id=None,
            data=image.getvalue(),
            content_type="image/png",
            position=position,
        )
        db_session.commit()
        keys.append(result.cover.storage_key)
        with Image.open(io.BytesIO(backend.read_bytes(keys[-1]))) as stored:
            assert stored.format == "WEBP"
    assert len(set(keys)) == 3
    assert [
        source_covers.get(db_session, source.id, position=i).storage_key
        for i in range(3)
    ] == keys


def test_gallery_content_requires_model_access(
    client,
    db_session,
    auth_headers,
    make_model,
    make_provenance_source,
    make_cover,
    make_user,
    headers_for,
):
    from app.modules.storage.storage_backend.runtime import get_backend

    model = make_model()
    source = make_provenance_source(model)
    image = make_cover(source, position=1)
    get_backend().write_bytes(b"private-gallery", image.storage_key)
    db_session.commit()
    url = f"/api/v1/models/{model.id}/provenance/{source.id}/cover/content?position=1"
    assert client.get(url).status_code == 401
    outsider = make_user(superuser=False)
    assert client.get(url, headers=headers_for(outsider)).status_code == 403
    allowed = client.get(url, headers=auth_headers)
    assert allowed.status_code == 200
    assert allowed.content == b"private-gallery"
    assert "private" in allowed.headers["Cache-Control"]


def test_refuses_duplicate_portable_gallery_positions():
    import pytest

    from app.modules.ingestion.library_transfer import _portable_source_images

    with pytest.raises(ValueError, match="portable_provenance_invalid"):
        _portable_source_images({"images": [{"position": 1}, {"position": 1}]})


def test_refuses_gallery_downgrade_with_saved_images(
    db_session, make_model, make_provenance_source, make_cover, monkeypatch
):
    import importlib.util

    import pytest

    from tests.paths import ALEMBIC_DIR

    spec = importlib.util.spec_from_file_location(
        "gallery_migration",
        ALEMBIC_DIR / "versions/55955029adf2_retain_ordered_provenance_image_.py",
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    source = make_provenance_source(make_model())
    make_cover(source, position=1)
    monkeypatch.setattr(migration.op, "get_bind", lambda: db_session.connection())
    with pytest.raises(RuntimeError, match="Cannot downgrade"):
        migration.downgrade()
    assert source_covers.get(db_session, source.id, position=1) is not None


def test_retains_gallery_during_source_merge(
    db_session, make_model, make_provenance_source, make_cover
):
    from app.modules.library import provenance

    model = make_model()
    target = make_provenance_source(model, source_item_id="123")
    old = make_provenance_source(model, source_item_id=None)
    first = make_cover(target, position=1)
    second = make_cover(old, position=1)
    third = make_cover(old, position=2)
    before = [row.storage_key for row in (first, second, third)]
    merged, _ = provenance._merge_sources(db_session, target, old)
    db_session.commit()
    rows = db_session.exec(
        select(ModelSourceCover)
        .where(ModelSourceCover.provenance_source_id == merged.id)
        .order_by(ModelSourceCover.position)
    ).all()
    assert [row.storage_key for row in rows] == before
    assert [row.position for row in rows] == [1, 2, 3]


def test_restores_every_gallery_image_from_backup(backup_env):
    import io
    from pathlib import Path

    from PIL import Image

    from app.modules.backups.backup import creation, restore
    from app.modules.storage.storage_backend.runtime import get_backend
    from tests.factories import build_model, build_provenance_source

    expected = {}
    with backup_env.new_session() as session:
        model = build_model(session)
        source = build_provenance_source(session, model)
        source_id = source.id
        for position, color in enumerate(("red", "green", "blue")):
            buffer = io.BytesIO()
            Image.new("RGB", (16, 16), color).save(buffer, "PNG")
            write = source_covers.put(
                session,
                get_backend(),
                provenance_source_id=source_id,
                actor_id=None,
                data=buffer.getvalue(),
                content_type="image/png",
                position=position,
            )
            session.commit()
            expected[write.cover.storage_key] = get_backend().read_bytes(
                write.cover.storage_key
            )
    archive = creation.create_backup()
    for key in expected:
        Path(key).unlink()
    restore.restore_backup(archive.id)
    with backup_env.new_session() as session:
        images = session.exec(
            select(ModelSourceCover).where(
                ModelSourceCover.provenance_source_id == source_id
            )
        ).all()
        assert {image.storage_key for image in images} == set(expected)
    assert {key: get_backend().read_bytes(key) for key in expected} == expected


def test_purges_all_owned_gallery_images(
    client, auth_headers, db_session, make_model, make_provenance_source
):
    from app.modules.storage.storage_backend.runtime import get_backend
    from app.modules.storage.storage_deletion import process_storage_delete_intents
    from tests.factories.content import png

    model = make_model()
    source = make_provenance_source(model)
    keys = []
    for position in range(3):
        write = source_covers.put(
            db_session,
            get_backend(),
            provenance_source_id=source.id,
            actor_id=None,
            data=png(width=8 + position),
            content_type="image/png",
            position=position,
        )
        db_session.commit()
        keys.append(write.cover.storage_key)
    deleted = client.delete(f"/api/v1/models/{model.id}", headers=auth_headers)
    assert deleted.status_code == 204, deleted.text
    assert (
        client.get(
            f"/api/v1/models/{model.id}/provenance/{source.id}/cover/content?position=1",
            headers=auth_headers,
        ).status_code
        == 404
    )
    purged = client.delete(f"/api/v1/models/{model.id}/purge", headers=auth_headers)
    assert purged.status_code == 200, purged.text
    process_storage_delete_intents()
    assert all(get_backend().object_info(key) is None for key in keys)
