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
