/*
 * Picking, renaming and saving filter views, from one control.
 *
 * The search exists because a real user accumulates dozens of views and the list
 * becomes unusable without it — so "searches a long list" is the shape under
 * test, not a nicety.
 *
 * The dirty-state row is the one with consequences. When the current filters no
 * longer match the selected view, the selector has to say so; showing the view as
 * cleanly active means the user hits "update" believing they are saving what they
 * see and instead overwrite it with something else, or navigates away thinking
 * their changes were stored.
 */

import "@testing-library/jest-dom/vitest";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SavedViewSelector } from "@/components/saved-view-selector";
import type { ComponentProps } from "react";
import type { SavedViewRead } from "@/types";

type SelectorProps = ComponentProps<typeof SavedViewSelector>;

function view(id: number, name: string): SavedViewRead {
  return {
    id,
    name,
    filters: { direct: true, tag: [], favorites: false },
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

/** One spy per selector callback, typed from the component's own props. */
function handlerSpies() {
  return {
    onSelect: vi.fn<SelectorProps["onSelect"]>(),
    onCreate: vi.fn<SelectorProps["onCreate"]>(),
    onUpdate: vi.fn<SelectorProps["onUpdate"]>(),
    onRename: vi.fn<SelectorProps["onRename"]>(),
    onDuplicate: vi.fn<SelectorProps["onDuplicate"]>(),
    onDelete: vi.fn<SelectorProps["onDelete"]>(),
  };
}

describe("SavedViewSelector", () => {
  it("searches a long saved-view list and applies the chosen view", async () => {
    const user = userEvent.setup();
    const handlers = handlerSpies();
    const views = [view(1, "Ready to print"), view(2, "Needs supports"), view(3, "Favorites")];
    render(<SavedViewSelector views={views} activeId={null} {...handlers} />);

    await user.click(screen.getByRole("button", { name: /saved views/i }));
    await user.type(screen.getByRole("textbox", { name: /find a saved view/i }), "support");
    expect(screen.queryByText("Ready to print")).not.toBeInTheDocument();
    await user.click(screen.getByText("Needs supports"));

    expect(handlers.onSelect).toHaveBeenCalledWith(views[1]);
  });

  it("updates and renames saved views from the selector", async () => {
    const user = userEvent.setup();
    const saved = view(1, "Workshop");
    const handlers = handlerSpies();
    handlers.onUpdate.mockResolvedValue(undefined);
    handlers.onRename.mockResolvedValue(undefined);
    render(<SavedViewSelector views={[saved]} activeId={1} {...handlers} />);

    await user.click(screen.getByRole("button", { name: /workshop/i }));
    await user.click(screen.getByRole("button", { name: "Update Workshop" }));
    expect(handlers.onUpdate).toHaveBeenCalledWith(saved);
    await user.click(screen.getByRole("button", { name: "Rename Workshop" }));
    const input = screen.getByDisplayValue("Workshop");
    await user.clear(input);
    await user.type(input, "Daily prints");
    await user.click(
      screen
        .getByRole("dialog", { name: /rename saved view/i })
        .querySelector('button[type="submit"]')!,
    );
    expect(handlers.onRename).toHaveBeenCalledWith(saved, "Daily prints");
  });

  it("starts saving current filters from the selector", async () => {
    const user = userEvent.setup();
    const handlers = handlerSpies();
    render(<SavedViewSelector views={[]} activeId={null} {...handlers} />);
    await user.click(screen.getByRole("button", { name: /saved views/i }));
    await user.click(screen.getByRole("button", { name: /save current view/i }));
    expect(handlers.onCreate).toHaveBeenCalledTimes(1);
  });

  it("marks an active saved view when current filters have changed", () => {
    const saved = view(1, "Workshop");
    render(<SavedViewSelector views={[saved]} activeId={1} modified {...handlerSpies()} />);
    expect(screen.getByLabelText("Modified saved view")).toBeInTheDocument();
  });
});
