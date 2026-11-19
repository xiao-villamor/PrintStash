/** Sidebar tabs keep controls in a bounded panel without discarding their state. */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { ToolpathLayout } from "../toolpath-layout";

describe("ToolpathLayout", () => {
  it("shows one control panel at a time", async () => {
    const user = userEvent.setup();
    render(
      <ToolpathLayout viewAvailable layers={<button>Plate 01</button>} view={<button>Fit</button>}>
        Canvas
      </ToolpathLayout>,
    );
    expect(screen.getByRole("button", { name: "Plate 01" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Fit" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "View" }));

    expect(screen.getByRole("button", { name: "Fit" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Plate 01" })).not.toBeInTheDocument();
  });

  it("preserves control state across keyboard tab changes", async () => {
    const user = userEvent.setup();
    render(
      <ToolpathLayout
        viewAvailable
        layers={<input aria-label="Layer" defaultValue="750" />}
        view={<button>Fit</button>}
      >
        Canvas
      </ToolpathLayout>,
    );
    await user.clear(screen.getByRole("textbox", { name: "Layer" }));
    await user.type(screen.getByRole("textbox", { name: "Layer" }), "375");
    screen.getByRole("tab", { name: "Layers" }).focus();

    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "View" })).toHaveFocus();
    await user.keyboard("{ArrowLeft}");

    expect(screen.getByRole("tab", { name: "Layers" })).toHaveFocus();
    expect(screen.getByRole("textbox", { name: "Layer" })).toHaveValue("375");
  });

  it("returns to the source panel when the preview is unavailable", async () => {
    const user = userEvent.setup();
    const view = render(
      <ToolpathLayout viewAvailable layers={<button>Plate 01</button>} view={<button>Fit</button>}>
        Canvas
      </ToolpathLayout>,
    );
    await user.click(screen.getByRole("tab", { name: "View" }));

    view.rerender(
      <ToolpathLayout layers={<button>Plate 01</button>}>Preview unavailable</ToolpathLayout>,
    );

    expect(screen.getByRole("tab", { name: "Layers" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("button", { name: "Plate 01" })).toBeVisible();
    expect(screen.queryByRole("tab", { name: "View" })).not.toBeInTheDocument();
  });
});
