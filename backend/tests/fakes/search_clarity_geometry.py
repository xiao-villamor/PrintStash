"""Original CC0 goose and repository-owned analytic search objects.

Frozen before inference. Geometry supplies no labels to the visual encoder.
"""

import trimesh

from tests.fakes.visual_corpus import geometry as existing_geometry


def ellipsoid(scale, at):
    mesh = trimesh.creation.icosphere(subdivisions=3)
    mesh.apply_scale(scale)
    mesh.apply_translation(at)
    return mesh


def geometry(key):
    if key == "phone_stand":
        base = trimesh.creation.box([36, 30, 4])
        back = trimesh.creation.box([36, 4, 44])
        back.apply_transform(trimesh.transformations.rotation_matrix(0.3, [1, 0, 0]))
        back.apply_translation([0, 8, 22])
        lip = trimesh.creation.box([36, 4, 8])
        lip.apply_translation([0, -12, 4])
        return trimesh.util.concatenate([base, back, lip])
    if key != "goose":
        return existing_geometry(key)
    # Long upright neck, oval body, small head and pointed horizontal beak.
    beak = trimesh.creation.cone(radius=2.4, height=9, sections=32)
    beak.apply_transform(
        trimesh.transformations.rotation_matrix(1.5707963267948966, [0, 1, 0])
    )
    beak.apply_translation([14, 0, 40])
    return trimesh.util.concatenate(
        [
            ellipsoid([17, 9, 10], [-6, 0, 12]),
            ellipsoid([3.2, 3.2, 15], [7, 0, 27]),
            ellipsoid([5.5, 4, 5], [10, 0, 41]),
            beak,
            ellipsoid([7, 3, 1.5], [0, 5, 1.5]),
            ellipsoid([7, 3, 1.5], [0, -5, 1.5]),
        ]
    )
