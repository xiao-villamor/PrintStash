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
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SavedViewSelector } from "@/components/saved-view-selector";
import type { ComponentProps } from "react";
import type { SavedViewRead } from "@/types";

type SelectorProps = ComponentProps<typeof SavedViewSelector>;

function view(id: number, name: string): SavedViewRead {
  return {
    id,
    name,
    filters: { library_view: "all", direct: true, tag: [], favorites: false },
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
  it("shows loading while the first list is pending", async () => {
    render(
      <SavedViewSelector
        views={[]}
        activeId={null}
        readState={{ status: "loading" }}
        {...handlerSpies()}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /Saved views/ }));
    expect(screen.getByText("Loading saved views…")).toBeVisible();
    expect(screen.queryByText("No saved views yet")).toBeNull();
    expect(screen.getByRole("button", { name: /Save current view/ })).toBeDisabled();
  });
  it("offers retry after a failed list", async () => {
    render(
      <SavedViewSelector
        views={[]}
        activeId={null}
        readState={{ status: "error", retry: () => {} }}
        {...handlerSpies()}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /Saved views/ }));
    expect(screen.getByText("Could not load saved views")).toBeVisible();
    expect(screen.getByRole("button", { name: "Retry saved views" })).toBeEnabled();
    expect(screen.queryByText("No saved views yet")).toBeNull();
  });
  it("preserves a newer rename draft after acknowledgement", async () => {
    const pending = Promise.withResolvers<void>();
    const handlers = handlerSpies();
    handlers.onRename.mockReturnValue(pending.promise);
    render(<SavedViewSelector views={[view(1, "Workshop")]} activeId={null} {...handlers} />);
    await userEvent.click(screen.getByRole("button", { name: /Saved views/ }));
    await userEvent.click(screen.getByRole("button", { name: "Rename Workshop" }));
    const dialog = screen.getByRole("dialog", { name: "Rename saved view" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Rename" }));
    await userEvent.type(within(dialog).getByRole("textbox"), " later");
    await act(async () => pending.resolve());
    expect(screen.getByRole("dialog", { name: "Rename saved view" })).toBeVisible();
    expect(within(dialog).getByRole("textbox")).toHaveValue("Workshop later");
  });
  it("preserves a reopened rename dialog after acknowledgement", async () => {
    const pending = Promise.withResolvers<void>();
    const handlers = handlerSpies();
    handlers.onRename.mockReturnValue(pending.promise);
    render(<SavedViewSelector views={[view(1, "Workshop")]} activeId={null} {...handlers} />);
    await userEvent.click(screen.getByRole("button", { name: /Saved views/ }));
    await userEvent.click(screen.getByRole("button", { name: "Rename Workshop" }));
    const dialog = screen.getByRole("dialog", { name: "Rename saved view" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Rename" }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await userEvent.click(screen.getByRole("button", { name: /Saved views/ }));
    await userEvent.click(screen.getByRole("button", { name: "Rename Workshop" }));
    await act(async () => pending.resolve());
    expect(screen.getByRole("dialog", { name: "Rename saved view" })).toBeVisible();
  });
  it("retains rename input after failure", async () => {
    const handlers = handlerSpies();
    handlers.onRename.mockRejectedValue(new Error("saved_view_name_exists"));
    render(<SavedViewSelector views={[view(1, "Workshop")]} activeId={null} {...handlers} />);
    await userEvent.click(screen.getByRole("button", { name: /Saved views/ }));
    await userEvent.click(screen.getByRole("button", { name: "Rename Workshop" }));
    const dialog = screen.getByRole("dialog", { name: "Rename saved view" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Rename" }));
    expect(within(dialog).getByRole("textbox")).toHaveValue("Workshop");
    expect(dialog).toBeVisible();
  });
  it("retains delete confirmation after failure", async () => {
    const handlers = handlerSpies();
    handlers.onDelete.mockRejectedValue(new Error("delete_failed"));
    render(<SavedViewSelector views={[view(1, "Workshop")]} activeId={null} {...handlers} />);
    await userEvent.click(screen.getByRole("button", { name: /Saved views/ }));
    await userEvent.click(screen.getByRole("button", { name: "Delete Workshop" }));
    const dialog = screen.getByRole("dialog", { name: "Delete saved view?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
    expect(dialog).toBeVisible();
  });
  it("preserves a reopened delete confirmation after acknowledgement", async () => {
    const pending = Promise.withResolvers<void>();
    const handlers = handlerSpies();
    handlers.onDelete.mockReturnValue(pending.promise);
    render(
      <SavedViewSelector
        views={[view(1, "Workshop"), view(2, "Next view")]}
        activeId={null}
        {...handlers}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /Saved views/ }));
    await userEvent.click(screen.getByRole("button", { name: "Delete Workshop" }));
    const first = screen.getByRole("dialog", { name: "Delete saved view?" });
    await userEvent.click(within(first).getByRole("button", { name: "Delete" }));
    await userEvent.keyboard("{Escape}");
    await userEvent.click(screen.getByRole("button", { name: /Saved views/ }));
    await userEvent.click(screen.getByRole("button", { name: "Delete Next view" }));

    await act(async () => pending.resolve());

    expect(screen.getByRole("dialog", { name: "Delete saved view?" })).toHaveTextContent(
      "Next view",
    );
  });
});
