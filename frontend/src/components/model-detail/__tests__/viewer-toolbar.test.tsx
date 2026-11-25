/*
 * The model screenshot control stays inert until the WebGL preview is ready,
 * then delegates to the viewer's asynchronous capture boundary exactly once.
 * Mode and grid buttons expose their current selection to assistive technology.
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
  actions: {
    fit?: () => void;
    setView?: STLViewerControls["setView"];
    setShowGrid?: (visible: boolean) => void;
    notifyScreenshotError?: (message: string) => void;
    viewerMode?: "model" | "gcode";
    showGrid?: boolean;
    hasGcode?: boolean;
  } = {},
) {
  const controls = createRef<STLViewerControls>();
  controls.current = {
    setView: actions.setView ?? vi.fn<STLViewerControls["setView"]>(),
    zoomIn: vi.fn<() => void>(),
    zoomOut: vi.fn<() => void>(),
    resetView: vi.fn<() => void>(),
    fit: actions.fit ?? vi.fn<() => void>(),
    screenshot,
    captureImage: vi.fn<() => Promise<Blob>>().mockResolvedValue(new Blob()),
  };
  render(
    <ViewerToolbar
      showGrid={actions.showGrid ?? true}
      setShowGrid={actions.setShowGrid ?? vi.fn<(visible: boolean) => void>()}
      controls={controls}
      viewerMode={actions.viewerMode ?? "model"}
      setViewerMode={vi.fn<(mode: "model" | "gcode") => void>()}
      hasGcode={actions.hasGcode ?? false}
      viewerReady={viewerReady}
      notifyScreenshotError={actions.notifyScreenshotError}
    />,
  );
  return screenshot;
}

describe("ViewerToolbar screenshot", () => {
  it("keeps model controls inert until the preview is ready", () => {
    renderToolbar(false);
    expect(screen.queryByRole("combobox", { name: "Display" })).not.toBeInTheDocument();
    for (const name of ["Fit to view", "Screenshot", "Build plate grid"]) {
      expect(screen.getByRole("button", { name })).toBeDisabled();
    }
  });

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
    const notifyScreenshotError = vi.fn<(message: string) => void>();
    const screenshot = renderToolbar(
      true,
      vi.fn<() => Promise<void>>(async () => {
        throw new Error("screenshot_empty");
      }),
      { notifyScreenshotError },
    );

    await user.click(screen.getByRole("button", { name: "Screenshot" }));

    expect(screenshot).toHaveBeenCalledOnce();
    await vi.waitFor(() =>
      expect(notifyScreenshotError).toHaveBeenCalledWith("Screenshot could not be created"),
    );
  });

  it.each([
    ["Front", "front"],
    ["Top", "top"],
    ["Isometric", "isometric"],
  ] as const)("selects the %s camera preset", async (label, preset) => {
    const user = userEvent.setup();
    const setView = vi.fn<STLViewerControls["setView"]>();
    renderToolbar(true, undefined, { setView });
    await user.click(screen.getByRole("button", { name: label }));
    expect(setView).toHaveBeenCalledWith(preset);
    expect(screen.getByRole("button", { name: label })).toHaveAttribute("aria-pressed", "true");
  });

  it("resets the camera selection when fitting the model", async () => {
    const user = userEvent.setup();
    renderToolbar(true);
    await user.click(screen.getByRole("button", { name: "Front" }));
    await user.click(screen.getByRole("button", { name: "Fit to view" }));
    expect(screen.getByRole("button", { name: "Isometric" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: "Front" })).toHaveAttribute("aria-pressed", "false");
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

describe("ViewerToolbar selection", () => {
  it("keeps mesh display controls out of the toolpath preview", () => {
    renderToolbar(true, undefined, { hasGcode: true, viewerMode: "gcode" });

    expect(screen.queryByRole("combobox", { name: "Display" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Build plate grid" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Fit to view" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Front" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Screenshot" })).toBeEnabled();
  });
  it("restores the selected camera when switching preview modes", async () => {
    const user = userEvent.setup();
    const controls = createRef<STLViewerControls>();
    const setView = vi.fn<STLViewerControls["setView"]>();
    controls.current = {
      setView,
      zoomIn: () => {},
      zoomOut: () => {},
      resetView: () => {},
      fit: () => {},
      screenshot: async () => {},
      captureImage: async () => new Blob(),
    };
    const props = {
      showGrid: true,
      setShowGrid: () => {},
      controls,
      setViewerMode: () => {},
      hasGcode: true,
      viewerReady: true,
    };
    const view = render(<ViewerToolbar {...props} viewerMode="model" />);
    await user.click(screen.getByRole("button", { name: "Front" }));
    setView.mockClear();
    view.rerender(<ViewerToolbar {...props} viewerMode="gcode" />);
    expect(setView).toHaveBeenCalledWith("front");
    expect(screen.getByRole("button", { name: "Front" })).toHaveAttribute("aria-pressed", "true");
  });

  it("announces the active preview mode", () => {
    renderToolbar(true, undefined, { hasGcode: true, viewerMode: "gcode" });

    expect(screen.getByRole("button", { name: "3D model" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    expect(screen.getByRole("button", { name: "G-code" })).toHaveAttribute("aria-pressed", "true");
  });

  it("offers a named screenshot icon without a display or expand control", () => {
    renderToolbar(true);

    expect(screen.getByRole("button", { name: "Screenshot" })).toHaveTextContent(/^$/);
    expect(screen.queryByRole("combobox", { name: "Display" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Expand" })).not.toBeInTheDocument();
  });

  it.each([true, false])("announces grid visibility when it is %s", (visible) => {
    renderToolbar(true, undefined, { showGrid: visible });

    expect(screen.getByRole("button", { name: "Build plate grid" })).toHaveAttribute(
      "aria-pressed",
      String(visible),
    );
  });
});
