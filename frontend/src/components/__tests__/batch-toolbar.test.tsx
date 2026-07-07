import "@testing-library/jest-dom/vitest";
import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { BatchToolbar } from "@/components/batch-toolbar";
import type { CollectionRead, TagRead } from "@/types";

function collection(over: Partial<CollectionRead> = {}): CollectionRead {
  return {
    id: 1,
    name: "Functional",
    slug: "functional",
    path: "functional",
    parent_id: null,
    model_count: 3,
    effective_role: "edit",
    ...over,
  };
}

function tag(over: Partial<TagRead> = {}): TagRead {
  return { id: 1, name: "draft", slug: "draft", model_count: 2, ...over };
}

function setup(overrides: Partial<React.ComponentProps<typeof BatchToolbar>> = {}) {
  const props = {
    count: 2,
    collections: [collection()],
    tags: [tag()],
    busy: false,
    onMove: vi.fn(),
    onApplyTags: vi.fn(),
    onDelete: vi.fn(),
    onClear: vi.fn(),
    ...overrides,
  };
  render(<BatchToolbar {...props} />);
  return props;
}

describe("BatchToolbar", () => {
  it("renders nothing when nothing is selected", () => {
    setup({ count: 0 });
    expect(screen.queryByText(/selected/)).not.toBeInTheDocument();
  });

  it("shows the selected count", () => {
    setup({ count: 3 });
    expect(screen.getByText("3 selected")).toBeInTheDocument();
  });

  it("moves the selection to the chosen collection", async () => {
    const user = userEvent.setup();
    const props = setup();

    await user.click(screen.getByRole("button", { name: /move/i }));
    await user.click(screen.getByText(/functional/i));
    await user.click(screen.getByRole("button", { name: /move here/i }));

    expect(props.onMove).toHaveBeenCalledWith("functional");
  });

  it("moves the selection to root", async () => {
    const user = userEvent.setup();
    const props = setup();

    await user.click(screen.getByRole("button", { name: /move/i }));
    await user.click(screen.getByText(/none \(root\)/i));
    await user.click(screen.getByRole("button", { name: /move here/i }));

    expect(props.onMove).toHaveBeenCalledWith("");
  });

  it("applies added tags", async () => {
    const user = userEvent.setup();
    const props = setup();

    await user.click(screen.getByRole("button", { name: /^tag$/i }));
    const addInput = screen.getByPlaceholderText(/search or create/i);
    await user.type(addInput, "needs-supports{Enter}");
    await user.click(screen.getByRole("button", { name: /apply/i }));

    expect(props.onApplyTags).toHaveBeenCalledWith(["needs-supports"], []);
  });

  it("confirms before deleting", async () => {
    const user = userEvent.setup();
    const props = setup({ count: 2 });

    await user.click(screen.getByRole("button", { name: /delete/i }));
    // Confirm inside the dialog (the toolbar also has a "Delete" button).
    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: /^delete$/i }));

    expect(props.onDelete).toHaveBeenCalledTimes(1);
  });

  it("clears the selection", async () => {
    const user = userEvent.setup();
    const props = setup();

    await user.click(screen.getByRole("button", { name: /clear selection/i }));
    expect(props.onClear).toHaveBeenCalledTimes(1);
  });
});
