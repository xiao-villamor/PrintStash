"""File download + thumbnail + on-the-fly STL conversion."""

from __future__ import annotations

import secrets
import tempfile
from pathlib import Path
from urllib.parse import quote

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
)
from fastapi.responses import (
    PlainTextResponse,
)
from sqlmodel import Session

from app.api.artifact_responses import delivery_request, render_delivery
from app.core.config import settings
from app.core.http import get_or_404
from app.core.logging import get_logger
from app.core.security import get_current_user, require_auth, require_user
from app.db.models import CollectionRole, DerivativeKind, File, FileType, Model, User
from app.db.session import get_session, get_session_factory
from app.modules.identity import auth, rbac
from app.modules.media.three_mf_preview import (
    EmbeddedGcodeError,
    read_embedded_gcode_path,
)
from app.modules.storage.artifact_content import (
    ArtifactContentError,
    ArtifactContentMissingError,
    resolve,
)
from app.modules.storage.artifact_delivery import (
    DeliveryPurpose,
    bytes_response_headers,
    plan_artifact,
    plan_stored_representation,
)
from app.modules.storage.delivery_contracts import content_disposition
from app.modules.storage.storage_backend.contracts import StorageCollisionError
from app.modules.storage.storage_backend.runtime import get_backend
from app.modules.storage.storage_ownership import publish_bytes
from app.schemas.jobs import DerivativeRead

logger = get_logger(__name__)

router = APIRouter(prefix="/files", tags=["files"])


def _live_file(session: Session, file_id: int) -> File:
    """Load a live file (its model and the file itself not deleted).

    No access control — callers must authorise via a user (``_accessible_file``)
    or a bearer capability such as a slicer download token.
    """
    f = get_or_404(session, File, file_id, "file_not_found")
    model = session.get(Model, f.model_id)
    if model is None or model.deleted_at is not None or f.deleted_at is not None:
        raise HTTPException(status_code=404, detail="file_not_found")
    return f


def _accessible_file(session: Session, file_id: int, user: User) -> File:
    f = _live_file(session, file_id)
    model = session.get(Model, f.model_id)
    if model is None:
        raise HTTPException(status_code=404, detail="file_not_found")
    rbac.require_model_collection_role(
        session,
        user,
        model.collection_id,
        CollectionRole.VIEW,
    )
    return f


def serve_artifact(
    artifact: File,
    request: Request | None,
    filename: str,
    media_type: str = "application/octet-stream",
    purpose: DeliveryPurpose = DeliveryPurpose.DOWNLOAD,
):
    """Render an original after the caller has authorized its exact Artifact."""
    try:
        return render_delivery(
            plan_artifact(
                artifact, delivery_request(request, filename, media_type, purpose)
            )
        )
    except (ArtifactContentMissingError, FileNotFoundError) as exc:
        raise HTTPException(status_code=410, detail="file_blob_missing") from exc


def _serve_download(
    session: Session,
    file_id: int,
    slicer_token: str | None,
    current_user: User | None,
    request: Request,
):
    # A logged-in user goes through the normal RBAC check. Otherwise the request
    # must carry a valid slicer download token — a short-lived bearer capability
    # for this one file, used by "Open in slicer" so an external slicer process
    # (which has no login session) can fetch the file. See `slicer_download_url`.
    if current_user is not None:
        f = _accessible_file(session, file_id, current_user)
    elif slicer_token and auth.verify_file_download_token(slicer_token, file_id):
        f = _live_file(session, file_id)
    else:
        raise HTTPException(status_code=401, detail="not_authenticated")
    purpose = (
        DeliveryPurpose.DOWNLOAD if current_user is not None else DeliveryPurpose.SLICER
    )
    return serve_artifact(f, request, f.original_filename, purpose=purpose)


