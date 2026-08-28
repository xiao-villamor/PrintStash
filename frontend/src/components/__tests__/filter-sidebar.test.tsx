/*
 * The outliner: the library's folder tree, plus the tag and printer filters.
 *
 * The tree is derived from a flat list of collections, and *what it hides* is
 * the whole of its behaviour. Typing in the filter box narrows it to matching
 * names — but a match deep in the tree is useless unless every folder above it
 * stays visible too, so the ancestors of a hit are kept. Drop that and a search
 * finds nothing it can show.
 *
 * A tag or printer filter narrows it a different way: those come with an
 * already-filtered model list, so the tree collapses to the folders that
 * actually hold those models. The distinction matters because a text query also
 * matches folder *names* while a facet filter only ever matches models.
 *
 * Drag and drop is how models and folders are reorganised, and both directions
 * are destructive-adjacent: a model dropped on the wrong folder is a model
 * nobody finds again, and a folder dropped into its own descendant is a cycle.
 */

import "@testing-library/jest-dom/vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FilterSidebar, type FilterSidebarProps } from "@/components/filter-sidebar";
import { aCollection, aPrinter, aTag } from "@/test-support/factories";
import { renderApp } from "@/test-support/render";
import type { OutlinerModelRead } from "@/types";

const TREE = [
  aCollection({ id: 1, name: "Parts", path: "parts", parent_id: null }),
  aCollection({ id: 2, name: "Brackets", path: "parts/brackets", parent_id: 1 }),
  aCollection({ id: 3, name: "Toys", path: "toys", parent_id: null }),
];

function outlinerModel(over: Partial<OutlinerModelRead> = {}): OutlinerModelRead {
  // The tree groups by `collection` *path*, not by id — a model with only an id
  // is invisible to it, which is exactly the drift this fixture pins down.
  return { id: 1, name: "Benchy", collection: "parts", collection_id: 1, ...over };
}

function renderSidebar(over: Partial<FilterSidebarProps> = {}) {
  const handlers = {
    onCollectionChange: vi.fn<FilterSidebarProps["onCollectionChange"]>(),
    onTagsChange: vi.fn<FilterSidebarProps["onTagsChange"]>(),
    onPrinterChange: vi.fn<FilterSidebarProps["onPrinterChange"]>(),
    onPrinterPresenceChange: vi.fn<FilterSidebarProps["onPrinterPresenceChange"]>(),
    onCreateCollection: vi.fn<FilterSidebarProps["onCreateCollection"]>(),
    onMoveModel: vi.fn<NonNullable<FilterSidebarProps["onMoveModel"]>>(),
    onMoveCollection: vi.fn<NonNullable<FilterSidebarProps["onMoveCollection"]>>(),
    onDeleteCollection: vi.fn<NonNullable<FilterSidebarProps["onDeleteCollection"]>>(),
  };
  // Model leaves are links into the vault, so the tree needs a router even
  // though nothing here navigates.
  const result = renderApp(
    <FilterSidebar
      collections={TREE}
      models={[]}
      tags={[aTag()]}
      printers={[aPrinter({ id: 4, name: "Voron" })]}
      selectedCollection={null}
      selectedTags={[]}
      selectedPrinterId={null}
      selectedPrinterPresence={null}
      {...handlers}
      {...over}
    />,
  );
  return { ...result, ...handlers };
}

