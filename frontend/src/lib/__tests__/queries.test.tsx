/*
 * The query hooks, and the two properties that decide whether the vault feels
 * broken.
 *
 * **Freshness.** Collections, tags, printers, profiles and vault stats all pass
 * `fresh: true`, because every one of them changes as a *result* of something the
 * user just did. A cached collection list after creating a collection shows the
 * user their new folder missing.
 *
 * **Continuity.** When a filter changes, the outliner and the facet groups must
 * stay mounted while the new data loads. Unmounting them is what produces the
 * layout collapsing and snapping back on every keystroke in a filter box — the
 * data is right either way, so nothing but a test like this notices.
 *
 * The `enabled` gate is asserted in both directions. A hook that fetches while
 * disabled is a request against a route the user may have no role on, which
 * surfaces as a spurious 403 in the console on pages that look fine.
 *
 * **Warm navigation.** Opening a folder must not drop the grid back to its
 * first-load skeleton, and a folder warmed on hover must be read from the cache
 * entry the prefetch filled — a warmer that fills a different key is a request
 * the user pays for twice.
 *
 * Pagination is server-owned: the sort goes to the server and the next cursor is
 * requested only on demand. A client that re-sorted locally would paginate a
 * different order than the one it displays.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";

import {
  QueryApiProvider,
  defaultQueryApi,
  useCollectionChildren,
  useCollectionLookup,
  useCollectionLookupById,
  useCollectionReadme,
  useCollectionSearch,
  useFilamentProfiles,
  useLibraryPrefetch,
  useModelFacets,
  useModelList,
  useMultipartModels,
  usePrinterProfiles,
  usePrinters,
  useOutlinerModels,
  useTags,
  useVaultStats,
  type QueryApi,
} from "@/lib/queries";
import type {
  FilamentProfileRead,
  ModelFacetsRead,
  ModelListItem,
  ModelPageRead,
  MultipartModelListItem,
  OutlinerModelRead,
  PrinterProfileRead,
  TagRead,
  VaultStatsRead,
} from "@/types";
import { aCollectionNode, aPrinter } from "@/test-support/factories";

// The hooks are thin, but they encode two real contracts worth locking down:
// (1) every shared read passes `{ fresh: true }` so TanStack Query — not the
// legacy in-memory cache in request.ts — is the single source of truth, and
// (2) usePrinters honours `enabled` so non-admins don't fetch a list they
// can't use.
//
// The hooks take their api through `QueryApiProvider`, so these stubs stand in
// for exactly the reads the hooks below perform; every other member keeps its
// real implementation and is never reached from here.
const stubs = {
  getCollectionReadme: vi.fn<QueryApi["getCollectionReadme"]>(),
  listCollectionChildren: vi.fn<QueryApi["listCollectionChildren"]>(),
  lookupCollection: vi.fn<QueryApi["lookupCollection"]>(),
  lookupCollectionById: vi.fn<QueryApi["lookupCollectionById"]>(),
  searchCollections: vi.fn<QueryApi["searchCollections"]>(),
  getModelFacets: vi.fn<QueryApi["getModelFacets"]>(),
  getVaultStats: vi.fn<QueryApi["getVaultStats"]>(),
  listFilamentProfiles: vi.fn<QueryApi["listFilamentProfiles"]>(),
  listModelPage: vi.fn<QueryApi["listModelPage"]>(),
  listMultipartModels: vi.fn<QueryApi["listMultipartModels"]>(),
  listOutlinerModels: vi.fn<QueryApi["listOutlinerModels"]>(),
  listPrinterProfiles: vi.fn<QueryApi["listPrinterProfiles"]>(),
  listPrinters: vi.fn<QueryApi["listPrinters"]>(),
  listTags: vi.fn<QueryApi["listTags"]>(),
};

const api: QueryApi = { ...defaultQueryApi, ...stubs };

const TIMESTAMP = "2026-01-01T00:00:00Z";

const tag: TagRead = { id: 1, name: "petg", slug: "petg", model_count: 1 };

const printer = aPrinter({ name: "Voron", moonraker_url: "http://10.0.0.1:7125" });

const printerProfile: PrinterProfileRead = {
  id: 1,
  name: "Ender",
  printer_model: null,
  slicer_name: null,
  nozzle_diameter_mm: null,
  notes: null,
  usage_count: 0,
  created_at: TIMESTAMP,
  updated_at: TIMESTAMP,
};

const filamentProfile: FilamentProfileRead = {
  id: 1,
  name: "PLA",
  material_type: null,
  material_brand: null,
  cost_per_kg: null,
  notes: null,
  usage_count: 0,
  spoolman_filament_id: null,
  density_g_cm3: null,
  diameter_mm: null,
  created_at: TIMESTAMP,
  updated_at: TIMESTAMP,
};

const vaultStats: VaultStatsRead = {
  model_count: 3,
  file_count: 0,
  source_file_count: 0,
  gcode_file_count: 0,
  collection_count: 0,
  tag_count: 0,
  printer_count: 0,
  indexed_size_bytes: 0,
  storage: {
    backend: "local",
    prefix: null,
    bucket: null,
    object_count: 0,
    total_size_bytes: 0,
    ok: true,
    error: null,
  },
};

const emptyPage: ModelPageRead = { items: [], next_cursor: null, total: 0 };

function makeListItem(id: number, name: string): ModelListItem {
  return {
    edit_version: 1,
    id,
    name,
    slug: name.toLowerCase().replaceAll(" ", "-"),
    collection: null,
    collection_id: null,
    collection_label: null,
    source_url: null,
    effective_role: null,
    tags: [],
    thumbnail_url: null,
    file_count: 1,
    mesh_file_id: null,
    printer_presence: [],
    updated_at: TIMESTAMP,
    print_summary: null,
    starred: false,
  };
}

function makeOutlinerModel(id: number, name: string): OutlinerModelRead {
  return { id, name, collection: null, collection_id: null, collection_label: null };
}

function emptyFacets(): ModelFacetsRead {
  return {
    file_type: [],
    material_type: [],
    slicer_name: [],
    printer_model: [],
    revision_status: [],
    print_outcome: [],
    storage: [],
    printed: [],
  };
}

/** `staleTime` mirrors production's 30s when a test depends on freshness. */
function wrapper(options: { staleTime?: number } = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: options.staleTime ?? 0 } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryApiProvider value={api}>
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    </QueryApiProvider>
  );
}

