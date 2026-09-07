/**
 * Inspect the actual upload artifact, not just the build directory. Missing
 * pages, development files or an overbroad manifest must fail before upload.
 * Run after zip:chrome; a missing ZIP is a failed prerequisite, never a skip.
 */
import { readFile } from "node:fs/promises";
import { unzipSync, strFromU8 } from "fflate";
import { describe, expect, it } from "vitest";

const metadata = JSON.parse(await readFile("package.json", "utf8"));
const files = unzipSync(await readFile(`.output/${metadata.name}-${metadata.version}-chrome.zip`));
const manifest = JSON.parse(strFromU8(files["manifest.json"]));

describe("Chrome Web Store ZIP", () => {
  it("packages a production MV3 manifest at the ZIP root", () => {
    expect(manifest.manifest_version).toBe(3);
    expect(manifest.version).toBe(metadata.version);
    expect(manifest.name).toBe("PrintStash");
    expect(manifest.description.length).toBeGreaterThan(0);
    expect(manifest.description.length).toBeLessThanOrEqual(132);
    expect(manifest.action.default_popup).toBe("popup.html");
    expect(manifest.browser_specific_settings).toBeUndefined();
    expect(manifest.key).toBeUndefined();
    expect(manifest.update_url).toBeUndefined();
  });

  it.each(["popup.html", "help.html"])("includes every page dependency in the ZIP: %s", (page) => {
    const document = new DOMParser().parseFromString(strFromU8(files[page]), "text/html");
    const paths = [...document.querySelectorAll("script[src],link[href],img[src],a[href]")]
      .map((element) => element.getAttribute("src") || element.getAttribute("href") || "")
      .filter((path) => !/^(?:data:|https:\/\/|#)/.test(path))
      .map((path) => path.replace(/^\//, ""));

    expect(paths.length).toBeGreaterThan(0);
    expect(paths.filter((path) => !files[path]?.length)).toEqual([]);
    expect(document.querySelectorAll("script:not([src]),[onclick],[onload]")).toHaveLength(0);
    expect(
      [...document.querySelectorAll("script[src]")].every((script) =>
        script.getAttribute("src")?.startsWith("/chunks/"),
      ),
    ).toBe(true);
  });

  it.each([16, 32, 48, 128])("includes a correctly sized PNG icon: %s", (size) => {
    const icon = files[manifest.icons[size]];
    const bytes = Buffer.from(icon);

    expect([...bytes.subarray(0, 8)]).toEqual([137, 80, 78, 71, 13, 10, 26, 10]);
    expect(bytes.readUInt32BE(16)).toBe(size);
    expect(bytes.readUInt32BE(20)).toBe(size);
  });

  it("limits the ZIP to production assets", () => {
    const unexpected = Object.keys(files).filter(
      (path) =>
        !/^(?:manifest\.json|(?:popup|help)\.html|icon(?:-\d+\.png|\.svg)|chunks\/[\w-]+\.js|assets\/[\w-]+\.css)$/.test(
          path,
        ),
    );

    expect(unexpected).toEqual([]);
  });

  it("preserves the intended permission surface", () => {
    expect(manifest.permissions).toEqual(["activeTab", "scripting", "storage"]);
    expect(manifest.host_permissions).toEqual([
      "http://localhost/*",
      "http://127.0.0.1/*",
      "http://[::1]/*",
    ]);
    expect(manifest.optional_host_permissions).toEqual(["http://*/*", "https://*/*"]);
    expect(manifest.content_scripts).toBeUndefined();
    expect(manifest.background).toBeUndefined();
    expect(manifest.externally_connectable).toBeUndefined();
  });

  it("bundles help and privacy disclosures", () => {
    const document = new DOMParser().parseFromString(strFromU8(files["help.html"]), "text/html");

    expect(document.querySelector("#setup")?.textContent).toBe("From a model page to your library");
    expect(document.querySelector("#privacy")?.textContent).toBe("Privacy policy");
    expect(document.body.textContent).toContain("browser device credential locally");
    expect(document.body.textContent).toContain(
      "Source-site cookies and session credentials are never copied",
    );
    expect(document.body.textContent).toContain("Disconnecting does not delete existing imports");
  });
});
