"""Generation proposals refuse provider/profile combinations before work starts."""

import pytest
from pydantic import ValidationError

from app.schemas.search_generations import GenerationProposal

LOCAL_MODEL = "a" * 64


class TestGenerationProposal:
    @pytest.mark.parametrize(
        "provider",
        [
            pytest.param({"endpoint_id": 1}, id="remote"),
            pytest.param({"local_model_id": LOCAL_MODEL}, id="local"),
        ],
    )
    def test_preserves_a_single_text_provider(self, provider):
        proposal = GenerationProposal.model_validate(provider)

        assert (
            proposal.model_dump(
                include={"endpoint_id", "local_model_id"}, exclude_none=True
            )
            == provider
        )
        assert proposal.profile == "semantic_text"

    @pytest.mark.parametrize(
        "profile,aggregation",
        [
            pytest.param("thumbnail", "mean", id="thumbnail"),
            pytest.param("multiview", "mean", id="multiview-mean"),
            pytest.param("multiview", "max", id="multiview-max"),
            pytest.param("point_cloud", "mean", id="point-cloud"),
        ],
    )
    def test_preserves_a_local_visual_profile(self, profile, aggregation):
        proposal = GenerationProposal.model_validate(
            {
                "local_model_id": LOCAL_MODEL,
                "profile": profile,
                "aggregation": aggregation,
            }
        )

        assert proposal.model_dump(
            include={"local_model_id", "profile", "aggregation"}
        ) == {
            "local_model_id": LOCAL_MODEL,
            "profile": profile,
            "aggregation": aggregation,
        }

    @pytest.mark.parametrize(
        "providers",
        [
            pytest.param({}, id="absent"),
            pytest.param(
                {"endpoint_id": 1, "local_model_id": LOCAL_MODEL}, id="ambiguous"
            ),
        ],
    )
    def test_refuses_an_ambiguous_provider_selection(self, providers):
        with pytest.raises(ValidationError, match="search_one_provider_required"):
            GenerationProposal.model_validate(providers)

    @pytest.mark.parametrize("profile", ["thumbnail", "multiview", "point_cloud"])
    @pytest.mark.parametrize(
        "provider_settings",
        [
            pytest.param({"endpoint_id": 1}, id="remote"),
            pytest.param(
                {"local_model_id": LOCAL_MODEL, "query_prefix": ""}, id="query-prefix"
            ),
            pytest.param(
                {"local_model_id": LOCAL_MODEL, "document_prefix": ""},
                id="document-prefix",
            ),
        ],
    )
    def test_refuses_a_visual_provider_without_a_local_paired_contract(
        self, profile, provider_settings
    ):
        with pytest.raises(
            ValidationError, match="search_visual_requires_local_paired_encoder"
        ):
            GenerationProposal.model_validate({"profile": profile, **provider_settings})

    @pytest.mark.parametrize("profile", ["semantic_text", "thumbnail", "point_cloud"])
    def test_refuses_max_aggregation_outside_multiview(self, profile):
        with pytest.raises(ValidationError, match="search_aggregation_unavailable"):
            GenerationProposal.model_validate(
                {
                    "local_model_id": LOCAL_MODEL,
                    "profile": profile,
                    "aggregation": "max",
                }
            )
