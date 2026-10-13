"""Optional annotation providers cannot become dependencies of manual library work."""

import pytest
from fastapi import APIRouter
from sqlmodel import select

from app.bootstrap import optional_features
from app.db.models import Model
from app.db.session import get_session_factory
from app.modules.ingestion import extensions as ingestion
from app.modules.library.model_views import extensions as annotations
from app.modules.media.fingerprints import FingerprintResult, FingerprintResultState
from app.modules.media.mesh_facts import FingerprintFailureCode


class TestOptionalFeatures:
    @pytest.mark.parametrize("missing", ["similarity", "inference"])
    def test_absent_feature_preserves_manual_library_work(
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
                    sessions,
                    123,
                    actor.id,
                    FingerprintResult(
                        state=FingerprintResultState.FAILED,
                        failure_code=FingerprintFailureCode.ANALYSIS_UNAVAILABLE,
                    ),
                    source_sha256="a" * 64,
                )
                is None
            )
            assert db_session.get(Model, model.id).name == model.name
        finally:
            annotations.bind_annotations(provider)
            ingestion.bind_derivatives(derivatives)

    @pytest.mark.parametrize("missing", ["similarity", "inference"])
    def test_installs_search_independently(self, monkeypatch, missing):
        from fastapi import FastAPI

        original = optional_features.find_spec
        monkeypatch.setattr(
            optional_features,
            "find_spec",
            lambda package: (
                None if package == f"app.modules.{missing}" else original(package)
            ),
        )
        router = APIRouter()
        optional_features.install_search_routes(router)
        application = FastAPI()
        application.include_router(router)
        paths = application.openapi()["paths"]
        assert ("/search" in paths) == (missing != "inference")
        assert "/similarity/search" not in paths