beforeEach(() => {
  window.sessionStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("FilterSidebar", () => {
  describe("the folder tree", () => {
    it("lists the root folders", () => {
      renderSidebar();

      expect(screen.getByText("Parts")).toBeInTheDocument();
      expect(screen.getByText("Toys")).toBeInTheDocument();
    });

    it("offers the whole library as a destination", () => {
      renderSidebar();

      expect(screen.getByLabelText("All Models")).toBeInTheDocument();
    });

    it("reports the folder the user chose", async () => {
      const user = userEvent.setup();
      const { onCollectionChange } = renderSidebar();

      await user.click(screen.getByText("Parts"));

      expect(onCollectionChange).toHaveBeenCalledWith("parts");
    });

    it("returns to the whole library from the root entry", async () => {
      const user = userEvent.setup();
      const { onCollectionChange } = renderSidebar({ selectedCollection: "parts" });

      await user.click(screen.getByLabelText("All Models"));

      expect(onCollectionChange).toHaveBeenCalledWith(null);
    });

    it("nests a child folder under its parent", () => {
      renderSidebar();

      expect(screen.getByText("Brackets")).toBeInTheDocument();
    });

    it("folds a branch away on request", async () => {
      // A deep library is unscannable fully expanded, so a parent has to be
      // collapsible without losing the selection inside it.
      const user = userEvent.setup();
      renderSidebar();

      await user.click(screen.getAllByRole("button", { name: "Collapse" })[0]);

      expect(screen.queryByText("Brackets")).toBeNull();
    });
  });

  describe("narrowing the tree by name", () => {
    /** The sidebar owns the filter box, so the query is typed rather than passed. */
    async function filterBy(user: ReturnType<typeof userEvent.setup>, term: string) {
      await user.type(screen.getByPlaceholderText("Filter outliner..."), term);
    }

    it("keeps a folder whose name matches", async () => {
      const user = userEvent.setup();
      renderSidebar();

      await filterBy(user, "brack");

      expect(screen.getByText("Brackets")).toBeInTheDocument();
    });

    it("keeps the ancestors of a match so it can be reached", async () => {
      // A hit nobody can navigate to is a hit nobody can use.
      const user = userEvent.setup();
      renderSidebar();

      await filterBy(user, "brack");

      expect(screen.getByText("Parts")).toBeInTheDocument();
    });

    it("drops a folder that matches nothing", async () => {
      const user = userEvent.setup();
      renderSidebar();

      await filterBy(user, "brack");

      expect(screen.queryByText("Toys")).toBeNull();
    });

    it("keeps a folder holding a matching model", async () => {
      const user = userEvent.setup();
      renderSidebar({ models: [outlinerModel()] });

      await filterBy(user, "benchy");

      expect(screen.getByText("Parts")).toBeInTheDocument();
    });
  });

  describe("narrowing the tree by facet", () => {
    it("keeps the folder holding a filtered model", () => {
      // A tag filter arrives with the model list already narrowed, so the tree
      // shows where those models actually live rather than the whole library.
      renderSidebar({ selectedTags: ["functional"], models: [outlinerModel()] });

      expect(screen.getAllByText("Parts").length).toBeGreaterThan(0);
    });

    it("drops a folder holding none of them", () => {
      renderSidebar({ selectedTags: ["functional"], models: [outlinerModel()] });

      expect(screen.queryByText("Toys")).toBeNull();
    });
  });

  describe("the printer filter", () => {
    it("offers every location by default", () => {
      renderSidebar();

      expect(screen.getByRole("button", { name: /Any location/ })).toBeInTheDocument();
    });

    it("reports a switch to models on no printer", async () => {
      const user = userEvent.setup();
      const { onPrinterPresenceChange } = renderSidebar();

      await user.click(screen.getByRole("button", { name: /Vault only/ }));

      expect(onPrinterPresenceChange).toHaveBeenCalledWith("none");
    });

    it("reports a switch to models on any printer", async () => {
      const user = userEvent.setup();
      const { onPrinterPresenceChange } = renderSidebar();

      await user.click(screen.getByRole("button", { name: /On a printer/ }));

      expect(onPrinterPresenceChange).toHaveBeenCalledWith("any");
    });

    it("hides the printer filter from someone who cannot see printers", () => {
      renderSidebar({ canViewPrinters: false });

      expect(screen.queryByRole("button", { name: /Any location/ })).toBeNull();
    });
  });

  describe("creating a folder", () => {
    it("asks the caller to open its form", async () => {
      const user = userEvent.setup();
      const { onCreateCollection } = renderSidebar();

      await user.click(screen.getByRole("button", { name: /New collection|Create Collection/i }));

      expect(onCreateCollection).toHaveBeenCalledTimes(1);
    });
  });

  describe("while the library is loading", () => {
    it("keeps the outliner usable rather than emptying", () => {
      // An empty sidebar and a loading one look identical, and the first reads
      // as "you have no collections".
      renderSidebar({ loading: true, collections: [] });

      expect(screen.getByPlaceholderText("Filter outliner...")).toBeInTheDocument();
    });
  });
  describe("deleting a folder", () => {
    it("asks before deleting", async () => {
      const user = userEvent.setup();
      const { onDeleteCollection } = renderSidebar();

      await user.click(screen.getAllByTitle("Delete collection")[0]);

      expect(onDeleteCollection).not.toHaveBeenCalled();
    });

    it("names the folder it is about to delete", async () => {
      // The rows are dense and identically shaped; a confirmation that does not
      // name the folder is a confirmation the user cannot check.
      const user = userEvent.setup();
      renderSidebar();

      await user.click(screen.getAllByTitle("Delete collection")[0]);

      expect(await screen.findByText(/Delete “Parts”\?/)).toBeInTheDocument();
    });

    it("warns that a folder with models is not empty", async () => {
      // Deleting one sends its models to the recycle bin; a bare "Delete?" hides
      // that entirely.
      const user = userEvent.setup();
      renderSidebar();

      await user.click(screen.getAllByTitle("Delete collection")[0]);

      expect(await screen.findByText(/models → recycle bin/)).toBeInTheDocument();
    });

    it("deletes the folder once confirmed", async () => {
      const user = userEvent.setup();
      const { onDeleteCollection } = renderSidebar();
      await user.click(screen.getAllByTitle("Delete collection")[0]);

      await user.click(await screen.findByRole("button", { name: "Delete" }));

      expect(onDeleteCollection).toHaveBeenCalledWith(1, true);
    });

    it("says a folder with nothing in it takes nothing with it", async () => {
      const user = userEvent.setup();
      renderSidebar({
        collections: [
          aCollection({ id: 9, name: "Empty", path: "empty", parent_id: null, model_count: 0 }),
        ],
      });

      await user.click(screen.getByTitle("Delete collection"));

      expect(screen.queryByText(/recycle bin/)).toBeNull();
    });

    it("backs out of the confirmation", async () => {
      const user = userEvent.setup();
      const { onDeleteCollection } = renderSidebar();
      await user.click(screen.getAllByTitle("Delete collection")[0]);

      await user.click(await screen.findByRole("button", { name: "Cancel" }));

      expect(onDeleteCollection).not.toHaveBeenCalled();
    });
  });

  describe("remembering the open folders", () => {
    it("hides a folder's children once it is collapsed", async () => {
      const user = userEvent.setup();
      renderSidebar();

      await user.click(screen.getAllByRole("button", { name: "Collapse" })[0]);

      await waitFor(() => expect(screen.queryByText("Brackets")).toBeNull());
    });

    it("reopens a folder the user expanded again", async () => {
      // Re-collapsing the tree on every navigation makes a deep vault unusable.
      const user = userEvent.setup();
      renderSidebar();
      await user.click(screen.getAllByRole("button", { name: "Collapse" })[0]);

      await user.click(await screen.findByRole("button", { name: "Expand" }));

      expect(await screen.findByText("Brackets")).toBeInTheDocument();
    });

    it("carries the open folders into the next visit", async () => {
      const user = userEvent.setup();
      renderSidebar();

      await user.click(screen.getAllByRole("button", { name: "Collapse" })[0]);

      await waitFor(() =>
        expect(window.sessionStorage.getItem("ps-filter-expanded")).not.toContain("parts"),
      );
    });

    it("opens the ancestors of the folder the user is in", async () => {
      // Landing in a nested folder with the tree collapsed leaves the user with
      // no idea where they are.
      renderSidebar({ selectedCollection: "parts/brackets" });

      expect(screen.getByText("Brackets")).toBeInTheDocument();
    });

    it("remembers that the model group was collapsed", async () => {
      const user = userEvent.setup();
      renderSidebar({ models: [outlinerModel({ id: 5, collection: null, collection_id: null })] });

      await user.click(screen.getAllByRole("button", { name: "Collapse" })[0]);

      await waitFor(() =>
        expect(window.sessionStorage.getItem("ps-filter-all-expanded")).toBe("false"),
      );
    });
  });
  describe("filtering by tag", () => {
    it("adds the tag the user clicked", async () => {
      const user = userEvent.setup();
      const { onTagsChange } = renderSidebar();

      await user.click(await screen.findByRole("button", { name: /functional/ }));

      expect(onTagsChange).toHaveBeenCalledWith(["functional"]);
    });

    it("takes a tag back off when it is clicked again", async () => {
      // The chip is the only way to remove it from here; without the toggle a
      // user has to clear every filter to drop one tag.
      const user = userEvent.setup();
      const { onTagsChange } = renderSidebar({ selectedTags: ["functional"] });

      await user.click(await screen.findByRole("button", { name: /functional/ }));

      expect(onTagsChange).toHaveBeenCalledWith([]);
    });

    it("keeps the other tags when one is removed", async () => {
      const user = userEvent.setup();
      const { onTagsChange } = renderSidebar({
        tags: [aTag(), aTag({ id: 2, name: "bracket", slug: "bracket" })],
        selectedTags: ["functional", "bracket"],
      });

      await user.click(await screen.findByRole("button", { name: /functional/ }));

      expect(onTagsChange).toHaveBeenCalledWith(["bracket"]);
    });
  });

  describe("resizing the sidebar", () => {
    it("remembers the width across visits", async () => {
      // The tree is the primary navigation for a deep vault; a width that
      // resets on every page load is a width nobody bothers setting.
      const { container } = renderSidebar();
      const handle = container.ownerDocument.querySelector(".cursor-col-resize")!;

      fireEvent.mouseDown(handle, { clientX: 200 });
      fireEvent.mouseMove(document, { clientX: 260 });
      fireEvent.mouseUp(document);

      await waitFor(() => expect(window.localStorage.getItem("ps-sidebar-width")).not.toBeNull());
    });

    it("stops resizing once the pointer is released", async () => {
      // Leaving the listeners attached makes every later mouse move drag the
      // sidebar, which reads as the page being possessed.
      const { container } = renderSidebar();
      const handle = container.ownerDocument.querySelector(".cursor-col-resize")!;
      fireEvent.mouseDown(handle, { clientX: 200 });
      fireEvent.mouseMove(document, { clientX: 260 });
      fireEvent.mouseUp(document);
      const settled = window.localStorage.getItem("ps-sidebar-width");

      fireEvent.mouseMove(document, { clientX: 400 });

      expect(window.localStorage.getItem("ps-sidebar-width")).toBe(settled);
    });
  });
});
