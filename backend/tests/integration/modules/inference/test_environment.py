"""Environment presets never contact a server before the admin's explicit action."""

from unittest.mock import patch

import pytest
from pydantic import SecretStr
from sqlmodel import select

from app.core.config import _overlay
from app.core.errors import OperationError
from app.db.models import InferenceEndpoint
from app.modules.inference.configuration import load
from app.modules.inference.environment import configured, import_endpoint
from app.modules.search import configuration
from app.schemas.inference import SearchSettings


class TestEnvironment:
    def test_loads_grouped_environment_defaults(self, db_session, monkeypatch):
        monkeypatch.setitem(_overlay, "ai_search_enabled", True)
        monkeypatch.setitem(_overlay, "ai_search_lexical_backend", "ranked_like")
        monkeypatch.setitem(_overlay, "embedding_local_enabled", True)
        monkeypatch.setitem(_overlay, "embedding_download_enabled", True)
        result = configuration.settings(db_session)
        assert result.enabled is True
        assert result.lexical_backend == "ranked_like"
        assert result.local_models_enabled is True
        assert result.download_enabled is True
        configuration.update(db_session, SearchSettings(enabled=False))
        assert configuration.settings(db_session).enabled is False

    def test_imports_environment_endpoint_only_on_admin_request(
        self, db_session, monkeypatch
    ):
        monkeypatch.setitem(_overlay, "embedding_provider", "openai_compatible")
        monkeypatch.setitem(_overlay, "embedding_endpoint", "http://inference.local/v1")
        monkeypatch.setitem(_overlay, "embedding_model", "fixture-encoder")
        monkeypatch.setitem(_overlay, "embedding_native_dimension", 4)
        monkeypatch.setitem(
            _overlay, "embedding_api_key", SecretStr("test-environment-key")
        )
        with patch(
            "app.modules.inference.remote.post_json",
            return_value={"data": [{"index": 0, "embedding": [1, 0, 0, 0]}]},
        ) as egress:
            assert configured() == ["embedding"]
            assert configuration.read(db_session).environment_endpoints == ["embedding"]
            assert db_session.exec(select(InferenceEndpoint)).all() == []
            egress.assert_not_called()
            result = import_endpoint(db_session, "embedding")
            assert egress.call_count == 1
        assert result.model == "fixture-encoder"
        assert result.has_credentials is True
        assert "test-environment-key" not in result.model_dump_json()
        assert (
            load(
                db_session.get(InferenceEndpoint, result.id)
            ).api_key.get_secret_value()
            == "test-environment-key"
        )

    def test_rejects_an_unconfigured_environment(self, db_session):
        with pytest.raises(OperationError, match="inference_environment_unconfigured"):
            import_endpoint(db_session, "embedding")

    def test_imports_environment_chat_capabilities(self, db_session, monkeypatch):
        monkeypatch.setitem(_overlay, "chat_endpoint", "http://chat.local/v1")
        monkeypatch.setitem(_overlay, "chat_model", "fixture-chat")
        monkeypatch.setitem(_overlay, "chat_timeout_seconds", 8)
        with patch(
            "app.modules.inference.chat.post_json",
            return_value={
                "choices": [
                    {"finish_reason": "stop", "message": {"content": '{"probe":"ok"}'}}
                ]
            },
        ):
            result = import_endpoint(db_session, "chat")
        assert result.kind == "chat"
        assert result.model == "fixture-chat"
        assert result.timeout_seconds == 8
        assert result.guarantee == "schema_constrained"
