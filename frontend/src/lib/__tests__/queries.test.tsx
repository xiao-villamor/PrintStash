import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";

import {
  useCollections,
  useFilamentProfiles,
  usePrinterProfiles,
  usePrinters,
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
