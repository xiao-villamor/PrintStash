"""Original CC0 analytic objects plus the repository's six attributed real meshes.

These shapes are an engineering regression corpus, not a human-labelled user
study. Queries are frozen separately before evaluating the first render recipe.
"""

from pathlib import Path

import numpy as np
import trimesh


def box(size, at=(0, 0, 0)):
    mesh = trimesh.creation.box(size)
    mesh.apply_translation(at)
    return mesh


def cylinder(radius, height, at=(0, 0, 0), sections=48):
    mesh = trimesh.creation.cylinder(radius=radius, height=height, sections=sections)
    mesh.apply_translation(at)
    return mesh


def revolve(points):
    return trimesh.creation.revolve(np.asarray(points), sections=48)


def ring(radius=10, tube=2, at=(0, 0, 0), upright=False):
    mesh = trimesh.creation.torus(major_radius=radius, minor_radius=tube)
    if upright:
        mesh.apply_transform(
            trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0])
        )
    mesh.apply_translation(at)
    return mesh


def joined(*parts):
    return trimesh.util.concatenate(parts)


def geometry(key: str):
    if key == "box":
        return box([30, 30, 30])
    if key == "ball":
        return trimesh.creation.icosphere(subdivisions=3, radius=20)
    if key == "cone":
        return trimesh.creation.cone(radius=15, height=35, sections=48)
    if key == "cylinder":
        return cylinder(15, 35)
    if key == "washer":
        return revolve([(8, 0), (18, 0), (18, 3), (8, 3), (8, 0)])
    if key == "gear":
        teeth = []
        for angle in np.linspace(0, 2 * np.pi, 16, endpoint=False):
            tooth = box([8, 3, 5], [17, 0, 0])
            tooth.apply_transform(
                trimesh.transformations.rotation_matrix(angle, [0, 0, 1])
            )
            teeth.append(tooth)
        return joined(cylinder(17, 5), *teeth)
    if key == "bolt":
        return joined(
            cylinder(6, 35, [0, 0, 17.5]),
            cylinder(12, 8, [0, 0, 39], sections=6),
            *[ring(6, 0.8, [0, 0, z]) for z in range(2, 32, 3)],
        )
    if key == "spool":
        return joined(
            cylinder(8, 25), cylinder(22, 3, [0, 0, -14]), cylinder(22, 3, [0, 0, 14])
        )
    if key == "pipe":
        return revolve([(8, 0), (11, 0), (11, 40), (8, 40), (8, 0)])
    if key == "funnel":
        return revolve([(3, 0), (5, 0), (5, 15), (22, 35), (20, 35), (3, 15), (3, 0)])
    if key == "vase":
        return revolve(
            [
                (0, 0),
                (10, 0),
                (18, 12),
                (13, 25),
                (7, 38),
                (11, 45),
                (9, 45),
                (5, 38),
                (11, 25),
                (16, 12),
                (8, 3),
                (0, 3),
                (0, 0),
            ]
        )
    if key == "bowl":
        return revolve(
            [
                (0, 0),
                (8, 0),
                (15, 5),
                (20, 14),
                (21, 20),
                (19, 20),
                (18, 14),
                (13, 6),
                (7, 2),
                (0, 2),
                (0, 0),
            ]
        )
    if key == "mug":
        return joined(
            revolve([(0, 0), (15, 0), (15, 32), (13, 32), (13, 3), (0, 3), (0, 0)]),
            ring(10, 2.5, [16, 0, 18], upright=True),
        )
    if key == "wineglass":
        return joined(
            cylinder(14, 2),
            cylinder(2, 25, [0, 0, 13]),
            revolve(
                [
                    (0, 25),
                    (7, 25),
                    (13, 35),
                    (14, 46),
                    (12, 46),
                    (11, 35),
                    (5, 28),
                    (0, 28),
                    (0, 25),
                ]
            ),
        )
    if key == "pawn":
        sphere = trimesh.creation.icosphere(subdivisions=2, radius=7)
        sphere.apply_translation([0, 0, 32])
        return joined(
            revolve(
                [(0, 0), (14, 0), (14, 4), (10, 7), (5, 18), (6, 26), (0, 26), (0, 0)]
            ),
            sphere,
        )
    if key == "rocket":
        tip = trimesh.creation.cone(radius=8, height=18, sections=48)
        tip.apply_translation([0, 0, 32])
        return joined(
            cylinder(8, 32, [0, 0, 16]),
            tip,
            box([28, 3, 15], [0, 0, 7.5]),
            box([3, 28, 15], [0, 0, 7.5]),
        )
    if key == "table":
        return joined(
            box([50, 35, 4], [0, 0, 32]),
            *[box([4, 4, 30], [x, y, 15]) for x in (-20, 20) for y in (-12, 12)],
        )
    if key == "chair":
        return joined(
            box([24, 24, 3], [0, 0, 22]),
            box([24, 3, 25], [0, 10, 35]),
            *[box([3, 3, 22], [x, y, 11]) for x in (-9, 9) for y in (-9, 9)],
        )
    if key == "ladder":
        return joined(
            box([3, 3, 65], [-12, 0, 32.5]),
            box([3, 3, 65], [12, 0, 32.5]),
            *[box([24, 3, 3], [0, 0, z]) for z in range(8, 64, 8)],
        )
    if key == "bracket":
        return joined(box([30, 25, 3], [0, 0, 0]), box([3, 25, 30], [-13.5, 0, 15]))
    if key == "tray":
        return joined(
            box([45, 30, 3]),
            box([45, 3, 8], [0, 13.5, 4]),
            box([45, 3, 8], [0, -13.5, 4]),
            box([3, 30, 8], [21, 0, 4]),
            box([3, 30, 8], [-21, 0, 4]),
        )
    if key == "ring":
        return ring(18, 3)
    if key == "capsule":
        return trimesh.creation.capsule(height=28, radius=9)
    if key == "tripod":
        legs = []
        for angle in (0, 2 * np.pi / 3, 4 * np.pi / 3):
            leg = box([4, 4, 40])
            leg.apply_transform(
                trimesh.transformations.rotation_matrix(0.45, [0, 1, 0])
            )
            leg.apply_translation([9, 0, 18])
            leg.apply_transform(
                trimesh.transformations.rotation_matrix(angle, [0, 0, 1])
            )
            legs.append(leg)
        return joined(cylinder(10, 3, [0, 0, 36]), *legs)
    if key == "tower":
        return joined(
            cylinder(12, 40, [0, 0, 20]),
            *[
                box([6, 6, 8], [x, y, 43])
                for x, y in ((-9, 0), (9, 0), (0, -9), (0, 9))
            ],
        )
    if key == "tree":
        cones = [cylinder(3, 10)]
        for radius, height, z in ((20, 25, 0), (15, 22, 14), (10, 18, 28)):
            cone = trimesh.creation.cone(radius=radius, height=height, sections=48)
            cone.apply_translation([0, 0, z])
            cones.append(cone)
        return joined(*cones)
    if key.startswith("thingi10k-"):
        path = (
            Path(__file__).resolve().parents[2]
            / "packages/printstash-core/tests/fixtures/similarity"
            / (key + ".stl")
        )
        return trimesh.load_mesh(path, process=False)
    raise ValueError("unknown_visual_fixture")
