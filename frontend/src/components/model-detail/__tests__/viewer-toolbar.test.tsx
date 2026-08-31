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

function renderToolbar(
  viewerReady: boolean,
  screenshot = vi.fn<() => Promise<void>>(async () => {}),
) {
  const controls = createRef<STLViewerControls>();
  controls.current = {
    zoomIn: vi.fn<() => void>(),
    zoomOut: vi.fn<() => void>(),
    resetView: vi.fn<() => void>(),
    fit: vi.fn<() => void>(),
    screenshot,
  };
  render(
    <ViewerToolbar
      displayMode="solid"
      setDisplayMode={vi.fn<(mode: "solid" | "xray" | "wireframe") => void>()}
      showGrid
      setShowGrid={vi.fn<(visible: boolean) => void>()}
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
});
