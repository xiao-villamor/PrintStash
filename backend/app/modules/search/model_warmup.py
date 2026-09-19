"""Warm active local exports without extending interactive query deadlines."""

import json
import threading
from collections import OrderedDict

from printstash_core.inference import EmbeddingError, EmbeddingSpace, InferenceContext
from sqlmodel import select

from app.core.config import settings
from app.db.models import EmbeddingSpace as StoredSpace
from app.db.models import IndexGeneration
from app.modules.inference import model_cache
from app.modules.inference.local import LocalEmbeddingProvider
from app.modules.inference.warmup import requests
from app.modules.search.configuration import settings as search_settings


class ModelWarmup:
    def __init__(self, sessions, *, provider_factory=LocalEmbeddingProvider):
        self.sessions, self.provider_factory = sessions, provider_factory
        self.stopped = threading.Event()
        self._attempted: OrderedDict[str, None] = OrderedDict()

    def enabled(self) -> bool:
        if self.stopped.is_set():
            return False
        with self.sessions.scoped_session() as session:
            config = search_settings(session)
            return config.enabled and config.local_models_enabled

    def stop(self):
        self.stopped.set()
        requests.clear()

    def work_one(self) -> bool:
        if not self.enabled():
            requests.clear()
            return False
        with self.sessions.scoped_session() as session:
            rows = session.exec(
                select(StoredSpace)
                .join(IndexGeneration, IndexGeneration.space_id == StoredSpace.id)
                .where(
                    IndexGeneration.state == "active",
                    StoredSpace.provider == "onnx_cpu",
                    StoredSpace.profile.in_(
                        ("semantic_text", "thumbnail", "multiview", "point_cloud")
                    ),
                )
                .order_by((StoredSpace.profile != "semantic_text"), IndexGeneration.id)
                .limit(4)
            ).all()
            models = {}
            for row in rows:
                try:
                    model = model_cache.for_space(
                        EmbeddingSpace(**json.loads(row.config_json))
                    )
                except (EmbeddingError, ValueError, TypeError):
                    continue
                models.setdefault(model.id, model)
        requested = requests.take()
        # The resident pool holds two exports. Preload the default text model
        # first, then one other; additional profiles warm on actual demand.
        identity = (
            requested
            if requested in models
            else next(
                (
                    identity
                    for identity in list(models)[:2]
                    if identity not in self._attempted
                ),
                None,
            )
        )
        if identity is None:
            return False
        self._attempted[identity] = None
        self._attempted.move_to_end(identity)
        while len(self._attempted) > 64:
            self._attempted.popitem(last=False)
        model = models[identity]
        try:
            provider = self.provider_factory(
                self.sessions,
                model.directory,
                model.manifest.model_key,
                settings.embedding_onnx_threads,
            )
            if not provider.is_warm:
                provider.validate(
                    context=InferenceContext.bounded(
                        120, priority="background", cancelled=lambda: not self.enabled()
                    )
                )
        except EmbeddingError:
            # Queries remain lexical; a later demand can retry the local cache.
            pass
        return True
