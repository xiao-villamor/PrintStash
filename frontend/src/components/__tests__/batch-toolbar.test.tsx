/*
 * Acting on many models at once, where every action is one the user cannot undo
 * per-item.
 *
 * The toolbar appears only when something is selected — rendering an empty one
 * leaves a bar of live buttons above a list with no selection, and the first one
 * clicked applies to nothing or to everything depending on the handler.
 *
 * Two cases are about the *destination* rather than the action. Moving to the
 * root is distinct from moving to a collection (the API takes a null, not an
 * empty string), and the destination search must exclude the selected folders'
 * own descendants — moving a folder into itself is a cycle the tree cannot
 * render and the backend will not refuse.
 *
 * Deleting confirms. Everything else applies immediately, which is why delete is
 * the only one with a gate.
 */

import "@testing-library/jest-dom/vitest";
import { describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { BatchToolbar } from "@/components/batch-toolbar";
import { collectionTreeRoutes } from "@/test-support/collection-tree";
import { renderApp } from "@/test-support/render";
import type { CollectionNodeRead, CollectionRead, TagRead } from "@/types";

function collection(over: Partial<CollectionRead> = {}): CollectionRead {
  return {
    id: 1,
    name: "Functional",
    slug: "functional",
    path: "functional",
    parent_id: null,
    model_count: 3,
    effective_role: "edit",
    tags: [],
    has_readme: false,
    ...over,
  };
}

/** A selected folder as the grid holds it: a tree node, labelled by its name. */
function node(row: CollectionRead): CollectionNodeRead {
  return { ...row, child_count: 0, descendant_count: 0, display_path: row.name };
}

function tag(over: Partial<TagRead> = {}): TagRead {
  return { id: 1, name: "draft", slug: "draft", model_count: 2, ...over };
}

type BatchToolbarProps = React.ComponentProps<typeof BatchToolbar>;

/** Render the toolbar over a library; destinations are searched from it. */
function setup({
  library = [collection()],
  ...overrides
}: Partial<BatchToolbarProps> & { library?: CollectionRead[] } = {}) {
  const props = {
    modelCount: 2,
    selectedCollections: [],
    tags: [tag()],
    busy: false,
    onMoveSelection: vi.fn<BatchToolbarProps["onMoveSelection"]>(),
    onRenameCollections: vi.fn<BatchToolbarProps["onRenameCollections"]>(),
    onApplyTags: vi.fn<BatchToolbarProps["onApplyTags"]>(),
    onDeleteSelection: vi.fn<BatchToolbarProps["onDeleteSelection"]>(),
    onClear: vi.fn<BatchToolbarProps["onClear"]>(),
    ...overrides,
  };
  renderApp(<BatchToolbar {...props} />, { routes: collectionTreeRoutes(library) });
  return props;
}

describe("BatchToolbar", () => {
  it("renders nothing when nothing is selected", () => {
    setup({ modelCount: 0 });
    expect(screen.queryByText(/selected/)).not.toBeInTheDocument();
  });

  it("shows the selected count", () => {
    setup({ modelCount: 3 });
    expect(screen.getByText("3 selected")).toBeInTheDocument();
  });

  it("moves the selection to the chosen collection", async () => {
    const user = userEvent.setup();
    const props = setup();

    await user.click(screen.getByRole("button", { name: /move/i }));
    await user.click(await screen.findByRole("option", { name: /Functional/ }));
    await user.click(screen.getByRole("button", { name: /move here/i }));

    expect(props.onMoveSelection).toHaveBeenCalledWith("functional", 1);
  });

  it("moves the selection to root", async () => {
    const user = userEvent.setup();
    const props = setup();

    await user.click(screen.getByRole("button", { name: /move/i }));
    await user.click(await screen.findByRole("option", { name: "None (root)" }));
    await user.click(screen.getByRole("button", { name: /move here/i }));

    expect(props.onMoveSelection).toHaveBeenCalledWith("", null);
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
    const props = setup({ modelCount: 2 });

    await user.click(screen.getByRole("button", { name: /delete/i }));
    // Confirm inside the dialog (the toolbar also has a "Delete" button).
    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: /^delete$/i }));

    expect(props.onDeleteSelection).toHaveBeenCalledTimes(1);
  });

  it("clears the selection", async () => {
    const user = userEvent.setup();
    const props = setup();

    await user.click(screen.getByRole("button", { name: /clear selection/i }));
    expect(props.onClear).toHaveBeenCalledTimes(1);
  });

  it("adapts actions and renames selected folders", async () => {
    const user = userEvent.setup();
    const folder = node(collection());
    const props = setup({ modelCount: 0, selectedCollections: [folder] });

    expect(screen.queryByRole("button", { name: /^tag$/i })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /rename/i }));
    const input = screen.getByDisplayValue("Functional");
    await user.clear(input);
    await user.type(input, "Fixtures");
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: /rename/i }));

    expect(props.onRenameCollections).toHaveBeenCalledWith({ 1: "Fixtures" });
  });

  it("searches destinations and excludes selected folder descendants", async () => {
    const user = userEvent.setup();
    const selected = collection({ id: 1, name: "Projects", path: "projects" });
    const child = collection({ id: 2, name: "Archive", path: "projects/archive", parent_id: 1 });
    const target = collection({ id: 3, name: "Storage", path: "storage" });
    setup({
      modelCount: 0,
      selectedCollections: [node(selected)],
      library: [selected, child, target],
    });

    await user.click(screen.getByRole("button", { name: /move/i }));
    expect(await screen.findByRole("option", { name: /Storage/ })).toBeVisible();
    expect(screen.queryByRole("option", { name: /Archive/ })).not.toBeInTheDocument();
  });
});
