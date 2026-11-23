/**
 * One plate of a multi-plate 3MF is the triangles over its rectangle.
 *
 * The viewer mesh keeps the slicer's build coordinates, where every plate is a
 * separate rectangle; a triangle whose centroid lies over a plate belongs to
 * it. Keeping a triangle of another plate shows parts that print elsewhere,
 * and dropping one of this plate's hides a part the user is about to print.
 */
import { describe, expect, it } from "vitest";
import * as THREE from "three";
import { geometryDimensions, trianglesOnPlate, type TriangleArrays } from "../plate-geometry";

const PLATE_ONE = { min_x: 0, min_y: 0, max_x: 256, max_y: 256 };

function triangleAt(x: number, y: number): number[] {
  return [x - 1, y - 1, 0, x + 1, y - 1, 0, x, y + 1, 5];
}

function mesh(...triangles: number[][]): TriangleArrays {
  const positions = new Float32Array(triangles.flat());
  const normals = new Float32Array(positions.length).fill(1);
  return { positions, normals };
}

describe("trianglesOnPlate", () => {
  it("keeps the triangles whose centroid lies over the plate", () => {
    const kept = trianglesOnPlate(mesh(triangleAt(100, 100), triangleAt(400, 100)), PLATE_ONE);

    expect(Array.from(kept.positions)).toEqual(triangleAt(100, 100));
  });

  it("keeps each kept triangle's normals beside its positions", () => {
    const source = mesh(triangleAt(400, 100), triangleAt(100, 100));
    source.normals.set([2, 2, 2, 2, 2, 2, 2, 2, 2], 9);

    const kept = trianglesOnPlate(source, PLATE_ONE);

    expect(Array.from(kept.normals)).toEqual([2, 2, 2, 2, 2, 2, 2, 2, 2]);
  });

  it("keeps a triangle overhanging the plate whose centroid is still over it", () => {
    const kept = trianglesOnPlate(mesh([240, 10, 0, 266, 10, 0, 240, 20, 0]), PLATE_ONE);

    expect(kept.positions).toHaveLength(9);
  });

  it("returns no triangles for a plate with nothing over it", () => {
    const kept = trianglesOnPlate(mesh(triangleAt(400, 100)), PLATE_ONE);

    expect(kept.positions).toHaveLength(0);
  });

  it("refuses positions that are not whole triangles", () => {
    const positions = new Float32Array(8);

    expect(() => trianglesOnPlate({ positions, normals: new Float32Array(8) }, PLATE_ONE)).toThrow(
      "mesh_arrays_misaligned",
    );
  });
});

describe("geometryDimensions", () => {
  it("measures the displayed source geometry in its original axes", () => {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute(
      "position",
      new THREE.Float32BufferAttribute([10, 20, 30, 14, 26, 38], 3),
    );
    expect(geometryDimensions(geometry)).toEqual({ x: 4, y: 6, z: 8 });
    geometry.dispose();
  });

  it("does not invent bounds for an empty plate", () => {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.Float32BufferAttribute([], 3));
    expect(geometryDimensions(geometry)).toBeNull();
    geometry.dispose();
  });
});
