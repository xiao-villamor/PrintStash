import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";

import {
  useCollections,
  useFilamentProfiles,
  useModelFacets,
  useModelList,
  usePrinterProfiles,
  usePrinters,
  useOutlinerModels,
  useTags,
  useVaultStats,
} from "@/lib/queries";
import * as api from "@/lib/api";

// The hooks are thin, but they encode two real contracts worth locking down:
// (1) every shared read passes `{ fresh: true }` so TanStack Query — not the
// legacy in-memory cache in request.ts — is the single source of truth, and
// (2) usePrinters honours `enabled` so non-admins don't fetch a list they
// can't use.
vi.mock("@/lib/api", () => ({
  listCollections: vi.fn(),
  listTags: vi.fn(),
  listPrinters: vi.fn(),
  listPrinterProfiles: vi.fn(),
  listFilamentProfiles: vi.fn(),
  listModelPage: vi.fn(),
  listOutlinerModels: vi.fn(),
  getModelFacets: vi.fn(),
  getVaultStats: vi.fn(),
}));

const mocked = vi.mocked(api);

function wrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

beforeEach(() => {
  mocked.listCollections.mockResolvedValue([{ id: 1, name: "Brackets" }] as never);
  mocked.listTags.mockResolvedValue([{ id: 1, name: "petg" }] as never);
  mocked.listPrinters.mockResolvedValue([{ id: 1, name: "Voron" }] as never);
  mocked.listPrinterProfiles.mockResolvedValue([{ id: 1, name: "Ender" }] as never);
  mocked.listFilamentProfiles.mockResolvedValue([{ id: 1, name: "PLA" }] as never);
  mocked.getVaultStats.mockResolvedValue({ model_count: 3 } as never);
  mocked.listModelPage.mockResolvedValue({
    items: [],
    next_cursor: null,
    total: 0,
  } as never);
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("taxonomy hooks", () => {
  it("useCollections fetches with fresh:true and exposes data", async () => {
    const { result } = renderHook(() => useCollections(), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([{ id: 1, name: "Brackets" }]);
    expect(mocked.listCollections).toHaveBeenCalledWith({ fresh: true });
  });

  it("useTags fetches with fresh:true", async () => {
    const { result } = renderHook(() => useTags(), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(mocked.listTags).toHaveBeenCalledWith({ fresh: true });
  });
});

describe("resource hooks", () => {
  it("usePrinterProfiles / useFilamentProfiles / useVaultStats pass fresh:true", async () => {
    const pp = renderHook(() => usePrinterProfiles(), { wrapper: wrapper() });
    await waitFor(() => expect(pp.result.current.isSuccess).toBe(true));
    expect(mocked.listPrinterProfiles).toHaveBeenCalledWith({ fresh: true });

    const fp = renderHook(() => useFilamentProfiles(), { wrapper: wrapper() });
    await waitFor(() => expect(fp.result.current.isSuccess).toBe(true));
    expect(mocked.listFilamentProfiles).toHaveBeenCalledWith({ fresh: true });

    const vs = renderHook(() => useVaultStats(), { wrapper: wrapper() });
    await waitFor(() => expect(vs.result.current.isSuccess).toBe(true));
    expect(mocked.getVaultStats).toHaveBeenCalledWith({ fresh: true });
  });
});

describe("usePrinters enabled gate", () => {
  it("fetches when enabled (default) with fresh:true", async () => {
    const { result } = renderHook(() => usePrinters(), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([{ id: 1, name: "Voron" }]);
    expect(mocked.listPrinters).toHaveBeenCalledWith(undefined, { fresh: true });
  });

  it("does NOT fetch when enabled is false", async () => {
    const { result } = renderHook(() => usePrinters({ enabled: false }), {
      wrapper: wrapper(),
    });
    // Disabled queries never run their queryFn; they sit pending with no data.
    await new Promise((r) => setTimeout(r, 20));
    expect(mocked.listPrinters).not.toHaveBeenCalled();
    expect(result.current.data).toBeUndefined();
    expect(result.current.fetchStatus).toBe("idle");
  });
});

describe("filter query continuity", () => {
  it("keeps outliner data mounted while changed filters refetch", async () => {
    const firstModels = [{ id: 1, name: "Drawer Housing" }];
    let resolveFiltered!: (value: typeof firstModels) => void;
    mocked.listOutlinerModels
      .mockResolvedValueOnce(firstModels as never)
      .mockImplementationOnce(() => new Promise((resolve) => { resolveFiltered = resolve; }) as never);

    const { result, rerender } = renderHook(
      ({ filtered }) => useOutlinerModels(
        { material_type: filtered ? ["PLA"] : undefined },
        500,
      ),
      { initialProps: { filtered: false }, wrapper: wrapper() },
    );

    await waitFor(() => expect(result.current.data).toEqual(firstModels));
    rerender({ filtered: true });
    await waitFor(() => expect(mocked.listOutlinerModels).toHaveBeenCalledTimes(2));

    expect(result.current.data).toEqual(firstModels);
    expect(result.current.isLoading).toBe(false);
    resolveFiltered([{ id: 2, name: "PLA Bracket" }]);
    await waitFor(() => expect(result.current.data).toEqual([{ id: 2, name: "PLA Bracket" }]));
  });

  it("keeps facet groups mounted while changed filters refetch", async () => {
    const firstFacets = {
      file_type: [{ value: "stl", count: 2 }],
      material_type: [{ value: "PLA", count: 2 }],
    };
    let resolveFiltered!: (value: typeof firstFacets) => void;
    mocked.getModelFacets
      .mockResolvedValueOnce(firstFacets as never)
      .mockImplementationOnce(() => new Promise((resolve) => { resolveFiltered = resolve; }) as never);

    const { result, rerender } = renderHook(
      ({ filtered }) => useModelFacets({ material_type: filtered ? ["PLA"] : undefined }),
      { initialProps: { filtered: false }, wrapper: wrapper() },
    );

    await waitFor(() => expect(result.current.data).toEqual(firstFacets));
    rerender({ filtered: true });
    await waitFor(() => expect(mocked.getModelFacets).toHaveBeenCalledTimes(2));

    expect(result.current.data).toEqual(firstFacets);
    expect(result.current.isLoading).toBe(false);
    resolveFiltered({ ...firstFacets, file_type: [{ value: "stl", count: 1 }] });
    await waitFor(() => expect(result.current.data?.file_type[0].count).toBe(1));
  });
});

describe("server-owned Model pagination", () => {
  it("sends the sort and only requests the next cursor on demand", async () => {
    mocked.listModelPage
      .mockResolvedValueOnce({
        items: [{ id: 1, name: "First" }],
        next_cursor: "cursor-2",
        total: 2,
      } as never)
      .mockResolvedValueOnce({
        items: [{ id: 2, name: "Second" }],
        next_cursor: null,
        total: 2,
      } as never);

    const { result } = renderHook(
      () => useModelList({ material_type: ["PLA"] }, 1, "success-desc"),
      { wrapper: wrapper() },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(mocked.listModelPage).toHaveBeenCalledTimes(1);
    expect(mocked.listModelPage).toHaveBeenNthCalledWith(1, {
      material_type: ["PLA"],
      limit: 1,
      sort: "success-desc",
      cursor: undefined,
    });

    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(mocked.listModelPage).toHaveBeenCalledTimes(1);

    await act(async () => {
      await result.current.fetchNextPage();
    });
    expect(mocked.listModelPage).toHaveBeenNthCalledWith(2, {
      material_type: ["PLA"],
      limit: 1,
      sort: "success-desc",
      cursor: "cursor-2",
    });
  });

  it("does not request outliner leaves while disabled", async () => {
    const { result } = renderHook(
      () => useOutlinerModels({}, 500, { enabled: false }),
      { wrapper: wrapper() },
    );
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(result.current.fetchStatus).toBe("idle");
    expect(mocked.listOutlinerModels).not.toHaveBeenCalled();
  });
});
