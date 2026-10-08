"""Badges and Saved View filters share the same two-endpoint EDIT boundary."""

from sqlmodel import select

from app.db.models import CollectionRole, Model
from app.modules.similarity.projections import has_open_candidates, summaries


class TestProjections:
    def test_counts_both_endpoints_without_hidden_pairs(
        self,
        db_session,
        make_model,
        make_file,
        make_user,
        make_collection,
        grant_role,
        make_geometry_fingerprint,
        make_similarity_candidate,
        make_similarity_observation,
    ):
        a, b, hidden = [
            make_model(collection=make_collection(path=name))
            for name in ("one", "two", "hidden")
        ]
        actor = make_user()
        for model in (a, b):
            grant_role(actor, model.collection_id, CollectionRole.EDIT)
        fa, fb, fh = [
            make_geometry_fingerprint(make_file(model), state="ready")
            for model in (a, b, hidden)
        ]
        shown = make_similarity_candidate(a, b)
        make_similarity_observation(shown, fa, fb)
        secret = make_similarity_candidate(a, hidden)
        make_similarity_observation(secret, fa, fh)

        counts = summaries(db_session, actor, [a.id, b.id, hidden.id])
        assert counts == {
            a.id: {"open_candidates": 1, "confirmed": 0},
            b.id: {"open_candidates": 1, "confirmed": 0},
        }
        matches = db_session.exec(
            select(Model.id).where(has_open_candidates(db_session, actor))
        ).all()
        assert set(matches) == {a.id, b.id}

    def test_rechecks_the_actor_after_a_previous_badge_read(
        self,
        db_session,
        make_model,
        make_file,
        make_user,
        make_collection,
        grant_role,
        make_geometry_fingerprint,
        make_similarity_candidate,
        make_similarity_observation,
    ):
        a, b = [
            make_model(collection=make_collection(path=name))
            for name in ("first", "second")
        ]
        administrator = make_user(superuser=True)
        restricted = make_user()
        grant_role(restricted, a.collection_id, CollectionRole.EDIT)
        fa, fb = [
            make_geometry_fingerprint(make_file(model), state="ready")
            for model in (a, b)
        ]
        pair = make_similarity_candidate(a, b)
        make_similarity_observation(pair, fa, fb)

        assert summaries(db_session, administrator, [a.id]) == {
            a.id: {"open_candidates": 1, "confirmed": 0}
        }
        assert summaries(db_session, restricted, [a.id]) == {}
        grant_role(restricted, b.collection_id, CollectionRole.EDIT)
        assert summaries(db_session, restricted, [a.id]) == {
            a.id: {"open_candidates": 1, "confirmed": 0}
        }

    def test_excludes_stale_or_decided_pairs_from_open_filter(
        self,
        db_session,
        make_model,
        make_file,
        make_user,
        make_geometry_fingerprint,
        make_similarity_candidate,
        make_similarity_observation,
    ):
        actor = make_user(superuser=True)
        a, b = make_model(), make_model()
        file = make_file(a)
        fa, fb = (
            make_geometry_fingerprint(file, state="ready"),
            make_geometry_fingerprint(make_file(b), state="ready"),
        )
        candidate = make_similarity_candidate(a, b, review_state="confirmed")
        make_similarity_observation(candidate, fa, fb)
        assert summaries(db_session, actor, [a.id])[a.id]["confirmed"] == 1
        assert (
            db_session.exec(
                select(Model.id).where(has_open_candidates(db_session, actor))
            ).all()
            == []
        )
        file.sha256 = "f" * 64
        db_session.add(file)
        db_session.commit()
        assert summaries(db_session, actor, [a.id, b.id]) == {}


class TestBoundedBadgeWork:
    def test_bounds_current_badges_to_candidate_identities(
        self,
        db_session,
        make_model,
        make_file,
        make_user,
        make_geometry_fingerprint,
        make_similarity_candidate,
        make_similarity_observation,
    ):
        from tests.fakes.sqlite_work import sqlite_work

        actor = make_user(superuser=True)
        a, b = make_model(), make_model()
        fa = make_geometry_fingerprint(make_file(a), state="ready")
        fb = make_geometry_fingerprint(make_file(b), state="ready")
        candidate = make_similarity_candidate(a, b)
        make_similarity_observation(candidate, fa, fb)
        target_id = a.id
        db_session.connection().exec_driver_sql("ANALYZE")
        db_session.expire_all()
        with sqlite_work(db_session) as small:
            before = summaries(db_session, actor, [target_id])
        for index in range(1000):
            make_model(f"Unrelated badge Model {index}")
        db_session.commit()
        db_session.expire_all()
        with sqlite_work(db_session) as large:
            after = summaries(db_session, actor, [target_id])
        assert before == after == {target_id: {"open_candidates": 1, "confirmed": 0}}
        assert large.instructions <= max(1000, small.instructions * 2)
