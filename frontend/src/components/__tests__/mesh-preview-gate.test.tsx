/*
 * The gate between opening a Model and downloading its mesh.
 *
 * A mesh within the budget is simply shown: the gate must add nothing, not even
 * a wrapper the viewer has to be sized inside. A mesh over the budget is not
 * mounted at all, because mounting the viewer is what starts the download; the
 * Model's thumbnail stands in, with the size the action would cost and the
 * action itself. Asking once is enough — the viewer then stays.
 */
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { MeshPreviewGate } from "@/components/mesh-preview-gate";
import { MESH_AUTO_LOAD_BYTES } from "@/lib/mesh-load-policy";

const LARGE = MESH_AUTO_LOAD_BYTES + 1;

function renderGate(sizeBytes: number, posterUrl: string | null = "blob:poster") {
  return render(
    <MeshPreviewGate sizeBytes={sizeBytes} posterUrl={posterUrl} posterAlt="Planter">
      <p>the 3D viewer</p>
    </MeshPreviewGate>,
  );
}

describe("MeshPreviewGate", () => {
  describe("a mesh within the budget", () => {
    it("shows the viewer straight away", () => {
      renderGate(MESH_AUTO_LOAD_BYTES);

      expect(screen.getByText("the 3D viewer")).toBeVisible();
    });

    it("offers no load action", () => {
      renderGate(MESH_AUTO_LOAD_BYTES);

      expect(screen.queryByRole("button", { name: "Load 3D preview" })).not.toBeInTheDocument();
    });
  });

  describe("a mesh over the budget", () => {
    it("does not mount the viewer", () => {
      renderGate(LARGE);

      expect(screen.queryByText("the 3D viewer")).not.toBeInTheDocument();
    });

    it("shows the Model's thumbnail in its place", () => {
      renderGate(LARGE);

      expect(screen.getByRole("img", { name: "Planter" })).toHaveAttribute("src", "blob:poster");
    });

    it("states the size the preview would download", () => {
      renderGate(48 * 1024 * 1024);

      expect(screen.getByText(/^Large 3D preview · 48(\.0)? MB$/)).toBeVisible();
    });

    it("mounts the viewer once asked", async () => {
      renderGate(LARGE);

      await userEvent.click(screen.getByRole("button", { name: "Load 3D preview" }));

      expect(screen.getByText("the 3D viewer")).toBeVisible();
    });

    it("still offers the load action without a thumbnail", () => {
      renderGate(LARGE, null);

      expect(screen.queryByRole("img")).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Load 3D preview" })).toBeVisible();
    });
  });
});
