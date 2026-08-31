/*
 * The model screenshot control stays inert until the WebGL preview is ready,
 * then delegates to the viewer's asynchronous capture boundary exactly once.
 */

import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createRef } from "react";
import { describe, expect, it, vi } from "vitest";

import { ViewerToolbar } from "@/components/model-detail/viewer-toolbar";
import type { STLViewerControls } from "@/components/stl-viewer";

const { toastError } = vi.hoisted(() => ({ toastError: vi.fn() }));
vi.mock("sonner", () => ({ toast: { error: toastError } }));

function renderToolbar(
  viewerReady: boolean,
  screenshot = vi.fn<() => Promise<void>>(async () => {}),
  actions: {
    fit?: () => void;
    setShowGrid?: (visible: boolean) => void;
  } = {},
) {
  const controls = createRef<STLViewerControls>();
  controls.current = {
    zoomIn: vi.fn<() => void>(),
    zoomOut: vi.fn<() => void>(),
    resetView: vi.fn<() => void>(),
    fit: actions.fit ?? vi.fn<() => void>(),
    screenshot,
  };
  render(
    <ViewerToolbar
      displayMode="solid"
      setDisplayMode={vi.fn<(mode: "solid" | "xray" | "wireframe") => void>()}
      showGrid
      setShowGrid={actions.setShowGrid ?? vi.fn<(visible: boolean) => void>()}
      controls={controls}
      viewerMode="model"
      setViewerMode={vi.fn<(mode: "model" | "gcode") => void>()}
      hasGcode={false}
      viewerReady={viewerReady}
    />,
  );
  return screenshot;
}

describe("ViewerToolbar screenshot", () => {
  it("is disabled until the model reports that it is loaded", () => {
    renderToolbar(false);

    expect(screen.getByRole("button", { name: "Screenshot" })).toBeDisabled();
  });

  it("uses the asynchronous viewer capture once ready", async () => {
    const user = userEvent.setup();
    const screenshot = renderToolbar(true);

    await user.click(screen.getByRole("button", { name: "Screenshot" }));

    expect(screenshot).toHaveBeenCalledOnce();
  });

  it("reports an asynchronous capture failure without downloading an empty image", async () => {
    const user = userEvent.setup();
    const screenshot = renderToolbar(
      true,
      vi.fn<() => Promise<void>>(async () => {
        throw new Error("screenshot_empty");
      }),
    );

    await user.click(screen.getByRole("button", { name: "Screenshot" }));

    expect(screenshot).toHaveBeenCalledOnce();
    await vi.waitFor(() =>
      expect(toastError).toHaveBeenCalledWith("Screenshot could not be created"),
    );
  });

  it("delegates visible preview controls", async () => {
    const user = userEvent.setup();
    const fit = vi.fn<() => void>();
    const setShowGrid = vi.fn<(visible: boolean) => void>();
    renderToolbar(true, undefined, { fit, setShowGrid });

    await user.click(screen.getByRole("button", { name: "Fit to view" }));
    await user.click(screen.getByRole("button", { name: "Build plate grid" }));

    expect(fit).toHaveBeenCalledOnce();
    expect(setShowGrid).toHaveBeenCalledWith(false);
  });
});