beforeEach(() => {
  stubs.listCollectionChildren.mockResolvedValue({ items: [aCollectionNode()], next_cursor: null });
  stubs.lookupCollection.mockResolvedValue({ collection: aCollectionNode(), ancestors: [] });
  stubs.lookupCollectionById.mockResolvedValue({ collection: aCollectionNode(), ancestors: [] });
  stubs.searchCollections.mockResolvedValue({ items: [aCollectionNode()], next_cursor: null });
  stubs.listTags.mockResolvedValue([tag]);
  stubs.listPrinters.mockResolvedValue([printer]);
  stubs.listPrinterProfiles.mockResolvedValue([printerProfile]);
  stubs.listFilamentProfiles.mockResolvedValue([filamentProfile]);
  stubs.getVaultStats.mockResolvedValue(vaultStats);
  stubs.listModelPage.mockResolvedValue(emptyPage);
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("taxonomy hooks", () => {
  it("loads the next child page only when requested", async () => {
    const first = aCollectionNode();
    const second = aCollectionNode({ id: 2, name: "Tools", path: "tools" });
    stubs.listCollectionChildren
      .mockResolvedValueOnce({ items: [first], next_cursor: "next" })
      .mockResolvedValueOnce({ items: [second], next_cursor: null });
    const { result } = renderHook(() => useCollectionChildren(null), { wrapper: wrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(stubs.listCollectionChildren).toHaveBeenCalledTimes(1);
    expect(stubs.listCollectionChildren).toHaveBeenCalledWith(null, null);
    expect(result.current.data?.pages[0].items).toEqual([first]);
    await act(async () => {
      await result.current.fetchNextPage();
    });
    expect(stubs.listCollectionChildren).toHaveBeenNthCalledWith(2, null, "next");
    await waitFor(() => expect(result.current.data?.pages[1]?.items).toEqual([second]));
  });

  it("leaves a closed child level idle", async () => {
    const { result } = renderHook(() => useCollectionChildren(3, { enabled: false }), {
      wrapper: wrapper(),
    });
    expect(result.current.fetchStatus).toBe("idle");
    expect(stubs.listCollectionChildren).not.toHaveBeenCalled();
  });

  it("leaves collection lookup idle without a path", async () => {
    const { result } = renderHook(() => useCollectionLookup(null), { wrapper: wrapper() });
    expect(result.current.fetchStatus).toBe("idle");
    expect(stubs.lookupCollection).not.toHaveBeenCalled();
  });

  it("resolves a selected collection by path", async () => {
    const { result } = renderHook(() => useCollectionLookup("parts"), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(stubs.lookupCollection).toHaveBeenCalledTimes(1);
    expect(stubs.lookupCollection).toHaveBeenCalledWith("parts");
    expect(result.current.data?.collection.path).toBe("parts");
  });

  it("searches collections at the caller's required role", async () => {
    const { result } = renderHook(() => useCollectionSearch("bracket", "edit"), {
      wrapper: wrapper(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(stubs.searchCollections).toHaveBeenCalledTimes(1);
    expect(stubs.searchCollections).toHaveBeenCalledWith("bracket", "edit", null);
  });

  it("resolves a saved collection by id", async () => {
    const { result } = renderHook(() => useCollectionLookupById(1), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.collection.id).toBe(1);
    expect(stubs.lookupCollectionById).toHaveBeenCalledWith(1);
  });

  it("leaves id lookup idle without a collection", () => {
    const { result } = renderHook(() => useCollectionLookupById(null), { wrapper: wrapper() });
    expect(result.current.fetchStatus).toBe("idle");
    expect(stubs.lookupCollectionById).not.toHaveBeenCalled();
  });

  it("useTags fetches with fresh:true", async () => {
    const { result } = renderHook(() => useTags(), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(stubs.listTags).toHaveBeenCalledWith({ fresh: true });
  });
});

describe("resource hooks", () => {
  it("usePrinterProfiles / useFilamentProfiles / useVaultStats pass fresh:true", async () => {
    const pp = renderHook(() => usePrinterProfiles(), { wrapper: wrapper() });
    await waitFor(() => expect(pp.result.current.isSuccess).toBe(true));
    expect(stubs.listPrinterProfiles).toHaveBeenCalledWith({
      fresh: true,
      signal: expect.any(AbortSignal),
    });

    const fp = renderHook(() => useFilamentProfiles(), { wrapper: wrapper() });
    await waitFor(() => expect(fp.result.current.isSuccess).toBe(true));
    expect(stubs.listFilamentProfiles).toHaveBeenCalledWith({
      fresh: true,
      signal: expect.any(AbortSignal),
    });

    const vs = renderHook(() => useVaultStats(), { wrapper: wrapper() });
    await waitFor(() => expect(vs.result.current.isSuccess).toBe(true));
    expect(stubs.getVaultStats).toHaveBeenCalledWith({ fresh: true });
  });
});

describe("usePrinters enabled gate", () => {
  it("cancels the canonical printer choice read on disposal", async () => {
    const response = Promise.withResolvers<Awaited<ReturnType<QueryApi["listPrinters"]>>>();
    let signal: AbortSignal | undefined;
    stubs.listPrinters.mockImplementationOnce((_group, options) => {
      signal = options?.signal;
      return response.promise;
    });
    const app = renderHook(() => usePrinters(), { wrapper: wrapper() });
    await waitFor(() => expect(signal).toBeDefined());
    app.unmount();
    expect(signal?.aborted).toBe(true);
    response.resolve([printer]);
  });
  it("fetches when enabled (default) with fresh:true", async () => {
    const { result } = renderHook(() => usePrinters(), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([printer]);
    expect(stubs.listPrinters).toHaveBeenCalledWith(undefined, {
      fresh: true,
      signal: expect.any(AbortSignal),
    });
  });

  it("does NOT fetch when enabled is false", async () => {
    const { result } = renderHook(() => usePrinters({ enabled: false }), {
      wrapper: wrapper(),
    });
    // Disabled queries never run their queryFn; they sit pending with no data.
    await new Promise((r) => setTimeout(r, 20));
    expect(stubs.listPrinters).not.toHaveBeenCalled();
    expect(result.current.data).toBeUndefined();
    expect(result.current.fetchStatus).toBe("idle");
  });
});

describe("filter query continuity", () => {
  it("keeps outliner data mounted while changed filters refetch", async () => {
    const firstModels = [makeOutlinerModel(1, "Drawer Housing")];
    const filteredModels = [makeOutlinerModel(2, "PLA Bracket")];
    let resolveFiltered!: (value: OutlinerModelRead[]) => void;
    stubs.listOutlinerModels.mockResolvedValueOnce(firstModels).mockImplementationOnce(
      () =>
        new Promise<OutlinerModelRead[]>((resolve) => {
          resolveFiltered = resolve;
        }),
    );

    const { result, rerender } = renderHook(
      ({ filtered }) => useOutlinerModels({ material_type: filtered ? ["PLA"] : undefined }, 500),
      { initialProps: { filtered: false }, wrapper: wrapper() },
    );

    await waitFor(() => expect(result.current.data).toEqual(firstModels));
    rerender({ filtered: true });
    await waitFor(() => expect(stubs.listOutlinerModels).toHaveBeenCalledTimes(2));

    expect(result.current.data).toEqual(firstModels);
    expect(result.current.isLoading).toBe(false);
    resolveFiltered(filteredModels);
    await waitFor(() => expect(result.current.data).toEqual(filteredModels));
  });

  it("keeps facet groups mounted while changed filters refetch", async () => {
    const firstFacets: ModelFacetsRead = {
      ...emptyFacets(),
      file_type: [{ value: "stl", count: 2 }],
      material_type: [{ value: "PLA", count: 2 }],
    };
    let resolveFiltered!: (value: ModelFacetsRead) => void;
    stubs.getModelFacets.mockResolvedValueOnce(firstFacets).mockImplementationOnce(
      () =>
        new Promise<ModelFacetsRead>((resolve) => {
          resolveFiltered = resolve;
        }),
    );

    const { result, rerender } = renderHook(
      ({ filtered }) => useModelFacets({ material_type: filtered ? ["PLA"] : undefined }),
      { initialProps: { filtered: false }, wrapper: wrapper() },
    );

    await waitFor(() => expect(result.current.data).toEqual(firstFacets));
    rerender({ filtered: true });
    await waitFor(() => expect(stubs.getModelFacets).toHaveBeenCalledTimes(2));

    expect(result.current.data).toEqual(firstFacets);
    expect(result.current.isLoading).toBe(false);
    resolveFiltered({ ...firstFacets, file_type: [{ value: "stl", count: 1 }] });
    await waitFor(() => expect(result.current.data?.file_type[0].count).toBe(1));
  });
});

describe("server-owned Model pagination", () => {
  it("sends the sort and only requests the next cursor on demand", async () => {
    stubs.listModelPage
      .mockResolvedValueOnce({
        items: [makeListItem(1, "First")],
        next_cursor: "cursor-2",
        total: 2,
      })
      .mockResolvedValueOnce({
        items: [makeListItem(2, "Second")],
        next_cursor: null,
        total: 2,
      });

    const { result } = renderHook(
      () => useModelList({ material_type: ["PLA"] }, 1, "success-desc"),
      { wrapper: wrapper() },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(stubs.listModelPage).toHaveBeenCalledTimes(1);
    expect(stubs.listModelPage).toHaveBeenNthCalledWith(1, {
      material_type: ["PLA"],
      limit: 1,
      sort: "success-desc",
      cursor: undefined,
    });

    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(stubs.listModelPage).toHaveBeenCalledTimes(1);

    await act(async () => {
      await result.current.fetchNextPage();
    });
    expect(stubs.listModelPage).toHaveBeenNthCalledWith(2, {
      material_type: ["PLA"],
      limit: 1,
      sort: "success-desc",
      cursor: "cursor-2",
    });
  });

  it("does not request outliner leaves while disabled", async () => {
    const { result } = renderHook(() => useOutlinerModels({}, 500, { enabled: false }), {
      wrapper: wrapper(),
    });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(result.current.fetchStatus).toBe("idle");
    expect(stubs.listOutlinerModels).not.toHaveBeenCalled();
  });
});

describe("folder navigation", () => {
  function aMultipartModel(id: number, name: string): MultipartModelListItem {
    return {
      edit_version: 1,
      id,
      name,
      slug: name.toLowerCase(),
      description: null,
      collection: "parts",
      collection_id: 1,
      collection_label: "Parts",
      part_count: 1,
      model_count: 1,
      guide_count: 0,
      cover_model_id: null,
      cover_image_url: null,
      cover_image_uploaded: false,
      cover_thumbnail_url: null,
      member_model_ids: [id],
      tags: [],
      starred: false,
      effective_role: "admin",
      updated_at: TIMESTAMP,
    };
  }

  it("keeps the previous folder's multipart list while the next one loads", async () => {
    // Dropping it flips the grid to its first-load skeleton on every folder.
    const parts = [aMultipartModel(1, "Rack")];
    let resolveNext!: (value: MultipartModelListItem[]) => void;
    stubs.listMultipartModels.mockResolvedValueOnce(parts).mockImplementationOnce(
      () =>
        new Promise<MultipartModelListItem[]>((resolve) => {
          resolveNext = resolve;
        }),
    );

    const { result, rerender } = renderHook(
      ({ collection }) => useMultipartModels({ collection, direct: true }),
      { initialProps: { collection: "parts" }, wrapper: wrapper() },
    );
    await waitFor(() => expect(result.current.data).toEqual(parts));
    rerender({ collection: "parts/rack" });
    await waitFor(() => expect(stubs.listMultipartModels).toHaveBeenCalledTimes(2));

    expect(result.current.data).toEqual(parts);
    expect(result.current.isLoading).toBe(false);
    resolveNext([]);
    await waitFor(() => expect(result.current.data).toEqual([]));
  });

  it("reads a folder's readme text", async () => {
    stubs.getCollectionReadme.mockResolvedValue({ readme: "# Rack" });

    const { result } = renderHook(() => useCollectionReadme(5), { wrapper: wrapper() });

    await waitFor(() => expect(result.current.data).toBe("# Rack"));
    expect(stubs.getCollectionReadme).toHaveBeenCalledWith(5);
  });

  it("does not request a readme while disabled", async () => {
    const { result } = renderHook(() => useCollectionReadme(5, { enabled: false }), {
      wrapper: wrapper(),
    });

    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(result.current.fetchStatus).toBe("idle");
    expect(stubs.getCollectionReadme).not.toHaveBeenCalled();
  });

  /** Warm through the prefetcher, then mount `read` on the same cache. */
  async function warmThenRead<T>(
    warm: (prefetch: ReturnType<typeof useLibraryPrefetch>) => Promise<void>,
    read: () => T,
  ) {
    const shared = wrapper({ staleTime: 30_000 });
    const warmer = renderHook(() => useLibraryPrefetch(), { wrapper: shared });
    await act(() => warm(warmer.result.current));
    return renderHook(read, { wrapper: shared }).result;
  }

  it("serves a warmed model page to the grid without another request", async () => {
    const page: ModelPageRead = { items: [makeListItem(1, "Rack")], next_cursor: null, total: 1 };
    stubs.listModelPage.mockResolvedValue(page);
    const filters = { collection: "parts", direct: true };

    const result = await warmThenRead(
      (prefetch) => prefetch.modelList(filters, 60, "date-desc"),
      () => useModelList(filters, 60, "date-desc"),
    );

    expect(result.current.data?.pages).toEqual([page]);
    expect(stubs.listModelPage).toHaveBeenCalledTimes(1);
  });

  it("serves a warmed multipart list without another request", async () => {
    const parts = [aMultipartModel(1, "Rack")];
    stubs.listMultipartModels.mockResolvedValue(parts);
    const filters = { collection: "parts", direct: true, limit: 500 };

    const result = await warmThenRead(
      (prefetch) => prefetch.multipartModels(filters),
      () => useMultipartModels(filters),
    );

    expect(result.current.data).toEqual(parts);
    expect(stubs.listMultipartModels).toHaveBeenCalledTimes(1);
  });

  it("serves warmed facets without another request", async () => {
    const facets = { ...emptyFacets(), material_type: [{ value: "PLA", count: 1 }] };
    stubs.getModelFacets.mockResolvedValue(facets);
    const filters = { collection: "parts", direct: true };

    const result = await warmThenRead(
      (prefetch) => prefetch.modelFacets(filters),
      () => useModelFacets(filters),
    );

    expect(result.current.data).toEqual(facets);
    expect(stubs.getModelFacets).toHaveBeenCalledTimes(1);
  });

  it("serves a warmed readme without another request", async () => {
    stubs.getCollectionReadme.mockResolvedValue({ readme: "# Rack" });

    const result = await warmThenRead(
      (prefetch) => prefetch.collectionReadme(5),
      () => useCollectionReadme(5),
    );

    expect(result.current.data).toBe("# Rack");
    expect(stubs.getCollectionReadme).toHaveBeenCalledTimes(1);
  });

  it("does not re-request a folder warmed moments ago", async () => {
    // Moving the pointer back and forth over a folder must not hammer the server.
    const filters = { collection: "parts", direct: true };
    const shared = wrapper({ staleTime: 30_000 });
    const { result } = renderHook(() => useLibraryPrefetch(), { wrapper: shared });

    await act(() => result.current.modelList(filters, 60, "date-desc"));
    await act(() => result.current.modelList(filters, 60, "date-desc"));

    expect(stubs.listModelPage).toHaveBeenCalledTimes(1);
  });
});
