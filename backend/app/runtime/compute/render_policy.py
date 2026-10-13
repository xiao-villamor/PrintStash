"""Bound preview batches without inventing benchmark qualification receipts."""

from dataclasses import dataclass

from printstash_core.mesh.preview_profile import PREVIEW_PROFILE, RASTERIZER_RECIPE

from app.modules.media.compute_geometry import decode, layout_for
from app.modules.media.compute_render import RECIPE
from app.modules.media.gpu_render_layout import (
    FRAME_STRIDE,
    VIEW_WORKSPACE_BYTES,
    upload_bytes,
)

from .contracts import ComputeUnavailable, Reason


@dataclass(frozen=True)
class RenderAdmission:
    projected_bytes: int
    attachment_bytes: int
    readback_bytes: int
    host_bytes: int

    @property
    def device_bytes(self) -> int:
        return (
            self.projected_bytes
            + self.attachment_bytes
            + self.readback_bytes
            + VIEW_WORKSPACE_BYTES
        )


def admission(payload: bytes, recipe: str, units: int) -> RenderAdmission:
    return admission_many([payload], [recipe], [units])


def admission_many(payloads, recipes, units) -> RenderAdmission:
    decoded = [decode(payload) for payload in payloads]
    return admission_decoded(decoded, list(map(len, payloads)), recipes, units)


def admission_decoded(
    decoded, sizes, recipes, units, *, gpu_finalize=False
) -> RenderAdmission:
    dimensions = {(width, height) for _, width, height, _, _ in decoded}
    if len(dimensions) != 1:
        raise ValueError("compute_batch_dimensions")
    for (prepared, width, height, views, matte), recipe, count in zip(
        decoded, recipes, units, strict=True
    ):
        expected = (
            f"{RECIPE}:{RASTERIZER_RECIPE}:{width}x{height}:{len(views)}:{int(matte)}"
        )
        if recipe != expected or count != prepared.face_count * len(views):
            raise ValueError("compute_render_identity")
    width, height = next(iter(dimensions))
    frames = sum(len(views) for _, _, _, views, _ in decoded)
    if frames > 8:
        raise ComputeUnavailable(Reason.CAPACITY)
    factor = PREVIEW_PROFILE.supersample_for(width)
    w, h = width * factor, height * factor
    readback = ((w * 4 + 255) // 256) * 256 * h * frames
    # Native vertices read compact indexed buffers directly; camera readbacks
    # occupy distinct slots until one fence. Geometry is separately charged.
    projected_bytes = 0
    # Staging/validation copies, canonical postprocess arrays, one normal-prep
    # chunk, joined outputs and a bounded 32 MiB shaded-image cache.
    host_bytes = (
        3 * sum(sizes)
        + w * h * 96
        + readback
        + width * height * 8 * frames
        + 96 * 1024**2
    )
    extra = 0
    if gpu_finalize:
        from app.modules.media.gpu_postprocess import workspace_bytes

        extra = workspace_bytes(width, height, factor)
        # Binary input bodies are already charged to the pinned geometry cache
        # and upload queue. Raw fallback finalization runs in the caller, not
        # this owner. Retain normal-preparation scratch, shaded output cache,
        # readback and joined frame copies in the renderer reservation.
        host_bytes = readback + width * height * 8 * frames + 96 * 1024**2
    from app.modules.media.gpu_normals import (
        DeferredRender,
        VisualRender,
        host_scratch_bytes,
    )

    # CSR construction and any canonical numerical fallback happen sequentially
    # under this owner's reservation; upload buffers remain in the device ledger.
    host_bytes += max(host_scratch_bytes(prepared) for prepared, *_ in decoded)
    # Grouped uploads own one host arena plus converted index/normal arrays.
    # Include every model conservatively, even if its device geometry is cached.
    host_bytes += (
        2
        * sum(
            upload_bytes(*layout_for(p).counts, layout_for(p).normal_mode)
            for p, *_ in decoded
        )
        + frames * FRAME_STRIDE
    )
    status_bytes = 4 * sum(
        isinstance(prepared, (DeferredRender, VisualRender)) for prepared, *_ in decoded
    )
    readback += status_bytes
    host_bytes += status_bytes
    return RenderAdmission(projected_bytes, w * h * 8 + extra, readback, host_bytes)
