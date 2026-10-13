/*
 * Two Models side by side, each in its own 3D pane.
 *
 * Opening a comparison must not download two meshes the reviewer did not ask
 * for: a pane whose source Artifact is over the automatic budget offers the
 * preview as an action instead, and the pane beside it is unaffected. The
 * measurement table never depends on either mesh.
 */
import "@testing-library/jest-dom/vitest";
import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SimilarityComparison } from "@/components/similarity-comparison";
import { MESH_AUTO_LOAD_BYTES } from "@/lib/mesh-load-policy";
import { aModel, aRevision } from "@/test-support/factories";
import { renderApp } from "@/test-support/render";
import { aSimilarityCandidate } from "@/test-support/similarity";

const HASH_A = "a".repeat(64);
const HASH_B = "b".repeat(64);

function renderComparison(sizeA: number, sizeB: number) {
  const base = aSimilarityCandidate();
  const candidate = {
    ...base,
    primary_lineage_key: "whole",
    observations: [
      {
        lineage_key: "whole",
        id: 1,
        fingerprint_a_id: null,
        fingerprint_b_id: null,
        input_hash_a: HASH_A,
        input_hash_b: HASH_B,
        kind: "whole",
        multiplicity_a: 1,
        multiplicity_b: 1,
        evidence: base.summary,
      },
    ],
  };
  const mesh = (id: number, model_id: number, sha256: string, size_bytes: number) =>
    aRevision({
      id,
      model_id,
      file_type: "stl",
      original_filename: `${id}.stl`,
      sha256,
      size_bytes,
    });
  return renderApp(
    <SimilarityComparison
      candidate={candidate}
      models={[
        aModel({ id: 1, name: "Left", files: [mesh(11, 1, HASH_A, sizeA)] }),
        aModel({ id: 2, name: "Right", files: [mesh(12, 2, HASH_B, sizeB)] }),
      ]}
    />,
  );
}

describe("SimilarityComparison", () => {
  describe("a pane over the mesh budget", () => {
    it("offers its preview as an action", () => {
      renderComparison(MESH_AUTO_LOAD_BYTES + 1, MESH_AUTO_LOAD_BYTES + 1);

      expect(screen.getAllByRole("button", { name: "Load 3D preview" })).toHaveLength(2);
    });

    it("leaves the pane beside it alone", () => {
      renderComparison(MESH_AUTO_LOAD_BYTES + 1, 1024);

      expect(screen.getAllByRole("button", { name: "Load 3D preview" })).toHaveLength(1);
    });

    it("requests no mesh", () => {
      const view = renderComparison(MESH_AUTO_LOAD_BYTES + 1, MESH_AUTO_LOAD_BYTES + 1);

      expect(view.requests().filter((request) => request.url.endsWith("/stl"))).toEqual([]);
    });
  });
});
