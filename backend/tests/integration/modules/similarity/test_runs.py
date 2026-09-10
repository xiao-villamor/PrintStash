"""Scopes and ownership remain durable across independent database sessions."""

import json
from datetime import timedelta

import pytest
from sqlmodel import Session, select

from app.core.errors import OperationError
from app.core.time import utcnow
from app.db.models import CollectionRole, FileType, SimilarityRun
from app.modules.similarity import configuration, runs


@pytest.fixture
def administrator(db_session, make_user):
    user = make_user(superuser=True)
    configuration.update_settings(db_session, user, {"enabled": True})
    return user


class TestRuns:
    def test_claims_one_run_per_normalized_scope(
        self, db_session, administrator, make_model
    ):
        first, second = make_model(), make_model()
        started = runs.start(
            db_session,
            administrator,
            scope="models",
            ids=[second.id, first.id, first.id],
        )
        with pytest.raises(OperationError, match="similarity_run_active"):
            runs.start(
                db_session, administrator, scope="models", ids=[first.id, second.id]
            )
        assert json.loads(started.scope_ids_json) == [first.id, second.id]
        assert len(db_session.exec(select(SimilarityRun)).all()) == 1

    def test_resumes_committed_checkpoint(self, db_session, administrator):
        started = runs.start(db_session, administrator)
        run, token = runs.claim(db_session)
        assert runs.checkpoint(
            db_session, run, token, progress={"file_id": 42}, counters={"ready": 3}
        )
        with Session(db_session.get_bind()) as restarted:
            resumed, successor = runs.claim(restarted)
            assert resumed.id == started.id
            assert successor != token
            assert json.loads(resumed.checkpoint_json) == {"file_id": 42}
            assert json.loads(resumed.counters_json) == {"ready": 3}

    def test_reclaims_expired_worker(self, db_session, administrator):
        runs.start(db_session, administrator)
        run, old = runs.claim(db_session)
        assert runs.claim(db_session) is None
        run.lease_expires_at = utcnow() - timedelta(seconds=1)
        db_session.add(run)
        db_session.commit()
        with Session(db_session.get_bind()) as second:
            resumed, token = runs.claim(second)
            assert token != old
            assert not runs.checkpoint(db_session, run, old, state="completed")
            assert runs.checkpoint(second, resumed, token, state="completed")
        db_session.refresh(run)
        assert run.state == "completed"
        assert run.active_scope_key is None

    def test_cancel_wins_over_inflight_completion(self, db_session, administrator):
        run = runs.start(db_session, administrator)
        claimed, token = runs.claim(db_session)
        with Session(db_session.get_bind()) as second:
            runs.cancel(second, administrator, run.id)
        assert runs.checkpoint(
            db_session, claimed, token, state="completed", counters={"verified": 1}
        )
        db_session.refresh(run)
        assert run.state == "cancelled"
        assert json.loads(run.counters_json) == {"verified": 1}
        assert runs.cancel(db_session, administrator, run.id).state == "cancelled"
        assert runs.start(db_session, administrator).id != run.id

    def test_refuses_disabled_start(self, db_session, make_user):
        with pytest.raises(OperationError, match="similarity_disabled"):
            runs.start(db_session, make_user(superuser=True))
        assert db_session.exec(select(SimilarityRun)).all() == []

    @pytest.mark.parametrize("ids", [[], [0], [-1], [True], list(range(1, 1002))])
    def test_rejects_invalid_model_scope(self, db_session, administrator, ids):
        with pytest.raises(OperationError, match="similarity_scope"):
            runs.start(db_session, administrator, scope="models", ids=ids)
        assert db_session.exec(select(SimilarityRun)).all() == []

    def test_requires_edit_on_every_model(
        self,
        db_session,
        administrator,
        make_user,
        make_collection,
        make_model,
        grant_role,
    ):
        collection = make_collection()
        first, hidden = make_model(collection=collection), make_model()
        editor = make_user()
        grant_role(editor, collection, CollectionRole.EDIT)
        run = runs.start(db_session, editor, scope="models", ids=[first.id])
        with pytest.raises(OperationError, match="similarity_scope_unavailable"):
            runs.start(db_session, editor, scope="models", ids=[first.id, hidden.id])
        with pytest.raises(OperationError, match="admin_required"):
            runs.start(db_session, editor)
        with pytest.raises(OperationError, match="similarity_run_not_found"):
            runs.require(db_session, make_user(), run.id)

    def test_source_cutoff_excludes_new_arrivals(
        self, db_session, administrator, make_model, make_file
    ):
        model = make_model()
        before = make_file(model, file_type=FileType.STL)
        run = runs.start(db_session, administrator, scope="models", ids=[model.id])
        make_file(
            model, file_type=FileType.STL, uploaded_at=run.cutoff + timedelta(seconds=1)
        )
        make_file(model, trashed=True)
        make_file(make_model())
        assert [
            row.id
            for row in db_session.exec(
                runs.source_query(db_session, run, administrator)
            )
        ] == [before.id]

    def test_schedule_is_disabled_by_default(self, db_session, administrator):
        assert runs.schedule_due(db_session) is None
        assert db_session.exec(select(SimilarityRun)).all() == []

    def test_scheduled_cadence_is_durable(self, db_session, administrator):
        configuration.update_settings(db_session, administrator, {"schedule_hours": 24})
        first = runs.schedule_due(db_session)
        assert first.trigger == "schedule"
        run, token = runs.claim(db_session)
        runs.checkpoint(db_session, run, token, state="completed")
        with Session(db_session.get_bind()) as restarted:
            assert runs.schedule_due(restarted) is None
        assert len(db_session.exec(select(SimilarityRun)).all()) == 1

    def test_limits_source_scope_to_selected_library(
        self,
        db_session,
        administrator,
        make_external_library,
        make_model,
        make_file,
        tmp_path,
    ):
        library = make_external_library(tmp_path)
        file = make_file(
            make_model(),
            filename="source.stl",
            external=True,
            external_library_id=library.id,
        )
        make_file(make_model(), filename="vault.stl")
        run = runs.start(db_session, administrator, scope="sources", ids=[library.id])
        assert [
            row.id
            for row in db_session.exec(
                runs.source_query(db_session, run, administrator)
            )
        ] == [file.id]

    def test_refuses_nonadmin_source_scope(
        self, db_session, make_user, make_external_library, tmp_path
    ):
        library = make_external_library(tmp_path)
        with pytest.raises(OperationError, match="admin_required"):
            runs.normalize_scope(db_session, make_user(), "sources", [library.id])

    def test_refuses_missing_library_source(self, db_session, administrator):
        with pytest.raises(OperationError, match="similarity_scope_unavailable"):
            runs.normalize_scope(db_session, administrator, "sources", [999])


