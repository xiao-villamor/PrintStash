/** Trying a preview look is free; only applying it re-renders existing images. */
import "@testing-library/jest-dom/vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PreviewAppearanceSettings } from "@/components/preview-appearance-settings";
import { aVaultConfig } from "@/test-support/factories";
import { json, renderApp } from "@/test-support/render";

afterEach(() => vi.unstubAllGlobals());

function renderSettings() {
  const config = aVaultConfig({ preview_look: "sky" });
  const view = renderApp(<PreviewAppearanceSettings config={config} disabled={false} />, {
    routes: {
      "PUT /api/v1/config": () =>
        json(
          aVaultConfig({
            preview_look: "graphite",
            edit_epoch: config.edit_epoch,
            edit_version: config.edit_version + 1,
          }),
        ),
    },
  });
  return view;
}

describe("PreviewAppearanceSettings", () => {
  it("previews a tried colour without saving the look", async () => {
    const view = renderSettings();
    fireEvent.click(screen.getByRole("radio", { name: "Graphite" }));

    expect(screen.getByRole("radio", { name: "Graphite" })).toBeChecked();
    expect(screen.getByText(/Not applied yet/)).toBeVisible();
    // Give a debounced save every chance to fire: none may.
    await new Promise((resolve) => setTimeout(resolve, 400));
    expect(view.requestsWithMethod("PUT")).toHaveLength(0);
  });

  it.each(["Sage", "Clay", "Plum"])("offers a %s preview swatch", (colour) => {
    renderSettings();
    const swatch = screen.getByRole("radio", { name: colour });
    fireEvent.click(swatch);
    expect(swatch).toBeChecked();
  });

  it("saves the look once, when applied", async () => {
    const view = renderSettings();
    fireEvent.click(screen.getByRole("radio", { name: "Sand" }));
    fireEvent.click(screen.getByRole("radio", { name: "Graphite" }));
    fireEvent.click(screen.getByRole("button", { name: "Apply look" }));

    await waitFor(() => expect(view.requestsWithMethod("PUT")).toHaveLength(1));
    expect(JSON.parse(view.requestsWithMethod("PUT")[0]!.body)).toEqual({
      preview_look: "graphite",
      preview_floor_grid: true,
    });
  });

  it("returns the preview to the saved look when discarded", () => {
    renderSettings();
    fireEvent.click(screen.getByRole("radio", { name: "Graphite" }));
    fireEvent.click(screen.getByRole("button", { name: "Discard" }));

    expect(screen.getByRole("radio", { name: "Sky" })).toBeChecked();
    expect(screen.queryByText(/Not applied yet/)).toBeNull();
    expect(screen.getByRole("button", { name: "Apply look" })).toBeDisabled();
  });
});
