/** Capture suggestions are bounded, literal labels from the importer-only endpoint. */
import { describe, expect, it } from "vitest";
import { captureDestinations, captureTags, selectedCaptureCover } from "../capture-preferences.ts";
import { CaptureAuthenticationError } from "../core.ts";

describe("capture preferences", () => {
  it("accepts only an explicitly selected bounded image", () => {
    expect(selectedCaptureCover(undefined)).toBeUndefined();
    const file = new File(["png"], "chosen.png", { type: "image/png" });
    expect(selectedCaptureCover(file)).toEqual({
      id: "cover",
      filename: "chosen.png",
      mediaType: "image/png",
      file,
    });
  });
  it.each([
    new File(["html"], "cover.html", { type: "text/html" }),
    new File([], "empty.png", { type: "image/png" }),
    new File([new Uint8Array(15 * 1024 * 1024 + 1)], "large.png", { type: "image/png" }),
  ])("rejects an invalid cover: $name", (file) => {
    expect(() => selectedCaptureCover(file)).toThrow("up to 15 MiB");
  });
  it("normalizes explicit tags without inventing tags", () => {
    expect(captureTags(" desk, assembly, desk, ,")).toEqual(["desk", "assembly"]);
    expect(captureTags("")).toEqual([]);
  });
  it.each(["a".repeat(129), Array.from({ length: 101 }, (_, n) => String(n)).join(",")])(
    "refuses excessive tags: %s",
    (value) => {
      expect(() => captureTags(value)).toThrow("at most 100");
    },
  );
  it("requests only matching collection labels with the importer credential", async () => {
    const requests: Array<{ url: string; authorization: string | null }> = [];
    const result = await captureDestinations({
      vault: "https://vault.test/",
      authorization: "test-device",
      query: "parts & tools",
      fetchImpl: async (url, init) => {
        requests.push({
          url: String(url),
          authorization: new Headers(init?.headers).get("Authorization"),
        });
        return Response.json([{ id: 3, label: "Library / <tools>" }]);
      },
    });
    expect(requests).toEqual([
      {
        url: "https://vault.test/api/v1/inbox/capture-destinations?q=parts%20%26%20tools",
        authorization: "Bearer test-device",
      },
    ]);
    expect(result).toEqual([{ id: 3, label: "Library / <tools>" }]);
  });
  it("requires reconnecting a revoked browser", async () => {
    await expect(
      captureDestinations({
        vault: "https://vault.test",
        authorization: "test-revoked",
        query: "",
        fetchImpl: async () => new Response(null, { status: 401 }),
      }),
    ).rejects.toBeInstanceOf(CaptureAuthenticationError);
  });
  it("keeps review available on older servers", async () => {
    await expect(
      captureDestinations({
        vault: "https://vault.test",
        authorization: "test-device",
        query: "",
        fetchImpl: async () => new Response(null, { status: 404 }),
      }),
    ).rejects.toThrow("Choose the collection in Pending Imports");
  });
  it.each([
    {},
    [{ id: -1, label: "bad" }],
    [{ id: 1, label: null }],
    Array.from({ length: 21 }, (_, i) => ({ id: i + 1, label: "too many" })),
  ])("refuses malformed suggestions: %j", async (body) => {
    await expect(
      captureDestinations({
        vault: "https://vault.test",
        authorization: "test-device",
        query: "",
        fetchImpl: async () => Response.json(body),
      }),
    ).rejects.toThrow("Invalid collection suggestions");
  });
});
