/** Cults capture uses the native free-download flow without exporting account or CSRF data. */
import { beforeEach, afterEach, describe, it, expect, vi } from "vitest";
import {
  requestCultsInMainWorld,
  cultsCaptureFromPage,
  downloadCultsCandidate,
} from "../cults-capture.ts";
const PAGE = "https://cults3d.com/en/3d-model/tool/cube";
function publication() {
  document.body.innerHTML =
    '<main><h2><a href="/en/users/Maker">Maker</a></h2><a href="/en/licenses#cc_by">CC BY</a><form method="post" action="/en/free_orders?creation_slug=cube"><input name="authenticity_token" value="never-export"></form></main>';
}
function order(
  body = '<main><a data-turbo-target="link" href="/en/3d-model/tool/cube">Cube</a><a data-turbo="false" href="/en/downloads/100?creation=cube">Download all</a></main>',
) {
  const response = new Response(body);
  Object.defineProperty(response, "url", { value: "https://cults3d.com/en/orders/200" });
  return response;
}
beforeEach(() => {
  vi.stubGlobal("location", {
    hostname: "cults3d.com",
    origin: "https://cults3d.com",
    pathname: "/en/3d-model/tool/cube",
  });
  publication();
});
afterEach(() => {
  vi.unstubAllGlobals();
  document.body.replaceChildren();
});
describe("requestCultsInMainWorld", () => {
  it("exposes the free publication package", async () => {
    const result = await requestCultsInMainWorld({ publicationId: "cube", operation: "metadata" });
    const capture = cultsCaptureFromPage(PAGE, result);
    expect(capture.candidates).toEqual([
      { id: "cults:cube:archive", filename: "cube.zip", fileType: "other" },
    ]);
    expect(capture.source.fields.license_text).toEqual({ value: "CC BY", origin: "confirmed" });
    expect(JSON.stringify(result)).not.toContain("never-export");
  });
  it("preserves additional published source terms", async () => {
    document.querySelector('main a[href*="licenses"]')!.outerHTML =
      '<ul><li><a href="/en/licenses#cc_by">CC BY</a></li><li><a href="/en/pages/no-ai"><svg><text>AI icon</text></svg>No AI</a></li></ul>';
    const capture = cultsCaptureFromPage(
      PAGE,
      await requestCultsInMainWorld({ publicationId: "cube", operation: "metadata" }),
    );
    expect(capture.source.fields.license_text).toEqual({
      value: "CC BY; No AI",
      origin: "confirmed",
    });
  });
  it("refuses paid checkout", async () => {
    document.querySelector("form")!.setAttribute("action", "/en/orders?creation_slug=cube");
    expect(await requestCultsInMainWorld({ publicationId: "cube", operation: "download" })).toEqual(
      { ok: false, code: "native_download_required" },
    );
  });
  it("rejects another publication", async () => {
    expect(
      await requestCultsInMainWorld({ publicationId: "other", operation: "download" }),
    ).toEqual({ ok: false, code: "contract_changed" });
  });
  it("resolves the native free download", async () => {
    vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
      expect(init.credentials).toBe("same-origin");
      if (init.method === "POST") {
        expect(url).toBe("https://cults3d.com/en/free_orders?creation_slug=cube");
        expect(String(init.body)).toBe("authenticity_token=never-export");
        return order();
      }
      expect(url).toBe("https://cults3d.com/en/downloads/100?creation=cube");
      const response = new Response("archive");
      Object.defineProperty(response, "url", {
        value: "https://download.cults3d.com/uploads/creation_zip/file/100/cube.zip",
      });
      return response;
    });
    const result = await requestCultsInMainWorld({ publicationId: "cube", operation: "download" });
    expect(result).toMatchObject({
      ok: true,
      downloadUrl: "https://download.cults3d.com/uploads/creation_zip/file/100/cube.zip",
    });
    expect(JSON.stringify(result)).not.toContain("never-export");
  });
  it("rejects an order for another publication", async () => {
    vi.stubGlobal("fetch", async () =>
      order(
        '<main><a data-turbo-target="link" href="/en/3d-model/tool/other">Other</a><a data-turbo="false" href="/en/downloads/100?creation=cube">Download</a></main>',
      ),
    );
    expect(await requestCultsInMainWorld({ publicationId: "cube", operation: "download" })).toEqual(
      { ok: false, code: "contract_changed" },
    );
  });
  it("bounds order response", async () => {
    vi.stubGlobal("fetch", async () => order("x".repeat(2 * 1024 * 1024 + 1)));
    expect(await requestCultsInMainWorld({ publicationId: "cube", operation: "download" })).toEqual(
      { ok: false, code: "contract_changed" },
    );
  });
  it("reports provider request failure", async () => {
    vi.stubGlobal("fetch", async () => {
      throw Error("offline");
    });
    expect(await requestCultsInMainWorld({ publicationId: "cube", operation: "download" })).toEqual(
      { ok: false, code: "request_failed" },
    );
  });
});
describe("downloadCultsCandidate", () => {
  it("retains original archive bytes", async () => {
    const file = await downloadCultsCandidate({
      candidate: { id: "cults:cube:archive", filename: "cube.zip", fileType: "other" },
      result: {
        ok: true,
        publicationId: "cube",
        source: {},
        jsonLd: [],
        downloadUrl: "https://download.cults3d.com/uploads/creation_zip/file/100/cube.zip",
      },
      fetchImpl: async (url) => {
        expect(url).toBe("https://download.cults3d.com/uploads/creation_zip/file/100/cube.zip");
        const response = new Response("original archive");
        Object.defineProperty(response, "url", {
          value: "https://download.cults3d.com/uploads/creation_zip/file/100/cube.zip",
        });
        return response;
      },
    });
    expect(await file.file.text()).toBe("original archive");
  });
  it.each([
    "https://evil.test/uploads/creation_zip/file/100/cube.zip",
    "https://download.cults3d.com/unrelated/cube.zip",
  ])("rejects unrelated redirect %s", async (url) => {
    await expect(
      downloadCultsCandidate({
        candidate: { id: "cults:cube:archive", filename: "cube.zip", fileType: "other" },
        result: {
          ok: true,
          publicationId: "cube",
          source: {},
          jsonLd: [],
          downloadUrl: url,
        },
        fetchImpl: async () => {
          const response = new Response("wrong");
          Object.defineProperty(response, "url", { value: url });
          return response;
        },
      }),
    ).rejects.toThrow("supported archive");
  });
});
