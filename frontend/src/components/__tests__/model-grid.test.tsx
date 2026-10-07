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
import { outlinerRoutes } from "@/test-support/outliner";

import "@testing-library/jest-dom/vitest";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useLocation, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LibraryStartupProvider } from "@/lib/library-startup-provider";
import { ModelBrowser } from "@/components/model-grid";
import { MODEL_DND_MIME, captureModelDrag } from "@/lib/model-dnd";
import { queryKeys } from "@/lib/query-client";
import { clearLogin } from "@/lib/auth-store";
import type {
  CollectionRead,
  ModelListItem,
  MultipartModelListItem,
  SavedViewRead,
  TagRead,
} from "@/types";
import { collectionTreeRoutes } from "@/test-support/collection-tree";
import { anEditingBase, aCollectionNode, aModelListItem, aPrinter } from "@/test-support/factories";
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
    tags: [],
    has_readme: false,
    ...override,
  };
}

function aTag(override: Partial<TagRead> = {}): TagRead {
  return { id: 1, name: "functional", slug: "functional", model_count: 3, ...override };
}

function aMultipartSet(override: Partial<MultipartModelListItem> = {}): MultipartModelListItem {
  return {
    edit_epoch: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    edit_version: 1,
    id: 40,
    name: "Dragon figure",
    slug: "dragon-figure",
    description: "A complete printable figure",
    collection: null,
    collection_id: null,
    collection_label: null,
    part_count: 2,
    model_count: 2,
    guide_count: 0,
    cover_model_id: 1,
    cover_image_url: null,
    cover_image_uploaded: false,
    cover_thumbnail_url: null,
    starred: false,
    member_model_ids: [1],
    tags: ["fantasy"],
    effective_role: "admin",
    updated_at: "2026-01-02T00:00:00Z",
    ...override,
  };
}

/** The filter set a view stores: every key present, nothing selected. */
const EMPTY_VIEW_FILTERS: SavedViewRead["filters"] = {
  library_view: "all",
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
    multipartModels?: MultipartModelListItem[];
    collections?: CollectionRead[];
    tags?: TagRead[];
    historyProbe?: boolean;
    startup?: boolean;
  } = {},
) {
  const {
    models = [],
    multipartModels = [],
    collections = [],
    tags = [],
    historyProbe = false,
    startup = false,
    seed = [],
    routes = {},
    ...rest
  } = options;
  return renderApp(
    <>
      <LibraryStartupProvider active={startup}>
        <ModelBrowser />
      </LibraryStartupProvider>
      {historyProbe && <HistoryProbe />}
    </>,
    {
      seed: [
        [queryKeys.tags, tags],
        [queryKeys.vaultStats, { model_count: models.length, file_count: 0, total_size_bytes: 0 }],
        ...seed,
      ],
      routes: {
        "GET /api/v1/models/facets": json(EMPTY_FACETS),
        "GET /api/v1/models/browse/revision": json({
          browse_revision: "r1",
          authorization_revision: "a1",
        }),
        "GET /api/v1/models/browse": (url) =>
          json({
            items: [
              ...(new URL(url, "http://printstash.test").searchParams.get("view") === "multipart"
                ? []
                : models.map((model) => ({ kind: "model", model }))),
              ...multipartModels.map((multipart) => ({ kind: "multipart", multipart })),
            ],
            total: models.length + multipartModels.length,
            next_cursor: null,
            browse_revision: "r1",
            authorization_revision: "a1",
          }),
        "GET /api/v1/models/outliner": json([]),
        "GET /api/v1/models": json(models),
        "GET /api/v1/saved-views": json([]),
        "GET /api/v1/documents": json([]),
        "GET /api/v1/multipart-models": json(multipartModels),
        ...collectionTreeRoutes(collections),
        ...outlinerRoutes(collections, [], multipartModels),
        "GET /api/v1/tags": json(tags),
        ...routes,
      },
      ...rest,
    },
  );
}

function HistoryProbe() {
  const location = useLocation();
  const navigate = useNavigate();
  return (
    <>
      <output data-testid="vault-location">{location.pathname + location.search}</output>
      <button onClick={() => navigate(-1)}>History back</button>
      <button onClick={() => navigate(1)}>History forward</button>
      <button onClick={() => navigate("/?type=multipart&sort=name-asc")}>
        Open multipart location
      </button>
      <button onClick={() => navigate("/?v=docs&type=all&sort=date-desc")}>
        Open documents location
      </button>
    </>
  );
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
    .filter(
      (call) =>
        call.method === "GET" &&
        new URL(call.url, "http://test").pathname === "/api/v1/models/browse",
    )
    .at(-1)?.url;
  return new URLSearchParams(url?.split("?")[1] ?? "");
}

function lastMultipartQuery(requests: () => { method: string; url: string }[]): URLSearchParams {
  const url = requests()
    .filter(
      (call) =>
        call.method === "GET" &&
        new URL(call.url, "http://test").pathname === "/api/v1/models/browse",
    )
    .at(-1)?.url;
  return new URLSearchParams(url?.split("?")[1] ?? "");
}

async function openFilters() {
  const trigger = screen.getAllByRole("button", { name: "Filters" }).at(-1)!;
  if (trigger.getAttribute("aria-expanded") === "false") await userEvent.setup().click(trigger);
}

