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
  useCollections,
  useFilamentProfiles,
  useModelFacets,
  useModelList,
  usePrinterProfiles,
  usePrinters,
  useOutlinerModels,
  useTags,
  useVaultStats,
  type QueryApi,
} from "@/lib/queries";
import type {
  CollectionRead,
  FilamentProfileRead,
  ModelFacetsRead,
  ModelListItem,
  ModelPageRead,
  OutlinerModelRead,
  PrinterProfileRead,
  TagRead,
  VaultStatsRead,
} from "@/types";
import { aPrinter } from "@/test-support/factories";

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
  getModelFacets: vi.fn<QueryApi["getModelFacets"]>(),
  getVaultStats: vi.fn<QueryApi["getVaultStats"]>(),
  listCollections: vi.fn<QueryApi["listCollections"]>(),
  listFilamentProfiles: vi.fn<QueryApi["listFilamentProfiles"]>(),
  listModelPage: vi.fn<QueryApi["listModelPage"]>(),
  listOutlinerModels: vi.fn<QueryApi["listOutlinerModels"]>(),
  listPrinterProfiles: vi.fn<QueryApi["listPrinterProfiles"]>(),
  listPrinters: vi.fn<QueryApi["listPrinters"]>(),
  listTags: vi.fn<QueryApi["listTags"]>(),
};

const api: QueryApi = { ...defaultQueryApi, ...stubs };

const TIMESTAMP = "2026-01-01T00:00:00Z";

const collection: CollectionRead = {
  id: 1,
  name: "Brackets",
  slug: "brackets",
  path: "Brackets",
  parent_id: null,
  model_count: 1,
  effective_role: null,
};

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
    id,
    name,
    slug: name.toLowerCase().replaceAll(" ", "-"),
    collection: null,
    collection_id: null,
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
  return { id, name, collection: null, collection_id: null };
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

function wrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryApiProvider value={api}>
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    </QueryApiProvider>
  );
}

beforeEach(() => {
  stubs.listCollections.mockResolvedValue([collection]);
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
  it("useCollections fetches with fresh:true and exposes data", async () => {
    const { result } = renderHook(() => useCollections(), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([collection]);
    expect(stubs.listCollections).toHaveBeenCalledWith({ fresh: true });
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
    expect(stubs.listPrinterProfiles).toHaveBeenCalledWith({ fresh: true });

    const fp = renderHook(() => useFilamentProfiles(), { wrapper: wrapper() });
    await waitFor(() => expect(fp.result.current.isSuccess).toBe(true));
    expect(stubs.listFilamentProfiles).toHaveBeenCalledWith({ fresh: true });

    const vs = renderHook(() => useVaultStats(), { wrapper: wrapper() });
    await waitFor(() => expect(vs.result.current.isSuccess).toBe(true));
    expect(stubs.getVaultStats).toHaveBeenCalledWith({ fresh: true });
  });
});

describe("usePrinters enabled gate", () => {
  it("fetches when enabled (default) with fresh:true", async () => {
    const { result } = renderHook(() => usePrinters(), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([printer]);
    expect(stubs.listPrinters).toHaveBeenCalledWith(undefined, { fresh: true });
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
