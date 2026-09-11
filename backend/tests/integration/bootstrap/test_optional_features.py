"""Optional annotation providers cannot become dependencies of manual library work."""

import pytest
from fastapi import APIRouter
from sqlmodel import select

from app.bootstrap import optional_features
from app.db.models import Model
from app.db.session import get_session_factory
from app.modules.ingestion import extensions as ingestion
from app.modules.library.model_views import extensions as annotations
from app.modules.media.fingerprints import FingerprintResult


class TestOptionalFeatures:
    @pytest.mark.parametrize("missing", ["similarity", "inference"])
    def test_absent_feature_leaves_manual_models_and_ingestion_available(
        self, monkeypatch, db_session, make_user, make_model, missing
    ):
        actor = make_user()
        model = make_model()
        provider, derivatives = annotations._annotations, ingestion._derivatives
        find_spec = optional_features.find_spec
        monkeypatch.setattr(
            optional_features,
            "find_spec",
            lambda package: (
                None if package == f"app.modules.{missing}" else find_spec(package)
            ),
        )
        try:
            router = APIRouter()
            optional_features.install_optional_routes(router)
            assert router.routes == []
            assert annotations.similarity_summaries(db_session, actor, [model.id]) == {}
            assert (
                db_session.exec(
                    select(Model).where(
                        annotations.has_open_candidates(db_session, actor)
                    )
                ).all()
                == []
            )
            sessions = get_session_factory()
            assert ingestion.extraction_options(sessions) == {}
            assert (
                ingestion.after_commit(
                    sessions, 123, actor.id, FingerprintResult(state="ready")
                )
                is None
            )
            assert db_session.get(Model, model.id).name == model.name
        finally:
            annotations.bind_annotations(provider)
            ingestion.bind_derivatives(derivatives)
