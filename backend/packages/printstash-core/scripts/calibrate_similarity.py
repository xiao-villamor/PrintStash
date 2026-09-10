#!/usr/bin/env python3
"""Reproduce the bounded SH projection asset; never run during ingestion.

Run from printstash-core with `uv run python scripts/calibrate_similarity.py`.
Only --write-basis replaces assets. Synthetic design IDs, not transformed
variants, separate the calibration and held-out projection sets. Classification
quality uses the independent licensed mesh corpus and is a separate report.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import zipfile
from pathlib import Path

import numpy as np

from printstash_core.mesh.similarity.descriptors import SH_RECIPE, sh_spectrum
from printstash_core.mesh.similarity.geometry import prepare_surface

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "src/printstash_core/mesh/similarity"


def radial_design(seed: int) -> tuple[np.ndarray, np.ndarray]:
    """Independent smooth radial designs with closed, consistently wound shells."""
    rng = np.random.Generator(np.random.PCG64(seed))
    latitude, longitude = 16, 32
    theta = np.arange(1, latitude)[:, None] * np.pi / latitude
    phi = np.arange(longitude)[None, :] * 2 * np.pi / longitude
    radius = np.ones((latitude - 1, longitude))
    for degree in range(1, 9):
        for order in range(1, 5):
            radius += (
                rng.uniform(-0.12, 0.12)
                * np.sin(degree * theta)
                * np.cos(order * phi + rng.uniform(0, 2 * np.pi))
            )
    radius = np.maximum(radius, 0.25)
    x, y, z = (
        radius * np.sin(theta) * np.cos(phi),
        radius * np.sin(theta) * np.sin(phi),
        radius * np.cos(theta),
    )
    vertices = np.vstack(
        ([0, 0, 1], np.column_stack((x.ravel(), y.ravel(), z.ravel())), [0, 0, -1])
    ) * rng.uniform(0.5, 1.5, 3)
    faces: list[tuple[int, int, int]] = []
    for column in range(longitude):
        following = (column + 1) % longitude
        faces.append((0, 1 + column, 1 + following))
        for row in range(latitude - 2):
            a, b = 1 + row * longitude + column, 1 + row * longitude + following
            faces.extend(((a, a + longitude, b), (b, a + longitude, b + longitude)))
        last = 1 + (latitude - 2) * longitude
        faces.append((len(vertices) - 1, last + following, last + column))
    return vertices, np.array(faces, dtype=np.int64)


def deterministic_npz(**arrays: np.ndarray) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, array in arrays.items():
            data = io.BytesIO()
            np.lib.format.write_array(data, array, allow_pickle=False)
            info = zipfile.ZipInfo(f"{name}.npy", date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data.getvalue())
    return output.getvalue()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-basis", action="store_true")
    args = parser.parse_args()
    spectra = []
    for seed in range(160):
        spectra.append(sh_spectrum(prepare_surface(*radial_design(seed)), fill=True))
    dataset = np.array(spectra)
    mean = dataset[:128].mean(axis=0)
    _, singular, vectors = np.linalg.svd(dataset[:128] - mean, full_matrices=False)
    components = vectors[:64]
    # Eigenvector signs are unspecified by SVD; freeze a deterministic convention.
    rows = np.arange(64)
    components *= np.sign(components[rows, np.abs(components).argmax(axis=1)])[:, None]
    held_out = dataset[128:] - mean
    reconstructed = (held_out @ components.T) @ components
    relative_error = np.linalg.norm(held_out - reconstructed, axis=1) / np.linalg.norm(
        held_out, axis=1
    )
    blob = deterministic_npz(
        mean=mean.astype("<f8"), components=components.astype("<f8")
    )
    manifest = {
        "recipe": SH_RECIPE,
        "sha256": hashlib.sha256(blob).hexdigest(),
        "generator": "calibrate_similarity.radial_design-v1",
        "numpy_version": np.__version__,
        "calibration_design_ids": list(range(128)),
        "evaluation_design_ids": list(range(128, 160)),
        "calibration_explained_variance": float(
            (singular[:64] ** 2).sum() / (singular**2).sum()
        ),
        "evaluation_relative_reconstruction_mean": float(relative_error.mean()),
        "evaluation_relative_reconstruction_max": float(relative_error.max()),
        "limitation": "Projection validation only; not a classification precision or recall result.",
    }
    if args.write_basis:
        (ASSETS / "sh_basis.npz").write_bytes(blob)
        (ASSETS / "sh_basis.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
