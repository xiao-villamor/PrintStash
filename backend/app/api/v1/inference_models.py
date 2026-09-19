"""Explicit model acquisition and verification; listing has no network effects."""

import importlib.util

from fastapi import APIRouter, Depends, Response
from printstash_core.inference import EmbeddingError
from printstash_core.inference.model_capabilities import capabilities_for
from sqlmodel import Session

from app.core.config import settings
from app.core.errors import ErrorKind, OperationError
from app.core.security import get_current_user, require_superuser
from app.db.models import User
from app.db.session import get_session, get_session_factory
from app.modules.inference import model_cache, model_registry
from app.modules.inference.local import LocalEmbeddingProvider
from app.modules.inference.manifest import (
    PointModelManifest,
    SparseModelManifest,
    TextModelManifest,
)
from app.modules.inference.sparse import LocalSparseProvider
from app.runtime import model_acquisition
from app.schemas.inference_models import (
    DownloadRead,
    InferenceModelRead,
    ModelValidationRead,
)

router = APIRouter(
    prefix="/inference/models",
    tags=["inference"],
    dependencies=[Depends(require_superuser)],
)


@router.get("", response_model=list[InferenceModelRead])
def list_models(session: Session = Depends(get_session)):
    installed = {model.id: model for model in model_cache.inventory()}
    curated = {entry.id: entry for entry in model_registry.entries()}
    result = []
    runtime = all(
        importlib.util.find_spec(name) is not None
        for name in ("onnxruntime", "onnx", "tokenizers")
    )
    for identity in sorted(set(installed) | set(curated)):
        model, entry = installed.get(identity), curated.get(identity)
        manifest = model.manifest if model else entry.manifest
        capabilities = (
            None
            if isinstance(manifest, SparseModelManifest)
            else capabilities_for(manifest.space())
        )
        result.append(
            InferenceModelRead(
                id=identity,
                key=manifest.model_key,
                revision=entry.revision if entry else manifest.model_revision,
                repository=entry.repository
                if entry
                else manifest.repository
                if isinstance(
                    manifest,
                    (TextModelManifest, PointModelManifest, SparseModelManifest),
                )
                else None,
                languages=list(entry.languages)
                if entry
                else list(manifest.language)
                if isinstance(manifest, (TextModelManifest, SparseModelManifest))
                else [],
                license=entry.license
                if entry
                else manifest.license
                if isinstance(
                    manifest,
                    (TextModelManifest, PointModelManifest, SparseModelManifest),
                )
                else None,
                modality="sparse"
                if isinstance(manifest, SparseModelManifest)
                else manifest.space().modality,
                native_dimension=manifest.native_dimension,
                size_bytes=model.size if model else entry.size,
                installed=model is not None,
                curated=entry is not None,
                referenced=model_cache.referenced(session, model) if model else False,
                runtime_available=runtime,
                mrl_dimensions=list(capabilities.mrl_dimensions)
                if capabilities
                else [],
            )
        )
    return result


@router.post("/{key}/download", response_model=DownloadRead, status_code=202)
def download_model(
    key: str,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    try:
        return DownloadRead(job_id=model_acquisition.start(session, user, key))
    except EmbeddingError as exc:
        raise OperationError(exc.code, kind=ErrorKind.INVALID) from None


@router.post("/downloads/{job_id}/cancel", status_code=204)
def cancel_download(job_id: str):
    model_acquisition.cancel(job_id)
    return Response(status_code=204)


@router.post("/{identity}/validate", response_model=ModelValidationRead)
def validate_model(identity: str):
    try:
        model = model_cache.resolve(identity)
        (
            LocalSparseProvider
            if isinstance(model.manifest, SparseModelManifest)
            else LocalEmbeddingProvider
        )(
            get_session_factory(),
            model.directory,
            model.manifest.model_key,
            settings.embedding_onnx_threads,
        ).validate()
        return ModelValidationRead(id=identity, ready=True)
    except EmbeddingError as exc:
        raise OperationError(exc.code, kind=ErrorKind.INVALID) from None


@router.delete("/{identity}", status_code=204)
def delete_model(identity: str, session: Session = Depends(get_session)):
    try:
        model_cache.remove(session, identity)
    except EmbeddingError as exc:
        raise OperationError(exc.code, kind=ErrorKind.CONFLICT) from None
    return Response(status_code=204)
