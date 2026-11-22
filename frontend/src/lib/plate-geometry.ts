/**
 * Keep one plate of a multi-plate 3MF out of its whole-build viewer mesh.
 *
 * A slicer places every object of plate N inside that plate's rectangle of the
 * build, and the viewer mesh keeps the build's coordinates, so a triangle
 * belongs to the plate its centroid lies over. Positions and normals are
 * non-indexed: nine floats per triangle.
 */
import * as THREE from "three";
import type { PlateRegion } from "@/types";

export interface TriangleArrays {
  positions: Float32Array<ArrayBuffer>;
  normals: Float32Array<ArrayBuffer>;
}

const FLOATS_PER_TRIANGLE = 9;

export function trianglesOnPlate(mesh: TriangleArrays, region: PlateRegion): TriangleArrays {
  const { positions, normals } = mesh;
  if (positions.length % FLOATS_PER_TRIANGLE !== 0 || normals.length !== positions.length)
    throw new Error("mesh_arrays_misaligned");
  const kept: number[] = [];
  for (let start = 0; start < positions.length; start += FLOATS_PER_TRIANGLE) {
    const x = (positions[start] + positions[start + 3] + positions[start + 6]) / 3;
    const y = (positions[start + 1] + positions[start + 4] + positions[start + 7]) / 3;
    if (x >= region.min_x && x <= region.max_x && y >= region.min_y && y <= region.max_y)
      kept.push(start);
  }
  const keptPositions = new Float32Array(kept.length * FLOATS_PER_TRIANGLE);
  const keptNormals = new Float32Array(kept.length * FLOATS_PER_TRIANGLE);
  kept.forEach((start, index) => {
    const end = start + FLOATS_PER_TRIANGLE;
    keptPositions.set(positions.subarray(start, end), index * FLOATS_PER_TRIANGLE);
    keptNormals.set(normals.subarray(start, end), index * FLOATS_PER_TRIANGLE);
  });
  return { positions: keptPositions, normals: keptNormals };
}

export function geometryDimensions(geometry: THREE.BufferGeometry | null) {
  if (!geometry || geometry.getAttribute("position").count === 0) return null;
  geometry.computeBoundingBox();
  if (!geometry.boundingBox) return null;
  const size = geometry.boundingBox.getSize(new THREE.Vector3());
  return { x: size.x, y: size.y, z: size.z };
}
