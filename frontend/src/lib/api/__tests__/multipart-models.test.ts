/** Wire contract for the standalone multipart-model API client. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  createMultipartModel,
  deleteMultipartModelCover,
  deleteMultipartModel,
  getMultipartModel,
  listMultipartModelCandidates,
  listMultipartModels,
  replaceMultipartModelTags,
  saveMultipartModel,
  starMultipartModel,
  unstarMultipartModel,
  uploadMultipartModelCover,
} from "@/lib/api/multipart-models";
import { clearLogin } from "@/lib/auth-store";
import { queryClient, queryKeys } from "@/lib/query-client";
import { invalidateApiCache } from "@/lib/api/request";
import { expectRequest, fetchMock, lastBody, respondWith } from "./_wire";

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  invalidateApiCache();
});

afterEach(() => vi.unstubAllGlobals());

describe("multipart model wire contract", () => {
  it.each([
    {
      label: "composition",
      write: () =>
        saveMultipartModel(
          4,
          {
            name: "Draft",
            description: null,
            collection_id: null,
            cover_model_id: null,
            cover_image_url: null,
            parts: [],
          },
          7,
        ),
    },
    { label: "tags", write: () => replaceMultipartModelTags(4, ["Draft"], 7) },
    {
      label: "cover upload",
      write: () => uploadMultipartModelCover(4, new File(["cover"], "cover.png"), 7),
    },
    { label: "cover removal", write: () => deleteMultipartModelCover(4, 7) },
  ])("sends the editor's Multipart version for $label", async ({ write }) => {
    respondWith({ id: 4, edit_version: 9 });

    await write();

    const headers = new Headers(fetchMock.mock.calls[0]?.[1]?.headers);
    expect(headers.get("If-Match")).toBe('"multipart-4-v7"');
    expect(headers.get("X-PrintStash-Edit-Contract")).toBe("conditional-v1");
  });

  it("encodes list filters", async () => {
    respondWith([]);
    await listMultipartModels({
      collection: "functional/brackets",
      direct: true,
      q: "desk",
      tag: ["fantasy", "display"],
      favorites: true,
      limit: 20,
      offset: 40,
    });
    expectRequest(
      "/api/v1/multipart-models?collection=functional%2Fbrackets&direct=true&q=desk&tag=fantasy&tag=display&favorites=true&limit=20&offset=40",
    );
  });

  it("omits absent list filters", async () => {
    respondWith([]);
    await listMultipartModels();
    expectRequest("/api/v1/multipart-models");
  });

  it("creates a grouping", async () => {
    respondWith({ id: 4, name: "Desk organiser" });
    await createMultipartModel({ name: "Desk organiser", description: null, collection_id: 3 });
    expectRequest("/api/v1/multipart-models", "POST");
    expect(lastBody()).toEqual({ name: "Desk organiser", description: null, collection_id: 3 });
  });

  it("reads a grouping", async () => {
    respondWith({ id: 4, parts: [] });
    await getMultipartModel(4);
    expectRequest("/api/v1/multipart-models/4");
  });

  it("saves the complete multipart draft atomically", async () => {
    respondWith({ id: 4, parts: [] });
    await saveMultipartModel(
      4,
      {
        name: "Updated",
        description: "Description",
        collection_id: 3,
        cover_model_id: 8,
        cover_image_url: "https://images.example.test/desk.webp",
        parts: [
          {
            name: "Base",
            quantity: 1,
            choices: [{ model_id: 7 }, { model_id: 8, choice_id: 33 }],
          },
        ],
      },
      1,
    );
    expectRequest("/api/v1/multipart-models/4", "PUT");
    expect(lastBody()).toEqual({
      name: "Updated",
      description: "Description",
      collection_id: 3,
      cover_model_id: 8,
      cover_image_url: "https://images.example.test/desk.webp",
      parts: [
        { name: "Base", quantity: 1, choices: [{ model_id: 7 }, { model_id: 8, choice_id: 33 }] },
      ],
    });
  });

  it("uploads a local image as the multipart cover", async () => {
    respondWith({ id: 4, cover_image_uploaded: true });
    const image = new File(["cover"], "cover.png", { type: "image/png" });

    await uploadMultipartModelCover(4, image, 1);

    expectRequest("/api/v1/multipart-models/4/cover", "PUT");
    const body = fetchMock.mock.calls[0]?.[1]?.body;
    expect(body).toBeInstanceOf(FormData);
    if (!(body instanceof FormData)) throw new Error("Expected multipart form data");
    expect(body.get("file")).toBe(image);
  });

  it("removes the uploaded multipart cover", async () => {
    respondWith({ id: 4, cover_image_uploaded: false });

    await deleteMultipartModelCover(4, 1);

    expectRequest("/api/v1/multipart-models/4/cover", "DELETE");
  });

  it("searches reusable candidates", async () => {
    respondWith([]);
    await listMultipartModelCandidates(4, { q: "handle", limit: 50 });
    expectRequest("/api/v1/multipart-models/4/candidates?q=handle&limit=50");
  });

  it("browses a page of direct collection members", async () => {
    respondWith([]);

    await listMultipartModelCandidates(4, {
      q: "base",
      limit: 49,
      offset: 48,
      collection: "miniatures/bases",
      direct: true,
    });

    expectRequest(
      "/api/v1/multipart-models/4/candidates?q=base&limit=49&offset=48&collection=miniatures%2Fbases&direct=true",
    );
  });

  it("lists reusable candidates without filters", async () => {
    respondWith([]);
    await listMultipartModelCandidates(4);
    expectRequest("/api/v1/multipart-models/4/candidates");
  });

  it("deletes only the grouping", async () => {
    respondWith(null, 204);
    await deleteMultipartModel(4);
    expectRequest("/api/v1/multipart-models/4", "DELETE");
  });

  it("replaces the grouping's own tags", async () => {
    respondWith({ id: 4, tags: ["Display"] });
    await replaceMultipartModelTags(4, ["Display"], 1);
    expectRequest("/api/v1/multipart-models/4/tags", "PUT");
    expect(lastBody()).toEqual({ tags: ["Display"] });
  });

  it("stars a grouping", async () => {
    respondWith({ multipart_model_id: 4, starred: true });
    await starMultipartModel(4);
    expectRequest("/api/v1/multipart-models/4/star", "PUT");
  });

  it("unstars a grouping", async () => {
    respondWith({ multipart_model_id: 4, starred: false });
    await unstarMultipartModel(4);
    expectRequest("/api/v1/multipart-models/4/star", "DELETE");
  });
});

/** Direct cover/star mutations acknowledge writes within their original session. */
describe("multipart mutation isolation", () => {
  it.each([
    {
      label: "cover upload",
      write: () => uploadMultipartModelCover(4, new File(["cover"], "cover.png"), 1),
    },
    { label: "cover deletion", write: () => deleteMultipartModelCover(4, 1) },
    { label: "favourite removal", write: () => unstarMultipartModel(4) },
  ])("discards a retired $label acknowledgement", async ({ write }) => {
    const headers = Promise.withResolvers<Response>();
    fetchMock.mockReturnValueOnce(headers.promise);
    const pending = write();
    const outcome = pending.catch((error: Error) => error);
    clearLogin();
    queryClient.setQueryData(queryKeys.multipartModels, [{ id: 9 }]);
    headers.resolve(new Response('{"id":4}'));
    expect(await outcome).toMatchObject({ name: "AbortError" });
    expect(queryClient.getQueryState(queryKeys.multipartModels)?.isInvalidated).toBe(false);
  });
});

describe("reader cancellation", () => {
  it.each([
    {
      label: "destinations",
      read: (signal: AbortSignal) => listMultipartModels({ limit: 30 }, { signal }),
    },
    { label: "detail", read: (signal: AbortSignal) => getMultipartModel(4, { signal }) },
  ])("aborts an active Multipart $label read", async ({ read }) => {
    const controller = new AbortController();
    let delivered: AbortSignal | null = null;
    fetchMock.mockImplementation(
      (_url, options) =>
        new Promise((_resolve, reject) => {
          const signal = options?.signal;
          if (!signal) throw new Error("Cancellation signal is required");
          delivered = signal;
          signal.addEventListener("abort", () => reject(signal.reason), { once: true });
        }),
    );
    const outcome = read(controller.signal).catch((error: Error) => error);
    controller.abort();
    await expect(outcome).resolves.toMatchObject({ name: "AbortError" });
    expect(delivered).toMatchObject({ aborted: true });
  });
});