class TestCollectionScopes:
    def test_includes_descendants_without_sql_wildcard_leakage(
        self, db_session, administrator, make_collection, make_model, make_file
    ):
        parent = make_collection(path="parts_100%")
        child = make_collection(path="parts_100%/child")
        unrelated = make_collection(path="partsX100extra/child")
        expected = [
            make_file(make_model(collection=item), file_type=FileType.STL).id
            for item in (parent, child)
        ]
        make_file(make_model(collection=unrelated))
        run = runs.start(
            db_session, administrator, scope="collections", ids=[parent.id]
        )
        assert [
            row.id
            for row in db_session.exec(
                runs.source_query(db_session, run, administrator)
            )
        ] == expected

    def test_requires_edit_access_to_every_selected_collection(
        self, db_session, administrator, make_user, make_collection, grant_role
    ):
        visible, hidden = (
            make_collection(path="visible"),
            make_collection(path="hidden"),
        )
        editor = make_user()
        grant_role(editor, visible, CollectionRole.EDIT)
        assert runs.normalize_scope(
            db_session, editor, "collections", [visible.id]
        ) == (visible.id,)
        with pytest.raises(OperationError, match="similarity_scope_unavailable"):
            runs.normalize_scope(
                db_session, editor, "collections", [visible.id, hidden.id]
            )

    @pytest.mark.parametrize("scope, ids", [("unknown", []), ("library", [1])])
    def test_refuses_ambiguous_scope(self, db_session, administrator, scope, ids):
        with pytest.raises(OperationError, match="similarity_scope_invalid"):
            runs.normalize_scope(db_session, administrator, scope, ids)


class TestScheduledOwnership:
    def test_defers_schedule_without_active_administrator(
        self, db_session, administrator
    ):
        configuration.update_settings(db_session, administrator, {"schedule_hours": 24})
        administrator.is_active = False
        db_session.add(administrator)
        db_session.commit()
        assert runs.schedule_due(db_session) is None
        assert db_session.exec(select(SimilarityRun)).all() == []

    def test_coalesces_schedule_with_existing_manual_run(
        self, db_session, administrator
    ):
        configuration.update_settings(db_session, administrator, {"schedule_hours": 24})
        manual = runs.start(db_session, administrator)
        assert runs.schedule_due(db_session) is None
        assert [run.id for run in db_session.exec(select(SimilarityRun))] == [manual.id]
