/** The mesh job count is read fresh and written under the vault-configuration editing base. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { meshWorkersApi } from "@/lib/api/mesh-workers";
import { aMeshWorkers } from "@/test-support/factories";
import { expectRequest, fetchMock, lastBody } from "./_wire";

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  fetchMock.mockImplementation(() =>
    Promise.resolve(new Response(JSON.stringify(aMeshWorkers({ edit_version: 2 })))),
  );
});
afterEach(() => vi.unstubAllGlobals());

describe("meshWorkersApi", () => {
  it("reads the current count", async () => {
    const current = await meshWorkersApi.read();
    expectRequest("/api/v1/config/mesh-workers");
    expect(current.maximum).toBe(4);
  });

  it("saves a count under the editing base", async () => {
    await meshWorkersApi.save(2, { base: aMeshWorkers() });
    expectRequest("/api/v1/config/mesh-workers", "PUT");
    expect(lastBody()).toEqual({ jobs: 2 });
    const headers = new Headers(fetchMock.mock.calls[0][1]?.headers);
    expect(headers.get("If-Match")).toBe('"vault-config-e00000000000000000000000000000001-v1"');
  });

  it("clears the stored count with a reset", async () => {
    await meshWorkersApi.reset({ base: aMeshWorkers() });
    expect(fetchMock.mock.calls.map(([url, init]) => [String(url), init?.method])).toEqual([
      ["/api/v1/config/mesh-workers", "DELETE"],
    ]);
  });

  it("refuses an acknowledgement that does not advance the base", async () => {
    fetchMock.mockImplementation(() =>
      Promise.resolve(new Response(JSON.stringify(aMeshWorkers({ edit_version: 1 })))),
    );
    await expect(meshWorkersApi.save(2, { base: aMeshWorkers() })).rejects.toThrow(
      "Invalid editing acknowledgement",
    );
  });
});
