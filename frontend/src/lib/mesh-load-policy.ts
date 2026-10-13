/**
 * Whether a Model's 3D preview loads by itself or waits to be asked for.
 *
 * Opening a Model must not cost a download the viewer did not choose: a mesh
 * above the budget shows its thumbnail and an explicit action instead. The
 * budget is the source Artifact's stored size, which is known before any
 * request is made.
 */
export type MeshLoadPolicy = "automatic" | "on_request";

/** Source Artifacts up to this size load their 3D preview without being asked. */
export const MESH_AUTO_LOAD_BYTES = 16 * 1024 * 1024;

export function meshLoadPolicy(sizeBytes: number): MeshLoadPolicy {
  return sizeBytes <= MESH_AUTO_LOAD_BYTES ? "automatic" : "on_request";
}
