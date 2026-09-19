"""A pinned tiny model host and isolated local cache for HTTP acquisition tests."""

from unittest.mock import patch

import httpx
import pytest

from app.core.config import _overlay
from app.modules.inference import model_registry
from app.runtime.model_acquisition import close
from tests.factories.embeddings import sparse_embedding_assets, text_embedding_assets
from tests.fakes.model_host import ModelHost
from tests.fakes.server import start_server
from tests.fakes.tls_storage import tls_storage


@pytest.fixture(name="model_host")
def model_host(tmp_path, monkeypatch, request):
    builder = (
        sparse_embedding_assets
        if getattr(request, "param", "text") == "sparse"
        else text_embedding_assets
    )
    fake = ModelHost.from_directory(builder(tmp_path / "source"))
    server = start_server(fake.app())
    cache = tmp_path / "cache"
    monkeypatch.setitem(_overlay, "embedding_cache_dir", cache)
    monkeypatch.setitem(_overlay, "embedding_local_model_dir", "")
    monkeypatch.setattr(model_registry, "entries", lambda: (fake.entry,))
    original_client = httpx.Client
    try:
        with tls_storage(server.base_url, tmp_path, omit_length=True) as tls:
            monkeypatch.setitem(_overlay, "embedding_mirror_url", tls.endpoint)
            with patch(
                "app.modules.inference.model_acquisition.httpx.Client",
                side_effect=lambda **kwargs: original_client(
                    verify=tls.client_context(), **kwargs
                ),
            ):
                try:
                    yield fake, cache
                finally:
                    close()
    finally:
        server.stop()
