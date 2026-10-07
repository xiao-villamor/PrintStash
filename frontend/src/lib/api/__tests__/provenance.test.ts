/*
 * Where a model came from, and the two manifest versions the inbox still reads.
 *
 * Provenance is partly captured from a source page and partly typed by the user,
 * and the API keeps those separate: a `PATCH` sends *only* the fields the user
 * explicitly overrode, so sending a full object would silently promote every
 * captured value to a user-confirmed one. Clearing is its own operation for the
 * same reason.
 *
 * The manifest tests are the interesting half. Installations upgrade, so a
 * pending import staged by an older release is a V1 manifest and a new one is
 * V2 — both have to parse. What must *not* happen is a malformed V2 quietly
 * falling back to the V1 reader: the two disagree about which files were
 * selected, so the fallback would import a different set than the user chose.
 *
 * The source-cover routes are private and multipart. They are pinned exactly
 * because a cover PUT to the wrong path returns a plausible success and leaves
 * the model with no image.
 */

import { anEditingBase } from "@/test-support/factories";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  deleteModelSourceCover,
  getModelProvenance,
  getModelSourceCover,
  getModelSourceCoverContentPath,
  patchModelProvenance,
  putModelSourceCover,
} from "@/lib/api/provenance";
import { getPendingImport, parseInboxManifest } from "@/lib/api/inbox";
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

