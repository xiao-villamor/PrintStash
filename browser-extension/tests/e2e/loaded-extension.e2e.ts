import assert from "node:assert/strict";

interface LoadedExtensionElement {
  getText(): Promise<string>;
  waitForExist(): Promise<void>;
}

interface LoadedExtensionBrowser {
  $(selector: string): LoadedExtensionElement;
  capabilities: { browserName?: string };
  execute<Result>(script: () => Result): Promise<Result>;
  installAddOn(path: string | undefined, temporary: boolean): Promise<string>;
  url(destination: string | undefined): Promise<void>;
}

declare const browser: LoadedExtensionBrowser;
declare const chrome: {
  permissions?: { contains?: unknown };
  scripting?: { executeScript?: unknown };
};

describe("loaded extension", () => {
  it("installs the manifest and opens a popup extension context", async () => {
    if (browser.capabilities.browserName === "firefox") {
      const addOnId = await browser.installAddOn(process.env.PRINTSTASH_EXTENSION_XPI, true);
      assert.equal(addOnId, "printstash-model-importer@printstash.local");
      await browser.url("about:debugging#/runtime/this-firefox");
      await browser.$("body").waitForExist();
      assert.match(await browser.$("body").getText(), /PrintStash Model Importer/);
      await browser.url(process.env.PRINTSTASH_EXTENSION_POPUP_URL);
    } else {
      await browser.url("chrome://extensions/");
      const extension = await browser.execute(() => {
        const manager = document.querySelector("extensions-manager");
        const list = manager?.shadowRoot?.querySelector("extensions-item-list");
        const item = [...(list?.shadowRoot?.querySelectorAll("extensions-item") || [])].find(
          (candidate) =>
            candidate.shadowRoot?.querySelector("#name")?.textContent?.trim() ===
            "PrintStash Model Importer",
        );
        return item
          ? { id: item.id, name: item.shadowRoot?.querySelector("#name")?.textContent?.trim() }
          : null;
      });

      assert.equal(extension?.name, "PrintStash Model Importer");
      assert.match(extension?.id || "", /^[a-p]{32}$/);
      if (extension === null) throw new Error("Loaded Chrome extension was not found");
      await browser.url(`chrome-extension://${extension.id}/popup.html`);
    }

    await browser.$("#connection-status").waitForExist();
    const apis = await browser.execute(() => ({
      permissions: typeof chrome.permissions?.contains,
      scripting: typeof chrome.scripting?.executeScript,
    }));
    assert.deepEqual(apis, { permissions: "function", scripting: "function" });
  });
});
