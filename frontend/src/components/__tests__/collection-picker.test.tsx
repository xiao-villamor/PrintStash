/*
 * Every "where should this go?" dialog picks its destination here. It searches
 * the server rather than listing the whole library, so what it must get right is
 * what it offers: only folders the reader can write to, never a folder that would
 * swallow itself, each named by its full path so two "Brackets" can be told
 * apart — and it must hand back the collection, since callers need its id.
 */

import "@testing-library/jest-dom/vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { CollectionPicker, type CollectionPickerProps } from "@/components/collection-picker";
import { collectionTreeRoutes } from "@/test-support/collection-tree";
import { aCollection, aCollectionNode } from "@/test-support/factories";
import { json, renderApp, type RouteTable } from "@/test-support/render";

const LIBRARY = [
  aCollection({ id: 1, name: "Parts", path: "parts", parent_id: null, effective_role: "edit" }),
  aCollection({
    id: 2,
    name: "Brackets",
    path: "parts/brackets",
    parent_id: 1,
    effective_role: "edit",
  }),
  aCollection({
    id: 3,
    name: "Reference",
    path: "reference",
    parent_id: null,
    effective_role: "view",
  }),
];

function renderPicker(over: Partial<CollectionPickerProps> = {}, routes: RouteTable = {}) {
  const onSelect = vi.fn<CollectionPickerProps["onSelect"]>();
  renderApp(
    <CollectionPicker
      minRole="edit"
      selectedPath={null}
      onSelect={onSelect}
      emptyLabel="Nothing here"
      {...over}
    />,
    { routes: { ...collectionTreeRoutes(LIBRARY), ...routes } },
  );
  return { onSelect };
}

describe("CollectionPicker", () => {
  it("names each destination by its full path", async () => {
    renderPicker();

    expect(await screen.findByRole("option", { name: /Parts\/Brackets/ })).toBeInTheDocument();
  });

  it("leaves out folders the reader cannot write to", async () => {
    renderPicker();
    await screen.findByRole("option", { name: /Parts\/Brackets/ });

    expect(screen.queryByRole("option", { name: /Reference/ })).toBeNull();
  });

  it("leaves out an excluded folder's whole subtree", async () => {
    renderPicker({ excludePaths: ["parts"] });

    expect(await screen.findByText("Nothing here")).toBeInTheDocument();
  });

  it("narrows the destinations to the search", async () => {
    const user = userEvent.setup();
    renderPicker();
    await screen.findByRole("option", { name: /Parts\/Brackets/ });

    await user.type(screen.getByRole("textbox", { name: "Find destination" }), "brack");

    await vi.waitFor(() => expect(screen.queryByRole("option", { name: /^Parts \(/ })).toBeNull());
  });

  it("hands back the chosen collection", async () => {
    const user = userEvent.setup();
    const { onSelect } = renderPicker();

    await user.click(await screen.findByRole("option", { name: /Parts\/Brackets/ }));

    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ id: 2 }));
  });

  it("offers the none option when asked to", async () => {
    const user = userEvent.setup();
    const { onSelect } = renderPicker({ noneLabel: "Vault only" });

    await user.click(await screen.findByRole("option", { name: "Vault only" }));

    expect(onSelect).toHaveBeenCalledWith(null);
  });

  it("marks the chosen destination as selected", async () => {
    renderPicker({ selectedPath: "parts/brackets" });

    expect(await screen.findByRole("option", { name: /Parts\/Brackets/ })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("recovers from a failed folder search", async () => {
    const user = userEvent.setup();
    let failing = true;
    renderPicker(
      {},
      {
        "GET /api/v1/collections/search": () =>
          failing
            ? json({ detail: "offline" }, 500)
            : json({ items: [aCollectionNode()], next_cursor: null }),
      },
    );

    expect(await screen.findByRole("alert")).toHaveTextContent("Folders could not be loaded.");
    expect(screen.queryByText("Nothing here")).toBeNull();
    failing = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("option", { name: /Parts/ })).toBeInTheDocument();
  });
});
