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
import { invalidateApiCache } from "@/lib/api/request";
import { expectRequest, fetchMock, lastBody, respondWith } from "./_wire";

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  invalidateApiCache();
});

afterEach(() => vi.unstubAllGlobals());

describe("multipart model wire contract", () => {
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
    await saveMultipartModel(4, {
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
    });
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

    await uploadMultipartModelCover(4, image);

    expectRequest("/api/v1/multipart-models/4/cover", "PUT");
    const body = fetchMock.mock.calls[0]?.[1]?.body;
    expect(body).toBeInstanceOf(FormData);
    if (!(body instanceof FormData)) throw new Error("Expected multipart form data");
    expect(body.get("file")).toBe(image);
  });

  it("removes the uploaded multipart cover", async () => {
    respondWith({ id: 4, cover_image_uploaded: false });

    await deleteMultipartModelCover(4);

    expectRequest("/api/v1/multipart-models/4/cover", "DELETE");
  });

  it("searches reusable candidates", async () => {
    respondWith([]);
    await listMultipartModelCandidates(4, { q: "handle", limit: 50 });
    expectRequest("/api/v1/multipart-models/4/candidates?q=handle&limit=50");
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
    await replaceMultipartModelTags(4, ["Display"]);
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
