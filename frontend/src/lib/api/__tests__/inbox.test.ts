import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getPendingImport, parseInboxManifest } from "@/lib/api/inbox";
import { invalidateApiCache } from "@/lib/api/request";

const fetchMock = vi.fn<typeof fetch>();

function reply(body: string): Response {
  return new Response(body, { headers: { "content-type": "application/json" } });
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  invalidateApiCache();
});

afterEach(() => vi.unstubAllGlobals());

describe("inbox API", () => {
  it("parses strict V2 and legacy V1 manifests while rejecting malformed contracts", () => {
    expect(
      parseInboxManifest({
        schema_version: 2,
        kind: "model_files",
        source: {
          provider: "printables",
          canonical_url: "https://printables.com/model/1",
          tags: ["calibration"],
          fields: {
            published_at: { value: "2026-08-24T00:00:00Z", origin: "confirmed" },
          },
        },
        files: [{ id: "f1", name: "part.stl", file_type: "stl", size: 1 }],
        selected_ids: ["f1"],
      }),
    ).not.toBeNull();
    expect(parseInboxManifest({ kind: "direct", title: "Legacy" })).not.toBeNull();
    expect(parseInboxManifest({ schema_version: 2, kind: "direct" })).toBeNull();
    expect(
      parseInboxManifest({
        schema_version: 2,
        kind: "model_files",
        source: { tags: [] },
        files: {},
        selected_ids: [],
      }),
    ).toBeNull();
    expect(
      parseInboxManifest({
        schema_version: 2,
        kind: "model_files",
        source: {
          provider: "x",
          canonical_url: "https://x",
          tags: [],
          fields: { secret: { value: "x", origin: "confirmed" } },
        },
        files: [],
        selected_ids: [],
      }),
    ).toBeNull();
  });

  it("GETs one pending import with V2 results and completion", async () => {
    fetchMock.mockResolvedValue(
      reply('{"id":41,"completion":"partial","results":[],"manifest":{"kind":"direct"}}'),
    );

    await expect(getPendingImport(41)).resolves.toMatchObject({
      id: 41,
      completion: "partial",
      results: [],
    });
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/inbox/41", expect.any(Object));
  });
});
