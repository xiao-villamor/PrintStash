/*
 * The vault: the page a user spends nearly all of their time on.
 *
 * It is the one screen that owns the whole filter state, and that state lives in
 * the URL rather than in React — a shared link, a bookmark, and the back button
 * all have to reproduce exactly what the person who sent it was looking at. So
 * the tests here drive the URL and assert on the request the grid made, because
 * "the filter is applied" and "the filter reached the server" are different
 * claims and only the second one is what the user sees.
 *
 * The URL is also user-editable, which makes it untrusted input. `?file_type=nonsense`
 * must be dropped rather than forwarded, or the grid asks the API for a value it
 * will reject and the user gets an error page for a typo.
 *
 * Collections are a tree rendered from a flat list, and the two derivations over
 * it — the children of the selected folder, and the breadcrumb trail back to the
 * root — are what make navigation possible at all. A breadcrumb that loses a
 * level strands the user in a folder they cannot leave.
 */

import "@testing-library/jest-dom/vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ModelBrowser } from "@/components/model-grid";
import { MODEL_DND_MIME } from "@/lib/model-dnd";
import { queryKeys } from "@/lib/query-client";
import type { CollectionRead, ModelListItem, SavedViewRead, TagRead } from "@/types";
import { aModelListItem, aPrinter } from "@/test-support/factories";
import {
  adminSession,
  json,
  memberSession,
  renderApp,
  type RenderAppOptions,
} from "@/test-support/render";

function aCollection(override: Partial<CollectionRead> = {}): CollectionRead {
  return {
    id: 1,
    name: "Parts",
    slug: "parts",
    path: "parts",
    parent_id: null,
    model_count: 2,
    effective_role: "admin",
    ...override,
  };
}

function aTag(override: Partial<TagRead> = {}): TagRead {
  return { id: 1, name: "functional", slug: "functional", model_count: 3, ...override };
}

/** The filter set a view stores: every key present, nothing selected. */
const EMPTY_VIEW_FILTERS: SavedViewRead["filters"] = {
  collection: null,
  direct: true,
  tag: [],
  q: null,
  printer_id: null,
  printer_presence: null,
  favorites: false,
  file_type: [],
  material_type: [],
  slicer_name: [],
  printer_model: [],
  revision_status: [],
  print_outcome: [],
  storage: [],
  printed: null,
  uploaded_after: null,
  uploaded_before: null,
};

function aSavedView(override: Partial<SavedViewRead> = {}): SavedViewRead {
  return {
    id: 1,
    name: "PETG only",
    filters: EMPTY_VIEW_FILTERS,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...override,
  };
}

/** No account, so no saved views and no write affordances. */
function signedOutSession() {
  return adminSession({ user: null });
}

const EMPTY_FACETS = {
  file_type: [],
  material_type: [],
  slicer_name: [],
  printer_model: [],
  revision_status: [],
  print_outcome: [],
  storage: [],
};

function renderVault(
  options: RenderAppOptions & {
    models?: ModelListItem[];
    collections?: CollectionRead[];
    tags?: TagRead[];
  } = {},
) {
  const { models = [], collections = [], tags = [], seed = [], routes = {}, ...rest } = options;
  return renderApp(<ModelBrowser />, {
    seed: [
      [queryKeys.collections, collections],
      [queryKeys.tags, tags],
      [queryKeys.vaultStats, { model_count: models.length, file_count: 0, total_size_bytes: 0 }],
      ...seed,
    ],
    routes: {
      "GET /api/v1/models/facets": json(EMPTY_FACETS),
      "GET /api/v1/models/page": json({ items: models, total: models.length, next_cursor: null }),
      "GET /api/v1/models/outliner": json([]),
      "GET /api/v1/models": json(models),
      "GET /api/v1/saved-views": json([]),
      "GET /api/v1/documents": json([]),
      "GET /api/v1/collections": json(collections),
      "GET /api/v1/tags": json(tags),
      ...routes,
    },
    ...rest,
  });
}

/**
 * The toolbar exists twice in the DOM: one bar for phones, one for desktop, with
 * Tailwind `md:` classes hiding whichever does not apply. jsdom applies no CSS,
 * so both are in the accessibility tree and a bare `getByRole` is ambiguous. The
 * desktop bar renders second, and the visible-at-one-width guarantee is checked
 * for real by `tests/e2e/vault.spec.ts`, where CSS is applied.
 */
function sortButton() {
  return screen.getAllByRole("button", { name: "Sort models" }).at(-1)!;
}

function uploadButton() {
  return screen.getAllByRole("button", { name: "Upload" }).at(-1)!;
}

/**
 * The query string of the last *page* request — the one that fetches the grid.
 * The facets and outliner calls share the `/api/v1/models` prefix and carry a
 * different parameter set, so matching the prefix alone reads the wrong request.
 */
