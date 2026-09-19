"""Actual rendering and HTTP VLM completion feed searchable, separately editable text."""

import asyncio
import base64
import hashlib
from io import BytesIO

import pytest
from PIL import Image

from app.db.models import FileType
from app.db.session import get_session_factory
from app.modules.inference.transport import close_client
from app.modules.search.caption_worker import CaptionProcessor
from tests.factories import build_file, build_model
from tests.factories.geometry import tetrahedron
from tests.fakes.inference import InferenceFake
from tests.fakes.server import start_server

pytestmark = pytest.mark.asyncio


class TestCaptionWorkflow:
    async def test_generates_a_searchable_caption_without_changing_human_text(
        self, api, e2e_db, superuser_headers, tmp_path
    ):
        payload = tetrahedron().export(file_type="stl")
        path = tmp_path / "opaque.stl"
        path.write_bytes(payload)
        model = build_model(e2e_db, "Opaque part", description="Human description")
        build_file(
            e2e_db,
            model,
            file_type=FileType.STL,
            path=str(path),
            external=True,
            sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
        )
        fake = InferenceFake(chat_result={"color": "red"})
        server = start_server(fake.app())
        try:
            response = await api.post(
                "/api/v1/config/ai-search/endpoints",
                headers=superuser_headers,
                json={
                    "base_url": server.base_url + "/v1",
                    "model": fake.model,
                    "kind": "chat",
                    "supports_images": True,
                },
            )
            assert response.status_code == 201, response.text
            endpoint = response.json()
            response = await api.put(
                "/api/v1/config/ai-search",
                headers=superuser_headers,
                json={
                    "enabled": True,
                    "captions_enabled": True,
                    "send_rendered_images": True,
                    "chat_endpoint_id": endpoint["id"],
                    "nl_filters_enabled": False,
                },
            )
            assert response.status_code == 200
            fake.chat_result = {"caption": "A zygomatic mounting bracket"}
            assert await asyncio.to_thread(
                CaptionProcessor(get_session_factory()).work_one
            )
            caption_url = f"/api/v1/subjects/model/{model.id}/caption"
            response = await api.get(caption_url, headers=superuser_headers)
            assert response.status_code == 200
            caption = response.json()
            assert caption["state"] == "generated"
            assert caption["phase"] == "ready", caption["error_code"]
            assert caption["model"] == fake.model
            response = await api.get(
                f"/api/v1/models/{model.id}", headers=superuser_headers
            )
            assert response.json()["description"] == "Human description"
            response = await api.get(
                "/api/v1/search",
                headers=superuser_headers,
                params={"q": "zygomatic", "mode": "lexical"},
            )
            assert [item["subject_id"] for item in response.json()["items"]] == [
                model.id
            ]
            body = fake.calls[-1]["body"]
            assert "Human description" not in str(body)
            image_url = next(
                part["image_url"]["url"]
                for part in body["messages"][-1]["content"]
                if part["type"] == "image_url"
            )
            image_data = base64.b64decode(image_url.split(",", 1)[1])
            assert Image.open(BytesIO(image_data)).size == (384, 384)
            assert len(image_data) <= 512 * 1024
            assert image_data != payload
            response = await api.patch(
                caption_url,
                headers=superuser_headers,
                json={"action": "dismiss", "version_token": caption["version_token"]},
            )
            assert response.status_code == 200
            response = await api.get(
                "/api/v1/search",
                headers=superuser_headers,
                params={"q": "zygomatic", "mode": "lexical"},
            )
            assert response.json()["items"] == []
            assert not await asyncio.to_thread(
                CaptionProcessor(get_session_factory()).work_one
            )
            assert len(fake.calls) == 2  # Explicit capability probe and one caption.
        finally:
            close_client()
            server.stop()
