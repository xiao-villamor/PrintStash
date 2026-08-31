import { readFile } from "node:fs/promises";
import { describe, expect, it } from "vitest";

describe("MV3 manifest contract", () => {
  it.each(["chrome-mv3", "firefox-mv3", "edge-mv3"])(
    "uses the package release version in the %s manifest",
    async (target) => {
      const packageMetadata = JSON.parse(await readFile("package.json", "utf8"));
      const manifest = JSON.parse(await readFile(`.output/${target}/manifest.json`, "utf8"));

      expect(manifest.version).toBe(packageMetadata.version);
    },
  );

  it("declares the exact Firefox data collection categories for explicit Vault transfers", async () => {
    const manifest = JSON.parse(await readFile(".output/firefox-mv3/manifest.json", "utf8"));
    expect(manifest.browser_specific_settings.gecko.id).toBe(
      "printstash-model-importer@printstash.local",
    );
    expect(manifest.browser_specific_settings.gecko.data_collection_permissions).toEqual({
      required: ["authenticationInfo", "browsingActivity", "websiteContent"],
      optional: [],
    });
  });

  it("ships the popup, icons, and only the intended permission surface", async () => {
    const manifest = JSON.parse(await readFile(".output/chrome-mv3/manifest.json", "utf8"));

    expect(manifest.manifest_version).toBe(3);
    expect(manifest.action.default_popup).toBe("popup.html");
    expect(manifest.permissions).toEqual(["activeTab", "scripting", "storage"]);
    expect(manifest.host_permissions).toEqual([
      "http://localhost/*",
      "http://127.0.0.1/*",
      "http://[::1]/*",
    ]);
    expect(manifest.optional_host_permissions).toEqual(["http://*/*", "https://*/*"]);
    expect(manifest.permissions).not.toContain("cookies");
    expect(manifest.permissions).not.toContain("webRequest");
    await Promise.all(
      Object.values(manifest.icons).map((icon) => readFile(`.output/chrome-mv3/${icon}`)),
    );
    await readFile(".output/chrome-mv3/popup.html");
  });
});