function lastModelsQuery(requests: () => { method: string; url: string }[]): URLSearchParams {
  const url = requests()
    .filter((call) => call.method === "GET" && call.url.startsWith("/api/v1/models/page"))
    .at(-1)?.url;
  return new URLSearchParams(url?.split("?")[1] ?? "");
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ModelBrowser", () => {
  describe("listing", () => {
    it("renders a card for every model", async () => {
      renderVault({
        models: [
          aModelListItem({ id: 1, name: "Benchy" }),
          aModelListItem({ id: 2, name: "Cube" }),
        ],
      });

      expect(await screen.findByText("Benchy")).toBeInTheDocument();
      expect(screen.getByText("Cube")).toBeInTheDocument();
    });

    it("offers the empty state when the library has nothing in it", async () => {
      renderVault();

      expect(await screen.findByText("No models found")).toBeInTheDocument();
    });
  });

  describe("filters carried in the URL", () => {
    it("forwards the collection the URL selects", async () => {
      // `?c=` rather than `?collection=`: the short form is what the vault links
      // and the remembered-folder href both write.
      const { requests } = renderVault({ at: "/?c=parts", collections: [aCollection()] });

      await waitFor(() => expect(lastModelsQuery(requests).get("collection")).toBe("parts"));
    });

    it("forwards every tag the URL repeats", async () => {
      const { requests } = renderVault({ at: "/?tag=functional&tag=bracket" });

      await waitFor(() =>
        expect(lastModelsQuery(requests).getAll("tag")).toEqual(["functional", "bracket"]),
      );
    });

    it("forwards a recognised structured filter", async () => {
      const { requests } = renderVault({ at: "/?file_type=stl&file_type=3mf" });

      await waitFor(() =>
        expect(lastModelsQuery(requests).getAll("file_type")).toEqual(["stl", "3mf"]),
      );
    });

    it("drops a structured filter value the API does not accept", async () => {
      // The URL is user-editable, so an unknown value must never be forwarded —
      // the API would reject it and the user would see an error for a typo.
      const { requests } = renderVault({ at: "/?file_type=nonsense&file_type=stl" });

      await waitFor(() => expect(lastModelsQuery(requests).getAll("file_type")).toEqual(["stl"]));
    });

    it("forwards the favourites flag", async () => {
      const { requests } = renderVault({ at: "/?favorites=true" });

      await waitFor(() => expect(lastModelsQuery(requests).get("favorites")).toBe("true"));
    });

    it("forwards a search term", async () => {
      const { requests } = renderVault({ at: "/?q=bracket" });

      await waitFor(() => expect(lastModelsQuery(requests).get("q")).toBe("bracket"));
    });
  });

  describe("collection navigation", () => {
    it("asks the API only for what is directly in the selected folder", async () => {
      // The grid shows one level; descendants are reached by navigating into
      // them, which is what keeps a deep library from loading everything at once.
      const { requests } = renderVault({
        at: "/?c=parts",
        collections: [
          aCollection({ id: 1, name: "Parts", path: "parts" }),
          aCollection({ id: 2, name: "Brackets", path: "parts/brackets", parent_id: 1 }),
        ],
      });

      await waitFor(() => expect(lastModelsQuery(requests).get("direct")).toBe("true"));
    });

    it("offers the child folders of the selected one", async () => {
      renderVault({
        at: "/?c=parts",
        collections: [
          aCollection({ id: 1, name: "Parts", path: "parts" }),
          aCollection({ id: 2, name: "Brackets", path: "parts/brackets", parent_id: 1 }),
        ],
      });

      expect(await screen.findAllByText("Brackets")).not.toHaveLength(0);
    });

    it("traces a breadcrumb back to the root", async () => {
      renderVault({
        at: "/?c=parts/brackets",
        collections: [
          aCollection({ id: 1, name: "Parts", path: "parts" }),
          aCollection({ id: 2, name: "Brackets", path: "parts/brackets", parent_id: 1 }),
        ],
      });

      // Every level of the path has to be reachable, or the user is stranded in
      // a folder with no way back up.
      await waitFor(() => {
        const labels = screen.getAllByRole("button").map((button) => button.textContent);
        expect(labels).toEqual(expect.arrayContaining([expect.stringContaining("Parts")]));
        expect(labels).toEqual(expect.arrayContaining([expect.stringContaining("Brackets")]));
      });
    });
  });

  describe("display preferences", () => {
    it("starts in the grid the user last chose", async () => {
      window.localStorage.setItem("ps-vault-view", "list");

      renderVault({ models: [aModelListItem({ name: "Benchy" })] });

      expect(await screen.findByText("Name")).toBeInTheDocument();
    });

    it("remembers a switch to the list view", async () => {
      const user = userEvent.setup();
      renderVault({ models: [aModelListItem({ name: "Benchy" })] });
      await screen.findByText("Benchy");

      await user.click(screen.getByRole("button", { name: /Display/ }));
      await user.click(screen.getByRole("menuitem", { name: "List View" }));

      expect(window.localStorage.getItem("ps-vault-view")).toBe("list");
    });

    it("remembers a sort choice", async () => {
      const user = userEvent.setup();
      renderVault({ models: [aModelListItem({ name: "Benchy" })] });
      await screen.findByText("Benchy");

      const sort = sortButton();
      await user.click(sort);
      // Each bar owns its own menu, so scope the choice to the one just opened.
      await user.click(screen.getAllByRole("menuitem", { name: "Name A–Z" }).at(-1)!);

      expect(window.localStorage.getItem("ps-vault-sort")).toBe("name-asc");
    });

    it("falls back to the newest sort when storage holds something unknown", async () => {
      window.localStorage.setItem("ps-vault-sort", "not-a-sort");

      renderVault({ models: [aModelListItem({ name: "Benchy" })] });

      await screen.findByText("Benchy");
      expect(sortButton()).toHaveTextContent("Newest");
    });
  });

  describe("the documents tab", () => {
    it("opens on the documents tab when the URL asks for it", async () => {
      renderVault({ at: "/?v=docs" });

      expect(await screen.findByRole("button", { name: "Documents" })).toBeInTheDocument();
    });
  });

  describe("permissions", () => {
    it("disables uploading for a signed-out visitor", async () => {
      // The control stays visible and says why, rather than vanishing — a missing
      // button reads as a broken page, a disabled one reads as "sign in".
      renderVault({ auth: memberSession({ user: null }) });

      await waitFor(() => {
        const upload = uploadButton();
        expect(upload).toBeDisabled();
        expect(upload).toHaveAttribute("title", expect.stringContaining("Sign in"));
      });
    });

    it("offers uploading to a signed-in user", async () => {
      renderVault();

      await screen.findByRole("button", { name: "Select" });
      expect(uploadButton()).toBeEnabled();
    });
  });

  describe("selection", () => {
    it("enters select mode on request", async () => {
      const user = userEvent.setup();
      renderVault({ models: [aModelListItem({ name: "Benchy" })] });
      await screen.findByText("Benchy");

      await user.click(screen.getByRole("button", { name: /Select/ }));

      expect(screen.getByRole("button", { name: /Done/ })).toBeInTheDocument();
    });

    it("counts what the user selected", async () => {
      const user = userEvent.setup();
      renderVault({
        models: [
          aModelListItem({ id: 1, name: "Benchy" }),
          aModelListItem({ id: 2, name: "Cube" }),
        ],
      });
      await screen.findByText("Benchy");
      await user.click(screen.getByRole("button", { name: /Select/ }));

      await user.click(screen.getAllByRole("checkbox")[0]);

      // The count renders in both the desktop toolbar and the mobile bar.
      expect(await screen.findAllByText(/1 selected/)).not.toHaveLength(0);
    });
  });

  describe("recent folders", () => {
    it("ignores a stored list that is not an array", async () => {
      // The value is a UI convenience written by this component, but a user can
      // edit it — a crash here would take the whole vault page down.
      window.localStorage.setItem("ps-recent-folders", '{"not":"an array"}');

      renderVault({ models: [aModelListItem({ name: "Benchy" })] });

      expect(await screen.findByText("Benchy")).toBeInTheDocument();
    });

    it("ignores a stored list that is not JSON", async () => {
      window.localStorage.setItem("ps-recent-folders", "broken");

      renderVault({ models: [aModelListItem({ name: "Benchy" })] });

      expect(await screen.findByText("Benchy")).toBeInTheDocument();
    });
  });

  describe("upload deep link", () => {
    it("opens the upload dialog for ?upload=1", async () => {
      renderVault({ at: "/?upload=1" });

      expect(await screen.findByRole("dialog")).toBeInTheDocument();
    });
  });

  describe("tag filtering", () => {
    it("keeps a tag from the URL in the active filters", async () => {
      renderVault({
        at: "/?tag=functional",
        models: [aModelListItem({ name: "Benchy" })],
        tags: [aTag({ name: "functional", slug: "functional" })],
      });

      await screen.findByText("Benchy");
      // The chip is the only way back out of a tag that arrived in the URL, so
      // losing it strands the user in a filtered view they cannot widen.
      expect(screen.getByRole("button", { name: /Clear all/ })).toBeInTheDocument();
    });
  });

  describe("collection permissions", () => {
    it("offers no folder actions in a collection the user may only view", async () => {
      renderVault({
        at: "/?c=parts",
        auth: memberSession(),
        collections: [aCollection({ effective_role: "view" })],
      });

      await waitFor(() => expect(screen.queryByRole("button", { name: /New folder/i })).toBeNull());
    });
  });

  describe("creating a folder", () => {
    it("POSTs the folder under the one the user is in", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderVault({
        at: "/?c=parts",
        collections: [aCollection()],
        models: [aModelListItem({ name: "Benchy" })],
        routes: { "POST /api/v1/collections": json(aCollection({ id: 9, name: "Bolts" })) },
      });
      await screen.findByText("Benchy");

      await user.click(screen.getByRole("button", { name: /New collection/ }));
      await user.type(screen.getByPlaceholderText(/New subcollection/), "Bolts");
      await user.click(screen.getByRole("button", { name: "Create" }));

      await waitFor(() =>
        expect(requestsWithMethod("POST").at(-1)?.url).toBe("/api/v1/collections"),
      );
      // The parent travels as an id, not as a path prefix, so renaming the parent
      // cannot orphan a folder created under its old name.
      expect(JSON.parse(requestsWithMethod("POST").at(-1)?.body ?? "{}")).toMatchObject({
        name: "Bolts",
        parent_id: 1,
      });
    });

    it("creates at the root when no folder is selected", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderVault({
        models: [aModelListItem({ name: "Benchy" })],
        routes: { "POST /api/v1/collections": json(aCollection({ id: 9, name: "Bolts" })) },
      });
      await screen.findByText("Benchy");

      await user.click(screen.getByRole("button", { name: /New collection/ }));
      await user.type(screen.getByPlaceholderText("Collection name..."), "Bolts");
      await user.click(screen.getByRole("button", { name: "Create" }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("POST").at(-1)?.body ?? "{}")).toMatchObject({
          name: "Bolts",
          parent_id: null,
        }),
      );
    });

    it("refuses to create a folder with no name", async () => {
      const user = userEvent.setup();
      renderVault({ models: [aModelListItem({ name: "Benchy" })] });
      await screen.findByText("Benchy");

      await user.click(screen.getByRole("button", { name: /New collection/ }));

      expect(screen.getByRole("button", { name: "Create" })).toBeDisabled();
    });

    it("abandons the form on cancel", async () => {
      const user = userEvent.setup();
      renderVault({ models: [aModelListItem({ name: "Benchy" })] });
      await screen.findByText("Benchy");
      await user.click(screen.getByRole("button", { name: /New collection/ }));

      await user.click(screen.getByRole("button", { name: "Cancel" }));

      expect(screen.queryByPlaceholderText("Collection name...")).toBeNull();
    });
  });

  describe("acting on several models at once", () => {
    async function selectBoth(user: ReturnType<typeof userEvent.setup>) {
      await screen.findByText("Benchy");
      await user.click(screen.getByRole("button", { name: "Select" }));
      await user.click(screen.getByRole("button", { name: /Select all on screen/ }));
    }

    it("moves the selection in one request", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderVault({
        collections: [aCollection()],
        models: [
          aModelListItem({ id: 1, name: "Benchy" }),
          aModelListItem({ id: 2, name: "Cube" }),
        ],
        routes: { "POST /api/v1/models/batch/move": json({ succeeded_ids: [1, 2] }) },
      });
      await selectBoth(user);

      await user.click(screen.getByRole("button", { name: "Move" }));
      const dialog = await screen.findByRole("dialog");
      await user.click(within(dialog).getByRole("button", { name: /None \(root\)/ }));
      await user.click(within(dialog).getByRole("button", { name: "Move here" }));

      await waitFor(() =>
        expect(requestsWithMethod("POST").some((call) => call.url.includes("/batch/move"))).toBe(
          true,
        ),
      );
    });

    it("deletes the selection in one request", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderVault({
        models: [
          aModelListItem({ id: 1, name: "Benchy" }),
          aModelListItem({ id: 2, name: "Cube" }),
        ],
        routes: { "POST /api/v1/models/batch/delete": json({ succeeded_ids: [1, 2] }) },
      });
      await selectBoth(user);

      await user.click(screen.getByRole("button", { name: "Delete" }));
      const dialog = await screen.findByRole("dialog");
      await user.click(within(dialog).getByRole("button", { name: /Delete|Move to trash/ }));

      await waitFor(() =>
        expect(requestsWithMethod("POST").some((call) => call.url.includes("/batch/delete"))).toBe(
          true,
        ),
      );
    });

    it("leaves select mode when the user is done", async () => {
      // Leaving the mode has to take the checkboxes with it, or the grid stays
      // in a state the user thought they had left.
      const user = userEvent.setup();
      renderVault({
        models: [
          aModelListItem({ id: 1, name: "Benchy" }),
          aModelListItem({ id: 2, name: "Cube" }),
        ],
      });
      await screen.findByText("Benchy");
      await user.click(screen.getByRole("button", { name: "Select" }));

      await user.click(screen.getByRole("button", { name: "Done" }));

      expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
    });

    it("selects every model the current filters match", async () => {
      // The toolbar acts on ids, so "select all matching" has to fetch the ids
      // the filters resolve to rather than the page the user can see.
      const user = userEvent.setup();
      const { requests } = renderVault({
        models: [
          aModelListItem({ id: 1, name: "Benchy" }),
          aModelListItem({ id: 2, name: "Cube" }),
        ],
      });
      await screen.findByText("Benchy");
      await user.click(screen.getByRole("button", { name: "Select" }));

      await user.click(screen.getByRole("button", { name: /Select all matching models/ }));

      await waitFor(() =>
        expect(requests().some((call) => call.url.includes("limit=500"))).toBe(true),
      );
    });
  });

  describe("acting on several folders at once", () => {
    /** Turn on select mode and tick the folder card. */
    async function selectFolder(user: ReturnType<typeof userEvent.setup>, name = "Parts") {
      await screen.findAllByText(name);
      await user.click(screen.getByRole("button", { name: "Select" }));
      await user.click(screen.getByLabelText(`Select folder ${name}`));
    }

    it("renames the folders the user picked", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderVault({
        collections: [aCollection()],
        routes: { "PATCH /api/v1/collections/1": json(aCollection({ name: "Spares" })) },
      });
      await selectFolder(user);
      await user.click(await screen.findByRole("button", { name: /Rename/ }));
      const dialog = await screen.findByRole("dialog");
      await user.clear(within(dialog).getByRole("textbox"));
      await user.type(within(dialog).getByRole("textbox"), "Spares");

      await user.click(within(dialog).getByRole("button", { name: /Rename/ }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("PATCH").at(-1)?.body ?? "{}")).toMatchObject({
          name: "Spares",
        }),
      );
    });

    it("offers no tagging when a folder is in the selection", async () => {
      // Tags belong to models; a folder has none, so the action would apply to
      // part of the selection and silently skip the rest.
      const user = userEvent.setup();
      renderVault({
        collections: [aCollection()],
        models: [aModelListItem({ id: 1, name: "Benchy" })],
      });
      await selectFolder(user);

      expect(screen.queryByRole("button", { name: /^Tag$/ })).toBeNull();
    });

    it("deletes a folder with everything inside it", async () => {
      // A folder is deleted with its contents; deleting only the row would leave
      // orphaned models with no way back to them.
      const user = userEvent.setup();
      const { requests } = renderVault({
        collections: [aCollection()],
        routes: { "DELETE /api/v1/collections/1": json(null, 204) },
      });
      await selectFolder(user);
      await user.click(await screen.findByRole("button", { name: /^Delete$/ }));

      await user.click(
        within(await screen.findByRole("dialog")).getByRole("button", {
          name: /Delete/,
        }),
      );

      await waitFor(() =>
        expect(
          requests().some(
            (call) => call.method === "DELETE" && call.url.includes("recursive=true"),
          ),
        ).toBe(true),
      );
    });

    it("moves a folder under the destination the user chose", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderVault({
        collections: [aCollection(), aCollection({ id: 2, name: "Spares", path: "spares" })],
        routes: { "PATCH /api/v1/collections/1": json(aCollection({ parent_id: 2 })) },
      });
      await selectFolder(user);
      await user.click(await screen.findByRole("button", { name: /Move/ }));
      const dialog = await screen.findByRole("dialog");

      await user.click(within(dialog).getByRole("button", { name: /spares/ }));
      await user.click(within(dialog).getByRole("button", { name: /^Move/ }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("PATCH").at(-1)?.body ?? "{}")).toMatchObject({
          parent_id: 2,
        }),
      );
    });

    it("reports the folders it could not act on", async () => {
      // A batch that half-succeeded and says "Renamed 1" hides the other one.
      const user = userEvent.setup();
      renderVault({
        collections: [aCollection()],
        routes: { "PATCH /api/v1/collections/1": json({ detail: "conflict" }, 409) },
      });
      await selectFolder(user);
      await user.click(await screen.findByRole("button", { name: /Rename/ }));
      const dialog = await screen.findByRole("dialog");
      await user.clear(within(dialog).getByRole("textbox"));
      await user.type(within(dialog).getByRole("textbox"), "Spares");

      await user.click(within(dialog).getByRole("button", { name: /Rename/ }));

      expect(await screen.findByText("1 skipped")).toBeInTheDocument();
    });
  });

  describe("batch outcomes", () => {
    async function selectOneModel(user: ReturnType<typeof userEvent.setup>) {
      await screen.findByText("Benchy");
      await user.click(screen.getByRole("button", { name: "Select" }));
      await user.click(screen.getAllByRole("checkbox")[0]);
    }

    it("tags the selection in one request", async () => {
      const user = userEvent.setup();
      const { requests } = renderVault({
        models: [aModelListItem({ id: 1, name: "Benchy" })],
        tags: [aTag()],
        routes: {
          "POST /api/v1/models/batch/tags": json({
            succeeded_ids: [1],
            succeeded_count: 1,
            failed_count: 0,
            failed: [],
          }),
        },
      });
      await selectOneModel(user);

      await user.click(screen.getByRole("button", { name: "Tag" }));
      const dialog = await screen.findByRole("dialog");
      await user.type(within(dialog).getAllByRole("combobox")[0], "functional{Enter}");
      await user.click(within(dialog).getByRole("button", { name: /Apply/ }));

      await waitFor(() =>
        expect(requests().some((call) => call.url.includes("/batch/tags"))).toBe(true),
      );
    });

    it("reports what a partial batch skipped", async () => {
      // A batch that half-succeeded must say so; reporting only the successes
      // leaves the user believing models moved that did not.
      const user = userEvent.setup();
      renderVault({
        models: [aModelListItem({ id: 1, name: "Benchy" })],
        routes: {
          "POST /api/v1/models/batch/delete": json({
            succeeded_ids: [],
            succeeded_count: 0,
            failed_count: 1,
            failed: [{ model_id: 1, reason: "forbidden" }],
          }),
        },
      });
      await selectOneModel(user);

      await user.click(screen.getByRole("button", { name: "Delete" }));
      const dialog = await screen.findByRole("dialog");
      await user.click(within(dialog).getByRole("button", { name: /Delete|Move to trash/ }));

      expect(await screen.findByText(/1 skipped/)).toBeInTheDocument();
    });

    it("surfaces a batch that failed outright", async () => {
      const user = userEvent.setup();
      renderVault({
        models: [aModelListItem({ id: 1, name: "Benchy" })],
        routes: {
          "POST /api/v1/models/batch/delete": json({ detail: "forbidden" }, 403),
        },
      });
      await selectOneModel(user);

      await user.click(screen.getByRole("button", { name: "Delete" }));
      const dialog = await screen.findByRole("dialog");
      await user.click(within(dialog).getByRole("button", { name: /Delete|Move to trash/ }));

      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    });
  });

  describe("dragging files onto the vault", () => {
    /**
     * The slice of `DataTransfer` the drop handlers read. jsdom cannot construct
     * a real one, and the handlers only ever touch these four members — a fuller
     * stand-in would assert nothing more.
     */
    interface DroppedPayload {
      types: string[];
      files: File[];
      items: DataTransferItem[];
      dropEffect: string;
    }

    function dataTransfer(files: File[]): DroppedPayload {
      return { types: ["Files"], files, items: [], dropEffect: "none" };
    }

    it("opens the upload dialog for a dropped mesh", async () => {
      renderVault({ models: [aModelListItem({ name: "Benchy" })] });
      const main = await screen.findByText("Benchy");

      fireEvent.drop(main, { dataTransfer: dataTransfer([new File(["x"], "cube.stl")]) });

      expect(await screen.findByRole("dialog")).toBeInTheDocument();
    });

    it("ignores a drop that carries nothing importable", async () => {
      renderVault({ models: [aModelListItem({ name: "Benchy" })] });
      const main = await screen.findByText("Benchy");

      fireEvent.drop(main, { dataTransfer: dataTransfer([new File(["x"], "notes.txt")]) });

      expect(screen.queryByRole("dialog")).toBeNull();
    });

    it("opens the upload dialog for a dropped archive", async () => {
      renderVault({ models: [aModelListItem({ name: "Benchy" })] });
      const main = await screen.findByText("Benchy");

      fireEvent.drop(main, { dataTransfer: dataTransfer([new File(["x"], "pack.zip")]) });

      expect(await screen.findByRole("dialog")).toBeInTheDocument();
    });

    it("opens the upload dialog for several dropped meshes", async () => {
      renderVault({ models: [aModelListItem({ name: "Benchy" })] });
      const main = await screen.findByText("Benchy");

      fireEvent.drop(main, {
        dataTransfer: dataTransfer([new File(["a"], "a.stl"), new File(["b"], "b.stl")]),
      });

      expect(await screen.findByRole("dialog")).toBeInTheDocument();
    });

    it("opens the upload dialog for a dropped G-code file", async () => {
      renderVault({ models: [aModelListItem({ name: "Benchy" })] });
      const main = await screen.findByText("Benchy");

      fireEvent.drop(main, { dataTransfer: dataTransfer([new File(["x"], "part.gcode")]) });

      expect(await screen.findByRole("dialog")).toBeInTheDocument();
    });

    it("ignores a model being dragged between folders", async () => {
      // An internal model drag carries its own MIME type and is handled by the
      // folder drop targets; treating it as a file upload would open the dialog
      // on top of the move the user is doing.
      renderVault({ models: [aModelListItem({ name: "Benchy" })] });
      const main = await screen.findByText("Benchy");

      const modelDrag: DroppedPayload = {
        types: ["application/x-printstash-model"],
        files: [],
        items: [],
        dropEffect: "move",
      };

      fireEvent.drop(main, { dataTransfer: modelDrag });

      expect(screen.queryByRole("dialog")).toBeNull();
    });
  });

  describe("saved views", () => {
    it("saves the current filters under a name", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderVault({
        at: "/?tag=functional",
        models: [aModelListItem({ name: "Benchy" })],
        routes: { "POST /api/v1/saved-views": json({ id: 1, name: "PETG", filters: {} }) },
      });
      await screen.findByText("Benchy");

      await user.click(screen.getByRole("button", { name: /Saved views/ }));
      await user.click(await screen.findByRole("button", { name: /Save current view/ }));
      const dialog = await screen.findByRole("dialog");
      await user.type(within(dialog).getByRole("textbox"), "PETG");
      await user.click(within(dialog).getByRole("button", { name: "Save view" }));

      await waitFor(() =>
        expect(requestsWithMethod("POST").some((call) => call.url.includes("saved-views"))).toBe(
          true,
        ),
      );
    });

    it("saves the filters that are actually on screen", async () => {
      // A view that stores something other than what the user was looking at is
      // worse than no view: it silently reproduces the wrong search later.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderVault({
        at: "/?tag=functional&favorites=true",
        models: [aModelListItem({ name: "Benchy" })],
        tags: [aTag()],
        routes: { "POST /api/v1/saved-views": json(aSavedView()) },
      });
      await screen.findByText("Benchy");
      await user.click(screen.getByRole("button", { name: /Saved views/ }));
      await user.click(await screen.findByRole("button", { name: /Save current view/ }));
      const dialog = await screen.findByRole("dialog");
      await user.type(within(dialog).getByRole("textbox"), "PETG");

      await user.click(within(dialog).getByRole("button", { name: "Save view" }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("POST").at(-1)?.body ?? "{}")).toMatchObject({
          name: "PETG",
          filters: { tag: ["functional"], favorites: true },
        }),
      );
    });

    it("will not save a view with no name", async () => {
      const user = userEvent.setup();
      renderVault({ models: [aModelListItem({ name: "Benchy" })] });
      await screen.findByText("Benchy");

      await user.click(screen.getByRole("button", { name: /Saved views/ }));
      await user.click(await screen.findByRole("button", { name: /Save current view/ }));

      expect(
        within(await screen.findByRole("dialog")).getByRole("button", { name: "Save view" }),
      ).toBeDisabled();
    });

    it("lists the views already saved", async () => {
      const user = userEvent.setup();
      renderVault({
        models: [aModelListItem({ name: "Benchy" })],
        routes: { "GET /api/v1/saved-views": json([aSavedView({ name: "Ready to print" })]) },
      });
      await screen.findByText("Benchy");

      await user.click(screen.getByRole("button", { name: /Saved views/ }));

      expect(await screen.findByRole("button", { name: /Rename Ready to print/ })).toBeVisible();
    });

    it("says so when no view has been saved", async () => {
      const user = userEvent.setup();
      renderVault({ models: [aModelListItem({ name: "Benchy" })] });
      await screen.findByText("Benchy");

      await user.click(screen.getByRole("button", { name: /Saved views/ }));

      expect(await screen.findByText("No saved views yet")).toBeInTheDocument();
    });

    it("puts the view's filters into the URL when it is chosen", async () => {
      // The URL is the filter state, so a view that only updates React would be
      // lost on reload and unshareable.
      const user = userEvent.setup();
      const { requests } = renderVault({
        models: [aModelListItem({ name: "Benchy" })],
        tags: [aTag()],
        routes: {
          "GET /api/v1/saved-views": json([
            aSavedView({ filters: { ...EMPTY_VIEW_FILTERS, tag: ["functional"] } }),
          ]),
        },
      });
      await screen.findByText("Benchy");
      await user.click(screen.getByRole("button", { name: /Saved views/ }));

      await user.click(await screen.findByRole("button", { name: "PETG only" }));

      await waitFor(() => expect(lastModelsQuery(requests).getAll("tag")).toEqual(["functional"]));
    });

    it("updates a view to the filters now on screen", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderVault({
        at: "/?favorites=true",
        models: [aModelListItem({ name: "Benchy" })],
        routes: {
          "GET /api/v1/saved-views": json([aSavedView()]),
          "PATCH /api/v1/saved-views/1": json(aSavedView()),
        },
      });
      await screen.findByText("Benchy");
      await user.click(screen.getByRole("button", { name: /Saved views/ }));

      await user.click(await screen.findByRole("button", { name: "Update PETG only" }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("PATCH").at(-1)?.body ?? "{}")).toMatchObject({
          filters: { favorites: true },
        }),
      );
    });

    it("renames a view", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderVault({
        models: [aModelListItem({ name: "Benchy" })],
        routes: {
          "GET /api/v1/saved-views": json([aSavedView()]),
          "PATCH /api/v1/saved-views/1": json(aSavedView({ name: "PLA only" })),
        },
      });
      await screen.findByText("Benchy");
      await user.click(screen.getByRole("button", { name: /Saved views/ }));
      await user.click(await screen.findByRole("button", { name: "Rename PETG only" }));
      const dialog = await screen.findByRole("dialog");
      await user.clear(within(dialog).getByRole("textbox"));
      await user.type(within(dialog).getByRole("textbox"), "PLA only");

      await user.click(within(dialog).getByRole("button", { name: /Save|Rename/ }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("PATCH").at(-1)?.body ?? "{}")).toMatchObject({
          name: "PLA only",
        }),
      );
    });

    it("duplicates a view under a name nobody is using", async () => {
      // Two views with the same name are indistinguishable in the picker, which
      // is the only place they are ever chosen from.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderVault({
        models: [aModelListItem({ name: "Benchy" })],
        routes: {
          "GET /api/v1/saved-views": json([
            aSavedView(),
            aSavedView({ id: 2, name: "PETG only copy" }),
          ]),
          "POST /api/v1/saved-views": json(aSavedView({ id: 3 })),
        },
      });
      await screen.findByText("Benchy");
      await user.click(screen.getByRole("button", { name: /Saved views/ }));

      await user.click(await screen.findByRole("button", { name: "Duplicate PETG only" }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("POST").at(-1)?.body ?? "{}")).toMatchObject({
          name: "PETG only copy 2",
        }),
      );
    });

    it("asks before deleting a view", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderVault({
        models: [aModelListItem({ name: "Benchy" })],
        routes: { "GET /api/v1/saved-views": json([aSavedView()]) },
      });
      await screen.findByText("Benchy");
      await user.click(screen.getByRole("button", { name: /Saved views/ }));

      await user.click(await screen.findByRole("button", { name: "Delete PETG only" }));

      expect(requestsWithMethod("DELETE").some((call) => call.url.includes("saved-views"))).toBe(
        false,
      );
    });

    it("deletes the view once confirmed", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderVault({
        models: [aModelListItem({ name: "Benchy" })],
        routes: {
          "GET /api/v1/saved-views": json([aSavedView()]),
          "DELETE /api/v1/saved-views/1": json(null, 204),
        },
      });
      await screen.findByText("Benchy");
      await user.click(screen.getByRole("button", { name: /Saved views/ }));
      await user.click(await screen.findByRole("button", { name: "Delete PETG only" }));

      await user.click(await screen.findByRole("button", { name: /^Delete$/ }));

      await waitFor(() =>
        expect(
          requestsWithMethod("DELETE").some((call) => call.url.endsWith("/saved-views/1")),
        ).toBe(true),
      );
    });

    it("offers no saved views to a signed-out visitor", async () => {
      // Views belong to an account, so a session without one simply has none —
      // rather than showing somebody else's.
      renderVault({
        auth: signedOutSession(),
        models: [aModelListItem({ name: "Benchy" })],
        routes: { "GET /api/v1/saved-views": json([aSavedView()]) },
      });

      await screen.findByText("Benchy");
      expect(screen.queryByRole("button", { name: /Saved views/ })).toBeNull();
    });
  });

  describe("clearing filters", () => {
    it("drops every active filter in one action", async () => {
      // Undoing them one at a time is the difference between "start over" and a
      // chore, and a filter left behind quietly narrows every later search.
      const user = userEvent.setup();
      const { requests } = renderVault({
        at: "/?tag=functional&favorites=true&q=bracket",
        models: [aModelListItem({ name: "Benchy" })],
      });
      await screen.findByText("Benchy");

      await user.click(await screen.findByRole("button", { name: /Clear all/ }));

      await waitFor(() => {
        const query = lastModelsQuery(requests);
        expect(query.getAll("tag")).toEqual([]);
        expect(query.get("favorites")).toBeNull();
      });
    });
  });

  describe("the documents tab", () => {
    it("offers a way to write a new document", async () => {
      renderVault({ at: "/?v=docs" });

      expect(await screen.findByRole("button", { name: /New document/ })).toBeInTheDocument();
    });

    it("offers a way to upload one", async () => {
      renderVault({ at: "/?v=docs" });

      expect(await screen.findByRole("button", { name: /Upload PDF/ })).toBeInTheDocument();
    });

    it("remembers the tab for the next visit", async () => {
      const user = userEvent.setup();
      renderVault({ models: [aModelListItem({ name: "Benchy" })] });
      await screen.findByText("Benchy");

      await user.click(screen.getByRole("button", { name: "Documents" }));

      expect(window.localStorage.getItem("printstash.last.view")).toBe("docs");
    });
  });

  describe("pagination", () => {
    it("offers more when the page reports a cursor", async () => {
      renderVault({
        models: [aModelListItem({ name: "Benchy" })],
        routes: {
          "GET /api/v1/models/page": json({
            items: [aModelListItem({ name: "Benchy" })],
            total: 120,
            next_cursor: "next",
          }),
        },
      });

      await screen.findByText("Benchy");
      await waitFor(() =>
        expect(screen.queryByRole("button", { name: /Load more/ })).toBeInTheDocument(),
      );
    });
  });
  describe("acting on a folder from the sidebar", () => {
    it("deletes the folder the sidebar asked to delete", async () => {
      // The sidebar owns the gesture; the vault owns the request. A folder that
      // disappears from the tree without a DELETE is a folder that comes back
      // on reload.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderVault({
        collections: [aCollection()],
        routes: { "DELETE /api/v1/collections/1": json(null, 204) },
      });
      await screen.findAllByText("Parts");
      await user.click(screen.getAllByTitle("Delete collection")[0]);

      await user.click(await screen.findByRole("button", { name: "Delete" }));

      await waitFor(() =>
        expect(
          requestsWithMethod("DELETE").some((call) => call.url.includes("/collections/1")),
        ).toBe(true),
      );
    });

    it("reports a folder the server would not delete", async () => {
      const user = userEvent.setup();
      renderVault({
        collections: [aCollection()],
        routes: { "DELETE /api/v1/collections/1": json({ detail: "collection_not_empty" }, 409) },
      });
      await screen.findAllByText("Parts");
      await user.click(screen.getAllByTitle("Delete collection")[0]);

      await user.click(await screen.findByRole("button", { name: "Delete" }));

      expect(
        await screen.findByText("Cannot delete: collection still has models assigned."),
      ).toBeInTheDocument();
    });
  });

  describe("creating a folder without the rights for it", () => {
    it("offers no way to create one inside a folder the user cannot administer", async () => {
      // Creating inside a folder needs admin on *that* folder, and the server
      // would 403 — refusing up front is the difference between a reason and a
      // red banner.
      renderVault({
        at: "/?c=parts",
        auth: memberSession(),
        collections: [aCollection({ effective_role: "view" })],
        models: [aModelListItem({ name: "Benchy" })],
      });
      await screen.findByText("Benchy");

      expect(screen.getByRole("button", { name: /New collection/ })).toBeDisabled();
    });

    it("says why it is refused", async () => {
      renderVault({
        at: "/?c=parts",
        auth: memberSession(),
        collections: [aCollection({ effective_role: "view" })],
        models: [aModelListItem({ name: "Benchy" })],
      });
      await screen.findByText("Benchy");

      expect(screen.getByRole("button", { name: /New collection/ })).toHaveAttribute(
        "title",
        "Admin access required for this collection",
      );
    });
  });

  describe("undoing a batch", () => {
    it("offers to undo a tag change", async () => {
      // Tagging fifty models is one click and fifty writes; without an undo the
      // only way back is fifty more.
      const user = userEvent.setup();
      renderVault({
        models: [aModelListItem({ id: 1, name: "Benchy" })],
        tags: [aTag()],
        routes: {
          "POST /api/v1/models/batch/tags": json({
            succeeded_ids: [1],
            succeeded_count: 1,
            failed: [],
            failed_count: 0,
          }),
        },
      });
      await screen.findByText("Benchy");
      await user.click(screen.getByRole("button", { name: "Select" }));
      await user.click(screen.getAllByRole("checkbox")[0]);
      await user.click(screen.getByRole("button", { name: "Tag" }));
      const dialog = await screen.findByRole("dialog");
      await user.type(within(dialog).getAllByRole("combobox")[0], "functional{Enter}");

      await user.click(within(dialog).getByRole("button", { name: /Apply/ }));

      expect(await screen.findByRole("button", { name: "Undo" })).toBeInTheDocument();
    });

    it("puts the original tags back when the undo is taken", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderVault({
        models: [aModelListItem({ id: 1, name: "Benchy", tags: ["draft"] })],
        tags: [aTag()],
        routes: {
          "POST /api/v1/models/batch/tags": json({
            succeeded_ids: [1],
            succeeded_count: 1,
            failed: [],
            failed_count: 0,
          }),
          "PATCH /api/v1/models/1": json({ id: 1, tags: ["draft"] }),
        },
      });
      await screen.findByText("Benchy");
      await user.click(screen.getByRole("button", { name: "Select" }));
      await user.click(screen.getAllByRole("checkbox")[0]);
      await user.click(screen.getByRole("button", { name: "Tag" }));
      const dialog = await screen.findByRole("dialog");
      await user.type(within(dialog).getAllByRole("combobox")[0], "functional{Enter}");
      await user.click(within(dialog).getByRole("button", { name: /Apply/ }));

      await user.click(await screen.findByRole("button", { name: "Undo" }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("PATCH").at(-1)?.body ?? "{}")).toMatchObject({
          tags: ["draft"],
        }),
      );
    });
  });
  describe("the active-filter chips", () => {
    it("names the printer being filtered on", async () => {
      // The filter is invisible otherwise: the grid just looks short, and the
      // user reports missing models.
      renderVault({
        at: "/?printer_id=4",
        seed: [[queryKeys.printers, [aPrinter({ id: 4, name: "Voron" })]]],
        routes: { "GET /api/v1/printers": json([aPrinter({ id: 4, name: "Voron" })]) },
      });

      expect(await screen.findByText("Printer: Voron")).toBeInTheDocument();
    });

    it("falls back to the id for a printer that is gone", async () => {
      // A deleted printer leaves its id in bookmarks; a blank chip hides an
      // active filter entirely.
      renderVault({ at: "/?printer_id=99" });

      expect(await screen.findByText("Printer: 99")).toBeInTheDocument();
    });

    it("drops the printer filter when its chip is removed", async () => {
      const user = userEvent.setup();
      const { requests } = renderVault({
        at: "/?printer_id=4",
        seed: [[queryKeys.printers, [aPrinter({ id: 4, name: "Voron" })]]],
        routes: { "GET /api/v1/printers": json([aPrinter({ id: 4, name: "Voron" })]) },
      });
      await screen.findByText("Printer: Voron");

      await user.click(screen.getByTitle("Remove Printer: Voron"));

      await waitFor(() => expect(lastModelsQuery(requests).get("printer_id")).toBeNull());
    });

    it("names a vault-only filter in words", async () => {
      // "printer_presence=none" means nothing to the person reading the chip.
      renderVault({ at: "/?printer_presence=none" });

      expect(await screen.findByText("Vault only")).toBeInTheDocument();
    });

    it("names an on-a-printer filter in words", async () => {
      renderVault({ at: "/?printer_presence=any" });

      expect(await screen.findByText("On a printer")).toBeInTheDocument();
    });

    it("names a structured filter readably", async () => {
      // The raw key is `revision_status=needs_test`; the chip has to read as
      // English or the user cannot tell which filter to drop.
      renderVault({ at: "/?revision_status=needs_test" });

      expect(await screen.findByText("revision status: needs test")).toBeInTheDocument();
    });

    it("names an upload-date filter", async () => {
      renderVault({ at: "/?uploaded_after=2026-01-01" });

      expect(await screen.findByText("Uploaded after: 2026-01-01")).toBeInTheDocument();
    });

    it("names the search term", async () => {
      renderVault({ at: "/?q=bracket" });

      expect(await screen.findByText("Search: bracket")).toBeInTheDocument();
    });
  });

  describe("dragging a model onto a folder", () => {
    /**
     * The folder card in the grid. The drop handlers sit on the card, and the
     * folder name also appears in the sidebar tree, so the grid one is found by
     * walking up from the name inside the card.
     */
    async function folderCard() {
      await screen.findAllByText("Parts");
      // SAFETY: the grid renders one card per collection and the fixture has
      // exactly one; the sidebar tree uses list rows, not this attribute.
      return document.querySelector('[data-collection-path="parts"]') as HTMLElement;
    }

    /** A model drag carries a MIME nobody else sets, so file drags pass through. */
    function modelDrag(id: number) {
      return {
        types: [MODEL_DND_MIME],
        getData: () => String(id),
        dropEffect: "",
      };
    }

    it("moves the model into the folder it was dropped on", async () => {
      const { requestsWithMethod } = renderVault({
        collections: [aCollection()],
        models: [aModelListItem({ id: 1, name: "Benchy" })],
        routes: { "PATCH /api/v1/models/1": json({ id: 1, collection: "parts" }) },
      });
      const folder = await folderCard();

      fireEvent.drop(folder, { dataTransfer: modelDrag(1) });

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("PATCH").at(-1)?.body ?? "{}")).toMatchObject({
          collection: "parts",
        }),
      );
    });

    it("ignores a file drag over a folder", async () => {
      // An OS file drag carries no model id; treating it as one would move a
      // random model every time somebody dragged a file across the tree.
      const { requestsWithMethod } = renderVault({
        collections: [aCollection()],
        models: [aModelListItem({ id: 1, name: "Benchy" })],
      });
      const folder = await folderCard();

      fireEvent.drop(folder, { dataTransfer: { types: ["Files"], files: [] } });

      expect(requestsWithMethod("PATCH")).toHaveLength(0);
    });
  });
  describe("the drop-to-upload overlay", () => {
    /** A drag carrying OS files, which is what the vault-wide zone reacts to. */
    function fileDrag() {
      return { types: ["Files"], dropEffect: "" };
    }

    it("invites the drop once a file drag enters the vault", async () => {
      // Without the overlay a user dragging a file over the page has no way to
      // know the page will take it, and drops it on the desktop instead.
      renderVault({ models: [aModelListItem({ name: "Benchy" })] });
      const main = await screen.findByText("Benchy");

      fireEvent.dragEnter(main, { dataTransfer: fileDrag() });

      expect(await screen.findByText("Drop to upload")).toBeInTheDocument();
    });

    it("ignores a model drag, which the folders handle", async () => {
      // The folder cards are the drop targets for a model; lighting the whole
      // vault up would suggest dropping anywhere works.
      renderVault({ models: [aModelListItem({ name: "Benchy" })] });
      const main = await screen.findByText("Benchy");

      fireEvent.dragEnter(main, { dataTransfer: { types: [MODEL_DND_MIME] } });

      expect(screen.queryByText("Drop to upload")).toBeNull();
    });

    it("keeps the invitation up while the drag crosses child elements", async () => {
      // A drag over a grid fires leave/enter pairs constantly as it passes
      // between cards; a naive handler flickers the overlay on every one.
      renderVault({ models: [aModelListItem({ name: "Benchy" })] });
      const main = await screen.findByText("Benchy");
      fireEvent.dragEnter(main, { dataTransfer: fileDrag() });
      fireEvent.dragEnter(main, { dataTransfer: fileDrag() });

      fireEvent.dragLeave(main, { dataTransfer: fileDrag() });

      expect(screen.getByText("Drop to upload")).toBeInTheDocument();
    });

    it("takes the invitation down when the drag really leaves", async () => {
      renderVault({ models: [aModelListItem({ name: "Benchy" })] });
      const main = await screen.findByText("Benchy");
      fireEvent.dragEnter(main, { dataTransfer: fileDrag() });

      fireEvent.dragLeave(main, { dataTransfer: fileDrag() });

      await waitFor(() => expect(screen.queryByText("Drop to upload")).toBeNull());
    });
  });

  describe("the back button", () => {
    it("restores the filters the previous page had", async () => {
      // The filter state lives in the URL but also in React, and only the URL
      // moves on a history pop — without this the back button changes the
      // address bar and nothing else.
      const { requests } = renderVault({
        at: "/?tag=functional",
        tags: [aTag()],
        models: [aModelListItem({ name: "Benchy" })],
      });
      await screen.findByText("Benchy");

      window.history.replaceState({}, "", "/?tag=bracket");
      fireEvent.popState(window);

      await waitFor(() => expect(lastModelsQuery(requests).getAll("tag")).toEqual(["bracket"]));
    });

    it("drops a filter the previous page did not have", async () => {
      const { requests } = renderVault({
        at: "/?favorites=true",
        models: [aModelListItem({ name: "Benchy" })],
      });
      await screen.findByText("Benchy");

      window.history.replaceState({}, "", "/");
      fireEvent.popState(window);

      await waitFor(() => expect(lastModelsQuery(requests).get("favorites")).toBeNull());
    });
  });
  describe("undoing a move", () => {
    async function selectAndMove(user: ReturnType<typeof userEvent.setup>) {
      await screen.findByText("Benchy");
      await user.click(screen.getByRole("button", { name: "Select" }));
      await user.click(screen.getByLabelText("Select Benchy"));
      await user.click(await screen.findByRole("button", { name: /Move/ }));
      const dialog = await screen.findByRole("dialog");
      await user.click(within(dialog).getByRole("button", { name: /spares/ }));
      await user.click(within(dialog).getByRole("button", { name: /^Move/ }));
    }

    it("offers to undo the move", async () => {
      // Moving fifty models is one click and one request; without an undo the
      // only way back is remembering where every one of them came from.
      const user = userEvent.setup();
      renderVault({
        collections: [aCollection({ id: 2, name: "Spares", path: "spares" })],
        models: [aModelListItem({ id: 1, name: "Benchy", collection: "parts" })],
        routes: {
          "POST /api/v1/models/batch/move": json({
            succeeded_ids: [1],
            succeeded_count: 1,
            failed: [],
            failed_count: 0,
          }),
        },
      });

      await selectAndMove(user);

      expect(await screen.findByRole("button", { name: "Undo" })).toBeInTheDocument();
    });

    it("puts each model back where it came from", async () => {
      // The models in one move can come from different folders, so the undo has
      // to group them by origin rather than send them all to one place.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderVault({
        collections: [aCollection({ id: 2, name: "Spares", path: "spares" })],
        models: [aModelListItem({ id: 1, name: "Benchy", collection: "parts" })],
        routes: {
          "POST /api/v1/models/batch/move": json({
            succeeded_ids: [1],
            succeeded_count: 1,
            failed: [],
            failed_count: 0,
          }),
        },
      });
      await selectAndMove(user);

      await user.click(await screen.findByRole("button", { name: "Undo" }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("POST").at(-1)?.body ?? "{}")).toMatchObject({
          collection: "parts",
        }),
      );
    });

    it("reports the models the move skipped", async () => {
      // A move that half-succeeded and says "Moved 1" leaves the user believing
      // models are somewhere they are not.
      const user = userEvent.setup();
      renderVault({
        collections: [aCollection({ id: 2, name: "Spares", path: "spares" })],
        models: [aModelListItem({ id: 1, name: "Benchy", collection: "parts" })],
        routes: {
          "POST /api/v1/models/batch/move": json({
            succeeded_ids: [],
            succeeded_count: 0,
            failed: [{ model_id: 1, reason: "forbidden" }],
            failed_count: 1,
          }),
        },
      });

      await selectAndMove(user);

      expect(await screen.findByText("1 skipped")).toBeInTheDocument();
    });
  });
});
