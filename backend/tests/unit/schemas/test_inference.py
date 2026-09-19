"""Advanced retrieval settings reject unbounded or ambiguous calibration inputs."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.schemas.inference import SearchSettings


class TestSearchSettings:
    @pytest.mark.parametrize(
        "values",
        [
            {"lexical_weight": 0},
            {"semantic_weight": 11},
            {"rrf_k": 0},
            {"query_timeout_seconds": 31},
            {"semantic_floor": 2},
            {"semantic_floor": float("nan")},
            {"semantic_floors": {"model-alias": 0.5}},
            {"semantic_floors": {"a" * 64: -2}},
        ],
    )
    def test_validates_advanced_retrieval_settings(self, values):
        with pytest.raises(ValidationError):
            SearchSettings(**values)

    def test_reads_retrieval_defaults_from_environment(self, monkeypatch):
        from app.schemas import inference

        monkeypatch.setenv("VAULT_AI_SEARCH_QUERY_TIMEOUT_SECONDS", "2")
        monkeypatch.setenv("VAULT_AI_SEARCH_LEXICAL_WEIGHT", "2")
        monkeypatch.setenv("VAULT_AI_SEARCH_RRF_K", "50")
        monkeypatch.setattr(inference, "environment", Settings())

        result = SearchSettings()

        assert (result.query_timeout_seconds, result.lexical_weight, result.rrf_k) == (
            2,
            2,
            50,
        )
        assert not result.enabled