@router.get(
    "/{file_id}/download",
    summary="Download the raw file blob",
    description="Streams the underlying artifact (G-code/STL/3MF/OBJ) from storage.",
)
def download_file(
    file_id: int,
    request: Request,
    slicer_token: str | None = None,
    current_user: User | None = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    return _serve_download(session, file_id, slicer_token, current_user, request)


@router.get(
    "/{file_id}/embedded-gcode",
    response_class=PlainTextResponse,
    responses={
        200: {
            "description": "Embedded G-code text",
            "content": {"text/plain": {"schema": {"type": "string"}}},
        },
        404: {"description": "Artifact is inaccessible or preview is unavailable"},
        410: {"description": "Artifact blob is missing from storage"},
        413: {"description": "Archive or embedded toolpath exceeds configured limits"},
        429: {"description": "Preview concurrency capacity is exhausted"},
    },
    summary="Serve a Bambu 3MF's embedded G-code preview",
    description=(
        "Reads Metadata/plate_<N>.gcode on demand from an authorized 3MF. "
        "The selected member is bounded and is never extracted or persisted."
    ),
)
def embedded_gcode(
    file_id: int,
    request: Request,
    plate_index: int | None = Query(default=None, ge=0),
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    artifact = _accessible_file(session, file_id, current_user)
    if artifact.file_type != FileType.THREE_MF:
        raise HTTPException(status_code=404, detail="embedded_gcode_not_supported")
    archive_cap = settings.three_mf_preview_max_archive_mb * 1024 * 1024
    if artifact.size_bytes > archive_cap:
        raise HTTPException(status_code=413, detail="embedded_gcode_archive_too_large")
    try:
        with resolve(artifact).materialize() as path:
            embedded = read_embedded_gcode_path(path, plate_index=plate_index)
    except EmbeddedGcodeError as exc:
        status_code = (
            413
            if exc.code
            in {
                "embedded_gcode_too_large",
                "embedded_gcode_bomb",
                "embedded_gcode_archive_too_large",
                "embedded_gcode_too_many_entries",
                "embedded_gcode_central_directory_too_large",
            }
            else 429
            if exc.code == "embedded_gcode_busy"
            else 404
        )
        raise HTTPException(status_code=status_code, detail=exc.code) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=410, detail="file_blob_missing") from exc
    except ArtifactContentError as exc:
        raise HTTPException(status_code=410, detail="file_blob_missing") from exc
    payload = embedded.content
    status_code, headers = bytes_response_headers(
        payload,
        delivery_request(
            request, embedded.filename, "text/plain", DeliveryPurpose.TRANSFORMED
        ),
    )
    headers["Content-Disposition"] = content_disposition(embedded.filename, inline=True)
    return PlainTextResponse(
        content=embedded.content if status_code == 200 else "",
        status_code=status_code,
        headers=headers,
    )


@router.get(
    "/{file_id}/slicer/{slicer_token}/{filename}",
    summary="Login-free download for opening in a slicer (token in path)",
    description=(
        "Streams the file blob for 'Open in slicer'. The token lives in the path "
        "and the original filename is the LAST path segment, so the URL ends in "
        "the file's extension. Slicers (OrcaSlicer, Bambu Studio, …) take the URL "
        "tail as the download name — if the extension isn't last (e.g. a "
        "?token=… query trailing it) they save the blob but won't open it. The "
        "filename is cosmetic; access is governed solely by the token."
    ),
)
def slicer_download(
    file_id: int,
    request: Request,
    slicer_token: str,
    filename: str,
    session: Session = Depends(get_session),
):
    return _serve_download(
        session, file_id, slicer_token, current_user=None, request=request
    )


@router.get(
    "/{file_id}/slicer-url",
    summary="Signed, login-free download URL for opening in a slicer",
    description=(
        "Returns a short-lived download URL the slicer (OrcaSlicer, Bambu "
        "Studio, …) can fetch without the user's login session. The token is in "
        "the path and the original filename is last, so the URL ends in the "
        "file's extension and the slicer detects the format."
    ),
)
def slicer_download_url(
    file_id: int,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> dict:
    f = _accessible_file(session, file_id, current_user)
    token = auth.create_file_download_token(file_id)
    name = quote(f.original_filename, safe="")
    return {"url": f"/api/v1/files/{file_id}/slicer/{token}/{name}"}


@router.get(
    "/{file_id}/thumbnail",
    summary="Get the thumbnail extracted from the file (if any)",
)
def file_thumbnail(
    file_id: int,
    request: Request,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    file_row = _accessible_file(session, file_id, current_user)
    return thumbnail_response(file_id, request, thumbnail_path=file_row.thumbnail_path)


def thumbnail_response(
    file_id: int,
    request: Request | None = None,
    *,
    thumbnail_path: str | None = None,
    purpose: DeliveryPurpose = DeliveryPurpose.THUMBNAIL,
):
    """Serve a file's thumbnail. No access checks — authorise the caller first."""
    backend = get_backend()
    thumb_key = thumbnail_path or backend.thumbnail_key(file_id)
    filename, media_type = f"{file_id}.webp", "image/webp"
    info = backend.object_info(thumb_key)
    if info is None and thumbnail_path is not None:
        thumb_key = backend.thumbnail_key(file_id)
        info = backend.object_info(thumb_key)
    if info is None:
        # Thumbnails written before the WebP switch are still PNG on disk.
        thumb_key = backend.legacy_thumbnail_key(file_id)
        filename, media_type = f"{file_id}.png", "image/png"
        info = backend.object_info(thumb_key)
        if info is None:
            raise HTTPException(status_code=404, detail="thumbnail_not_found")
    return render_delivery(
        plan_stored_representation(
            backend,
            thumb_key,
            delivery_request(request, filename, media_type, purpose),
            info=info,
        )
    )


@router.get(
    "/{file_id}/stl",
    summary="Serve any mesh file as STL for 3D preview",
    description=(
        "If the file is already STL it is served directly. 3MF and OBJ files are "
        "converted to binary STL on the fly via trimesh."
    ),
)
def file_as_stl(
    file_id: int,
    request: Request,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    f = _accessible_file(session, file_id, current_user)
    return stl_response(f, request)


def stl_response(
    f: File, request: Request, purpose: DeliveryPurpose = DeliveryPurpose.TRANSFORMED
):
    """Serve a mesh File as binary STL (cached). No access checks — callers
    are responsible for authorising access to *f* first."""
    stem = Path(f.original_filename).stem
    delivery = delivery_request(request, f"{stem}.stl", "application/sla", purpose)
    if Path(f.original_filename).suffix.lower() == ".stl":
        return serve_artifact(
            f,
            request,
            f"{stem}.stl",
            "application/sla",
            DeliveryPurpose.BROWSER_FETCH
            if purpose == DeliveryPurpose.TRANSFORMED
            else purpose,
        )

    backend = get_backend()
    cache_key = backend.stl_cache_key(f.sha256)
    if backend.exists(cache_key):
        return render_delivery(plan_stored_representation(backend, cache_key, delivery))

    # Mesh parsing is unbounded work on an uploaded file, so it runs in a
    # disposable worker rather than in this process (#259).
    from app.modules.media import stl_isolation
    from app.modules.media.mesh_isolation import MeshWorkerError
    from app.modules.storage.capacity import CapacityManager, CapacityResource
    from app.modules.storage.capacity_estimates import vault_allocation

    estimate = max(f.size_bytes * 3, 16 * 1024**2)
    with CapacityManager(get_session_factory()).hold(
        f"media-conversion:{secrets.token_hex(12)}",
        [
            CapacityResource.for_path(
                Path(tempfile.gettempdir()), estimate, role="mesh conversion"
            ),
            vault_allocation(estimate, role="derived STL publication"),
        ],
    ):
        try:
            with resolve(f).materialize(capacity_claimed=True) as path:
                data = stl_isolation.to_stl_bytes(path, file_type=f.file_type.value)
        except ArtifactContentMissingError as exc:
            raise HTTPException(status_code=410, detail="file_blob_missing") from exc
        except MeshWorkerError as exc:
            logger.warning(
                "stl conversion failed in its worker",
                extra={"file_id": f.id, "reason": exc.reason.value},
            )
            raise HTTPException(
                status_code=500, detail="stl_conversion_failed"
            ) from exc
        if data is None:
            raise HTTPException(status_code=500, detail="stl_conversion_failed")

        cached = False
        try:
            with get_session_factory().scoped_session() as ownership_session:
                publish_bytes(
                    ownership_session,
                    backend,
                    cache_key,
                    data,
                    object_kind="derived_stl_cache",
                )
                ownership_session.commit()
                cached = True
        except StorageCollisionError:
            # Another request won the create-only race. Serve our in-memory result;
            # subsequent requests will use the already-published cache object.
            pass
        except Exception:
            logger.warning("stl cache write failed for file %s", f.id, exc_info=True)

        if cached:
            return render_delivery(
                plan_stored_representation(backend, cache_key, delivery)
            )
        status_code, headers = bytes_response_headers(data, delivery)
        return Response(
            content=data if status_code == 200 else b"",
            status_code=status_code,
            media_type="application/sla",
            headers=headers,
        )


@router.get(
    "/{file_id}/derivatives",
    response_model=list[DerivativeRead],
    summary="Derivative states of one Artifact at the current recipes",
    description=(
        "Every derivative kind that applies to the Artifact (metadata, thumbnail, "
        "toolpath) with its state. `pending` means no attempt exists yet at the "
        "current recipe, so every value the kind supplies is still unknown."
    ),
)
def list_file_derivatives(
    file_id: int,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> list[DerivativeRead]:
    from app.modules.derivatives import records

    file = _accessible_file(session, file_id, current_user)
    return records.read(session, file)


@router.post(
    "/{file_id}/derivatives/{kind}/retry",
    response_model=list[DerivativeRead],
    status_code=202,
    dependencies=[Depends(require_auth)],
    summary="Retry one failed or cancelled derivative of an Artifact",
)
def retry_file_derivative(
    file_id: int,
    kind: DerivativeKind,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> list[DerivativeRead]:
    from app.modules.derivatives import records
    from app.modules.derivatives.kinds import definitions_for_kind, recipes_for
    from app.modules.work import nudge

    file = _accessible_file(session, file_id, current_user)
    rbac.require_model_collection_role(
        session,
        current_user,
        _model_collection_id(session, file),
        CollectionRole.EDIT,
    )
    recipes = recipes_for(file)
    if kind not in recipes:
        raise HTTPException(status_code=404, detail="derivative_kind_not_found")
    if not records.reset(session, file, {kind: recipes[kind]}):
        raise HTTPException(status_code=409, detail="derivative_not_retryable")
    session.commit()
    for definition in definitions_for_kind(kind):
        nudge(definition)
    return records.read(session, file)


def _model_collection_id(session: Session, file: File) -> int | None:
    model = session.get(Model, file.model_id)
    return model.collection_id if model is not None else None


@router.get(
    "/{file_id}/toolpath",
    summary="Bounded ASCII toolpath of a G-code Artifact",
    description=(
        "ASCII G-code is served from the Artifact itself. Binary G-code (.bgcode) "
        "is served from its toolpath derivative; while that is still being "
        "derived the response is 202 with the derivative's state, and a failed "
        "conversion answers 422 with its reason."
    ),
)
def get_toolpath(
    file_id: int,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    from app.api.toolpath_response import toolpath_response

    file = _accessible_file(session, file_id, current_user)
    return toolpath_response(session, file)
