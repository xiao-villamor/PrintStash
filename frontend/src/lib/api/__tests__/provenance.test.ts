import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  deleteModelSourceCover,
  getModelProvenance,
  getModelSourceCover,
  getModelSourceCoverContentPath,
  patchModelProvenance,
  putModelSourceCover,
} from "@/lib/api/provenance";
import { invalidateApiCache } from "@/lib/api/request";
import type { ModelProvenancePatch } from "@/types/provenance";

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

describe("provenance API", () => {
  it("GETs the explicit model provenance read contract", async () => {
    fetchMock.mockResolvedValue(reply('{"sources":[]}'));

    await expect(getModelProvenance(41)).resolves.toEqual({ sources: [] });
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/models/41/provenance", expect.any(Object));
  });

  it("PATCHes only explicit overrides and clears at a provenance source", async () => {
    fetchMock.mockResolvedValue(reply('{"sources":[]}'));
    const payload: ModelProvenancePatch = {
      overrides: { title: "Bench" },
      clear_overrides: ["description"],
    };

    await patchModelProvenance(41, 8, payload);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/models/41/provenance/8",
      expect.objectContaining({ method: "PATCH", body: JSON.stringify(payload) }),
    );
  });

  it("uses the exact private source-cover routes and PUT multipart upload", async () => {
    const cover =
      '{"id":3,"provenance_source_id":8,"content_type":"image/webp","size_bytes":12,"updated_at":"2026-08-24T00:00:00Z"}';
    fetchMock.mockResolvedValueOnce(reply(cover)).mockResolvedValueOnce(reply(cover));

    await getModelSourceCover(41, 8);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/models/41/provenance/8/cover",
      expect.any(Object),
    );

    await putModelSourceCover(41, 8, new File(["cover"], "cover.png", { type: "image/png" }));
    expect(fetchMock).toHaveBeenLastCalledWith(
      "/api/v1/models/41/provenance/8/cover",
      expect.objectContaining({ method: "PUT", body: expect.any(FormData) }),
    );

    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
    await deleteModelSourceCover(41, 8);
    expect(fetchMock).toHaveBeenLastCalledWith(
      "/api/v1/models/41/provenance/8/cover",
      expect.objectContaining({ method: "DELETE" }),
    );
    expect(getModelSourceCoverContentPath(41, 8)).toBe(
      "/api/v1/models/41/provenance/8/cover/content",
    );
  });
});
