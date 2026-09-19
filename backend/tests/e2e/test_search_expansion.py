"""Preplaced sparse inference to authorized HTTP retrieval without query inference."""

import pytest
from printstash_core.search.passages import SearchSubject, SubjectType

from app.core.config import _overlay
from app.db.session import get_session_factory
from app.modules.inference import model_cache, model_registry
from app.modules.inference.worker_pool import pool
from app.modules.search.expansion_worker import ExpansionProcessor
from app.modules.search.passages import sync_subject
from tests.factories import build_model
from tests.factories.embeddings import sparse_embedding_assets

pytestmark = pytest.mark.asyncio


class TestSparseSearch:
    async def test_retrieves_a_separately_expanded_synonym(
        self, api, e2e_db, superuser_headers, tmp_path, monkeypatch
    ):
        root = tmp_path / "models"
        directory = sparse_embedding_assets(root / "sparse")
        monkeypatch.setitem(_overlay, "embedding_cache_dir", root)
        monkeypatch.setitem(_overlay, "embedding_local_model_dir", "")
        model = model_cache.inspect(directory)
        monkeypatch.setattr(
            model_registry,
            "entries",
            lambda: (model_registry.RegistryEntry(model.manifest, ()),),
        )
        subject = build_model(e2e_db, "bicycle")
        sync_subject(e2e_db, SearchSubject(SubjectType.MODEL, subject.id))
        e2e_db.commit()
        response = await api.put(
            "/api/v1/config/ai-search",
            headers=superuser_headers,
            json={
                "enabled": True,
                "local_models_enabled": True,
                "sparse_expansion_enabled": True,
                "sparse_model_id": model.id,
            },
        )
        assert response.status_code == 200, response.text
        response = await api.post(
            f"/api/v1/inference/models/{model.id}/validate", headers=superuser_headers
        )
        assert response.status_code == 200, response.text
        assert ExpansionProcessor(get_session_factory()).work_one()
        # A cold, missing model cannot be consulted by the following HTTP search.
        pool.close()
        monkeypatch.setitem(_overlay, "embedding_cache_dir", tmp_path / "offline")
        response = await api.get(
            "/api/v1/search",
            headers=superuser_headers,
            params={"q": "bike", "mode": "lexical"},
        )
        assert response.status_code == 200, response.text
        items = response.json()["items"]
        assert [item["subject_id"] for item in items] == [subject.id]
        assert items[0]["name"] == "bicycle"
        response = await api.patch(
            "/api/v1/search/settings",
            headers=superuser_headers,
            json={"sparse_expansion_enabled": False},
        )
        assert response.status_code == 200, response.text
        response = await api.get(
            "/api/v1/search",
            headers=superuser_headers,
            params={"q": "bike", "mode": "lexical"},
        )
        assert response.json()["items"] == []
        response = await api.get(
            "/api/v1/search",
            headers=superuser_headers,
            params={"q": "bicycle", "mode": "lexical"},
        )
        assert [item["subject_id"] for item in response.json()["items"]] == [subject.id]
