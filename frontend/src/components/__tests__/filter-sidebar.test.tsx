/*
 * Lazy library navigation through real query hooks and HTTP fixtures.
 * Counts describe the whole visible branch; pages describe only downloaded
 * rows. Global name search is independent of which branches are open.
 */
import { queryKeys } from "@/lib/query-client";
import { outlinerRoutes } from "@/test-support/outliner";

import "@testing-library/jest-dom/vitest";
import { useState } from "react";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FilterSidebar, type FilterSidebarProps } from "@/components/filter-sidebar";
import { collectionTreeRoutes } from "@/test-support/collection-tree";
import {
  aOutlinerModel,
  aCollection,
  aCollectionNode,
  aPrinter,
  aTag,
} from "@/test-support/factories";
import { json, renderApp, type RouteTable } from "@/test-support/render";
import type { CollectionRead, MultipartModelListItem, OutlinerModelRead } from "@/types";

const TREE = [
  aCollection({ id: 1, name: "Parts", path: "parts", parent_id: null }),
  aCollection({ id: 2, name: "Brackets", path: "parts/brackets", parent_id: 1 }),
  aCollection({ id: 3, name: "Toys", path: "toys", parent_id: null }),
];

function multipartSet(over: Partial<MultipartModelListItem> = {}): MultipartModelListItem {
  return {
    edit_epoch: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    edit_version: 1,
    id: 40,
    name: "Dragon figure",
    slug: "dragon-figure",
    description: null,
    collection: null,
    collection_id: null,
    collection_label: null,
    part_count: 2,
    model_count: 1,
    guide_count: 0,
    cover_model_id: 1,
    cover_image_url: null,
    cover_image_uploaded: false,
    cover_thumbnail_url: null,
    starred: false,
    member_model_ids: [1],
    tags: [],
    effective_role: "admin",
    updated_at: "2026-01-02T00:00:00Z",
    ...over,
  };
}

/**
 * Render the sidebar over a library. The tree is read from the server a level at
 * a time, so the library is what the tree routes answer from, not a prop.
 */
function renderSidebar({
  collections = TREE,
  models = [],
  multipartModels = [],
  routes = {},
  ...over
}: Partial<FilterSidebarProps> & {
  collections?: CollectionRead[];
  models?: OutlinerModelRead[];
  multipartModels?: MultipartModelListItem[];
  routes?: RouteTable;
} = {}) {
  const handlers = {
    onCollectionChange: vi.fn<FilterSidebarProps["onCollectionChange"]>(),
    onTagsChange: vi.fn<FilterSidebarProps["onTagsChange"]>(),
    onPrinterChange: vi.fn<FilterSidebarProps["onPrinterChange"]>(),
    onPrinterPresenceChange: vi.fn<FilterSidebarProps["onPrinterPresenceChange"]>(),
    onCreateCollection: vi.fn<FilterSidebarProps["onCreateCollection"]>(),
    onMoveModel: vi.fn<NonNullable<FilterSidebarProps["onMoveModel"]>>(),
    onMoveCollection: vi.fn<NonNullable<FilterSidebarProps["onMoveCollection"]>>(),
    onDeleteCollection: vi.fn<NonNullable<FilterSidebarProps["onDeleteCollection"]>>(),
    onLibraryViewChange: vi.fn<FilterSidebarProps["onLibraryViewChange"]>(),
  };
  // Model leaves are links into the vault, so the tree needs a router even
  // though nothing here navigates.
  function SidebarHarness() {
    const [selectedCollection, setSelectedCollection] = useState(over.selectedCollection ?? null);
    return (
      <>
        <output aria-label="Selected collection">{selectedCollection ?? "All Models"}</output>
        <FilterSidebar
          outlinerFilters={{
            tag: over.selectedTags,
            printer_id: over.selectedPrinterId ?? undefined,
            printer_presence: over.selectedPrinterPresence ?? undefined,
          }}
          tags={[aTag()]}
          printers={[aPrinter({ id: 4, name: "Voron" })]}
          selectedTags={[]}
          selectedPrinterId={null}
          selectedPrinterPresence={null}
          libraryView="all"
          {...handlers}
          {...over}
          selectedCollection={selectedCollection}
          onCollectionChange={(path) => {
            handlers.onCollectionChange(path);
            setSelectedCollection(path);
          }}
        />
      </>
    );
  }
  const result = renderApp(<SidebarHarness />, {
    routes: {
      ...collectionTreeRoutes(collections),
      ...outlinerRoutes(collections, models, multipartModels),
      ...routes,
    },
  });
  return { ...result, ...handlers };
}

