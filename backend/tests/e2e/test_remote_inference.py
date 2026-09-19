"""Administrator setup proves actual canary egress through the complete HTTP API."""

import pytest

from app.modules.inference.transport import close_client
from tests.fakes.inference import InferenceFake
from tests.fakes.server import start_server


class TestRemoteInferenceSetup:
    @pytest.mark.asyncio
    async def test_configures_a_probed_embedding_endpoint(self, api, superuser_headers):
        fake = InferenceFake()
        running = start_server(fake.app())
        try:
            response = await api.post(
                "/api/v1/config/ai-search/endpoints",
                headers=superuser_headers,
                json={
                    "base_url": running.base_url + "/v1",
                    "model": fake.model,
                    "native_dimension": 4,
                    "api_key": "test-e2e-key",
                },
            )

            assert response.status_code == 201, response.text
            assert response.json()["native_dimension"] == 4
            assert fake.calls[0]["headers"]["authorization"] == "Bearer test-e2e-key"
            settings = await api.get(
                "/api/v1/config/ai-search", headers=superuser_headers
            )
            assert settings.json()["endpoints"] == [response.json()]
        finally:
            close_client()
            running.stop()

    @pytest.mark.asyncio
    async def test_configures_chat_with_reported_json_guarantees(
        self, api, superuser_headers
    ):
        fake = InferenceFake(chat_dialect="json", chat_result={"probe": "ok"})
        running = start_server(fake.app())
        try:
            response = await api.post(
                "/api/v1/config/ai-search/endpoints",
                headers=superuser_headers,
                json={
                    "base_url": running.base_url + "/v1",
                    "model": fake.model,
                    "kind": "chat",
                },
            )

            assert response.status_code == 201, response.text
            assert response.json()["guarantee"] == "validated_json"
            enabled = await api.put(
                "/api/v1/config/ai-search",
                headers=superuser_headers,
                json={
                    "chat_endpoint_id": response.json()["id"],
                    "nl_filters_enabled": True,
                },
            )
            assert enabled.status_code == 200, enabled.text
            assert enabled.json()["settings"]["captions_enabled"] is False
        finally:
            close_client()
            running.stop()
