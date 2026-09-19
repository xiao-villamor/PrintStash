"""Optional annotation providers cannot become dependencies of manual library work."""

import pytest
from fastapi import APIRouter
from sqlmodel import select

from app.bootstrap import optional_features
from app.core.errors import OperationError
from app.db.models import Model
from app.db.session import get_session_factory
from app.modules.ingestion import extensions as ingestion
from app.modules.library.model_views import extensions as annotations
from app.modules.library.model_views.filters import filtered_with_rank
from app.modules.media.fingerprints import FingerprintResult
from app.schemas.models import ModelFilters


class TestOptionalFeatures:
    def test_omits_family_annotations_when_the_package_is_absent(
        self, monkeypatch, db_session, make_user, make_model
    ):
        actor, model = make_user(), make_model()
        previous = annotations._families
        find_spec = optional_features.find_spec
        monkeypatch.setattr(
            optional_features,
            "find_spec",
            lambda package: (
                None
                if package == "app.modules.library.families"
                else find_spec(package)
            ),
        )
        try:
            router = APIRouter()
            optional_features.install_family_routes(router)
            assert router.routes == []
            assert annotations.family_summaries(db_session, actor, [model.id]) == {}
        finally:
            annotations.bind_families(previous)

    def test_rejects_family_filters_when_the_provider_is_absent(
        self, db_session, make_user
    ):
        actor = make_user()
        previous = annotations._families
        annotations.bind_families(None)
        try:
            with pytest.raises(OperationError, match="family_unavailable"):
                filtered_with_rank(db_session, actor, ModelFilters(family_id=42))
        finally:
            annotations.bind_families(previous)

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
                    sessions, 123, actor.id, FingerprintResult(state="ready")
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
