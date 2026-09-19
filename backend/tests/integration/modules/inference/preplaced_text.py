"""Explicit real BGE lane: provision pinned files first; this never downloads."""

import os
import time
from pathlib import Path

import numpy as np
from printstash_core.inference import EmbeddingInput
from printstash_core.inference.context import InferenceContext

from app.db.session import get_session_factory
from app.modules.inference.local import LocalEmbeddingProvider
from app.modules.inference.model_registry import require


class TestPreplacedText:
    def test_embeds_with_the_pinned_bge_model(self, db_session):
        directory = Path(os.environ["AI_SEARCH_TEXT_ASSETS"])
        entry = require("bge-small-en-v1.5")
        provider = LocalEmbeddingProvider(
            get_session_factory(), directory, entry.manifest.model_key, 1
        )
        assert provider.validate() == entry.manifest
        queries = (
            "a tiny boat for checking a printer",
            "a box for storing loose screws",
        )
        results = []
        for query in queries:
            started = time.monotonic()
            vector = provider.embed(
                (EmbeddingInput("text", text=provider.space.query_prefix + query),),
                provider.space,
                context=InferenceContext.bounded(3),
            )[0]
            results.append(vector)
            print(f"native_query_seconds={time.monotonic() - started:.4f}")
            assert len(vector) == 384
            np.testing.assert_allclose(np.linalg.norm(vector), 1, atol=1e-6)
        assert not np.allclose(results[0], results[1])
