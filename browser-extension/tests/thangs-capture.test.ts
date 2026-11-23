/** Thangs keeps account authorization in the source page and transfers only selected bytes. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  requestThangsInMainWorld,
  thangsCaptureFromPage,
  downloadThangsCandidate,
} from "../thangs-capture.ts";
const PAGE = "https://thangs.com/designer/Test/3d-model/Cube-123";
const TOKEN = "explicit-test-access-token";
const candidate = { id: "thangs:123:package", filename: "Cube.zip", fileType: "other" as const };

beforeEach(() => {
  vi.stubGlobal("location", {
    hostname: "thangs.com",
    pathname: "/designer/Test/3d-model/Cube-123",
  });
  localStorage.clear();
});
afterEach(() => {
  vi.unstubAllGlobals();
  document.body.replaceChildren();
  localStorage.clear();
});

describe("requestThangsInMainWorld", () => {
  it("extracts only publication metadata from a multipart model", async () => {
    const script = document.createElement("script");
    script.id = "__NEXT_DATA__";
    script.type = "application/json";
    script.textContent = JSON.stringify({
      props: {
        pageProps: {
          fallback: {
            "models/v2/123?likes=false": {
              id: "123",
              name: "Cube",
              description: "A test model",
              owner: { username: "Maker", privateEmail: "never-export@example.test" },
              parts: [{ name: "A.stl" }, { name: "B.stl" }],
              license: "private/storage/path",
            },
          },
        },
      },
    });
    document.body.append(script);
    const result = await requestThangsInMainWorld({ modelId: "123", operation: "metadata" });
    expect(result).toEqual({
      ok: true,
      kind: "metadata",
      filename: "Cube.zip",
      source: {
        title: "Cube",
        description: "A test model",
        creatorName: "Maker",
        creatorUrl: "https://thangs.com/designer/Maker",
      },
    });
  });
  it("requires a source-page sign in for downloads", async () => {
    expect(await requestThangsInMainWorld({ modelId: "123", operation: "download" })).toEqual({
      ok: false,
      code: "sign_in_required",
    });
  });
  it("resolves a temporary URL without exporting account authorization", async () => {
    localStorage.setItem("accessToken", TOKEN);
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>(async () =>
        Response.json({
          fileName: "Cube.zip",
          signedUrl: "https://storage.googleapis.com/test/Cube.zip?signature=test",
          downloadId: "ignored",
        }),
      ),
    );
    const result = await requestThangsInMainWorld({ modelId: "123", operation: "download" });
    expect(result).toEqual({
      ok: true,
      kind: "download",
      filename: "Cube.zip",
      url: "https://storage.googleapis.com/test/Cube.zip?signature=test",
    });
  });
  it("uses the file URL name when the service returns a display title", async () => {
    localStorage.setItem("accessToken", TOKEN);
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>(async () =>
        Response.json({
          fileName: "Cube",
          signedUrl: "https://storage.googleapis.com/test/Cube.zip?signature=test",
        }),
      ),
    );
    expect(await requestThangsInMainWorld({ modelId: "123", operation: "download" })).toEqual({
      ok: true,
      kind: "download",
      filename: "Cube.zip",
      url: "https://storage.googleapis.com/test/Cube.zip?signature=test",
    });
  });
  it.each([402, 403, 429])("reports unavailable downloads for HTTP %s", async (status) => {
    localStorage.setItem("accessToken", TOKEN);
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>(async () => new Response("", { status })),
    );
    expect(await requestThangsInMainWorld({ modelId: "123", operation: "download" })).toEqual({
      ok: false,
      code: "download_unavailable",
    });
  });
  it("rejects a download response with a different host", async () => {
    localStorage.setItem("accessToken", TOKEN);
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>(async () =>
        Response.json({ fileName: "Cube.zip", signedUrl: "https://attacker.test/Cube.zip" }),
      ),
    );
    expect(await requestThangsInMainWorld({ modelId: "123", operation: "download" })).toEqual({
      ok: false,
      code: "contract_changed",
    });
  });
  it("rejects an oversized download response", async () => {
    localStorage.setItem("accessToken", TOKEN);
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>(async () => new Response("x".repeat(65537))),
    );
    expect(await requestThangsInMainWorld({ modelId: "123", operation: "download" })).toEqual({
      ok: false,
      code: "contract_changed",
    });
  });
  it("refuses a stale tab before reading account authorization", async () => {
    vi.stubGlobal("location", {
      hostname: "thangs.com",
      pathname: "/designer/Test/3d-model/Another-999",
    });
    const request = vi.fn();
    vi.stubGlobal("fetch", request);
    localStorage.setItem("accessToken", TOKEN);
    expect(await requestThangsInMainWorld({ modelId: "123", operation: "download" })).toEqual({
      ok: false,
      code: "contract_changed",
    });
    expect(request).not.toHaveBeenCalled();
  });
});
describe("thangsCaptureFromPage", () => {
  it("offers the package for explicit confirmation", () => {
    const result = thangsCaptureFromPage(PAGE, {
      ok: true,
      kind: "metadata",
      source: { title: "Cube" },
      filename: "Cube.zip",
    });
    expect(result.state).toBe("ready");
    expect(result.candidates).toEqual([candidate]);
  });
});
describe("downloadThangsCandidate", () => {
  it("returns the exact downloaded bytes", async () => {
    const result = await downloadThangsCandidate({
      candidate,
      result: {
        ok: true,
        kind: "download",
        filename: "Cube.zip",
        url: "https://storage.googleapis.com/test/Cube.zip",
      },
      ensureOriginPermission: async () => {},
      fetchImpl: async () => new Response(new Uint8Array([80, 75, 3, 4])),
    });
    expect(new Uint8Array(await result.file.arrayBuffer())).toEqual(new Uint8Array([80, 75, 3, 4]));
  });
  it("rejects a changed package format", async () => {
    await expect(
      downloadThangsCandidate({
        candidate,
        result: {
          ok: true,
          kind: "download",
          filename: "Cube.stl",
          url: "https://storage.googleapis.com/test/Cube.stl",
        },
        ensureOriginPermission: async () => {},
      }),
    ).rejects.toThrow("different file format");
  });
  it("rejects an HTML challenge as file bytes", async () => {
    await expect(
      downloadThangsCandidate({
        candidate,
        result: {
          ok: true,
          kind: "download",
          filename: "Cube.zip",
          url: "https://storage.googleapis.com/test/Cube.zip",
        },
        ensureOriginPermission: async () => {},
        fetchImpl: async () =>
          new Response("challenge", { headers: { "Content-Type": "text/html" } }),
      }),
    ).rejects.toThrow("file download failed");
  });
});
