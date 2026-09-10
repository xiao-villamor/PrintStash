import * as THREE from "three";

export interface CameraPose {
  position: [number, number, number];
  target: [number, number, number];
}
export interface ComparisonCamera {
  publish: (sender: symbol, pose: CameraPose) => void;
  subscribe: (listener: (sender: symbol, pose: CameraPose) => void) => () => void;
}
export function createComparisonCamera(): ComparisonCamera {
  const listeners = new Set<(sender: symbol, pose: CameraPose) => void>();
  return {
    publish(sender, pose) {
      for (const listener of listeners) listener(sender, pose);
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}
export interface MeshComparison {
  referenceSizeMm: number;
  alignment?: number[][];
  compensateScale?: boolean;
  compensateMirror?: boolean;
}
/** Keep the verified rotation; apply scale/reflection only when selected. */
export function comparisonTransform(options: MeshComparison): THREE.Matrix4 {
  if (!options.alignment) return new THREE.Matrix4();
  const rows = options.alignment;
  if (
    rows.length !== 4 ||
    rows.some((row) => row.length !== 4 || row.some((value) => !Number.isFinite(value)))
  )
    return new THREE.Matrix4();
  const matrix = new THREE.Matrix4().fromArray(rows.flat()).transpose();
  const quaternion = new THREE.Quaternion();
  const scale = new THREE.Vector3();
  matrix.decompose(new THREE.Vector3(), quaternion, scale);
  if (Math.min(Math.abs(scale.x), Math.abs(scale.y), Math.abs(scale.z)) < 1e-12)
    return new THREE.Matrix4();
  if (!options.compensateScale)
    scale.set(Math.sign(scale.x), Math.sign(scale.y), Math.sign(scale.z));
  if (!options.compensateMirror) scale.set(Math.abs(scale.x), Math.abs(scale.y), Math.abs(scale.z));
  return new THREE.Matrix4().compose(new THREE.Vector3(), quaternion, scale);
}
export function sharedScale(referenceSizeMm: number): number {
  return Number.isFinite(referenceSizeMm) && referenceSizeMm > 0 ? 10 / referenceSizeMm : 1;
}
