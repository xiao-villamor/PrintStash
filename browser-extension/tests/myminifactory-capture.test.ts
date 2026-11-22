/** Native MyMiniFactory forms bind selected files to one publication and approved download hosts. */
import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";
import {
  readMyMiniFactoryInMainWorld,
  myMiniFactoryCaptureFromPage,
  downloadMyMiniFactoryCandidate,
} from "../myminifactory-capture.ts";
const PAGE = "https://www.myminifactory.com/object/3d-print-cube-100";
function publication() {
  document.body.innerHTML =
    '<script type="application/json" data-component-name="DownloadButton">{"download_url":"/download/100","isLoggedIn":true,"csrf_token":"never-export"}</script><script type="application/ld+json">{"@type":"CreativeWork","name":"Cube"}</script><form method="get" action="https://www.myminifactory.com/download/100"><button name="downloadfile" data-file="200" value="cube.stl"></button></form>';
}
beforeEach(() =>
  vi.stubGlobal("location", {
    hostname: "www.myminifactory.com",
    origin: "https://www.myminifactory.com",
    pathname: "/object/3d-print-cube-100",
  }),
);
afterEach(() => {
  vi.unstubAllGlobals();
  document.body.replaceChildren();
});
describe("readMyMiniFactoryInMainWorld", () => {
  it("selects only native publication files", () => {
    publication();
    const result = readMyMiniFactoryInMainWorld({ publicationId: "3d-print-cube-100" });
    expect(myMiniFactoryCaptureFromPage(PAGE, result).candidates).toEqual([
      { id: "200", filename: "cube.stl", fileType: "stl" },
    ]);
    expect(JSON.stringify(result)).not.toContain("never-export");
  });
  it("preserves the creator and literal published license", () => {
    publication();
    document.body.insertAdjacentHTML(
      "beforeend",
      '<div class="user-download-container"><div class="designerWordFlex"><p class="txt-obj-medium"><a href="/users/Maker">Maker</a></p></div></div><table class="table-spec"><tbody><tr><td><a href="https://www.myminifactory.com/object-licensing">MyMiniFactory - Credit - Remix - Noncommercial</a></td></tr></tbody></table>',
    );
    const capture = myMiniFactoryCaptureFromPage(
      PAGE,
      readMyMiniFactoryInMainWorld({ publicationId: "3d-print-cube-100" }),
    );
    expect(capture.source.fields.creator_name).toEqual({ value: "Maker", origin: "confirmed" });
    expect(capture.source.fields.license_text).toEqual({
      value: "MyMiniFactory - Credit - Remix - Noncommercial",
      origin: "confirmed",
    });
  });
  it("rejects a form bound to another publication", () => {
    publication();
    document.querySelector("form")!.action = "https://www.myminifactory.com/download/101";
    expect(readMyMiniFactoryInMainWorld({ publicationId: "3d-print-cube-100" })).toEqual({
      ok: false,
      code: "contract_changed",
    });
  });
  it("rejects a stale page", () => {
    publication();
    expect(readMyMiniFactoryInMainWorld({ publicationId: "3d-print-another-101" })).toEqual({
      ok: false,
      code: "contract_changed",
    });
  });
  it.each(["../cube.stl", "cube.exe", "folder/cube.stl"])("rejects unsafe filename %s", (name) => {
    publication();
    document.querySelector("button")!.value = name;
    expect(readMyMiniFactoryInMainWorld({ publicationId: "3d-print-cube-100" })).toEqual({
      ok: false,
      code: "contract_changed",
    });
  });
});
describe("downloadMyMiniFactoryCandidate", () => {
  it("retains selected bytes after a provider CDN redirect", async () => {
    publication();
    const result = readMyMiniFactoryInMainWorld({ publicationId: "3d-print-cube-100" }),
      candidate = myMiniFactoryCaptureFromPage(PAGE, result).candidates[0];
    const file = await downloadMyMiniFactoryCandidate({
      candidate,
      result,
      ensureOriginPermissions: async () => {},
      fetchImpl: async (url, init) => {
        expect(String(url)).toBe(
          "https://www.myminifactory.com/download/100?downloadfile=cube.stl",
        );
        expect(init?.credentials).toBe("include");
        const response = new Response("original STL");
        Object.defineProperty(response, "url", {
          value: "https://dl4.myminifactory.com/object-assets/cube.stl",
        });
        return response;
      },
    });
    expect(await file.file.text()).toBe("original STL");
  });
  it("requires an authenticated native download", async () => {
    publication();
    const result = readMyMiniFactoryInMainWorld({ publicationId: "3d-print-cube-100" });
    if (!result.ok) throw Error("Invalid fixture");
    result.loggedIn = false;
    const candidate = myMiniFactoryCaptureFromPage(PAGE, result).candidates[0];
    await expect(
      downloadMyMiniFactoryCandidate({
        candidate,
        result,
        ensureOriginPermissions: async () => {},
      }),
    ).rejects.toThrow("Sign in");
  });
  it("rejects an unrelated redirected host", async () => {
    publication();
    const result = readMyMiniFactoryInMainWorld({ publicationId: "3d-print-cube-100" }),
      candidate = myMiniFactoryCaptureFromPage(PAGE, result).candidates[0];
    await expect(
      downloadMyMiniFactoryCandidate({
        candidate,
        result,
        ensureOriginPermissions: async () => {},
        fetchImpl: async () => {
          const response = new Response("wrong");
          Object.defineProperty(response, "url", {
            value: "https://dl4.myminifactory.com.evil.test/object-assets/cube.stl",
          });
          return response;
        },
      }),
    ).rejects.toThrow("supported file download");
  });
});