describe("getModelProvenance", () => {
  it("GETs the explicit model provenance read contract", async () => {
    fetchMock.mockResolvedValue(
      reply('{"edit_epoch":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","edit_version":1,"sources":[]}'),
    );

    await expect(getModelProvenance(41)).resolves.toEqual({
      edit_epoch: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      edit_version: 1,
      sources: [],
    });
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/models/41/provenance", expect.any(Object));
  });

  it("PATCHes only explicit overrides and clears at a provenance source", async () => {
    fetchMock.mockResolvedValue(
      reply('{"edit_epoch":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","edit_version":2,"sources":[]}'),
    );
    const payload: ModelProvenancePatch = {
      overrides: { title: "Bench" },
      clear_overrides: ["description"],
    };

    await patchModelProvenance(
      41,
      8,
      payload,
      anEditingBase({ edit_epoch: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", edit_version: 1 }),
    );

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/models/41/provenance/8",
      expect.objectContaining({ method: "PATCH", body: JSON.stringify(payload) }),
    );
  });

  it("uses the exact private source-cover routes and PUT multipart upload", async () => {
    const cover =
      '{"id":3,"provenance_source_id":8,"content_type":"image/webp","size_bytes":12,"updated_at":"2026-08-24T00:00:00Z"}';
    fetchMock.mockResolvedValueOnce(reply(cover)).mockResolvedValueOnce(
      new Response(cover, {
        headers: { ETag: '"model-41-eaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-v2"' },
      }),
    );

    await getModelSourceCover(41, 8);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/models/41/provenance/8/cover",
      expect.any(Object),
    );

    await putModelSourceCover(
      41,
      8,
      new File(["cover"], "cover.png", { type: "image/png" }),
      anEditingBase({ edit_epoch: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", edit_version: 1 }),
    );
    expect(fetchMock).toHaveBeenLastCalledWith(
      "/api/v1/models/41/provenance/8/cover",
      expect.objectContaining({ method: "PUT", body: expect.any(FormData) }),
    );

    fetchMock.mockResolvedValueOnce(
      new Response(null, {
        status: 204,
        headers: { ETag: '"model-41-eaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-v3"' },
      }),
    );
    await deleteModelSourceCover(
      41,
      8,
      anEditingBase({ edit_epoch: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", edit_version: 1 }),
    );
    expect(fetchMock).toHaveBeenLastCalledWith(
      "/api/v1/models/41/provenance/8/cover",
      expect.objectContaining({ method: "DELETE" }),
    );
    expect(getModelSourceCoverContentPath(41, 8)).toBe(
      "/api/v1/models/41/provenance/8/cover/content",
    );
  });
});

describe("conditional Source editing", () => {
  it.each([
    { label: "override", payload: { overrides: { title: "Mine" }, clear_overrides: [] } },
    { label: "restore", payload: { overrides: {}, clear_overrides: ["title"] } },
  ] satisfies { label: string; payload: ModelProvenancePatch }[])(
    "sends conditional $label edits",
    async ({ payload }) => {
      fetchMock.mockResolvedValue(
        reply('{"edit_epoch":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","edit_version":9,"sources":[]}'),
      );
      await patchModelProvenance(
        41,
        8,
        payload,
        anEditingBase({ edit_epoch: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", edit_version: 4 }),
      );
      const headers = new Headers(fetchMock.mock.calls[0][1]?.headers);
      expect(headers.get("If-Match")).toBe('"model-41-eaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-v4"');
      expect(headers.get("X-PrintStash-Edit-Contract")).toBe("conditional-v1");
    },
  );

  it.each([
    { label: "upload", method: "PUT" },
    { label: "delete", method: "DELETE" },
  ])("sends conditional cover $label", async ({ method }) => {
    fetchMock.mockResolvedValue(
      new Response(
        method === "PUT"
          ? JSON.stringify({
              id: 3,
              provenance_source_id: 8,
              content_type: "image/webp",
              size_bytes: 12,
              updated_at: "2026-08-24T00:00:00Z",
            })
          : null,
        {
          status: method === "PUT" ? 200 : 204,
          headers: { ETag: '"model-41-eaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-v9"' },
        },
      ),
    );
    const receipt =
      method === "PUT"
        ? await putModelSourceCover(
            41,
            8,
            new File(["x"], "cover.png", { type: "image/png" }),
            anEditingBase({ edit_epoch: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", edit_version: 4 }),
          )
        : await deleteModelSourceCover(
            41,
            8,
            anEditingBase({ edit_epoch: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", edit_version: 4 }),
          );
    const headers = new Headers(fetchMock.mock.calls[0][1]?.headers);
    expect(headers.get("If-Match")).toBe('"model-41-eaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-v4"');
    expect(headers.get("X-PrintStash-Edit-Contract")).toBe("conditional-v1");
    expect(receipt).toMatchObject({
      edit_epoch: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      edit_version: 9,
    });
  });

  it.each([
    { label: "missing", etag: null },
    { label: "previous history", etag: '"model-41-ebbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-v9"' },
    { label: "wrong entity", etag: '"model-42-eaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-v9"' },
    { label: "stale", etag: '"model-41-eaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-v4"' },
    { label: "malformed", etag: '"model-41-eaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-vNaN"' },
  ])("rejects a $label cover acknowledgement", async ({ etag }) => {
    fetchMock.mockResolvedValue(
      new Response(null, { status: 204, headers: etag ? { ETag: etag } : {} }),
    );
    await expect(
      deleteModelSourceCover(
        41,
        8,
        anEditingBase({ edit_epoch: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", edit_version: 4 }),
      ),
    ).rejects.toThrow(/Invalid (Source|editing)/);
  });

  it.each([0, 4, null])("rejects an unusable override acknowledgement %s", async (version) => {
    fetchMock.mockResolvedValue(
      reply(
        JSON.stringify({
          edit_epoch: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
          edit_version: version,
          sources: [],
        }),
      ),
    );
    await expect(
      patchModelProvenance(
        41,
        8,
        { overrides: { title: "Mine" }, clear_overrides: [] },
        anEditingBase({ edit_epoch: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", edit_version: 4 }),
      ),
    ).rejects.toThrow(/Invalid (Source|editing)/);
  });

  it.each([0, -1, Number.MAX_SAFE_INTEGER + 1])(
    "refuses invalid editing base %s before sending",
    async (version) => {
      await expect(
        putModelSourceCover(
          41,
          8,
          new File(["x"], "cover.png", { type: "image/png" }),
          anEditingBase({ edit_epoch: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", edit_version: version }),
        ),
      ).rejects.toThrow("Invalid editing base");
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );

  it.each([0, null, Number.MAX_SAFE_INTEGER + 1])(
    "rejects malformed Source read version %s",
    async (version) => {
      fetchMock.mockResolvedValue(
        reply(
          JSON.stringify({
            edit_epoch: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            edit_version: version,
            sources: [],
          }),
        ),
      );
      await expect(getModelProvenance(41)).rejects.toThrow("Invalid editing base");
    },
  );

  it("rejects cover metadata for another source", async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ id: 3, provenance_source_id: 99 }), {
        headers: { ETag: '"model-41-eaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-v9"' },
      }),
    );
    await expect(
      putModelSourceCover(
        41,
        8,
        new File(["x"], "cover.png", { type: "image/png" }),
        anEditingBase({ edit_epoch: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", edit_version: 4 }),
      ),
    ).rejects.toThrow("Invalid Source cover acknowledgement");
  });

  it("cancels a retired Source read", async () => {
    const controller = new AbortController();
    const response = Promise.withResolvers<Response>();
    fetchMock.mockReturnValue(response.promise);
    const read = getModelProvenance(41, { signal: controller.signal });
    const rejected = read.catch((error) => error);
    controller.abort();
    response.resolve(
      reply('{"edit_epoch":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","edit_version":1,"sources":[]}'),
    );
    await expect(rejected).resolves.toMatchObject({ name: "AbortError" });
    expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(true);
  });
});

describe("parseInboxManifest", () => {
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