beforeEach(() => {
  window.sessionStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

/** The row a folder's name sits in: its expand, delete and badge controls live there. */
async function folderRow(name: string): Promise<HTMLElement> {
  const row = (await screen.findByRole("button", { name })).parentElement;
  if (row === null) throw new Error(`no row for ${name}`);
  return row;
}

/** Open a folder the way a user does, by its chevron. */
async function openFolder(user: ReturnType<typeof userEvent.setup>, name: string) {
  await user.click(within(await folderRow(name)).getByRole("button", { name: "Expand" }));
}

describe("FilterSidebar", () => {
  it("keeps the gesture version when an outliner read changes during dragging", async () => {
    const user = userEvent.setup();
    const models = [aOutlinerModel({ id: 1, name: "Original model", edit_version: 7 })];
    const view = renderSidebar({ models });
    await openFolder(user, "Parts");
    const source = await screen.findByRole("button", { name: "Original model" });
    const destination = await folderRow("Toys");
    // jsdom has no geometry. Give the actual mouse sensor two separated hit regions.
    const sourceRect = vi
      .spyOn(source, "getBoundingClientRect")
      .mockReturnValue(new DOMRect(0, 0, 100, 20));
    const destinationRect = vi
      .spyOn(destination, "getBoundingClientRect")
      .mockReturnValue(new DOMRect(0, 100, 100, 20));
    try {
      fireEvent.mouseDown(source, { button: 0, buttons: 1, clientX: 10, clientY: 10 });
      fireEvent.mouseMove(document, { buttons: 1, clientX: 20, clientY: 10 });
      await waitFor(() => expect(source).toHaveAttribute("aria-pressed", "true"));
      models[0] = aOutlinerModel({ id: 1, name: "Changed model", edit_version: 8 });
      await act(async () => view.client.invalidateQueries({ queryKey: queryKeys.outliner }));
      await screen.findByRole("button", { name: "Changed model" });
      fireEvent.mouseMove(document, { buttons: 1, clientX: 20, clientY: 110 });
      fireEvent.mouseUp(document, { button: 0, clientX: 20, clientY: 110 });
      expect(view.onMoveModel).toHaveBeenCalledWith(
        expect.objectContaining({ id: 1, name: "Original model", edit_version: 7 }),
        "toys",
      );
    } finally {
      sourceRect.mockRestore();
      destinationRect.mockRestore();
    }
  });
  describe("the folder tree", () => {
    it("lists the root folders", async () => {
      renderSidebar();

      expect(await screen.findByText("Parts")).toBeInTheDocument();
      expect(screen.getByText("Toys")).toBeInTheDocument();
    });

    it("offers the whole library as a destination", () => {
      renderSidebar();

      expect(screen.getByLabelText("All Models")).toBeInTheDocument();
    });

    it("reports the folder the user chose", async () => {
      const user = userEvent.setup();
      const { onCollectionChange } = renderSidebar();

      await user.click(await screen.findByText("Parts"));

      expect(onCollectionChange).toHaveBeenCalledWith("parts");
    });

    it("keeps a folder open after a double-click", async () => {
      const user = userEvent.setup();
      renderSidebar();

      await user.dblClick(await screen.findByRole("button", { name: "Parts" }));

      expect(screen.getByRole("status", { name: "Selected collection" })).toHaveTextContent(
        "parts",
      );
    });

    it("keeps the selected folder open on another click", async () => {
      const user = userEvent.setup();
      renderSidebar({ selectedCollection: "parts" });

      await user.click(await screen.findByRole("button", { name: "Parts" }));

      expect(screen.getByRole("status", { name: "Selected collection" })).toHaveTextContent(
        "parts",
      );
    });

    it("returns to the whole library from the root entry", async () => {
      const user = userEvent.setup();
      const { onCollectionChange } = renderSidebar({ selectedCollection: "parts" });

      await user.click(screen.getByLabelText("All Models"));

      expect(onCollectionChange).toHaveBeenCalledWith(null);
    });

    it("nests a child folder under its parent", async () => {
      const user = userEvent.setup();
      renderSidebar();

      await openFolder(user, "Parts");

      expect(await screen.findByText("Brackets")).toBeInTheDocument();
    });

    it("asks for a folder's children only once it is opened", async () => {
      // Loading every level up front is what took a 9,000-folder library a
      // minute to open (#295).
      const user = userEvent.setup();
      const { requests } = renderSidebar();
      await screen.findByText("Parts");
      const childRequests = () =>
        requests().filter(
          (call) =>
            call.url.startsWith("/api/v1/outliner/collections") &&
            new URL(call.url, "http://test").searchParams.get("parent_id") === "1",
        );
      expect(childRequests()).toHaveLength(0);

      await openFolder(user, "Parts");

      await waitFor(() => expect(childRequests()).toHaveLength(1));
    });

    it("offers the next page of a long level", async () => {
      const user = userEvent.setup();
      const page = (name: string, id: number, cursor: string | null) =>
        json({
          items: [
            {
              ...aCollectionNode({ id, name, path: name.toLowerCase(), display_path: name }),
              direct_entry_count: 0,
              subtree_entry_count: 0,
              visible_child_count: 0,
            },
          ],
          parent_direct_entry_count: 0,
          revealed: null,
          next_cursor: cursor,
        });
      renderApp(
        <FilterSidebar
          outlinerFilters={{}}
          tags={[]}
          printers={[]}
          selectedCollection={null}
          selectedTags={[]}
          selectedPrinterId={null}
          selectedPrinterPresence={null}
          libraryView="all"
          onCollectionChange={vi.fn<FilterSidebarProps["onCollectionChange"]>()}
          onTagsChange={vi.fn<FilterSidebarProps["onTagsChange"]>()}
          onPrinterChange={vi.fn<FilterSidebarProps["onPrinterChange"]>()}
          onPrinterPresenceChange={vi.fn<FilterSidebarProps["onPrinterPresenceChange"]>()}
          onCreateCollection={vi.fn<FilterSidebarProps["onCreateCollection"]>()}
          onLibraryViewChange={vi.fn<FilterSidebarProps["onLibraryViewChange"]>()}
        />,
        {
          routes: {
            "GET /api/v1/outliner/collections": (url) =>
              url.includes("cursor=") ? page("Toys", 2, null) : page("Parts", 1, "next"),
          },
        },
      );

      await user.click(await screen.findByRole("button", { name: "Show more folders" }));

      expect(await screen.findByText("Toys")).toBeInTheDocument();
    });

    it("shows a root multipart set", async () => {
      renderSidebar({ multipartModels: [multipartSet()] });

      expect(await screen.findByText("Dragon figure")).toBeInTheDocument();
    });

    it("nests a multipart set in its folder", async () => {
      const user = userEvent.setup();
      renderSidebar({
        multipartModels: [
          multipartSet({ collection: "parts", collection_id: 1, collection_label: "Parts" }),
        ],
      });

      await openFolder(user, "Parts");

      expect(await screen.findByText("Dragon figure")).toBeInTheDocument();
    });

    it("counts a multipart set in its folder", async () => {
      renderSidebar({
        collections: [aCollection({ model_count: 0 })],
        multipartModels: [
          multipartSet({ collection: "parts", collection_id: 1, collection_label: "Parts" }),
        ],
      });

      expect(await folderRow("Parts")).toHaveTextContent("Parts1");
    });

    it("shows parent totals that include models in nested folders", async () => {
      const user = userEvent.setup();
      renderSidebar({
        collections: [
          aCollection({ id: 1, name: "Parent", path: "parent", parent_id: null, model_count: 4 }),
          aCollection({ id: 2, name: "Child", path: "parent/child", parent_id: 1, model_count: 3 }),
          aCollection({
            id: 3,
            name: "Grandchild",
            path: "parent/child/grandchild",
            parent_id: 2,
            model_count: 2,
          }),
        ],
      });

      await openFolder(user, "Parent");

      expect(await folderRow("Parent")).toHaveTextContent("Parent4");
      expect(await folderRow("Child")).toHaveTextContent("Child3");
    });

    it("uses the collection total when only part of a folder is loaded", async () => {
      renderSidebar({
        collections: [aCollection({ id: 1, name: "Archive", path: "archive", model_count: 501 })],
        models: [aOutlinerModel({ collection: "archive", collection_id: 1 })],
      });

      expect(await folderRow("Archive")).toHaveTextContent("Archive501");
    });

    it("includes multipart sets stored under child folders in the parent total", async () => {
      renderSidebar({
        collections: [
          aCollection({ id: 1, name: "Parent", path: "parent", parent_id: null, model_count: 0 }),
          aCollection({ id: 2, name: "Child", path: "parent/child", parent_id: 1, model_count: 0 }),
        ],
        multipartModels: [
          multipartSet({
            collection: "parent/child",
            collection_id: 2,
            collection_label: "Parent/Child",
          }),
        ],
      });

      expect(await folderRow("Parent")).toHaveTextContent("Parent1");
    });

    it("shows the location of a matching model", async () => {
      const user = userEvent.setup();
      const inChild = {
        collection: "parent/child",
        collection_id: 2,
        collection_label: "Parent/Child",
      };
      renderSidebar({
        collections: [
          aCollection({ id: 1, name: "Parent", path: "parent", parent_id: null, model_count: 3 }),
          aCollection({ id: 2, name: "Child", path: "parent/child", parent_id: 1, model_count: 2 }),
        ],
        models: [
          aOutlinerModel({
            id: 1,
            name: "Other",
            collection: "parent",
            collection_id: 1,
            collection_label: "Parent",
          }),
          aOutlinerModel({ id: 2, name: "Match", ...inChild }),
          aOutlinerModel({ id: 3, name: "Another", ...inChild }),
        ],
      });

      await user.type(screen.getByPlaceholderText("Filter outliner..."), "Match");

      expect(await screen.findByText("Match")).toBeInTheDocument();
      expect(screen.getByText("Parent/Child")).toBeInTheDocument();
      expect(screen.queryByText("Other")).toBeNull();
    });

    it("counts only Multipart Models in the Multipart view", async () => {
      renderSidebar({
        collections: [
          aCollection({ id: 1, name: "Parent", path: "parent", parent_id: null, model_count: 5 }),
          aCollection({ id: 2, name: "Child", path: "parent/child", parent_id: 1, model_count: 5 }),
        ],
        multipartModels: [
          multipartSet({
            collection: "parent/child",
            collection_id: 2,
            collection_label: "Parent/Child",
          }),
        ],
        libraryView: "multipart",
      });

      expect(await folderRow("Parent")).toHaveTextContent("Parent1");
    });

    it("folds a branch away on request", async () => {
      // A deep library is unscannable fully expanded, so a parent has to be
      // collapsible without losing the selection inside it.
      const user = userEvent.setup();
      renderSidebar();
      await openFolder(user, "Parts");
      await screen.findByText("Brackets");

      await user.click(within(await folderRow("Parts")).getByRole("button", { name: "Collapse" }));

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

      expect(await screen.findByText("Brackets")).toBeInTheDocument();
    });

    it("shows the ancestor path of a match", async () => {
      // A hit nobody can navigate to is a hit nobody can use.
      const user = userEvent.setup();
      renderSidebar();

      await filterBy(user, "brack");

      expect(await screen.findByText("Parts/Brackets")).toBeInTheDocument();
    });

    it("drops a folder that matches nothing", async () => {
      const user = userEvent.setup();
      renderSidebar();

      await filterBy(user, "brack");
      await screen.findByText("Brackets");

      expect(screen.queryByText("Toys")).toBeNull();
    });

    it("keeps a folder holding a matching model", async () => {
      const user = userEvent.setup();
      renderSidebar({ models: [aOutlinerModel()] });

      await filterBy(user, "benchy");

      expect(await screen.findByText("Parts")).toBeInTheDocument();
    });

    it("finds a multipart set by name", async () => {
      const user = userEvent.setup();
      renderSidebar({
        multipartModels: [
          multipartSet({ collection: "parts", collection_id: 1, collection_label: "Parts" }),
        ],
      });

      await filterBy(user, "dragon");

      expect(await screen.findByText("Dragon figure")).toBeInTheDocument();
    });
  });

  describe("narrowing the tree by facet", () => {
    it("keeps the folder holding a filtered model", async () => {
      // A tag filter arrives with the model list already narrowed, so the tree
      // shows where those models actually live rather than the whole library.
      renderSidebar({ selectedTags: ["functional"], models: [aOutlinerModel()] });

      expect((await screen.findAllByText("Parts")).length).toBeGreaterThan(0);
    });

    it("drops a folder holding none of them", () => {
      renderSidebar({ selectedTags: ["functional"], models: [aOutlinerModel()] });

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

      await user.click((await screen.findAllByTitle("Delete collection"))[0]);

      expect(onDeleteCollection).not.toHaveBeenCalled();
    });

    it("names the folder it is about to delete", async () => {
      // The rows are dense and identically shaped; a confirmation that does not
      // name the folder is a confirmation the user cannot check.
      const user = userEvent.setup();
      renderSidebar();

      await user.click((await screen.findAllByTitle("Delete collection"))[0]);

      expect(await screen.findByText(/Delete “Parts”\?/)).toBeInTheDocument();
    });

    it("warns that a folder with models is not empty", async () => {
      // Deleting one sends its models to the recycle bin; a bare "Delete?" hides
      // that entirely.
      const user = userEvent.setup();
      renderSidebar();

      await user.click((await screen.findAllByTitle("Delete collection"))[0]);

      expect(await screen.findByText(/models → recycle bin/)).toBeInTheDocument();
    });

    it("counts every folder nested at any depth beneath it", async () => {
      const user = userEvent.setup();
      renderSidebar({
        collections: [
          ...TREE,
          aCollection({ id: 4, name: "Small", path: "parts/brackets/small", parent_id: 2 }),
        ],
      });

      await user.click((await screen.findAllByTitle("Delete collection"))[0]);

      expect(await screen.findByText("2 subcollections")).toBeInTheDocument();
    });

    it("deletes the folder once confirmed", async () => {
      const user = userEvent.setup();
      const { onDeleteCollection } = renderSidebar();
      await user.click((await screen.findAllByTitle("Delete collection"))[0]);

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

      await user.click(await screen.findByTitle("Delete collection"));

      expect(screen.queryByText(/recycle bin/)).toBeNull();
    });

    it("backs out of the confirmation", async () => {
      const user = userEvent.setup();
      const { onDeleteCollection } = renderSidebar();
      await user.click((await screen.findAllByTitle("Delete collection"))[0]);

      await user.click(await screen.findByRole("button", { name: "Cancel" }));

      expect(onDeleteCollection).not.toHaveBeenCalled();
    });
  });

  describe("remembering the open folders", () => {
    it("keeps a restored branch usable while a newly opened branch is pending", async () => {
      const user = userEvent.setup();
      const slow = Promise.withResolvers<Response>();
      const started = Promise.withResolvers<void>();
      const fastModel = aOutlinerModel({
        id: 100,
        name: "Restored bracket",
        collection: "parts",
        collection_id: 1,
      });
      const slowModel = aOutlinerModel({
        id: 200,
        name: "Pending toy",
        collection: "toys",
        collection_id: 3,
      });
      sessionStorage.setItem("ps-filter-expanded", JSON.stringify(["parts"]));
      renderSidebar({
        models: [fastModel, slowModel],
        routes: {
          "GET /api/v1/outliner/entries": (url) => {
            const id = new URL(url, "http://test").searchParams.get("collection_id");
            if (id === "3") {
              started.resolve();
              return slow.promise;
            }
            return json({
              items: id === "1" ? [{ ...fastModel, kind: "model" }] : [],
              next_cursor: null,
            });
          },
        },
      });
      try {
        await screen.findByRole("button", { name: "Restored bracket" });
        await openFolder(user, "Toys");
        await started.promise;
        expect(await screen.findByRole("button", { name: "Restored bracket" })).toBeVisible();
        expect(screen.queryByRole("button", { name: "Pending toy" })).not.toBeInTheDocument();

        await user.click(screen.getByRole("button", { name: "Parts" }));

        expect(screen.getByLabelText("Selected collection")).toHaveTextContent("parts");
        expect(await screen.findByRole("button", { name: "Restored bracket" })).toBeVisible();
      } finally {
        await act(async () =>
          slow.resolve(json({ items: [{ ...slowModel, kind: "model" }], next_cursor: null })),
        );
      }
      expect(await screen.findByRole("button", { name: "Pending toy" })).toBeVisible();
    });

    it("starts a first visit at the top level", async () => {
      // A tree that loads a level at a time cannot open a whole library, and
      // opening a large one whole is what took a minute (#295).
      renderSidebar();
      await screen.findByText("Parts");

      expect(screen.queryByText("Brackets")).toBeNull();
    });

    it("hides a folder's children once it is collapsed", async () => {
      const user = userEvent.setup();
      renderSidebar();
      await openFolder(user, "Parts");
      await screen.findByText("Brackets");

      await user.click(within(await folderRow("Parts")).getByRole("button", { name: "Collapse" }));

      await waitFor(() => expect(screen.queryByText("Brackets")).toBeNull());
    });

    it("reopens a folder the user expanded again", async () => {
      // Re-collapsing the tree on every navigation makes a deep vault unusable.
      const user = userEvent.setup();
      renderSidebar();
      await openFolder(user, "Parts");
      await user.click(within(await folderRow("Parts")).getByRole("button", { name: "Collapse" }));

      await openFolder(user, "Parts");

      expect(await screen.findByText("Brackets")).toBeInTheDocument();
    });

    it("carries the open folders into the next visit", async () => {
      const user = userEvent.setup();
      renderSidebar();

      await openFolder(user, "Parts");

      await waitFor(() =>
        expect(window.sessionStorage.getItem("ps-filter-expanded")).toContain("parts"),
      );
    });

    it("opens the ancestors of the folder the user is in", async () => {
      // Landing in a nested folder with the tree collapsed leaves the user with
      // no idea where they are.
      renderSidebar({ selectedCollection: "parts/brackets" });

      expect(await screen.findByText("Brackets")).toBeInTheDocument();
    });

    it("remembers that the model group was collapsed", async () => {
      const user = userEvent.setup();
      renderSidebar({ models: [aOutlinerModel({ id: 5, collection: null, collection_id: null })] });

      await user.click((await screen.findAllByRole("button", { name: "Collapse" }))[0]);

      await waitFor(() =>
        expect(window.sessionStorage.getItem("ps-filter-all-expanded")).toBe("false"),
      );
    });
  });

  describe("library views", () => {
    it("switches presentation without replacing the library sidebar", async () => {
      const user = userEvent.setup();
      const { onLibraryViewChange } = renderSidebar();

      await user.click(screen.getByRole("button", { name: "Multipart sets only" }));

      expect(onLibraryViewChange).toHaveBeenCalledWith("multipart");
    });

    it("exposes only the supported library view controls", () => {
      renderSidebar({ models: [aOutlinerModel()], multipartModels: [multipartSet()] });

      expect(screen.getByRole("button", { name: "Everything" })).toBeVisible();
      expect(screen.getByRole("button", { name: "Multipart sets only" })).toBeVisible();
      expect(screen.queryByRole("button", { name: "Organized" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Parts only" })).not.toBeInTheDocument();
    });

    it("shows referenced models in Everything", async () => {
      renderSidebar({
        models: [aOutlinerModel({ collection: null, collection_id: null })],
        multipartModels: [multipartSet()],
        libraryView: "all",
      });

      expect(await screen.findByText("Benchy")).toBeInTheDocument();
    });

    it("shows unrelated models in Everything", async () => {
      renderSidebar({
        models: [aOutlinerModel({ id: 2, collection: null, collection_id: null })],
        multipartModels: [multipartSet()],
        libraryView: "all",
      });

      expect(await screen.findByText("Benchy")).toBeInTheDocument();
    });

    it("hides regular models in the multipart-only view", () => {
      renderSidebar({
        models: [aOutlinerModel({ collection: null, collection_id: null })],
        multipartModels: [multipartSet()],
        libraryView: "multipart",
      });

      expect(screen.queryByText("Benchy")).toBeNull();
    });
  });

  describe("filtering by tag", () => {
    it("keeps tag names in their original case", () => {
      renderSidebar({ tags: [aTag({ name: "Mixed Case" })] });

      expect(screen.getByRole("button", { name: /Mixed Case/ })).not.toHaveClass("uppercase");
    });

    it("includes multipart sets in shared tag counts", () => {
      renderSidebar({
        tags: [aTag({ model_count: 3, multipart_model_count: 2 })],
      });

      expect(screen.getByRole("button", { name: /functional/ })).toHaveTextContent("5");
    });

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

describe("outliner pages", () => {
  const models = Array.from({ length: 51 }, (_, i) =>
    aOutlinerModel({ id: i + 1, name: `Part ${String(i).padStart(3, "0")}` }),
  );

  it("reuses pages of the opened branch after reopening", async () => {
    const user = userEvent.setup();
    const app = renderSidebar({ models });
    await screen.findByText("Parts");
    expect(app.requests().filter((r) => r.url.includes("/outliner/entries"))).toHaveLength(0);
    await openFolder(user, "Parts");
    await screen.findByText("Part 000");
    expect(screen.queryByText("Part 050")).not.toBeInTheDocument();
    const more = screen.getByRole("button", { name: "Show more models" });
    more.focus();
    await user.keyboard("{Enter}");
    await screen.findByText("Part 050");
    expect(screen.getByRole("button", { name: "All items loaded" })).toHaveFocus();
    const requests = app.requests().filter((r) => r.url.includes("/outliner/entries"));
    expect(requests).toHaveLength(2);
    expect(
      requests.every(
        (r) => new URL(r.url, "http://test").searchParams.get("collection_id") === "1",
      ),
    ).toBe(true);
    await user.click(within(await folderRow("Parts")).getByRole("button", { name: "Collapse" }));
    await openFolder(user, "Parts");
    await screen.findByText("Part 050");
    expect(app.requests().filter((r) => r.url.includes("/outliner/entries"))).toHaveLength(2);
  });

  it("keeps an opened branch mounted when selecting its child", async () => {
    const user = userEvent.setup();
    const app = renderSidebar({ models });
    await openFolder(user, "Parts");
    const parent = await screen.findByRole("button", { name: "Parts" });
    const child = await screen.findByRole("button", { name: "Brackets" });
    const leaf = await screen.findByRole("button", { name: "Part 000" });

    await user.click(child);
    await waitFor(() => expect(app.client.isFetching()).toBe(0));

    expect(parent).toBeInTheDocument();
    expect(child).toBeInTheDocument();
    expect(leaf).toBeInTheDocument();
  });

  it("retains downloaded sibling pages when changing selection", async () => {
    const user = userEvent.setup();
    const collections = Array.from({ length: 51 }, (_, index) =>
      aCollection({
        id: index + 1,
        name: `Folder ${String(index).padStart(3, "0")}`,
        path: `folder-${index}`,
        parent_id: null,
      }),
    );
    const app = renderSidebar({ collections });
    await user.click(await screen.findByRole("button", { name: "Show more folders" }));
    const last = await screen.findByRole("button", { name: "Folder 050" });
    const first = screen.getByRole("button", { name: "Folder 000" });

    await user.click(last);
    await waitFor(() => expect(app.client.isFetching()).toBe(0));
    await user.click(first);
    await waitFor(() => expect(app.client.isFetching()).toBe(0));

    expect(last).toBeInTheDocument();
    expect(first).toBeInTheDocument();
    expect(
      app.requests().filter((request) => request.url.includes("/outliner/collections")),
    ).toHaveLength(2);
  });

  it("recovers a failed continuation without losing loaded rows", async () => {
    const user = userEvent.setup();
    const app = renderSidebar({ models });
    await openFolder(user, "Parts");
    await screen.findByText("Part 000");
    app.route({ "GET /api/v1/outliner/entries": json({ detail: "temporary" }, 503) });
    await user.click(screen.getByRole("button", { name: "Show more models" }));
    await screen.findByText("Could not load this list.");
    expect(screen.getByText("Part 000")).toBeInTheDocument();
    app.route(outlinerRoutes(TREE, models));
    await user.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByText("Part 050");
    expect(screen.getAllByText("Part 000")).toHaveLength(1);
  });

  it("shows an initial failure with retry instead of pretending the branch is empty", async () => {
    const user = userEvent.setup();
    const app = renderSidebar({ models });
    await screen.findByText("Parts");
    app.route({ "GET /api/v1/outliner/entries": json({ detail: "temporary" }, 503) });
    await openFolder(user, "Parts");
    await screen.findByText("Could not load this list.");
    app.route(outlinerRoutes(TREE, models));
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Part 000")).toBeInTheDocument();
  });

  it("keeps downloaded folders visible during a location reveal", async () => {
    const pending = Promise.withResolvers<Response>();
    const app = renderSidebar({
      selectedCollection: "toys",
      routes: {
        "GET /api/v1/outliner/collections": (url) => {
          if (url.includes("reveal_id=")) return pending.promise;
          return json({
            items: [
              {
                ...aCollectionNode({ id: 1, name: "Parts", path: "parts" }),
                direct_entry_count: 0,
                subtree_entry_count: 0,
                visible_child_count: 0,
              },
            ],
            next_cursor: null,
            parent_direct_entry_count: 0,
            revealed: null,
          });
        },
      },
    });
    const folder = await screen.findByRole("button", { name: "Parts" });
    await waitFor(() =>
      expect(app.requests().some((request) => request.url.includes("reveal_id=3"))).toBe(true),
    );

    expect(folder).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Toys" })).not.toBeInTheDocument();
    await act(async () =>
      pending.resolve(
        json({
          items: [],
          next_cursor: null,
          parent_direct_entry_count: 0,
          revealed: {
            ...aCollectionNode({ id: 3, name: "Toys", path: "toys" }),
            direct_entry_count: 0,
            subtree_entry_count: 0,
            visible_child_count: 0,
          },
        }),
      ),
    );
    await screen.findByRole("button", { name: "Toys" });
    expect(folder).toBeInTheDocument();
  });

  it("recovers a failed location reveal without clearing downloaded folders", async () => {
    const user = userEvent.setup();
    const app = renderSidebar({
      selectedCollection: "toys",
      routes: {
        "GET /api/v1/outliner/collections": (url) => {
          if (url.includes("reveal_id=")) return json({ detail: "temporary" }, 503);
          return json({
            items: [
              {
                ...aCollectionNode({ id: 1, name: "Parts", path: "parts" }),
                direct_entry_count: 0,
                subtree_entry_count: 0,
                visible_child_count: 0,
              },
            ],
            next_cursor: null,
            parent_direct_entry_count: 0,
            revealed: null,
          });
        },
      },
    });
    const folder = await screen.findByRole("button", { name: "Parts" });
    await screen.findByText("Could not load this list.");
    expect(folder).toBeInTheDocument();
    app.route(outlinerRoutes(TREE));

    await user.click(screen.getByRole("button", { name: "Retry" }));

    await screen.findByRole("button", { name: "Toys" });
    expect(folder).toBeInTheDocument();
    expect(screen.queryByText("Could not load this list.")).not.toBeInTheDocument();
  });

  it("reveals a selected location beyond the first sibling page without walking previous pages", async () => {
    const collections = Array.from({ length: 65 }, (_, i) =>
      aCollection({
        id: i + 1,
        name: `Folder ${String(i).padStart(3, "0")}`,
        path: `folder-${i}`,
        parent_id: null,
      }),
    );
    const app = renderSidebar({ collections, selectedCollection: "folder-64" });
    const selected = await screen.findByText("Folder 064");
    expect(selected).toBeVisible();
    expect(selected.closest("button")).toHaveAccessibleName("Folder 064");
    expect(screen.getAllByText("Folder 064")).toHaveLength(1);
    expect(
      app
        .requests()
        .filter((r) => r.url.includes("/outliner/collections"))
        .every((r) => !new URL(r.url, "http://test").searchParams.has("cursor")),
    ).toBe(true);
    await userEvent.click(screen.getByRole("button", { name: "Show more folders" }));
    expect(await screen.findByText("Folder 063")).toBeVisible();
    expect(screen.getAllByText("Folder 064")).toHaveLength(1);
  });

  it("restores the expanded tree after Escape from global search", async () => {
    const user = userEvent.setup();
    renderSidebar({ models, selectedCollection: "parts" });
    await openFolder(user, "Parts");
    await screen.findByText("Part 000");
    const search = screen.getByPlaceholderText("Filter outliner...");
    await user.type(search, "Part 050");
    await screen.findByRole("button", { name: "Part 050" });
    expect(screen.queryByText("Part 000")).not.toBeInTheDocument();
    await user.keyboard("{Escape}");
    await screen.findByText("Part 000");
    expect(screen.getByLabelText("Selected collection")).toHaveTextContent("parts");
  });
});
