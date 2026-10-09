"""Bound preview batches without inventing benchmark qualification receipts."""

from dataclasses import dataclass

from printstash_core.mesh.preview_profile import PREVIEW_PROFILE, RASTERIZER_RECIPE

from app.modules.media.compute_geometry import decode
from app.modules.media.compute_render import RECIPE

from .contracts import ComputeUnavailable, Reason


@dataclass(frozen=True)
class RenderAdmission:
    projected_bytes: int
    attachment_bytes: int
    readback_bytes: int
    host_bytes: int

    @property
    def device_bytes(self) -> int:
        return self.projected_bytes + self.attachment_bytes + self.readback_bytes + 116


def admission(payload: bytes, recipe: str, units: int) -> RenderAdmission:
    return admission_many([payload], [recipe], [units])


def admission_many(payloads, recipes, units) -> RenderAdmission:
    decoded = [decode(payload) for payload in payloads]
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
    # Projected triangles/textures are reused; camera readbacks occupy distinct
    # slots until one completion fence. Input geometry is separately charged.
    projected_bytes = max(p.face_count for p, *_ in decoded) * 96
    # Staging/validation copies, canonical postprocess arrays, one normal-prep
    # chunk, joined outputs and a bounded 32 MiB shaded-image cache.
    host_bytes = (
        3 * sum(map(len, payloads))
        + w * h * 96
        + readback
        + width * height * 8 * frames
        + 96 * 1024**2
    )
    return RenderAdmission(projected_bytes, w * h * 8, readback, host_bytes)
