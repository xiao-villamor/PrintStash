"""Conditional metadata saves reject stale drafts while old clients advance versions."""

import pytest
from sqlalchemy import text

from app.schemas.editing import EditingBase


@pytest.fixture(
    params=[
        ("model", "models", "patch", "make_model"),
        ("multipart", "multipart-models", "patch", "make_multipart_model"),
        ("document", "documents", "put", "make_document"),
    ],
    ids=["model", "multipart", "document"],
)
def resource(request):
    kind, endpoint, method, factory = request.param
    row = request.getfixturevalue(factory)("Original")
    return kind, f"/api/v1/{endpoint}/{row.id}", method, row


class TestConditionalEdit:
    def test_returns_detail_etag(self, client, auth_headers, resource):
        kind, path, _method, row = resource

        response = client.get(path, headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.json()["edit_version"] == 1
        assert response.headers["etag"] == f'"{kind}-{row.id}-e{row.edit_epoch}-v1"'

    def test_performs_conditional_metadata_edit(self, client, auth_headers, resource):
        kind, path, method, row = resource
        headers = {
            **auth_headers,
            "X-PrintStash-Edit-Contract": "conditional-v1",
            "If-Match": f'"{kind}-{row.id}-e{row.edit_epoch}-v1"',
        }

        response = client.request(
            method, path, json={"name": "Changed"}, headers=headers
        )

        assert response.status_code == 200, response.text
        assert response.json()["name"] == "Changed"
        assert response.json()["edit_version"] > 1
        assert (
            response.headers["etag"]
            == f'"{kind}-{row.id}-e{row.edit_epoch}-v{response.json()["edit_version"]}"'
        )

    def test_rejects_stale_metadata_edit(self, client, auth_headers, resource):
        kind, path, method, row = resource
        first = client.request(
            method, path, json={"name": "Winner"}, headers=auth_headers
        )
        assert first.status_code == 200, first.text

        response = client.request(
            method,
            path,
            json={"name": "Loser"},
            headers={
                **auth_headers,
                "X-PrintStash-Edit-Contract": "conditional-v1",
                "If-Match": f'"{kind}-{row.id}-e{row.edit_epoch}-v1"',
            },
        )

        assert response.status_code == 412, response.text
        assert response.json()["detail"] == "edit_conflict"
        current = client.get(path, headers=auth_headers)
        assert current.json()["name"] == "Winner"

    def test_requires_opted_in_precondition(self, client, auth_headers, resource):
        _kind, path, method, _row = resource

        response = client.request(
            method,
            path,
            json={"name": "Missing match"},
            headers={**auth_headers, "X-PrintStash-Edit-Contract": "conditional-v1"},
        )

        assert response.status_code == 428, response.text
        assert response.json()["detail"] == "edit_precondition_required"
        assert client.get(path, headers=auth_headers).json()["name"] == "Original"

    def test_advances_legacy_metadata_edit(self, client, auth_headers, resource):
        _kind, path, method, _row = resource

        response = client.request(
            method, path, json={"name": "Legacy"}, headers=auth_headers
        )

        assert response.status_code == 200, response.text
        assert response.json()["edit_version"] > 1

    def test_rejects_unknown_edit_contract(self, client, auth_headers, resource):
        _kind, path, method, _row = resource

        response = client.request(
            method,
            path,
            json={"name": "Unknown"},
            headers={**auth_headers, "X-PrintStash-Edit-Contract": "conditional-v2"},
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "edit_contract_invalid"

    @pytest.mark.parametrize(
        "tag",
        ['W/"model-1-v1"', "*", "wrong", '"model-999-v1"'],
        ids=["weak", "wildcard", "malformed", "wrong-entity"],
    )
    def test_rejects_wrong_etag(self, client, auth_headers, resource, tag):
        _kind, path, method, _row = resource

        response = client.request(
            method,
            path,
            json={"name": "Bad tag"},
            headers={**auth_headers, "If-Match": tag},
        )

        assert response.status_code == 412, response.text
        assert response.json()["detail"] == "edit_conflict"

    def test_advances_all_metadata_writer_versions(
        self, client, auth_headers, resource, db_session
    ):
        kind, path, method, row = resource
        table = {
            "model": "models",
            "multipart": "multipart_models",
            "document": "documents",
        }[kind]
        db_session.execute(
            text(f"UPDATE {table} SET name='External metadata' WHERE id=:id"),
            {"id": row.id},
        )
        db_session.commit()

        response = client.request(
            method,
            path,
            json={"name": "Old draft"},
            headers={
                **auth_headers,
                "If-Match": f'"{kind}-{row.id}-e{row.edit_epoch}-v1"',
            },
        )

        assert response.status_code == 412, response.text
        assert (
            client.get(path, headers=auth_headers).json()["name"] == "External metadata"
        )

    def test_ignores_background_thumbnail_for_edit_version(
        self, client, auth_headers, make_model, db_session
    ):
        model = make_model()
        db_session.execute(
            text(
                "UPDATE models SET thumbnail_path='derived.webp', updated_at='2026-08-01' WHERE id=:id"
            ),
            {"id": model.id},
        )
        db_session.commit()

        response = client.get(f"/api/v1/models/{model.id}", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.json()["edit_version"] == 1

    def test_enforces_current_edit_permissions(
        self, client, headers_for, make_user, make_collection, resource, db_session
    ):
        user = make_user()
        folder = make_collection("Protected")
        kind, path, method, row = resource
        table = {
            "model": "models",
            "multipart": "multipart_models",
            "document": "documents",
        }[kind]
        db_session.execute(
            text(f"UPDATE {table} SET collection_id=:folder WHERE id=:id"),
            {"folder": folder.id, "id": row.id},
        )
        db_session.commit()
        version = db_session.execute(
            text(f"SELECT edit_version FROM {table} WHERE id=:id"), {"id": row.id}
        ).scalar_one()

        response = client.request(
            method,
            path,
            json={"name": "Denied"},
            headers={
                **headers_for(user),
                "If-Match": f'"{kind}-{row.id}-e{row.edit_epoch}-v{version}"',
            },
        )

        assert response.status_code == 403, response.text
        assert db_session.execute(
            text(f"SELECT name,edit_version FROM {table} WHERE id=:id"), {"id": row.id}
        ).one() == ("Original", version)

    def test_preserves_version_on_failed_aggregate_save(
        self, client, auth_headers, make_model, make_multipart_model
    ):
        outsider = make_model()
        group = make_multipart_model()

        response = client.put(
            f"/api/v1/multipart-models/{group.id}",
            headers={
                **auth_headers,
                "If-Match": f'"multipart-{group.id}-e{group.edit_epoch}-v1"',
            },
            json={"name": "Invalid", "cover_model_id": outsider.id, "parts": []},
        )

        assert response.status_code == 400, response.text
        current = client.get(
            f"/api/v1/multipart-models/{group.id}", headers=auth_headers
        ).json()
        assert current["edit_version"] == 1
        assert current["name"] == group.name

    def test_conflicts_after_related_metadata_writer(
        self, client, auth_headers, make_model, make_multipart_model
    ):
        member = make_model()
        group = make_multipart_model()
        response = client.put(
            f"/api/v1/multipart-models/{group.id}/parts",
            json={"parts": [{"name": "Body", "model_ids": [member.id]}]},
            headers=auth_headers,
        )
        assert response.status_code == 200, response.text

        stale = client.patch(
            f"/api/v1/multipart-models/{group.id}",
            headers={
                **auth_headers,
                "If-Match": f'"multipart-{group.id}-e{group.edit_epoch}-v1"',
            },
            json={"name": "Old draft"},
        )

        assert stale.status_code == 412, stale.text
        assert (
            client.get(
                f"/api/v1/multipart-models/{group.id}", headers=auth_headers
            ).json()["part_count"]
            == 1
        )


class TestMultipartCoverEdit:
    def test_requires_upload_precondition(
        self, client, auth_headers, make_multipart_model
    ):
        from tests.factories.content import png

        group = make_multipart_model()

        response = client.put(
            f"/api/v1/multipart-models/{group.id}/cover",
            headers={**auth_headers, "X-PrintStash-Edit-Contract": "conditional-v1"},
            files={"file": ("cover.png", png(), "image/png")},
        )

        assert response.status_code == 428, response.text
        assert (
            client.get(
                f"/api/v1/multipart-models/{group.id}", headers=auth_headers
            ).json()["cover_image_uploaded"]
            is False
        )

    def test_rejects_stale_cover_upload(
        self, client, auth_headers, make_multipart_model
    ):
        from tests.factories.content import png

        group = make_multipart_model()
        changed = client.patch(
            f"/api/v1/multipart-models/{group.id}",
            headers=auth_headers,
            json={"name": "New metadata"},
        )
        assert changed.status_code == 200, changed.text

        response = client.put(
            f"/api/v1/multipart-models/{group.id}/cover",
            headers={
                **auth_headers,
                "If-Match": f'"multipart-{group.id}-e{group.edit_epoch}-v1"',
            },
            files={"file": ("cover.png", png(), "image/png")},
        )

        assert response.status_code == 412, response.text
        detail = client.get(
            f"/api/v1/multipart-models/{group.id}", headers=auth_headers
        ).json()
        assert detail["cover_image_uploaded"] is False
        assert detail["edit_version"] == changed.json()["edit_version"]

    def test_conditions_cover_deletion(
        self, client, auth_headers, make_multipart_model
    ):
        from tests.factories.content import png

        group = make_multipart_model()
        path = f"/api/v1/multipart-models/{group.id}/cover"
        uploaded = client.put(
            path,
            headers={
                **auth_headers,
                "If-Match": f'"multipart-{group.id}-e{group.edit_epoch}-v1"',
            },
            files={"file": ("cover.png", png(), "image/png")},
        )
        assert uploaded.status_code == 200, uploaded.text
        stale = client.delete(
            path,
            headers={
                **auth_headers,
                "If-Match": f'"multipart-{group.id}-e{group.edit_epoch}-v1"',
            },
        )
        assert stale.status_code == 412, stale.text

        deleted = client.delete(
            path, headers={**auth_headers, "If-Match": uploaded.headers["etag"]}
        )

        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["cover_image_uploaded"] is False
        assert deleted.json()["edit_version"] > uploaded.json()["edit_version"]
        assert (
            deleted.headers["etag"]
            == f'"multipart-{group.id}-e{group.edit_epoch}-v{deleted.json()["edit_version"]}"'
        )


@pytest.fixture(
    params=[
        ("model", "Model", "build_model"),
        ("multipart", "MultipartModel", "build_multipart_model"),
        ("document", "Document", "build_document"),
    ],
    ids=["model", "multipart", "document"],
)
def edit_database(request, tmp_path):
    from sqlalchemy import event
    from sqlmodel import Session, SQLModel, create_engine

    from app.db import models
    from app.db.session import _set_sqlite_pragmas
    from tests import factories

    kind, entity_name, factory_name = request.param
    engine = create_engine(
        f"sqlite:///{tmp_path / 'edit.sqlite'}",
        connect_args={"check_same_thread": False},
    )
    event.listen(engine, "connect", _set_sqlite_pragmas)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        user = factories.build_user(session, superuser=True)
        row = getattr(factories, factory_name)(session, "Original")
        yield engine, kind, getattr(models, entity_name), user.id, row.id
    engine.dispose()


class TestClaim:
    def test_permits_only_one_atomic_editor(self, edit_database):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier

        from sqlmodel import Session

        from app.core.errors import OperationError
        from app.db.models import User
        from app.modules.library.edit_preconditions import EditPrecondition, claim

        engine, kind, entity, user_id, row_id = edit_database
        start = Barrier(2)

        def editor(name):
            with Session(engine) as session:
                user = session.get(User, user_id)
                row = session.get(entity, row_id)
                start.wait(timeout=10)
                try:
                    claim(
                        session,
                        user,
                        row,
                        EditPrecondition(
                            if_match=f'"{kind}-{row_id}-e{row.edit_epoch}-v1"',
                            contract="conditional-v1",
                        ),
                    )
                    row.name = name
                    session.add(row)
                    session.commit()
                    return "saved"
                except OperationError as exc:
                    session.rollback()
                    return exc.detail

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(editor, "First editor")
            second = executor.submit(editor, "Second editor")
            results = [first.result(timeout=20), second.result(timeout=20)]

        assert sorted(results) == ["edit_conflict", "saved"]
        with Session(engine) as session:
            assert session.get(entity, row_id).name in {"First editor", "Second editor"}

    def test_preserves_version_after_rollback(self, edit_database):
        from sqlmodel import Session

        from app.db.models import User
        from app.modules.library.edit_preconditions import EditPrecondition, claim

        engine, kind, entity, user_id, row_id = edit_database
        with Session(engine) as session:
            row = session.get(entity, row_id)
            claim(
                session,
                session.get(User, user_id),
                session.get(entity, row_id),
                EditPrecondition(if_match=f'"{kind}-{row_id}-e{row.edit_epoch}-v1"'),
            )
            session.rollback()

        with Session(engine) as session:
            row = session.get(entity, row_id)
            assert row.edit_version == 1
            assert row.name == "Original"

    @pytest.mark.parametrize(
        "change", ["disabled", "session", "admin"], ids=["disabled", "session", "admin"]
    )
    def test_rejects_revoked_actor(self, edit_database, change):
        from sqlmodel import Session

        from app.core.errors import OperationError
        from app.db.models import User
        from app.modules.library.edit_preconditions import EditPrecondition, claim

        engine, kind, entity, user_id, row_id = edit_database
        with Session(engine) as session:
            actor = session.get(User, user_id)
            row = session.get(entity, row_id)
            with Session(engine) as other:
                revoked = other.get(User, user_id)
                if change == "disabled":
                    revoked.is_active = False
                elif change == "session":
                    revoked.auth_version += 1
                else:
                    revoked.is_superuser = False
                other.add(revoked)
                other.commit()

            with pytest.raises(OperationError, match="collection_permission_denied"):
                claim(
                    session,
                    actor,
                    row,
                    EditPrecondition(
                        if_match=f'"{kind}-{row_id}-e{row.edit_epoch}-v1"'
                    ),
                )
            session.rollback()

        with Session(engine) as session:
            assert session.get(entity, row_id).edit_version == 1


class TestConditionalBatchEdit:
    @pytest.mark.parametrize(
        "path, changes",
        [("move", {"collection": "New folder"}), ("tags", {"add": ["new-tag"]})],
        ids=["move", "tags"],
    )
    def test_skips_stale_selected_entity(
        self, client, auth_headers, make_model, path, changes
    ):
        stale = make_model("Stale")
        current = make_model("Current")
        edited = client.patch(
            f"/api/v1/models/{stale.id}",
            headers=auth_headers,
            json={"name": "Concurrent"},
        )
        assert edited.status_code == 200, edited.text

        response = client.post(
            f"/api/v1/models/batch/{path}",
            headers={**auth_headers, "X-PrintStash-Edit-Contract": "conditional-v1"},
            json={
                "model_ids": [stale.id, current.id],
                "expected_versions": {
                    str(stale.id): {"edit_version": 1, "edit_epoch": stale.edit_epoch},
                    str(current.id): {
                        "edit_version": 1,
                        "edit_epoch": current.edit_epoch,
                    },
                },
                **changes,
            },
        )

        assert response.status_code == 200, response.text
        current_version = client.get(
            f"/api/v1/models/{current.id}", headers=auth_headers
        ).json()["edit_version"]
        assert response.json() == {
            "succeeded_versions": {
                str(current.id): {
                    "edit_version": current_version,
                    "edit_epoch": current.edit_epoch,
                }
            },
            "succeeded_ids": [current.id],
            "failed": [{"model_id": stale.id, "reason": "edit_conflict"}],
            "succeeded_count": 1,
            "failed_count": 1,
        }
        assert (
            client.get(f"/api/v1/models/{stale.id}", headers=auth_headers).json()[
                "name"
            ]
            == "Concurrent"
        )
        assert (
            client.get(f"/api/v1/models/{current.id}", headers=auth_headers).json()[
                "edit_version"
            ]
            > 1
        )

    @pytest.mark.parametrize(
        "path, changes",
        [
            ("move", {"collection": "Uncreated folder"}),
            ("tags", {"add": ["uncreated-tag"]}),
        ],
        ids=["move", "tags"],
    )
    def test_requires_every_selected_precondition(
        self, client, auth_headers, make_model, path, changes
    ):
        first = make_model()
        second = make_model()

        response = client.post(
            f"/api/v1/models/batch/{path}",
            headers={**auth_headers, "X-PrintStash-Edit-Contract": "conditional-v1"},
            json={
                "model_ids": [first.id, second.id],
                "expected_versions": {
                    str(first.id): {"edit_version": 1, "edit_epoch": first.edit_epoch}
                },
                **changes,
            },
        )

        assert response.status_code == 428, response.text
        assert response.json()["detail"] == "edit_precondition_required"
        assert (
            client.get(f"/api/v1/models/{first.id}", headers=auth_headers).json()[
                "edit_version"
            ]
            == 1
        )

    @pytest.mark.parametrize(
        "path, changes, undo",
        [
            ("move", {"collection": "New folder"}, {"collection": ""}),
            ("tags", {"add": ["new-tag"]}, {"remove": ["new-tag"]}),
        ],
        ids=["move", "tags"],
    )
    def test_rejects_undo_after_newer_metadata_edit(
        self, client, auth_headers, make_model, path, changes, undo
    ):
        model = make_model()
        headers = {**auth_headers, "X-PrintStash-Edit-Contract": "conditional-v1"}
        saved = client.post(
            f"/api/v1/models/batch/{path}",
            headers=headers,
            json={
                "model_ids": [model.id],
                "expected_versions": {
                    str(model.id): {"edit_version": 1, "edit_epoch": model.edit_epoch}
                },
                **changes,
            },
        )
        assert saved.status_code == 200, saved.text
        versions = saved.json()["succeeded_versions"]
        current = client.get(f"/api/v1/models/{model.id}", headers=auth_headers)
        assert versions == {
            str(model.id): {
                "edit_version": current.json()["edit_version"],
                "edit_epoch": current.json()["edit_epoch"],
            }
        }
        edited = client.patch(
            f"/api/v1/models/{model.id}",
            headers=auth_headers,
            json={"name": "Newer edit"},
        )
        assert edited.status_code == 200, edited.text

        undone = client.post(
            f"/api/v1/models/batch/{path}",
            headers=headers,
            json={"model_ids": [model.id], "expected_versions": versions, **undo},
        )

        assert undone.status_code == 200, undone.text
        assert undone.json()["succeeded_versions"] == {}
        assert undone.json()["failed"] == [
            {"model_id": model.id, "reason": "edit_conflict"}
        ]
        retained = client.get(f"/api/v1/models/{model.id}", headers=auth_headers).json()
        assert retained["name"] == "Newer edit"
        assert retained["collection"] == ("new-folder" if path == "move" else None)
        assert ("new-tag" in retained["tags"]) == (path == "tags")

    @pytest.mark.parametrize(
        "edit_database",
        [("model", "Model", "build_model")],
        indirect=True,
        ids=["model"],
    )
    @pytest.mark.parametrize("path", ["move", "tags"], ids=["move", "tags"])
    def test_captures_acknowledged_version_before_commit_returns(
        self, edit_database, path
    ):
        from sqlalchemy import event
        from sqlmodel import Session

        from app.db.models import User
        from app.modules.library.commands import batch_move_models, batch_tag_models
        from app.schemas.models import ModelBatchMove, ModelBatchTags

        engine, _kind, entity, user_id, row_id = edit_database
        acknowledged = {}

        def external_writer(_session):
            # after_commit also fires for SAVEPOINT release. Only the outer
            # physical commit admits a separate writer on the locked row.
            if _session.in_nested_transaction():
                return
            with Session(engine) as other:
                row = other.get(entity, row_id)
                acknowledged[str(row_id)] = EditingBase.model_validate(
                    row, from_attributes=True
                )
                row.name = "Committed externally"
                other.add(row)
                other.commit()

        with Session(engine) as session:
            event.listen(session, "after_commit", external_writer)
            command = batch_move_models if path == "move" else batch_tag_models
            payload = (
                ModelBatchMove(
                    model_ids=[row_id],
                    expected_versions={
                        row_id: EditingBase.model_validate(
                            session.get(entity, row_id), from_attributes=True
                        )
                    },
                )
                if path == "move"
                else ModelBatchTags(
                    model_ids=[row_id],
                    expected_versions={
                        row_id: EditingBase.model_validate(
                            session.get(entity, row_id), from_attributes=True
                        )
                    },
                    add=["new-tag"],
                )
            )
            result = command(payload, session.get(User, user_id), session)

        assert result.succeeded_versions == acknowledged
        with Session(engine) as session:
            latest = session.get(entity, row_id)
            assert latest.name == "Committed externally"
            assert (
                latest.edit_version
                > result.succeeded_versions[str(row_id)].edit_version
            )

    @pytest.mark.parametrize(
        "edit_database",
        [("model", "Model", "build_model")],
        indirect=True,
        ids=["model"],
    )
    @pytest.mark.parametrize("path", ["move", "tags"], ids=["move", "tags"])
    def test_aborts_batch_for_revoked_actor(self, edit_database, path):
        from sqlmodel import Session

        from app.core.errors import OperationError
        from app.db.models import User
        from app.modules.library.commands import batch_move_models, batch_tag_models
        from app.schemas.models import ModelBatchMove, ModelBatchTags
        from tests.factories import build_model

        engine, _kind, entity, user_id, row_id = edit_database
        with Session(engine) as setup:
            peer_id = build_model(setup, "Peer").id
        with Session(engine) as session:
            actor = session.get(User, user_id)
            with Session(engine) as other:
                revoked = other.get(User, user_id)
                revoked.is_active = False
                other.add(revoked)
                other.commit()
            ids = [row_id, peer_id]
            payload = (
                ModelBatchMove(
                    model_ids=ids,
                    expected_versions={
                        id: EditingBase.model_validate(
                            session.get(entity, id), from_attributes=True
                        )
                        for id in ids
                    },
                )
                if path == "move"
                else ModelBatchTags(
                    model_ids=ids,
                    expected_versions={
                        id: EditingBase.model_validate(
                            session.get(entity, id), from_attributes=True
                        )
                        for id in ids
                    },
                    add=["new-tag"],
                )
            )
            command = batch_move_models if path == "move" else batch_tag_models

            with pytest.raises(OperationError, match="collection_permission_denied"):
                command(payload, actor, session)

        with Session(engine) as session:
            assert session.get(entity, row_id).edit_version == 1
            assert session.get(entity, peer_id).edit_version == 1

    def test_keeps_batch_claim_in_outer_transaction(
        self, db_session, make_user, make_model
    ):
        from app.modules.library.commands import batch_move_models
        from app.schemas.models import ModelBatchMove

        user = make_user(superuser=True)
        model = make_model()
        result = batch_move_models(
            ModelBatchMove(
                model_ids=[model.id],
                expected_versions={
                    model.id: EditingBase.model_validate(model, from_attributes=True)
                },
            ),
            user,
            db_session,
            commit=False,
        )
        assert result.succeeded_ids == [model.id]

        db_session.rollback()

        db_session.refresh(model)
        assert model.edit_version == 1

    @pytest.mark.parametrize(
        "path, changes",
        [("move", {"collection": "New folder"}), ("tags", {"add": ["new-tag"]})],
        ids=["move", "tags"],
    )
    def test_enforces_supplied_versions_without_contract(
        self, client, auth_headers, make_model, path, changes
    ):
        model = make_model()
        edited = client.patch(
            f"/api/v1/models/{model.id}", headers=auth_headers, json={"name": "Current"}
        )
        assert edited.status_code == 200, edited.text

        response = client.post(
            f"/api/v1/models/batch/{path}",
            headers=auth_headers,
            json={
                "model_ids": [model.id],
                "expected_versions": {
                    str(model.id): {"edit_version": 1, "edit_epoch": model.edit_epoch}
                },
                **changes,
            },
        )

        assert response.status_code == 200, response.text
        assert response.json()["succeeded_ids"] == []
        assert response.json()["failed"] == [
            {"model_id": model.id, "reason": "edit_conflict"}
        ]

    def test_rejects_unselected_version_key(self, client, auth_headers, make_model):
        model = make_model()

        response = client.post(
            "/api/v1/models/batch/tags",
            headers=auth_headers,
            json={
                "model_ids": [model.id],
                "expected_versions": {
                    str(model.id): {"edit_version": 1, "edit_epoch": model.edit_epoch},
                    "9999": {"edit_version": 1, "edit_epoch": model.edit_epoch},
                },
                "add": ["new-tag"],
            },
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "edit_precondition_invalid"


class TestEditingHistory:
    def test_reads_the_database_incarnation_with_the_entity(
        self, client, auth_headers, resource
    ):
        _kind, path, _method, row = resource
        response = client.get(path, headers=auth_headers)
        assert response.status_code == 200, response.text
        assert response.json()["edit_epoch"] == row.edit_epoch
        assert "edit_epoch" not in row.__table__.columns

    def test_rejects_the_previous_database_incarnation(
        self, client, auth_headers, resource, db_session
    ):
        from app.db.models import LibraryRevision

        _kind, path, method, row = resource
        before = client.get(path, headers=auth_headers)
        old_tag = before.headers["etag"]
        authority = db_session.get_one(LibraryRevision, 1)
        authority.epoch = "b" * 32
        db_session.add(authority)
        db_session.commit()
        rejected = client.request(
            method,
            path,
            headers={
                **auth_headers,
                "If-Match": old_tag,
                "X-PrintStash-Edit-Contract": "conditional-v1",
            },
            json={"name": "Old history"},
        )
        assert rejected.status_code == 412, rejected.text
        current = client.get(path, headers=auth_headers).json()
        assert current["name"] == "Original"
        assert current["edit_version"] == before.json()["edit_version"]
        assert current["edit_epoch"] == "b" * 32

    @pytest.mark.parametrize("operation", ["move", "tags"])
    def test_rejects_a_batch_from_an_old_history(
        self, client, auth_headers, make_model, operation
    ):
        model = make_model()
        payload = {
            "model_ids": [model.id],
            "expected_versions": {
                str(model.id): {
                    "edit_version": model.edit_version,
                    "edit_epoch": "b" * 32,
                }
            },
        }
        payload.update(
            {"collection": "old-destination"}
            if operation == "move"
            else {"add": ["old-tag"]}
        )
        response = client.post(
            f"/api/v1/models/batch/{operation}",
            headers={**auth_headers, "X-PrintStash-Edit-Contract": "conditional-v1"},
            json=payload,
        )
        assert response.status_code == 200, response.text
        assert response.json()["succeeded_versions"] == {}
        assert response.json()["failed"] == [
            {"model_id": model.id, "reason": "edit_conflict"}
        ]
        current = client.get(f"/api/v1/models/{model.id}", headers=auth_headers).json()
        assert current["collection"] is None
        assert current["tags"] == []
        assert current["edit_version"] == model.edit_version

    def test_rejects_cover_bytes_from_an_old_history(
        self, client, auth_headers, make_multipart_model
    ):
        from tests.factories.content import png

        group = make_multipart_model()
        path = f"/api/v1/multipart-models/{group.id}"
        before = client.get(path, headers=auth_headers)
        rejected = client.put(
            f"{path}/cover",
            headers={
                **auth_headers,
                "If-Match": f'"multipart-{group.id}-e{"b" * 32}-v{group.edit_version}"',
            },
            files={"file": ("cover.png", png(), "image/png")},
        )
        assert rejected.status_code == 412, rejected.text
        after = client.get(path, headers=auth_headers)
        assert after.json()["cover_image_uploaded"] is False
        assert after.headers["etag"] == before.headers["etag"]
