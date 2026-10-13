import { describe, expect, it } from "vitest";

import { installChromeExtension } from "./e2e/_chrome_extension";

describe("installed Chrome extension identity", () => {
  it("installs the exact directory through the driver and returns its acknowledged id", async () => {
    const requests: unknown[] = [];
    const driver = {
      async webExtensionInstall(request: unknown) {
        requests.push(request);
        return { extension: "abcdefghijklmnopabcdefghijklmnop" };
      },
    };

    await expect(installChromeExtension(driver, "/extension/output")).resolves.toBe(
      "abcdefghijklmnopabcdefghijklmnop",
    );
    expect(requests).toEqual([{ extensionData: { type: "path", path: "/extension/output" } }]);
  });

  it("preserves installation failure without inventing an extension id", async () => {
    const failure = new Error("Extension manifest is invalid");
    const driver = {
      async webExtensionInstall() {
        throw failure;
      },
    };

    await expect(installChromeExtension(driver, "/extension/output")).rejects.toBe(failure);
  });

  it.each(["", "invalid-extension-id"])("rejects an invalid acknowledged id: %s", async (id) => {
    const driver = {
      async webExtensionInstall() {
        return { extension: id };
      },
    };

    await expect(installChromeExtension(driver, "/extension/output")).rejects.toThrow(
      "WebDriver returned no valid Chrome extension id",
    );
  });
});
