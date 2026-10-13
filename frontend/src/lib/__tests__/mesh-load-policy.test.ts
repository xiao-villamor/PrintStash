/*
 * Which 3D previews load without being asked.
 *
 * Opening a Model used to download its whole mesh, whatever its size: a single
 * 43 MB Artifact cost more than the rest of a browsing session put together.
 * The policy draws one line at a byte budget, and the budget itself belongs to
 * the automatic side — a mesh exactly at the limit is one the limit allows.
 */
import { describe, expect, it } from "vitest";

import { MESH_AUTO_LOAD_BYTES, meshLoadPolicy } from "@/lib/mesh-load-policy";

describe("meshLoadPolicy", () => {
  it.each([0, 1, MESH_AUTO_LOAD_BYTES])("loads a %d byte mesh automatically", (sizeBytes) => {
    expect(meshLoadPolicy(sizeBytes)).toBe("automatic");
  });

  it.each([MESH_AUTO_LOAD_BYTES + 1, 100 * 1024 * 1024])(
    "waits to be asked for a %d byte mesh",
    (sizeBytes) => {
      expect(meshLoadPolicy(sizeBytes)).toBe("on_request");
    },
  );
});