async function openLibraryTools() {
  const trigger = screen.getByRole("button", { name: "Library tools" });
  if (trigger.getAttribute("aria-expanded") === "false") await userEvent.setup().click(trigger);
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ModelBrowser", () => {
  describe("library tools", () => {
    it("keeps advanced organization out of the initial toolbar", async () => {
      renderVault();
      expect(await screen.findByRole("button", { name: "All Models" })).toBeVisible();
      expect(screen.queryByRole("region", { name: "Filters" })).toBeNull();
      expect(uploadButton()).toBeVisible();
      expect(screen.getByRole("button", { name: "Library tools" })).toHaveAttribute(
        "aria-expanded",
        "false",
      );
    });
    it("reveals organization tools on request", async () => {
      renderVault();
      await openLibraryTools();
      expect(screen.getByRole("button", { name: "New multipart set" })).toBeVisible();
      expect(screen.queryByRole("button", { name: "New Family" })).not.toBeInTheDocument();
    });
    it("links to library-wide similar models", async () => {
      renderVault();
      await openLibraryTools();

      expect(
        within(screen.getByRole("region", { name: "Library tools" })).getByRole("link", {
          name: "Similar models",
        }),
      ).toHaveAttribute("href", "/library/similar");
    });
    it("opens multipart creation from the mobile More menu", async () => {
      const user = userEvent.setup();
      renderVault();
      await screen.findByRole("button", { name: "All Models" });
      await user.click(screen.getByRole("button", { name: "More" }));
      await user.click(screen.getByRole("menuitem", { name: "New multipart set" }));
      expect(screen.getByRole("dialog", { name: "New multipart set" })).toBeVisible();
    });
    it("can collapse the tools after opening them", async () => {
      renderVault();
      await openLibraryTools();
      await userEvent.setup().click(screen.getByRole("button", { name: "Library tools" }));
      expect(screen.getByRole("button", { name: "Library tools" })).toHaveAttribute(
        "aria-expanded",
        "false",
      );
      expect(screen.queryByRole("region", { name: "Library tools" })).not.toBeInTheDocument();
    });
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

    it("toggles a multipart card favorite", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderVault({
        multipartModels: [aMultipartSet()],
        routes: {
          "PUT /api/v1/multipart-models/40/star": json({
            multipart_model_id: 40,
            starred: true,
          }),
        },
      });

      await user.click(
        await screen.findByRole("button", { name: "Add Dragon figure to favorites" }),
      );

      await waitFor(() => expect(requestsWithMethod("PUT")).toHaveLength(1));
      expect(
        screen.getByRole("button", { name: "Remove Dragon figure from favorites" }),
      ).toBeVisible();
    });

    it("opens the multipart card tag editor", async () => {
      const user = userEvent.setup();
      const set = aMultipartSet({ tags: [] });
      const saved = { ...set, tags: ["functional"] };
      const { requestsWithMethod } = renderVault({
        multipartModels: [set],
        tags: [aTag()],
        routes: { "PUT /api/v1/multipart-models/40/tags": json(saved) },
      });

      await user.click(await screen.findByRole("button", { name: "Add tags to Dragon figure" }));
      await user.click(await screen.findByRole("button", { name: "functional" }));
      await user.click(screen.getByRole("button", { name: "Save tags" }));

      await waitFor(() => expect(requestsWithMethod("PUT")).toHaveLength(1));
      expect(JSON.parse(requestsWithMethod("PUT")[0].body)).toEqual({ tags: ["functional"] });
    });
  });

  describe("progressive startup", () => {
    it("bounds the initial model page without changing URL filters", async () => {
      const { requests } = renderVault({ at: "/?tag=functional", startup: true });
      await waitFor(() =>
        expect(
          requests().some(
            (request) => new URL(request.url, "http://test").pathname === "/api/v1/models/browse",
          ),
        ).toBe(true),
      );
      const request = requests().find(
        (request) => new URL(request.url, "http://test").pathname === "/api/v1/models/browse",
      )!;
      const params = new URL(request.url, "http://test").searchParams;
      expect(params.get("limit")).toBe("24");
      expect(params.getAll("tag")).toEqual(["functional"]);
    });

    it("defers catalogs until coherent primary content is visible", async () => {
      let deliver: (response: Response) => void = () => {};
      const primary = new Promise<Response>((resolve) => {
        deliver = resolve;
      });
      const { requests } = renderVault({
        startup: true,
        routes: { "GET /api/v1/models/browse": () => primary },
      });
      await waitFor(() =>
        expect(
          requests().some(
            (request) => new URL(request.url, "http://test").pathname === "/api/v1/models/browse",
          ),
        ).toBe(true),
      );
      expect(requests().some((request) => request.url.startsWith("/api/v1/models/facets"))).toBe(
        false,
      );
      expect(requests().some((request) => request.url.startsWith("/api/v1/saved-views"))).toBe(
        false,
      );
      deliver(
        json({
          items: [{ kind: "model", model: aModelListItem({ name: "Ready bracket" }) }],
          total: 1,
          next_cursor: null,
          browse_revision: "r1",
          authorization_revision: "a1",
        }),
      );
      expect(await screen.findByText("Ready bracket")).toBeVisible();
      await waitFor(() =>
        expect(requests().some((request) => request.url.startsWith("/api/v1/models/facets"))).toBe(
          true,
        ),
      );
    });

    it("keeps secondary reads deferred when the navigation tree is used early", async () => {
      const user = userEvent.setup();
      const { requests } = renderVault({
        startup: true,
        collections: [aCollection()],
        routes: { "GET /api/v1/models/browse": () => new Promise(() => {}) },
      });
      const outliner = await screen.findByRole("complementary");
      const folder = await within(outliner).findByTitle("Parts");
      await user.hover(folder);
      await user.click(folder);
      await waitFor(() =>
        expect(requests().some((request) => request.url.includes("collection=parts"))).toBe(true),
      );
      expect(requests().some((request) => request.url.startsWith("/api/v1/models/facets"))).toBe(
        false,
      );
      expect(requests().some((request) => request.url.startsWith("/api/v1/tags"))).toBe(false);
    });

    it("loads filter options immediately when filters are opened early", async () => {
      const { requests } = renderVault({
        startup: true,
        routes: { "GET /api/v1/models/browse": () => new Promise(() => {}) },
      });
      await userEvent.click(screen.getAllByRole("button", { name: "Filters" })[0]);
      await waitFor(() =>
        expect(requests().some((request) => request.url.startsWith("/api/v1/models/facets"))).toBe(
          true,
        ),
      );
      expect(requests().some((request) => request.url.startsWith("/api/v1/saved-views"))).toBe(
        false,
      );
    });

    it("waits for child folders before releasing secondary reads", async () => {
      const pending = Promise.withResolvers<Response>();
      const { requests } = renderVault({
        startup: true,
        models: [aModelListItem({ name: "Ready model" })],
        routes: { "GET /api/v1/collections/children": () => pending.promise },
      });
      await waitFor(() =>
        expect(requests().some((r) => r.url.startsWith("/api/v1/collections/children"))).toBe(true),
      );
      expect(screen.queryByText("Ready model")).not.toBeInTheDocument();
      expect(requests().some((r) => r.url.startsWith("/api/v1/models/facets"))).toBe(false);
      pending.resolve(json({ items: [], next_cursor: null }));
      expect(await screen.findByText("Ready model")).toBeVisible();
      await waitFor(() =>
        expect(requests().some((r) => r.url.startsWith("/api/v1/models/facets"))).toBe(true),
      );
    });

    it("releases recovery controls after child folders fail", async () => {
      const { requests } = renderVault({
        startup: true,
        routes: { "GET /api/v1/collections/children": json({ detail: "folder_unavailable" }, 503) },
      });
      expect(await screen.findByText(/folder_unavailable/)).toBeVisible();
      expect(screen.queryByText("No models found")).not.toBeInTheDocument();
      await waitFor(() =>
        expect(requests().some((r) => r.url.startsWith("/api/v1/models/facets"))).toBe(true),
      );
      expect(screen.getAllByRole("button", { name: "Filters" })[0]).toBeEnabled();
    });

    it("releases catalog requests after a primary error", async () => {
      const { requests } = renderVault({
        startup: true,
        routes: { "GET /api/v1/models/browse": json({ detail: "startup_failed" }, 500) },
      });
      await screen.findByText(/startup_failed/, {}, { timeout: 3000 });
      await waitFor(() =>
        expect(requests().some((request) => request.url.startsWith("/api/v1/models/facets"))).toBe(
          true,
        ),
      );
      expect(screen.getAllByRole("button", { name: "Filters" })[0]).toBeEnabled();
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
      expect(lastMultipartQuery(requests).get("favorites")).toBe("true");
    });

    it("forwards a search term", async () => {
      const { requests } = renderVault({ at: "/?q=bracket" });

      await waitFor(() => expect(lastModelsQuery(requests).get("q")).toBe("bracket"));
    });
  });

  describe("Library filter projection", () => {
    it("removes malformed boolean constraints before browsing", async () => {
      const { requests } = renderVault({
        at: "/?printed=invalid&has_similar_candidates=false",
        models: [aModelListItem({ name: "Benchy" })],
      });
      await screen.findByText("Benchy");
      expect(lastModelsQuery(requests).has("printed")).toBe(false);
      expect(lastModelsQuery(requests).has("has_similar_candidates")).toBe(false);
    });

    it("replaces a malformed printer bookmark", async () => {
      const user = userEvent.setup();
      const { requests } = renderVault({
        at: "/?printer_id=-1&unknown=keep",
        auth: adminSession(),
        historyProbe: true,
        models: [aModelListItem({ name: "Benchy" })],
      });
      await screen.findByText("Benchy");
      expect(lastModelsQuery(requests).has("printer_id")).toBe(false);
      await waitFor(() =>
        expect(screen.getByTestId("vault-location")).not.toHaveTextContent("printer_id"),
      );
      expect(screen.getByTestId("vault-location")).toHaveTextContent("unknown=keep");
      await user.click(screen.getByRole("button", { name: "Open multipart location" }));
      await user.click(screen.getByRole("button", { name: "History back" }));
      expect(screen.getByTestId("vault-location")).not.toHaveTextContent("printer_id");
      expect(screen.getByTestId("vault-location")).toHaveTextContent("unknown=keep");
    });

    it("preserves a stored admin view when a member applies it", async () => {
      const user = userEvent.setup();
      const saved = aSavedView({ filters: { ...aSavedView().filters, printer_id: 7 } });
      const { requests, requestsWithMethod } = renderVault({
        auth: memberSession(),
        models: [aModelListItem({ name: "Benchy" })],
        routes: { "GET /api/v1/saved-views": json([saved]) },
      });
      await screen.findByText("Benchy");
      await openLibraryTools();
      await user.click(screen.getByRole("button", { name: /Saved views/ }));
      await user.click(await screen.findByRole("button", { name: saved.name }));
      expect(lastModelsQuery(requests).has("printer_id")).toBe(false);
      expect(screen.queryByText("Printer: 7")).not.toBeInTheDocument();
      expect(requestsWithMethod("PATCH")).toHaveLength(0);
      expect(saved.filters.printer_id).toBe(7);
    });

    it("saves only effective member filters", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderVault({
        at: "/?printer_id=7&printer_presence=none",
        auth: memberSession(),
        models: [aModelListItem({ name: "Benchy" })],
        routes: { "POST /api/v1/saved-views": json(aSavedView()) },
      });
      await screen.findByText("Benchy");
      await openLibraryTools();
      await user.click(screen.getByRole("button", { name: /Saved views/ }));
      await user.click(await screen.findByRole("button", { name: /Save current view/ }));
      const dialog = await screen.findByRole("dialog");
      await user.type(within(dialog).getByRole("textbox"), "Member view");
      await user.click(within(dialog).getByRole("button", { name: "Save view" }));
      await waitFor(() =>
        expect(
          JSON.parse(
            requestsWithMethod("POST").find((call) => call.url.includes("saved-views"))?.body ??
              "{}",
          ),
        ).toMatchObject({ filters: { printer_id: null, printer_presence: null } }),
      );
    });

    it("duplicates only effective member filters", async () => {
      const user = userEvent.setup();
      const saved = aSavedView({ filters: { ...aSavedView().filters, printer_id: 7 } });
      const { requestsWithMethod } = renderVault({
        auth: memberSession(),
        models: [aModelListItem({ name: "Benchy" })],
        routes: {
          "GET /api/v1/saved-views": json([saved]),
          "POST /api/v1/saved-views": json(aSavedView({ id: 2 })),
        },
      });
      await screen.findByText("Benchy");
      await openLibraryTools();
      await user.click(screen.getByRole("button", { name: /Saved views/ }));
      await user.click(await screen.findByRole("button", { name: `Duplicate ${saved.name}` }));
      await waitFor(() =>
        expect(
          JSON.parse(
            requestsWithMethod("POST").find((call) => call.url.includes("saved-views"))?.body ??
              "{}",
          ),
        ).toMatchObject({ filters: { printer_id: null, printer_presence: null } }),
      );
      expect(saved.filters.printer_id).toBe(7);
    });

    it("shares effective filters across Library requests", async () => {
      const { requests } = renderVault({
        at: "/?file_type=stl&file_type=bad&printed=no&tag=functional",
        models: [aModelListItem({ name: "Benchy" })],
      });
      await screen.findByText("Benchy");
      await openFilters();
      await waitFor(() =>
        expect(requests().some((call) => call.url.includes("/models/facets"))).toBe(true),
      );
      const facets = new URL(
        requests()
          .filter((call) => call.url.includes("/models/facets"))
          .at(-1)!.url,
        "http://test",
      ).searchParams;
      for (const params of [lastModelsQuery(requests), facets]) {
        expect(params.getAll("file_type")).toEqual(["stl"]);
        expect(params.get("printed")).toBe("false");
        expect(params.getAll("tag")).toEqual(["functional"]);
      }
    });
  });

  describe("collection navigation", () => {
    it("keeps existing root collections visible beside an imported root", async () => {
      renderVault({
        collections: [
          aCollection({ id: 1, name: "Christine", path: "christine", model_count: 17 }),
          aCollection({ id: 2, name: "Pegboard", path: "pegboard", model_count: 8 }),
          aCollection({ id: 3, name: "Models", path: "models", model_count: 0 }),
        ],
      });

      const main = await screen.findByRole("main");
      for (const path of ["christine", "pegboard", "models"]) {
        expect(main.querySelector(`[data-collection-path="${path}"]`)).toBeVisible();
      }
    });

    it("finds an original root after an overlapping import", async () => {
      renderVault({
        at: "/?q=christine",
        collections: [
          aCollection({ id: 1, name: "Christine", path: "christine", model_count: 17 }),
          aCollection({ id: 2, name: "Models", path: "models", model_count: 0 }),
        ],
      });

      const main = await screen.findByRole("main");
      expect(within(main).getByRole("button", { name: /Christine/ })).toBeVisible();
    });

    it("opens a collection from its grid card", async () => {
      const user = userEvent.setup();
      renderVault({ collections: [aCollection()] });

      const main = await screen.findByRole("main");
      await user.click(within(main).getByRole("button", { name: /Parts/ }));

      expect(await screen.findByRole("heading", { name: "Parts" })).toBeVisible();
    });

    it("does not add history when the current collection breadcrumb is clicked", async () => {
      renderVault({ collections: [aCollection()], historyProbe: true });

      const main = await screen.findByRole("main");
      fireEvent.click(within(main).getByRole("button", { name: /Parts/ }));
      expect(await screen.findByRole("heading", { name: "Parts" })).toBeVisible();

      const breadcrumb = within(main).getByRole("navigation");
      fireEvent.click(within(breadcrumb).getByRole("button", { name: "Parts" }));
      fireEvent.click(screen.getByRole("button", { name: "History back" }));

      await waitFor(() =>
        expect(screen.getByTestId("vault-location")).toHaveTextContent("/?type=all&sort=date-desc"),
      );
      expect(screen.getByRole("heading", { name: "All Models" })).toBeVisible();
    }, 20_000);

    it("shows multipart sets in the collection tree", async () => {
      renderVault({ multipartModels: [aMultipartSet()] });

      const outlinerFilter = screen.getByPlaceholderText("Filter outliner...");
      const outliner = outlinerFilter.closest("aside")!;
      expect(await within(outliner).findByText("Dragon figure")).toBeVisible();
    });

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

  describe("loading the library", () => {
    it("never asks for the whole collection tree", async () => {
      // Every page load fetched all 9,000 folders of the library in #295.
      const { requests } = renderVault({
        at: "/?c=parts",
        collections: [
          aCollection({ id: 1, name: "Parts", path: "parts" }),
          aCollection({ id: 2, name: "Brackets", path: "parts/brackets", parent_id: 1 }),
        ],
        models: [aModelListItem({ name: "Benchy" })],
      });
      await screen.findByText("Benchy");

      const whole = requests().filter(
        (call) => new URL(call.url, "http://test").pathname === "/api/v1/collections",
      );

      expect(whole).toEqual([]);
    });

    it("labels a model card with its folder's names", async () => {
      renderVault({
        models: [
          aModelListItem({
            name: "Benchy",
            collection: "parts/brackets",
            collection_id: 2,
            collection_label: "Parts/Brackets",
          }),
        ],
      });

      // The chip shows the folder's own name and carries its full path.
      expect(await screen.findByTitle("Parts/Brackets")).toHaveTextContent("Brackets");
    });
  });

  describe("moving between folders", () => {
    const PARTS_TREE = [
      aCollection({ id: 1, name: "Parts", path: "parts" }),
      aCollection({ id: 2, name: "Brackets", path: "parts/brackets", parent_id: 1 }),
    ];

    /**
     * A folder card in the grid (the sidebar tree lists the same names). The
     * cards arrive with the open folder's children, after the page has drawn.
     */
    async function folderCard(path: string) {
      return waitFor(() => {
        const card = screen
          .getByRole("main")
          .querySelector<HTMLElement>(`[data-collection-path="${path}"]`);
        if (!card) throw new Error(`no folder card for ${path}`);
        return card;
      });
    }

    function requestsFor(
      requests: () => { method: string; url: string }[],
      prefix: string,
      collection: string,
    ) {
      return requests().filter(
        (call) =>
          call.method === "GET" &&
          call.url.startsWith(prefix) &&
          new URLSearchParams(call.url.split("?")[1] ?? "").get("collection") === collection,
      );
    }

    it("keeps one coherent folder result while the destination loads", async () => {
      const user = userEvent.setup();
      const pending = Promise.withResolvers<Response>();
      const { requests } = renderVault({
        at: "/?c=parts",
        collections: PARTS_TREE,
        routes: {
          "GET /api/v1/models/browse": (url) =>
            url.includes("parts%2Fbrackets")
              ? pending.promise
              : json({
                  items: [{ kind: "model", model: aModelListItem({ name: "Shelf rig" }) }],
                  total: 1,
                  next_cursor: null,
                  browse_revision: "r1",
                  authorization_revision: "a1",
                }),
        },
      });
      await screen.findByText("Shelf rig");
      await user.click(await folderCard("parts/brackets"));
      await waitFor(() =>
        expect(requestsFor(requests, "/api/v1/models/browse", "parts/brackets")).toHaveLength(1),
      );
      expect(screen.getByRole("heading", { name: "Parts" })).toBeVisible();
      expect(screen.getByText("Shelf rig")).toBeVisible();
      const sourceLink = screen.getByRole("link", { name: /Shelf rig/ });
      const sourceHref = new URL(sourceLink.getAttribute("href")!, "http://test");
      expect(sourceHref.searchParams.get("return")).toBe("/?c=parts&type=all&sort=date-desc");
      expect(screen.queryByText("Bracket piece")).not.toBeInTheDocument();
      expect(await folderCard("parts/brackets")).toBeVisible();
      pending.resolve(
        json({
          items: [{ kind: "model", model: aModelListItem({ name: "Bracket piece" }) }],
          total: 1,
          next_cursor: null,
          browse_revision: "r1",
          authorization_revision: "a1",
        }),
      );
      expect(await screen.findByRole("heading", { name: "Brackets" })).toBeVisible();
      expect(screen.getByText("Bracket piece")).toBeVisible();
      expect(screen.queryByText("Shelf rig")).not.toBeInTheDocument();
      expect(
        screen.getByRole("main").querySelector('[data-collection-path="parts/brackets"]'),
      ).toBeNull();
    });

    it("keeps breadcrumbs with the displayed result across rapid destinations", async () => {
      const root = Promise.withResolvers<Response>();
      const child = Promise.withResolvers<Response>();
      const { requests } = renderVault({
        at: "/?c=parts",
        collections: PARTS_TREE,
        routes: {
          "GET /api/v1/models/browse": (url) => {
            const collection = new URL(url, "http://test").searchParams.get("collection");
            if (collection === null) return root.promise;
            if (collection === "parts/brackets") return child.promise;
            return json({
              items: [{ kind: "model", model: aModelListItem({ name: "Displayed shelf" }) }],
              total: 1,
              next_cursor: null,
              browse_revision: "r1",
              authorization_revision: "a1",
            });
          },
        },
      });
      await screen.findByText("Displayed shelf");
      const breadcrumb = within(screen.getByRole("main")).getByRole("navigation");
      fireEvent.click(within(breadcrumb).getByRole("button", { name: "All Models" }));
      await waitFor(() =>
        expect(
          requests().some(
            (call) =>
              call.url.startsWith("/api/v1/models/browse?") &&
              !new URL(call.url, "http://test").searchParams.has("collection"),
          ),
        ).toBe(true),
      );
      expect(within(breadcrumb).getByRole("button", { name: "Parts" })).toBeVisible();
      fireEvent.click(await folderCard("parts/brackets"));
      await waitFor(() =>
        expect(requestsFor(requests, "/api/v1/models/browse", "parts/brackets")).toHaveLength(1),
      );
      root.resolve(
        json({
          items: [{ kind: "model", model: aModelListItem({ name: "Late root" }) }],
          total: 1,
          next_cursor: null,
          browse_revision: "r1",
          authorization_revision: "a1",
        }),
      );
      expect(screen.getByRole("heading", { name: "Parts" })).toBeVisible();
      expect(within(breadcrumb).getByRole("button", { name: "Parts" })).toBeVisible();
      const href = new URL(
        screen.getByRole("link", { name: /Displayed shelf/ }).getAttribute("href")!,
        "http://test",
      );
      expect(href.searchParams.get("return")).toBe("/?c=parts&type=all&sort=date-desc");
      expect(screen.queryByText("Late root")).not.toBeInTheDocument();
      child.resolve(
        json({
          items: [{ kind: "model", model: aModelListItem({ name: "Current bracket" }) }],
          total: 1,
          next_cursor: null,
          browse_revision: "r1",
          authorization_revision: "a1",
        }),
      );
      await screen.findByText("Current bracket");
      expect(screen.getByRole("heading", { name: "Brackets" })).toBeVisible();
      expect(within(breadcrumb).getByRole("button", { name: "Brackets" })).toBeVisible();
      expect(screen.queryByText("Displayed shelf")).not.toBeInTheDocument();
      expect(screen.queryByText("Late root")).not.toBeInTheDocument();
    });

    it("warms a folder when the pointer rests on its card", async () => {
      const user = userEvent.setup();
      const { requests } = renderVault({ at: "/?c=parts", collections: PARTS_TREE });
      await screen.findByRole("heading", { name: "Parts" });

      await user.hover(await folderCard("parts/brackets"));

      await waitFor(() => {
        for (const prefix of ["/api/v1/models/browse", "/api/v1/models/facets"]) {
          expect(requestsFor(requests, prefix, "parts/brackets")).toHaveLength(1);
        }
      });
    });

    it("opens a warmed folder without asking the server again", async () => {
      const user = userEvent.setup();
      const { requests } = renderVault({ at: "/?c=parts", collections: PARTS_TREE });
      await screen.findByRole("heading", { name: "Parts" });
      await user.hover(await folderCard("parts/brackets"));
      await waitFor(() =>
        expect(requestsFor(requests, "/api/v1/models/browse", "parts/brackets")).toHaveLength(1),
      );

      await user.click(await folderCard("parts/brackets"));

      await screen.findByRole("heading", { name: "Brackets" });
      expect(requestsFor(requests, "/api/v1/models/browse", "parts/brackets")).toHaveLength(1);
      expect(requestsFor(requests, "/api/v1/multipart-models", "parts/brackets")).toHaveLength(0);
    });

    it("warms a folder focused from the keyboard", async () => {
      const { requests } = renderVault({ at: "/?c=parts", collections: PARTS_TREE });
      await screen.findByRole("heading", { name: "Parts" });

      fireEvent.focus(await folderCard("parts/brackets"));

      await waitFor(() =>
        expect(requestsFor(requests, "/api/v1/models/browse", "parts/brackets")).toHaveLength(1),
      );
    });

    it("warms a folder hovered in the sidebar tree", async () => {
      const user = userEvent.setup();
      const { requests } = renderVault({ collections: [aCollection()] });
      const outliner = screen.getByPlaceholderText("Filter outliner...").closest("aside")!;

      await user.hover(await within(outliner).findByTitle("Parts"));

      await waitFor(() =>
        expect(requestsFor(requests, "/api/v1/models/browse", "parts")).toHaveLength(1),
      );
    });

    it("warms the readme of a hovered folder that has one", async () => {
      const user = userEvent.setup();
      const { requests } = renderVault({
        at: "/?c=parts",
        collections: [
          aCollection({ id: 1, name: "Parts", path: "parts" }),
          aCollection({
            id: 2,
            name: "Brackets",
            path: "parts/brackets",
            parent_id: 1,
            has_readme: true,
          }),
        ],
        routes: { "GET /api/v1/collections/2/readme": json({ readme: "Shelf brackets." }) },
      });
      await screen.findByRole("heading", { name: "Parts" });

      await user.hover(await folderCard("parts/brackets"));

      await waitFor(() =>
        expect(
          requests().filter((call) => call.url.endsWith("/api/v1/collections/2/readme")),
        ).toHaveLength(1),
      );
    });

    it("does not re-warm the folder that is already open", async () => {
      // Its data is on screen; the sidebar row for it is the most-hovered one.
      const user = userEvent.setup();
      const { requests, client } = renderVault({ at: "/?c=parts", collections: PARTS_TREE });
      await screen.findByRole("heading", { name: "Parts" });
      await waitFor(() =>
        expect(requestsFor(requests, "/api/v1/models/browse", "parts")).toHaveLength(1),
      );
      // Past production's staleTime, a prefetch of this folder would refetch it.
      client.setDefaultOptions({ queries: { retry: false, staleTime: 0 } });
      const outliner = screen.getByPlaceholderText("Filter outliner...").closest("aside")!;

      await user.hover(within(outliner).getByTitle("Parts"));

      await new Promise((resolve) => setTimeout(resolve, 20));
      expect(requestsFor(requests, "/api/v1/models/browse", "parts")).toHaveLength(1);
    });

    it("warms a folder hovered in the list view", async () => {
      window.localStorage.setItem("ps-vault-view", "list");
      const user = userEvent.setup();
      const { requests } = renderVault({ at: "/?c=parts", collections: PARTS_TREE });
      await screen.findByRole("heading", { name: "Parts" });

      await user.hover(await folderCard("parts/brackets"));

      await waitFor(() =>
        expect(requestsFor(requests, "/api/v1/models/browse", "parts/brackets")).toHaveLength(1),
      );
    });

    it("warms a folder focused in the list view", async () => {
      window.localStorage.setItem("ps-vault-view", "list");
      const { requests } = renderVault({ at: "/?c=parts", collections: PARTS_TREE });
      await screen.findByRole("heading", { name: "Parts" });

      fireEvent.focus(await folderCard("parts/brackets"));

      await waitFor(() =>
        expect(requestsFor(requests, "/api/v1/models/browse", "parts/brackets")).toHaveLength(1),
      );
    });

    it("warms multipart results after migrating the parts-only preference", async () => {
      // The retired selection becomes Everything, including multipart cards.
      window.localStorage.setItem("ps-vault-library-view", "components");
      const user = userEvent.setup();
      const { requests } = renderVault({ at: "/?c=parts", collections: PARTS_TREE });
      await screen.findByRole("heading", { name: "Parts" });

      await user.hover(await folderCard("parts/brackets"));

      await waitFor(() =>
        expect(requestsFor(requests, "/api/v1/models/browse", "parts/brackets")).toHaveLength(1),
      );
      expect(requestsFor(requests, "/api/v1/multipart-models", "parts/brackets")).toHaveLength(0);
    });

    it("warms a folder focused in the sidebar tree", async () => {
      const { requests } = renderVault({ collections: [aCollection()] });
      const outliner = screen.getByPlaceholderText("Filter outliner...").closest("aside")!;

      fireEvent.focus(await within(outliner).findByTitle("Parts"));

      await waitFor(() =>
        expect(requestsFor(requests, "/api/v1/models/browse", "parts")).toHaveLength(1),
      );
    });

    it("does not warm folders while the user is selecting", async () => {
      // In select mode a click toggles the folder instead of opening it.
      const user = userEvent.setup();
      const { requests } = renderVault({ at: "/?c=parts", collections: PARTS_TREE });
      await screen.findByRole("heading", { name: "Parts" });
      await openLibraryTools();
      await user.click(screen.getByRole("button", { name: /Select/ }));

      await user.hover(await folderCard("parts/brackets"));

      await new Promise((resolve) => setTimeout(resolve, 20));
      expect(requestsFor(requests, "/api/v1/models/browse", "parts/brackets")).toHaveLength(0);
    });

    it("does not ask for the readme of a folder the list says has none", async () => {
      const user = userEvent.setup();
      const { requests } = renderVault({ at: "/?c=parts", collections: PARTS_TREE });
      await screen.findByRole("heading", { name: "Parts" });

      await user.click(await folderCard("parts/brackets"));

      await screen.findByRole("button", { name: /Add a description for this collection/ });
      expect(requests().some((call) => call.url.endsWith("/readme"))).toBe(false);
    });

    it("shows the readme of a folder the list says has one", async () => {
      renderVault({
        at: "/?c=parts",
        collections: [aCollection({ id: 1, name: "Parts", path: "parts", has_readme: true })],
        routes: { "GET /api/v1/collections/1/readme": json({ readme: "Shelf rig parts." }) },
      });

      expect(await screen.findByText("Shelf rig parts.")).toBeVisible();
    });
  });

  describe("display preferences", () => {
    it("starts in the everything library view when no preference exists", async () => {
      renderVault({ models: [aModelListItem({ name: "Benchy" })] });

      await screen.findByText("Benchy");
      expect(screen.getAllByRole("button", { name: "Everything" })[0]).toHaveAttribute(
        "aria-pressed",
        "true",
      );
    });

    it("remembers a library view choice", async () => {
      const user = userEvent.setup();
      renderVault({ models: [aModelListItem({ name: "Benchy" })] });
      await screen.findByText("Benchy");

      await user.click(screen.getAllByRole("button", { name: "Multipart sets only" })[0]);

      expect(window.localStorage.getItem("ps-vault-library-view")).toBe("multipart");
    });

    it("migrates the retired parts-only preference to Everything", async () => {
      window.localStorage.setItem("ps-vault-library-view", "components");

      renderVault({ models: [aModelListItem({ name: "Benchy" })] });

      const everything = await screen.findAllByRole("button", { name: "Everything" });
      expect(everything[0]).toHaveAttribute("aria-pressed", "true");
    });

    it("falls back to everything when the remembered library view is unknown", async () => {
      window.localStorage.setItem("ps-vault-library-view", "unknown");

      renderVault({ models: [aModelListItem({ name: "Benchy" })] });

      await screen.findByText("Benchy");
      expect(screen.getAllByRole("button", { name: "Everything" })[0]).toHaveAttribute(
        "aria-pressed",
        "true",
      );
    });

    it("lets the URL override the remembered library view", async () => {
      window.localStorage.setItem("ps-vault-library-view", "components");

      renderVault({ at: "/?type=all", models: [aModelListItem({ name: "Benchy" })] });

      await screen.findByText("Benchy");
      expect(screen.getAllByRole("button", { name: "Everything" })[0]).toHaveAttribute(
        "aria-pressed",
        "true",
      );
    });

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

    it("writes a sort choice into the URL", async () => {
      const user = userEvent.setup();
      renderVault({ models: [aModelListItem({ name: "Benchy" })], historyProbe: true });
      await screen.findByText("Benchy");

      await user.click(sortButton());
      await user.click(screen.getAllByRole("menuitem", { name: "Name A–Z" }).at(-1)!);

      expect(screen.getByTestId("vault-location")).toHaveTextContent("sort=name-asc");
    });

    it("restores URL-owned mode on history navigation", async () => {
      const user = userEvent.setup();
      renderVault({ models: [aModelListItem({ name: "Benchy" })], historyProbe: true });
      await screen.findByText("Benchy");

      await user.click(screen.getByRole("button", { name: "Open multipart location" }));
      expect(screen.getAllByRole("button", { name: "Multipart sets only" })[0]).toHaveAttribute(
        "aria-pressed",
        "true",
      );
      await user.click(screen.getByRole("button", { name: "History back" }));

      expect(screen.getAllByRole("button", { name: "Everything" })[0]).toHaveAttribute(
        "aria-pressed",
        "true",
      );
      expect(sortButton()).toHaveTextContent("Newest");
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

    it("restores the selected relevance ordering for Model browse", async () => {
      const user = userEvent.setup();
      const app = renderVault({ at: "/?q=benchy", models: [aModelListItem({ name: "Benchy" })] });
      await screen.findByText("Benchy");
      await user.click(sortButton());
      await user.click(screen.getAllByRole("menuitem", { name: "Relevance" }).at(-1)!);
      expect(window.localStorage.getItem("ps-vault-sort")).toBe("relevance");
      await waitFor(() =>
        expect(
          app
            .requests()
            .some(
              (request) =>
                request.url.includes("sort=relevance") && request.url.includes("q=benchy"),
            ),
        ).toBe(true),
      );
    });

    it("falls back to the newest sort when storage holds something unknown", async () => {
      window.localStorage.setItem("ps-vault-sort", "not-a-sort");

      renderVault({ models: [aModelListItem({ name: "Benchy" })] });

      await screen.findByText("Benchy");
      expect(sortButton()).toHaveTextContent("Newest");
    });
  });

  describe("the documents tab", () => {
    it("follows the document tab through browser history", async () => {
      const user = userEvent.setup();
      renderVault({ historyProbe: true });
      await screen.findByRole("tab", { name: "Models" });

      await user.click(screen.getByRole("button", { name: "Open documents location" }));
      expect(screen.getByRole("tab", { name: "Documents" })).toHaveAttribute(
        "aria-selected",
        "true",
      );
      await user.click(screen.getByRole("button", { name: "History back" }));
      expect(screen.getByRole("tab", { name: "Models" })).toHaveAttribute("aria-selected", "true");
      await user.click(screen.getByRole("button", { name: "History forward" }));

      expect(screen.getByRole("tab", { name: "Documents" })).toHaveAttribute(
        "aria-selected",
        "true",
      );
    });

    it("opens on the documents tab when the URL asks for it", async () => {
      renderVault({ at: "/?v=docs" });

      expect(await screen.findByRole("tab", { name: "Documents" })).toHaveAttribute(
        "aria-selected",
        "true",
      );
    });
  });

  describe("the unified model library", () => {
    it("keeps referenced Models visible after retiring Organized", async () => {
      window.localStorage.setItem("ps-vault-library-view", "organized");
      renderVault({
        models: [
          aModelListItem({ id: 1, name: "Dragon body" }),
          aModelListItem({ id: 2, name: "Calibration cube" }),
        ],
        multipartModels: [aMultipartSet()],
      });

      expect(await screen.findByRole("link", { name: /Dragon figure/ })).toBeVisible();
      expect(screen.getByText("Calibration cube")).toBeVisible();
      expect(screen.getByText("Dragon body")).toBeVisible();
      expect(screen.getByText("Multipart")).toBeVisible();
      expect(screen.queryByRole("tab", { name: "Multipart sets" })).not.toBeInTheDocument();
    });

    it("keeps a root Model visible when its set belongs to another collection", async () => {
      window.localStorage.setItem("ps-vault-library-view", "organized");
      const nestedSet = aMultipartSet({ collection: "figures", collection_id: 1 });
      renderVault({
        collections: [aCollection({ id: 1, name: "Figures", slug: "figures", path: "figures" })],
        models: [
          aModelListItem({ id: 1, name: "Dragon body", collection: null }),
          aModelListItem({ id: 2, name: "Calibration cube", collection: null }),
        ],
        routes: {
          "GET /api/v1/multipart-models": (url) =>
            json(
              new URL(url, "http://printstash.test").searchParams.has("direct") ? [] : [nestedSet],
            ),
        },
      });

      expect(await screen.findByText("Calibration cube")).toBeVisible();
      expect(await screen.findByText("Dragon body")).toBeVisible();
    });

    it("reveals reusable component models in the everything view", async () => {
      const user = userEvent.setup();
      window.localStorage.setItem("ps-vault-library-view", "organized");
      renderVault({
        models: [aModelListItem({ id: 1, name: "Dragon body" })],
        multipartModels: [aMultipartSet()],
      });

      await screen.findByRole("link", { name: /Dragon figure/ });
      await user.click(screen.getAllByRole("button", { name: "Everything" })[0]);

      expect(await screen.findByText("Dragon body")).toBeVisible();
      expect(screen.getByRole("link", { name: /Dragon figure/ })).toHaveAttribute(
        "href",
        "/multipart-models/40?return=%2F%3Ftype%3Dall%26sort%3Ddate-desc%26v%3Dmodels",
      );
    });

    it("sorts the unified library by name", async () => {
      window.localStorage.setItem("ps-vault-sort", "name-asc");
      renderVault({
        models: [aModelListItem({ id: 2, name: "Alpha calibration" })],
        multipartModels: [aMultipartSet({ name: "Zebra figure", member_model_ids: [] })],
      });

      const model = await screen.findByText("Alpha calibration");
      const multipart = screen.getByRole("link", { name: /Zebra figure/ });

      expect(model.compareDocumentPosition(multipart) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(
        0,
      );
    });

    it("shows only groupings in Multipart Sets", async () => {
      const user = userEvent.setup();
      renderVault({
        models: [
          aModelListItem({ id: 1, name: "Dragon body" }),
          aModelListItem({ id: 2, name: "Calibration cube" }),
        ],
        multipartModels: [aMultipartSet()],
      });

      await screen.findByRole("link", { name: /Dragon figure/ });
      await user.click(screen.getAllByRole("button", { name: "Multipart sets only" })[0]);

      await waitFor(() => expect(screen.queryByText("Dragon body")).not.toBeInTheDocument());
      expect(screen.queryByText("Calibration cube")).not.toBeInTheDocument();
      expect(screen.getByRole("link", { name: /Dragon figure/ })).toBeVisible();
    });

    it("reveals a matching Model with a retired Organized preference", async () => {
      window.localStorage.setItem("ps-vault-library-view", "organized");
      renderVault({
        at: "/?q=dragon",
        models: [aModelListItem({ id: 1, name: "Dragon body" })],
        multipartModels: [aMultipartSet()],
      });

      expect(await screen.findByText("Dragon body")).toBeVisible();
    });

    it("avoids the retired global grouping request", async () => {
      window.localStorage.setItem("ps-vault-library-view", "organized");
      renderVault({
        models: [aModelListItem({ id: 1, name: "Dragon body" })],
        routes: {
          "GET /api/v1/multipart-models": (url) =>
            new URL(url, "http://printstash.test").searchParams.has("direct")
              ? json([])
              : json({ detail: "multipart_grouping_unavailable" }, 503),
        },
      });

      expect(await screen.findByText("Dragon body")).toBeVisible();
      expect(screen.queryByText("[503] multipart_grouping_unavailable")).not.toBeInTheDocument();
    });

    it("keeps the normal workspace chrome for multipart-only bookmarks", async () => {
      renderVault({ at: "/?v=multipart", multipartModels: [aMultipartSet()] });

      expect(await screen.findByRole("heading", { name: "All Models" })).toBeInTheDocument();
      expect(screen.getByRole("tab", { name: "Models" })).toHaveAttribute("aria-selected", "true");
      expect(screen.getAllByRole("button", { name: "Multipart sets only" })[0]).toHaveAttribute(
        "aria-pressed",
        "true",
      );
      await openFilters();
      expect(screen.getByText("Printer")).toBeInTheDocument();
      expect(screen.getAllByRole("button", { name: "Upload" })).not.toHaveLength(0);
      await openLibraryTools();
      expect(screen.getAllByRole("button", { name: "New multipart set" })).not.toHaveLength(0);
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

      await openLibraryTools();
      await screen.findByRole("button", { name: "Select" });
      expect(uploadButton()).toBeEnabled();
    });
  });

  describe("selection", () => {
    it("enters select mode on request", async () => {
      const user = userEvent.setup();
      renderVault({ models: [aModelListItem({ name: "Benchy" })] });
      await screen.findByText("Benchy");

      await openLibraryTools();
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
      await openLibraryTools();
      await user.click(screen.getByRole("button", { name: /Select/ }));

      await user.click(screen.getByRole("checkbox", { name: "Select Benchy" }));

      // The count renders in both the desktop toolbar and the mobile bar.
      expect(await screen.findAllByText(/1 selected/)).not.toHaveLength(0);
    });
  });

  describe("recent folders", () => {
    it("remembers a visited folder by its names", async () => {
      renderVault({
        at: "/?c=parts%2Fbrackets",
        collections: [
          aCollection({ id: 1, name: "Parts", path: "parts" }),
          aCollection({ id: 2, name: "Brackets", path: "parts/brackets", parent_id: 1 }),
        ],
      });

      await waitFor(() =>
        expect(window.localStorage.getItem("ps-recent-folders-labelled")).toBe(
          JSON.stringify([["parts/brackets", "Parts/Brackets"]]),
        ),
      );
    });

    it("still offers folders an older version remembered", async () => {
      // Those were stored as bare paths, before labels were kept (#295).
      const user = userEvent.setup();
      window.localStorage.setItem("ps-recent-folders", JSON.stringify(["parts"]));
      renderVault({ models: [aModelListItem({ name: "Benchy" })] });
      await screen.findByText("Benchy");

      await user.click(screen.getAllByRole("button", { name: /Recent/ }).at(-1)!);

      expect(await screen.findByRole("menuitem", { name: "parts" })).toBeInTheDocument();
    });

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

      await openLibraryTools();
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

      await openLibraryTools();
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

      await openLibraryTools();
      await user.click(screen.getByRole("button", { name: /New collection/ }));

      expect(screen.getByRole("button", { name: "Create" })).toBeDisabled();
    });

    it("abandons the form on cancel", async () => {
      const user = userEvent.setup();
      renderVault({ models: [aModelListItem({ name: "Benchy" })] });
      await screen.findByText("Benchy");
      await openLibraryTools();
      await user.click(screen.getByRole("button", { name: /New collection/ }));

      await user.click(screen.getByRole("button", { name: "Cancel" }));

      expect(screen.queryByPlaceholderText("Collection name...")).toBeNull();
    });
  });

  describe("acting on several models at once", () => {
    async function selectBoth(user: ReturnType<typeof userEvent.setup>) {
      await screen.findByText("Benchy");
      await openLibraryTools();
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
        routes: {
          "POST /api/v1/models/batch/move": json({
            succeeded_ids: [1, 2],
            succeeded_versions: {
              1: anEditingBase({ edit_version: 9 }),
              2: anEditingBase({ edit_version: 9 }),
            },
            succeeded_count: 2,
            failed: [],
            failed_count: 0,
          }),
        },
      });
      await selectBoth(user);

      await user.click(screen.getByRole("button", { name: "Move" }));
      const dialog = await screen.findByRole("dialog");
      await user.click(await within(dialog).findByRole("option", { name: "None (root)" }));
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
      await openLibraryTools();
      await user.click(screen.getByRole("button", { name: "Select" }));

      await user.click(screen.getByRole("button", { name: "Done" }));

      expect(screen.queryAllByRole("checkbox", { name: /^Select / })).toHaveLength(0);
    });

    it.each([
      { label: "selects every model through valid browse pages", emptyMiddle: false },
      { label: "selects models beyond an empty continuation page", emptyMiddle: true },
    ])("$label", async ({ emptyMiddle }) => {
      const user = userEvent.setup();
      const first = aModelListItem({ id: 1, name: "Benchy" });
      const later = aModelListItem({ id: 2, name: "Cube" });
      const group = aMultipartSet();
      let selecting = false;
      const { requests } = renderVault({
        models: [first],
        routes: {
          "GET /api/v1/models/browse": (url) => {
            const params = new URL(url, "http://test").searchParams;
            // BrowseQuery independently defines this wire limit. The stand-in
            // must reject requests the actual FastAPI endpoint would reject.
            const limit = Number(params.get("limit"));
            if (limit < 1 || limit > 100) return json({ detail: "invalid_limit" }, 422);
            const cursor = params.get("cursor");
            const empty = selecting && emptyMiddle && cursor === "next";
            return json({
              items: empty
                ? []
                : cursor
                  ? [{ kind: "model", model: later }]
                  : [
                      { kind: "model", model: first },
                      { kind: "multipart", multipart: group },
                    ],
              total: 3,
              next_cursor: empty ? "last" : cursor ? null : "next",
              browse_revision: "r1",
              authorization_revision: "a1",
            });
          },
        },
      });
      await screen.findByText("Benchy");
      await openLibraryTools();
      await user.click(screen.getByRole("button", { name: "Select" }));
      selecting = true;
      await user.click(screen.getByRole("button", { name: /Select all matching models/ }));

      expect(await screen.findAllByText("2 selected")).not.toHaveLength(0);
      const pages = requests().filter((call) => call.url.includes("/models/browse?"));
      expect(
        pages.some(
          (call) =>
            new URL(call.url, "http://test").searchParams.get("cursor") ===
            (emptyMiddle ? "last" : "next"),
        ),
      ).toBe(true);
      expect(screen.getByRole("checkbox", { name: "Select Benchy" })).toBeChecked();
    });

    it("preserves selection when continuation becomes stale", async () => {
      const user = userEvent.setup();
      const models = [
        aModelListItem({ id: 1, name: "Benchy" }),
        aModelListItem({ id: 2, name: "Cube" }),
      ];
      let selecting = false;
      const { requests } = renderVault({
        models,
        routes: {
          "GET /api/v1/models/browse": (url) => {
            const params = new URL(url, "http://test").searchParams;
            const limit = Number(params.get("limit"));
            if (limit < 1 || limit > 100) return json({ detail: "invalid_limit" }, 422);
            if (params.has("cursor")) return json({ detail: "browse_refresh_required" }, 409);
            return json({
              items: (selecting ? models.slice(0, 1) : models).map((model) => ({
                kind: "model",
                model,
              })),
              total: 2,
              next_cursor: selecting ? "stale" : null,
              browse_revision: "r1",
              authorization_revision: "a1",
            });
          },
        },
      });
      await selectBoth(user);
      selecting = true;
      await user.click(screen.getByRole("button", { name: /Select all matching models/ }));

      await waitFor(() =>
        expect(requests().some((call) => call.url.includes("cursor=stale"))).toBe(true),
      );
      await waitFor(() =>
        expect(screen.getByRole("button", { name: /Select all matching models/ })).toBeEnabled(),
      );
      expect(screen.getByRole("checkbox", { name: "Select Benchy" })).toBeChecked();
      expect(screen.getByRole("checkbox", { name: "Select Cube" })).toBeChecked();
      expect(screen.getAllByText("2 selected")).not.toHaveLength(0);
    });
  });

  describe("acting on several folders at once", () => {
    /** Turn on select mode and tick the folder card. */
    async function selectFolder(user: ReturnType<typeof userEvent.setup>, name = "Parts") {
      await screen.findAllByText(name);
      await openLibraryTools();
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
      expect(await screen.findByText("Renamed 1 folder")).toBeInTheDocument();
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

      await user.click(await within(dialog).findByRole("option", { name: /Spares/ }));
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
      await openLibraryTools();
      await user.click(screen.getByRole("button", { name: "Select" }));
      await user.click(screen.getByRole("checkbox", { name: "Select Benchy" }));
    }

    it("tags the selection in one request", async () => {
      const user = userEvent.setup();
      const { requests } = renderVault({
        models: [aModelListItem({ id: 1, name: "Benchy" })],
        tags: [aTag()],
        routes: {
          "POST /api/v1/models/batch/tags": json({
            succeeded_ids: [1],
            succeeded_versions: { 1: anEditingBase({ edit_version: 9 }) },
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

    it("opens the upload dialog for a dropped BGCODE file", async () => {
      renderVault({ models: [aModelListItem({ name: "Benchy" })] });
      const main = await screen.findByText("Benchy");

      fireEvent.drop(main, { dataTransfer: dataTransfer([new File(["GCDE"], "part.bgcode")]) });

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
    it("retires the rename draft after a denied list refresh", async () => {
      const user = userEvent.setup();
      const app = renderVault({
        models: [aModelListItem({ name: "Benchy" })],
        routes: { "GET /api/v1/saved-views": json([aSavedView()]) },
      });
      await screen.findByText("Benchy");
      await openLibraryTools();
      await user.click(screen.getByRole("button", { name: /Saved views/ }));
      await user.click(await screen.findByRole("button", { name: "Rename PETG only" }));
      expect(screen.getByDisplayValue("PETG only")).toBeVisible();
      app.route({ "GET /api/v1/saved-views": json({ detail: "not_authenticated" }, 401) });

      await act(async () => {
        await app.client.invalidateQueries({ queryKey: ["saved-views"] });
      });

      expect(screen.queryByDisplayValue("PETG only")).toBeNull();
      expect(screen.queryByRole("button", { name: "PETG only" })).toBeNull();
      expect(
        app.requestsWithMethod("GET").filter((request) => request.url === "/api/v1/saved-views"),
      ).toHaveLength(2);
    });

    it("immediately retires the private create draft with its session", async () => {
      const user = userEvent.setup();
      renderVault({ models: [aModelListItem({ name: "Benchy" })] });
      await screen.findByText("Benchy");
      await openLibraryTools();
      await user.click(screen.getByRole("button", { name: /Saved views/ }));
      await user.click(await screen.findByRole("button", { name: /Save current view/ }));
      const dialog = await screen.findByRole("dialog");
      await user.type(within(dialog).getByRole("textbox"), "Private draft");

      act(() => clearLogin());

      expect(screen.queryByDisplayValue("Private draft")).toBeNull();
    });

    it("recovers the list after retry", async () => {
      const user = userEvent.setup();
      const app = renderVault({
        models: [aModelListItem({ name: "Benchy" })],
        routes: { "GET /api/v1/saved-views": json({ detail: "temporary_failure" }, 503) },
      });
      await screen.findByText("Benchy");
      await openLibraryTools();
      await user.click(screen.getByRole("button", { name: /Saved views/ }));
      await screen.findByText("Could not load saved views");
      expect(screen.queryByText("No saved views yet")).toBeNull();
      app.route({ "GET /api/v1/saved-views": json([aSavedView()]) });

      await user.click(screen.getByRole("button", { name: "Retry saved views" }));

      expect(await screen.findByRole("button", { name: "PETG only" })).toBeVisible();
    });

    it("preserves a newer create name after acknowledgement", async () => {
      const pending = Promise.withResolvers<Response>();
      const user = userEvent.setup();
      const app = renderVault({
        models: [aModelListItem({ name: "Benchy" })],
        routes: { "POST /api/v1/saved-views": () => pending.promise },
      });
      await screen.findByText("Benchy");
      await openLibraryTools();
      await user.click(screen.getByRole("button", { name: /Saved views/ }));
      await user.click(await screen.findByRole("button", { name: /Save current view/ }));
      const dialog = await screen.findByRole("dialog");
      await user.type(within(dialog).getByRole("textbox"), "First name");
      await user.click(within(dialog).getByRole("button", { name: "Save view" }));
      await waitFor(() =>
        expect(
          app.requestsWithMethod("POST").filter((request) => request.url === "/api/v1/saved-views"),
        ).toHaveLength(1),
      );
      await user.clear(within(dialog).getByRole("textbox"));
      await user.type(within(dialog).getByRole("textbox"), "Next draft");

      await act(async () => pending.resolve(json(aSavedView({ name: "First name" }))));

      expect(screen.getByRole("dialog", { name: "Save current view" })).toBeVisible();
      expect(within(dialog).getByRole("textbox")).toHaveValue("Next draft");
    });

    it("saves the library mode", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderVault({
        at: "/?type=multipart",
        multipartModels: [aMultipartSet()],
        routes: { "POST /api/v1/saved-views": json(aSavedView()) },
      });
      await screen.findByRole("link", { name: /Dragon figure/ });
      await openLibraryTools();
      await user.click(screen.getByRole("button", { name: /Saved views/ }));
      await user.click(await screen.findByRole("button", { name: /Save current view/ }));
      const dialog = await screen.findByRole("dialog");
      await user.type(within(dialog).getByRole("textbox"), "Sets");

      await user.click(within(dialog).getByRole("button", { name: "Save view" }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("POST").at(-1)?.body ?? "{}")).toMatchObject({
          filters: { library_view: "multipart" },
        }),
      );
    });

    it("restores the saved library mode", async () => {
      const user = userEvent.setup();
      renderVault({
        at: "/?type=all",
        models: [aModelListItem({ name: "Benchy" })],
        multipartModels: [aMultipartSet()],
        routes: {
          "GET /api/v1/saved-views": json([
            aSavedView({ filters: { ...EMPTY_VIEW_FILTERS, library_view: "multipart" } }),
          ]),
        },
      });
      await screen.findByText("Benchy");
      await openLibraryTools();
      await user.click(screen.getByRole("button", { name: /Saved views/ }));

      await user.click(await screen.findByRole("button", { name: "PETG only" }));

      expect(screen.getAllByRole("button", { name: "Multipart sets only" })[0]).toHaveAttribute(
        "aria-pressed",
        "true",
      );
    });

    it("saves the current filters under a name", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderVault({
        at: "/?tag=functional",
        models: [aModelListItem({ name: "Benchy" })],
        routes: { "POST /api/v1/saved-views": json(aSavedView({ name: "PETG" })) },
      });
      await screen.findByText("Benchy");

      await openLibraryTools();
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
      await openLibraryTools();
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

      await openLibraryTools();
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

      await openLibraryTools();
      await user.click(screen.getByRole("button", { name: /Saved views/ }));

      expect(await screen.findByRole("button", { name: /Rename Ready to print/ })).toBeVisible();
    });

    it("says so when no view has been saved", async () => {
      const user = userEvent.setup();
      renderVault({ models: [aModelListItem({ name: "Benchy" })] });
      await screen.findByText("Benchy");

      await openLibraryTools();
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
      await openLibraryTools();
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
      await openLibraryTools();
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
      await openLibraryTools();
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
      expect(await screen.findByText("Saved view renamed")).toBeInTheDocument();
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
      await openLibraryTools();
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
      await openLibraryTools();
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
      await openLibraryTools();
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
      await openLibraryTools();
      expect(screen.queryByRole("button", { name: /Saved views/ })).toBeNull();
    });
  });

  describe("clearing filters", () => {
    it("clears all filters while retaining sort", async () => {
      localStorage.setItem("ps-vault-sort", "name-asc");
      const { requests } = renderVault({
        at: "/?file_type=stl&tag=functional&favorites=true&sort=name-asc",
        models: [aModelListItem({ name: "Benchy" })],
      });
      await screen.findByText("Benchy");
      await userEvent.setup().click(screen.getAllByRole("button", { name: /Clear all/ })[0]);
      await waitFor(() => {
        const query = lastModelsQuery(requests);
        expect(query.get("file_type")).toBeNull();
        expect(query.get("tag")).toBeNull();
        expect(query.get("favorites")).toBeNull();
        expect(query.get("sort")).toBe("name-asc");
      });
    });
    it("keeps active tag chips available when controls are collapsed", async () => {
      renderVault({ at: "/?tag=functional", tags: [aTag()] });
      await screen.findByTitle("Remove Tag: functional");
      await userEvent.setup().click(screen.getAllByRole("button", { name: "Filters" }).at(-1)!);
      expect(screen.getByTitle("Remove Tag: functional")).toBeVisible();
      expect(screen.getAllByRole("button", { name: /Clear all/ })[0]).toBeVisible();
    });
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

      await user.click(screen.getByRole("tab", { name: "Documents" }));

      expect(window.localStorage.getItem("printstash.last.view")).toBe("docs");
    });
  });

  describe("pagination", () => {
    it("retries a failed continuation without discarding read pages", async () => {
      const user = userEvent.setup();
      const first = aModelListItem({ id: 1, name: "Already read" });
      const later = aModelListItem({ id: 2, name: "Next page" });
      let rateLimited = true;
      renderVault({
        models: [first],
        routes: {
          "GET /api/v1/models/browse": (url) => {
            const continuation = new URL(url, "http://test").searchParams.has("cursor");
            if (continuation && rateLimited) return json({ detail: "rate_limited" }, 429);
            return json({
              items: [{ kind: "model", model: continuation ? later : first }],
              total: 2,
              next_cursor: continuation ? null : "next",
              browse_revision: "r1",
              authorization_revision: "a1",
            });
          },
        },
      });
      expect(await screen.findByText("Already read")).toBeVisible();
      await user.click(screen.getByRole("button", { name: /Load more/ }));
      expect(await screen.findByText("[429] rate_limited")).toBeVisible();
      expect(screen.getByText("Already read")).toBeVisible();
      expect(screen.queryByText("Next page")).not.toBeInTheDocument();
      rateLimited = false;
      await user.click(screen.getByRole("button", { name: /Load more/ }));
      expect(await screen.findByText("Next page")).toBeVisible();
      expect(screen.getByText("Already read")).toBeVisible();
      expect(screen.queryByText("[429] rate_limited")).not.toBeInTheDocument();
    });

    it("keeps a wide folder level to one page until more is requested", async () => {
      const user = userEvent.setup();
      const first = aCollectionNode();
      const second = aCollectionNode({
        id: 2,
        name: "Tools",
        slug: "tools",
        path: "tools",
        display_path: "Tools",
      });
      const { requests } = renderVault({
        routes: {
          "GET /api/v1/collections/children": (url) =>
            new URL(url, "http://test").searchParams.has("cursor")
              ? json({ items: [second], next_cursor: null })
              : json({ items: [first], next_cursor: "next" }),
        },
      });

      const main = within(screen.getByRole("main"));
      expect(await main.findByText("Parts")).toBeInTheDocument();
      expect(main.queryByText("Tools")).not.toBeInTheDocument();
      expect(
        requests().filter((call) => call.url.startsWith("/api/v1/collections/children")),
      ).toHaveLength(1);

      await user.click(main.getByRole("button", { name: "Show more folders" }));
      expect(await main.findByText("Tools")).toBeInTheDocument();
      expect(
        requests().filter((call) => call.url.startsWith("/api/v1/collections/children")),
      ).toHaveLength(2);
    });

    it("preserves server order when another mixed page arrives", async () => {
      const user = userEvent.setup();
      renderVault({
        at: "/?type=all&sort=name-asc",
        routes: {
          "GET /api/v1/models/browse": (url) =>
            json({
              items: url.includes("cursor=")
                ? [{ kind: "model", model: aModelListItem({ id: 2, name: "Älpha" }) }]
                : [{ kind: "multipart", multipart: aMultipartSet({ name: "Zeta" }) }],
              total: 2,
              next_cursor: url.includes("cursor=") ? null : "next",
              browse_revision: "r1",
              authorization_revision: "a1",
            }),
        },
      });
      const first = await screen.findByRole("link", { name: /Zeta/ });

      await user.click(screen.getByRole("button", { name: /Load more/ }));

      const second = await screen.findByText("Älpha");
      expect(first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);
    });

    it("reaches matching results after an empty browse page", async () => {
      const user = userEvent.setup();
      renderVault({
        at: "/?file_type=stl",
        routes: {
          "GET /api/v1/models/browse": (url) =>
            json({
              items: url.includes("cursor=")
                ? [{ kind: "model", model: aModelListItem({ name: "Match" }) }]
                : [],
              total: 1,
              next_cursor: url.includes("cursor=") ? null : "next",
              browse_revision: "r1",
              authorization_revision: "a1",
            }),
        },
      });

      await user.click(await screen.findByRole("button", { name: /Load more/ }));

      expect(await screen.findByText("Match")).toBeVisible();
    });

    it("removes a confirmed favorite from the displayed grid", async () => {
      const pending = Promise.withResolvers<Response>();
      const user = userEvent.setup();
      renderVault({
        at: "/?favorites=true",
        models: [aModelListItem({ name: "Favorite bracket", starred: true })],
        routes: {
          "DELETE /api/v1/models/1/star": () => pending.promise,
        },
      });
      await screen.findByText("Favorite bracket");

      await user.click(
        screen.getByRole("button", { name: "Remove Favorite bracket from favorites" }),
      );
      expect(screen.getByText("Favorite bracket")).toBeVisible();
      pending.resolve(json({ model_id: 1, starred: false }));

      await waitFor(() => expect(screen.queryByText("Favorite bracket")).not.toBeInTheDocument());
    });

    it("offers more when the page reports a cursor", async () => {
      renderVault({
        models: [aModelListItem({ name: "Benchy" })],
        routes: {
          "GET /api/v1/models/browse": json({
            items: [{ kind: "model", model: aModelListItem({ name: "Benchy" }) }],
            browse_revision: "r1",
            authorization_revision: "a1",
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
  describe("Library refresh projection", () => {
    const changedRevision = json({ browse_revision: "r2", authorization_revision: "a1" });
    function modelPage(name: string) {
      return json({
        items: [{ kind: "model", model: aModelListItem({ name }) }],
        next_cursor: null,
        total: 1,
        browse_revision: "r2",
        authorization_revision: "a1",
      });
    }
    function folderPage(name: string, id = 1) {
      return json({
        items: [aCollectionNode({ id, name, path: name.toLowerCase(), model_count: 7 })],
        next_cursor: null,
      });
    }
    function mainHas(name: string) {
      return within(screen.getByRole("main")).queryByText(name);
    }

    it("waits for refreshed folders before replacing models", async () => {
      const user = userEvent.setup();
      const pending = Promise.withResolvers<Response>();
      const app = renderVault({
        models: [aModelListItem({ name: "Original model" })],
        collections: [aCollection({ name: "Original folder" })],
        routes: { "GET /api/v1/models/browse/revision": changedRevision },
      });
      await screen.findByRole("button", { name: "Refresh library" });
      app.route({
        "GET /api/v1/models/browse": modelPage("Current model"),
        "GET /api/v1/collections/children": () => pending.promise,
      });
      await user.click(screen.getByRole("button", { name: "Refresh library" }));
      await waitFor(() =>
        expect(
          app.requests().filter((call) => call.url.includes("/collections/children")),
        ).toHaveLength(2),
      );
      expect(mainHas("Original model")).toBeVisible();
      expect(mainHas("Original folder")).toBeVisible();
      expect(mainHas("Current model")).not.toBeInTheDocument();
      pending.resolve(folderPage("Current folder"));
      expect(await within(screen.getByRole("main")).findByText("Current folder")).toBeVisible();
      expect(mainHas("Current model")).toBeVisible();
      expect(mainHas("Original folder")).not.toBeInTheDocument();
    });

    it("waits for refreshed models before replacing folders", async () => {
      const user = userEvent.setup();
      const pending = Promise.withResolvers<Response>();
      const app = renderVault({
        models: [aModelListItem({ name: "Original model" })],
        collections: [aCollection({ name: "Original folder" })],
        routes: { "GET /api/v1/models/browse/revision": changedRevision },
      });
      await screen.findByRole("button", { name: "Refresh library" });
      app.route({
        "GET /api/v1/models/browse": () => pending.promise,
        "GET /api/v1/collections/children": folderPage("Current folder"),
      });
      await user.click(screen.getByRole("button", { name: "Refresh library" }));
      await waitFor(() =>
        expect(
          app.requests().filter((call) => call.url.includes("/collections/children")),
        ).toHaveLength(2),
      );
      expect(mainHas("Original model")).toBeVisible();
      expect(mainHas("Original folder")).toBeVisible();
      expect(mainHas("Current folder")).not.toBeInTheDocument();
      pending.resolve(modelPage("Current model"));
      expect(await screen.findByText("Current model")).toBeVisible();
      expect(mainHas("Current folder")).toBeVisible();
    });

    it("refreshes the selected folder lookup", async () => {
      const user = userEvent.setup();
      const original = [
        aCollection({ id: 1, name: "Original parent", path: "parts" }),
        aCollection({ id: 2, name: "Original child", path: "parts/child", parent_id: 1 }),
      ];
      const app = renderVault({
        at: "/?c=parts/child",
        models: [aModelListItem({ name: "Original model" })],
        collections: original,
        routes: { "GET /api/v1/models/browse/revision": changedRevision },
      });
      await screen.findByRole("button", { name: "Refresh library" });
      app.route({
        ...collectionTreeRoutes([
          aCollection({ ...original[0], name: "Current parent" }),
          aCollection({ ...original[1], name: "Current child", model_count: 9 }),
        ]),
        "GET /api/v1/models/browse": modelPage("Current model"),
      });
      await user.click(screen.getByRole("button", { name: "Refresh library" }));
      expect(await screen.findByRole("heading", { name: "Current child" })).toBeVisible();
      expect(
        within(screen.getByRole("main")).getByRole("button", { name: "Current parent" }),
      ).toBeVisible();
      expect(mainHas("Current model")).toBeVisible();
    });

    it("keeps folder metadata with the displayed snapshot", async () => {
      const user = userEvent.setup();
      const pending = Promise.withResolvers<Response>();
      const app = renderVault({
        at: "/?c=parts",
        models: [aModelListItem({ name: "Original model" })],
        collections: [aCollection({ tags: ["original-tag"] })],
        routes: { "GET /api/v1/models/browse/revision": changedRevision },
      });
      await screen.findByRole("button", { name: "Refresh library" });
      app.route({
        ...collectionTreeRoutes([aCollection({ tags: ["current-tag"] })]),
        "GET /api/v1/models/browse": () => pending.promise,
      });
      await user.click(screen.getByRole("button", { name: "Refresh library" }));
      await waitFor(() =>
        expect(
          app
            .requests()
            .filter(
              (call) => new URL(call.url, "http://test").pathname === "/api/v1/models/browse",
            ),
        ).toHaveLength(2),
      );
      expect(mainHas("original-tag")).toBeVisible();
      expect(mainHas("current-tag")).not.toBeInTheDocument();
      pending.resolve(modelPage("Current model"));
      expect(await within(screen.getByRole("main")).findByText("current-tag")).toBeVisible();
      expect(mainHas("Current model")).toBeVisible();
    });

    it("follows the refreshed folder identity", async () => {
      const user = userEvent.setup();
      const app = renderVault({
        at: "/?c=parts",
        models: [aModelListItem({ name: "Original model" })],
        collections: [aCollection()],
        routes: { "GET /api/v1/models/browse/revision": changedRevision },
      });
      await screen.findByRole("button", { name: "Refresh library" });
      app.route({
        ...collectionTreeRoutes([
          aCollection({ id: 7, name: "Replacement", path: "parts" }),
          aCollection({ id: 8, name: "Replacement child", path: "parts/child", parent_id: 7 }),
        ]),
        "GET /api/v1/models/browse": modelPage("Current model"),
      });
      await user.click(screen.getByRole("button", { name: "Refresh library" }));
      expect(await within(screen.getByRole("main")).findByText("Replacement child")).toBeVisible();
      expect(
        app
          .requests()
          .some(
            (call) =>
              call.url.includes("/collections/children") &&
              new URL(call.url, "http://test").searchParams.get("parent_id") === "7",
          ),
      ).toBe(true);
    });

    it("refreshes active folder search results", async () => {
      const user = userEvent.setup();
      const app = renderVault({
        at: "/?q=gear",
        models: [aModelListItem({ name: "Original model" })],
        collections: [aCollection({ name: "Gear original" })],
        routes: { "GET /api/v1/models/browse/revision": changedRevision },
      });
      await screen.findByRole("button", { name: "Refresh library" });
      app.route({
        "GET /api/v1/models/browse": modelPage("Current model"),
        "GET /api/v1/collections/search": folderPage("Gear current"),
      });
      await user.click(screen.getByRole("button", { name: "Refresh library" }));
      expect(await within(screen.getByRole("main")).findByText("Gear current")).toBeVisible();
      expect(mainHas("Gear original")).not.toBeInTheDocument();
    });

    it("restarts refreshed folder pagination", async () => {
      const user = userEvent.setup();
      const app = renderVault({
        models: [aModelListItem({ name: "Original model" })],
        routes: {
          "GET /api/v1/models/browse/revision": changedRevision,
          "GET /api/v1/collections/children": (url) =>
            json({
              items: [
                aCollectionNode({
                  id: url.includes("cursor=") ? 2 : 1,
                  name: url.includes("cursor=") ? "Second folder" : "First folder",
                  path: url.includes("cursor=") ? "second" : "first",
                }),
              ],
              next_cursor: url.includes("cursor=") ? null : "old-cursor",
            }),
        },
      });
      await user.click(await screen.findByRole("button", { name: "Show more folders" }));
      await within(screen.getByRole("main")).findByText("Second folder");
      app.route({
        "GET /api/v1/models/browse": modelPage("Current model"),
        "GET /api/v1/collections/children": folderPage("Fresh first"),
      });
      await user.click(screen.getByRole("button", { name: "Refresh library" }));
      expect(await within(screen.getByRole("main")).findByText("Fresh first")).toBeVisible();
      expect(mainHas("Second folder")).not.toBeInTheDocument();
      const childrenReads = app
        .requests()
        .filter((call) => call.url.includes("/collections/children"));
      expect(childrenReads.filter((call) => call.url.includes("cursor="))).toHaveLength(1);
      expect(childrenReads.at(-1)?.url).not.toContain("cursor=");
    });

    it("retries a failed folder refresh", async () => {
      const user = userEvent.setup();
      const app = renderVault({
        models: [aModelListItem({ name: "Original model" })],
        collections: [aCollection({ name: "Original folder" })],
        routes: { "GET /api/v1/models/browse/revision": changedRevision },
      });
      await screen.findByRole("button", { name: "Refresh library" });
      app.route({
        "GET /api/v1/models/browse": modelPage("Current model"),
        "GET /api/v1/collections/children": json({ detail: "folder unavailable" }, 503),
      });
      await user.click(screen.getByRole("button", { name: "Refresh library" }));
      await screen.findByRole("button", { name: "Retry" });
      expect(mainHas("Original model")).toBeVisible();
      expect(mainHas("Original folder")).toBeVisible();
      expect(mainHas("Current model")).not.toBeInTheDocument();
      app.route({ "GET /api/v1/collections/children": folderPage("Current folder") });
      await user.click(screen.getByRole("button", { name: "Retry" }));
      expect(await within(screen.getByRole("main")).findByText("Current folder")).toBeVisible();
      expect(mainHas("Current model")).toBeVisible();
    });

    it("retries a failed lookup refresh", async () => {
      const user = userEvent.setup();
      const app = renderVault({
        at: "/?c=parts",
        models: [aModelListItem({ name: "Original model" })],
        collections: [aCollection()],
        routes: { "GET /api/v1/models/browse/revision": changedRevision },
      });
      await screen.findByRole("button", { name: "Refresh library" });
      app.route({
        "GET /api/v1/models/browse": modelPage("Current model"),
        "GET /api/v1/collections/lookup": json({ detail: "lookup unavailable" }, 503),
      });
      await user.click(screen.getByRole("button", { name: "Refresh library" }));
      await screen.findByRole("button", { name: "Retry" });
      expect(screen.getByRole("heading", { name: "Parts" })).toBeVisible();
      expect(mainHas("Original model")).toBeVisible();
      app.route(collectionTreeRoutes([aCollection({ name: "Current folder" })]));
      await user.click(screen.getByRole("button", { name: "Retry" }));
      expect(await screen.findByRole("heading", { name: "Current folder" })).toBeVisible();
      expect(mainHas("Current model")).toBeVisible();
    });

    it("retires a missing folder snapshot", async () => {
      const user = userEvent.setup();
      const retry = Promise.withResolvers<Response>();
      const app = renderVault({
        at: "/?c=parts",
        models: [aModelListItem({ name: "Private model" })],
        collections: [aCollection({ name: "Private folder", tags: ["private-tag"] })],
        routes: { "GET /api/v1/models/browse/revision": changedRevision },
      });
      await screen.findByRole("button", { name: "Refresh library" });
      expect(screen.getByRole("heading", { name: "Private folder" })).toBeVisible();
      app.route({
        "GET /api/v1/collections/lookup": json({ detail: "collection_not_found" }, 404),
      });
      await user.click(screen.getByRole("button", { name: "Refresh library" }));
      await screen.findByRole("button", { name: "Retry" });
      expect(mainHas("Private model")).not.toBeInTheDocument();
      expect(mainHas("private-tag")).not.toBeInTheDocument();
      expect(
        within(screen.getByRole("main")).queryByRole("heading", { name: "Private folder" }),
      ).not.toBeInTheDocument();
      expect(
        within(screen.getByRole("main")).queryByRole("button", { name: "Private folder" }),
      ).not.toBeInTheDocument();
      app.route({ "GET /api/v1/collections/lookup": () => retry.promise });
      await user.click(screen.getByRole("button", { name: "Retry" }));
      expect(mainHas("Private model")).not.toBeInTheDocument();
      expect(mainHas("private-tag")).not.toBeInTheDocument();
      expect(
        within(screen.getByRole("main")).queryByRole("heading", { name: "Private folder" }),
      ).not.toBeInTheDocument();
      expect(
        within(screen.getByRole("main")).queryByRole("button", { name: "Private folder" }),
      ).not.toBeInTheDocument();
      retry.resolve(json({ detail: "collection_not_found" }, 404));
      await screen.findByRole("button", { name: "Retry" });
    });

    it("ignores a refresh across rapid destinations", async () => {
      const user = userEvent.setup();
      const pending = Promise.withResolvers<Response>();
      const middle = Promise.withResolvers<Response>();
      const app = renderVault({
        models: [aModelListItem({ name: "Original model" })],
        collections: [
          aCollection(),
          aCollection({ id: 2, name: "Tools", path: "tools", slug: "tools" }),
        ],
        routes: { "GET /api/v1/models/browse/revision": changedRevision },
      });
      await screen.findByRole("button", { name: "Refresh library" });
      app.route({
        "GET /api/v1/models/browse": (url) => {
          const collection = new URL(url, "http://test").searchParams.get("collection");
          return collection === "parts"
            ? middle.promise
            : collection === "tools"
              ? modelPage("Destination model")
              : pending.promise;
        },
      });
      await user.click(screen.getByRole("button", { name: "Refresh library" }));
      await user.click(within(screen.getByRole("main")).getByRole("button", { name: /Parts/ }));
      await waitFor(() =>
        expect(
          app
            .requests()
            .some(
              (call) =>
                new URL(call.url, "http://test").pathname === "/api/v1/models/browse" &&
                new URL(call.url, "http://test").searchParams.get("collection") === "parts",
            ),
        ).toBe(true),
      );
      await user.click(within(screen.getByRole("main")).getByRole("button", { name: /Tools/ }));
      await screen.findByText("Destination model");
      middle.resolve(modelPage("Middle model"));
      pending.resolve(modelPage("Retired model"));
      await act(async () => {
        await Promise.all([pending.promise, middle.promise]);
      });
      expect(mainHas("Destination model")).toBeVisible();
      expect(screen.getByRole("heading", { name: "Tools" })).toBeVisible();
      expect(mainHas("Middle model")).not.toBeInTheDocument();
      expect(mainHas("Retired model")).not.toBeInTheDocument();
    });

    it("retires a refresh with its session", async () => {
      const user = userEvent.setup();
      const pending = Promise.withResolvers<Response>();
      const app = renderVault({
        models: [aModelListItem({ name: "Private model" })],
        routes: { "GET /api/v1/models/browse/revision": changedRevision },
      });
      await screen.findByRole("button", { name: "Refresh library" });
      app.route({ "GET /api/v1/models/browse": () => pending.promise });
      await user.click(screen.getByRole("button", { name: "Refresh library" }));
      act(() => clearLogin());
      pending.resolve(modelPage("Retired model"));
      await act(async () => {
        await pending.promise;
      });
      expect(screen.queryByText("Private model")).not.toBeInTheDocument();
      expect(screen.queryByText("Retired model")).not.toBeInTheDocument();
    });
  });

  describe("Library authority", () => {
    it("keeps displayed rows until explicit refresh", async () => {
      const user = userEvent.setup();
      const app = renderVault({
        models: [aModelListItem({ name: "Original bracket" })],
        routes: {
          "GET /api/v1/models/browse/revision": json({
            browse_revision: "r2",
            authorization_revision: "a1",
          }),
        },
      });
      await screen.findByText("The library has changed. Refresh before loading more results.");
      expect(screen.getByText("Original bracket")).toBeVisible();
      expect(
        app
          .requests()
          .filter(
            (request) => new URL(request.url, "http://test").pathname === "/api/v1/models/browse",
          ),
      ).toHaveLength(1);
      app.route({
        "GET /api/v1/models/browse": json({
          items: [{ kind: "model", model: aModelListItem({ name: "Current bracket" }) }],
          next_cursor: null,
          total: 1,
          browse_revision: "r2",
          authorization_revision: "a1",
        }),
      });

      await user.click(screen.getByRole("button", { name: "Refresh library" }));

      expect(await screen.findByText("Current bracket")).toBeVisible();
      expect(screen.queryByText("Original bracket")).not.toBeInTheDocument();
      expect(
        screen.queryByText("The library has changed. Refresh before loading more results."),
      ).not.toBeInTheDocument();
    });

    it("restarts a rejected continuation from the first page", async () => {
      const user = userEvent.setup();
      const app = renderVault({
        routes: {
          "GET /api/v1/models/browse": (url) =>
            url.includes("cursor=")
              ? json({ detail: "browse_refresh_required" }, 409)
              : json({
                  items: [{ kind: "model", model: aModelListItem({ name: "Original bracket" }) }],
                  next_cursor: "old",
                  total: 2,
                  browse_revision: "r1",
                  authorization_revision: "a1",
                }),
        },
      });
      await user.click(await screen.findByRole("button", { name: /Load more/ }));
      await screen.findByText("The library has changed. Refresh before loading more results.");
      expect(screen.getByText("Original bracket")).toBeVisible();
      app.route({
        "GET /api/v1/models/browse": json({
          items: [{ kind: "model", model: aModelListItem({ name: "Current bracket" }) }],
          next_cursor: null,
          total: 1,
          browse_revision: "r1",
          authorization_revision: "a1",
        }),
      });

      await user.click(screen.getByRole("button", { name: "Refresh library" }));

      expect(await screen.findByText("Current bracket")).toBeVisible();
      const reads = app
        .requests()
        .filter(
          (request) => new URL(request.url, "http://test").pathname === "/api/v1/models/browse",
        );
      expect(reads.filter((request) => request.url.includes("cursor="))).toHaveLength(1);
      expect(reads.at(-1)?.url).not.toContain("cursor=");
    });

    it("hides private content when authorization changes", async () => {
      const authority = Promise.withResolvers<Response>();
      renderVault({
        auth: adminSession({ refresh: async () => {} }),
        models: [aModelListItem({ name: "Private bracket" })],
        collections: [aCollection({ name: "Private folder" })],
        routes: { "GET /api/v1/models/browse/revision": () => authority.promise },
      });
      await screen.findByText("Private bracket");
      authority.resolve(json({ browse_revision: "r2", authorization_revision: "a2" }));
      await waitFor(() => expect(screen.queryByText("Private bracket")).not.toBeInTheDocument());
      expect(screen.queryByText("Private folder")).not.toBeInTheDocument();
    });

    it("retries an unavailable authority check", async () => {
      const user = userEvent.setup();
      const app = renderVault({
        models: [aModelListItem({ name: "Original bracket" })],
        routes: { "GET /api/v1/models/browse/revision": json({ detail: "unavailable" }, 503) },
      });
      await screen.findByText("Could not check whether the library is up to date.");
      expect(screen.getByText("Original bracket")).toBeVisible();
      app.route({
        "GET /api/v1/models/browse/revision": json({
          browse_revision: "r1",
          authorization_revision: "a1",
        }),
      });

      await user.click(screen.getByRole("button", { name: "Check again" }));

      await waitFor(() =>
        expect(
          screen.queryByText("Could not check whether the library is up to date."),
        ).not.toBeInTheDocument(),
      );
      expect(
        app
          .requests()
          .filter(
            (request) => new URL(request.url, "http://test").pathname === "/api/v1/models/browse",
          ),
      ).toHaveLength(1);
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

      await openLibraryTools();
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

      await openLibraryTools();
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
            succeeded_versions: { 1: anEditingBase({ edit_version: 9 }) },
            succeeded_count: 1,
            failed: [],
            failed_count: 0,
          }),
        },
      });
      await screen.findByText("Benchy");
      await openLibraryTools();
      await user.click(screen.getByRole("button", { name: "Select" }));
      await user.click(screen.getByRole("checkbox", { name: "Select Benchy" }));
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
            succeeded_versions: { 1: anEditingBase({ edit_version: 9 }) },
            succeeded_count: 1,
            failed: [],
            failed_count: 0,
          }),
          "PATCH /api/v1/models/1": json({ id: 1, tags: ["draft"], edit_version: 12 }),
        },
      });
      await screen.findByText("Benchy");
      await openLibraryTools();
      await user.click(screen.getByRole("button", { name: "Select" }));
      await user.click(screen.getByRole("checkbox", { name: "Select Benchy" }));
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
        getData: () => JSON.stringify(captureModelDrag(aModelListItem({ id }))),
        dropEffect: "",
      };
    }

    it.each(["grid", "list"])(
      "moves with the version captured by the native %s drag",
      async (layout) => {
        window.localStorage.setItem("ps-vault-view", layout);
        const versions: (string | null)[] = [];
        renderVault({
          collections: [aCollection()],
          models: [aModelListItem({ id: 1, name: "Benchy", edit_version: 7 })],
          routes: {
            "PATCH /api/v1/models/1": (_url, init) => {
              versions.push(new Headers(init?.headers).get("If-Match"));
              return json({ id: 1, edit_version: 8, collection: "parts" });
            },
          },
        });
        const folder = await folderCard();
        await screen.findByText("Benchy");
        const values = new Map<string, string>();
        const dataTransfer = {
          types: [MODEL_DND_MIME],
          setData: (kind: string, value: string) => values.set(kind, value),
          getData: (kind: string) => values.get(kind) ?? "",
          effectAllowed: "",
          dropEffect: "",
        };
        fireEvent.dragStart(
          layout === "grid"
            ? screen.getByRole("article")
            : screen.getByRole("link", { name: /Benchy/ }),
          { dataTransfer },
        );
        fireEvent.drop(folder, { dataTransfer });
        await waitFor(() =>
          expect(versions).toEqual(['"model-1-eaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-v7"']),
        );
      },
    );

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

    it.each(["1", "{", "null", JSON.stringify({ id: 1, edit_version: 0 })])(
      "ignores malformed native drag data %s",
      async (raw) => {
        const view = renderVault({
          collections: [aCollection()],
          models: [aModelListItem({ id: 1 })],
        });
        const folder = await folderCard();
        fireEvent.drop(folder, { dataTransfer: { types: [MODEL_DND_MIME], getData: () => raw } });
        expect(view.requestsWithMethod("PATCH")).toHaveLength(0);
      },
    );

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

  describe("undoing a move", () => {
    async function selectAndMove(user: ReturnType<typeof userEvent.setup>) {
      await screen.findByText("Benchy");
      await openLibraryTools();
      await user.click(screen.getByRole("button", { name: "Select" }));
      await user.click(screen.getByLabelText("Select Benchy"));
      await user.click(await screen.findByRole("button", { name: /Move/ }));
      const dialog = await screen.findByRole("dialog");
      await user.click(await within(dialog).findByRole("option", { name: /Spares/ }));
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
            succeeded_versions: { 1: anEditingBase({ edit_version: 9 }) },
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
            succeeded_versions: { 1: anEditingBase({ edit_version: 9 }) },
            succeeded_count: 1,
            failed: [],
            failed_count: 0,
          }),
        },
      });
      await selectAndMove(user);

      const undoButton = await screen.findByRole("button", { name: "Undo" });
      expect(undoButton.closest("li")).toHaveTextContent("Moved 1");
      await user.click(undoButton);

      await waitFor(() =>
        expect(
          requestsWithMethod("POST")
            .filter((request) => request.url.endsWith("/models/batch/move"))
            .map((request) => JSON.parse(request.body)),
        ).toEqual([
          {
            model_ids: [1],
            collection: "spares",
            expected_versions: { 1: anEditingBase({ edit_version: 1 }) },
          },
          {
            model_ids: [1],
            collection: "parts",
            expected_versions: { 1: anEditingBase({ edit_version: 9 }) },
          },
        ]),
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
            succeeded_versions: {},
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
