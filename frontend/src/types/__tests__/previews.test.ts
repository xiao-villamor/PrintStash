/** The wire decoder admits only the two documented preview cases. */
import { describe, expect, it } from "vitest";
import {
  isModelPreviews,
  NO_PREVIEWS,
  previewArtifact,
  previewsInFlight,
  publishedViews,
  type ArtifactPreviews,
} from "../previews";

const published: ArtifactPreviews = {
  kind: "artifact",
  cover_camera: "unknown",
  file_id: 7,
  state: "ready",
  reason: null,
  published: {
    profile: "orbit",
    manifest_url: "/api/v1/models/1/previews/g",
    views: [{ id: "orbit-0", role: "orbit", url: "/api/v1/models/1/previews/g/orbit-0" }],
  },
};

describe("model previews", () => {
  it("exposes no views or Artifact for a Model without a mesh", () => {
    expect(publishedViews(NO_PREVIEWS)).toEqual([]);
    expect(previewArtifact(NO_PREVIEWS)).toBeNull();
    expect(previewsInFlight(NO_PREVIEWS)).toBe(false);
  });

  it("keeps a previous generation visible while a replacement runs", () => {
    const running = { ...published, state: "running" as const };
    expect(publishedViews(running)).toHaveLength(1);
    expect(previewsInFlight(running)).toBe(true);
    expect(previewArtifact(running)).toBe(7);
  });

  it.each(["failed", "cancelled", "unsupported", "ready"] as const)(
    "stops following a settled %s set",
    (state) => expect(previewsInFlight({ ...published, state })).toBe(false),
  );

  it("accepts both documented cases", () => {
    expect(isModelPreviews(NO_PREVIEWS)).toBe(true);
    expect(isModelPreviews(published)).toBe(true);
    expect(isModelPreviews({ ...published, state: "pending", published: null })).toBe(true);
  });

  it.each([
    ["an unknown case", { kind: "other", cover_camera: "unknown" }],
    ["an invented cover camera", { ...NO_PREVIEWS, cover_camera: "orbit-0" }],
    ["an unknown state", { ...published, state: "done" }],
    ["a missing Artifact", { ...published, file_id: 0 }],
    ["an empty publication", { ...published, published: { ...published.published!, views: [] } }],
    [
      "an unknown view",
      {
        ...published,
        published: { ...published.published!, views: [{ id: "side", role: "orbit", url: "/x" }] },
      },
    ],
    ["null", null],
  ])("rejects %s", (_, value) =>
    // Malformed values reach the decoder as parsed HTTP JSON, never as typed data.
    expect(isModelPreviews(JSON.parse(JSON.stringify(value)))).toBe(false),
  );
});
