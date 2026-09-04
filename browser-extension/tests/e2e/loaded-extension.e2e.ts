/*
 * The extension actually installs, and its popup actually runs.
 *
 * Everything else in this package is tested against `@webext-core/fake-browser`,
 * which is fast and proves the logic but cannot prove the thing that breaks most
 * often: that the *manifest* is acceptable to a real browser, and that the APIs
 * the popup calls exist in the context the browser gives it. A manifest error, a
 * permission a browser silently drops, or an MV3 API missing from a popup are all
 * invisible to a fake and fatal in a release.
 *
 * So this spec installs the built extension into a real browser and opens the
 * real popup. Two browsers, two mechanisms — Firefox takes a signed XPI over
 * WebDriver's `installAddOn`, Chrome takes an unpacked directory over CDP — and
 * both end at the same assertions, because the point is what the popup can do
 * rather than how it got there.
 *
 * The APIs asserted at the end are the ones the capture flow depends on:
 * `chrome.permissions.contains` for the optional-host grant the importer asks
 * for, and `chrome.scripting.executeScript` for reading the page. Both are MV3
 * spellings; if either is missing, capture fails at the moment a user clicks, and
 * nothing before this point would have noticed.
 */

import assert from "node:assert/strict";

import { installChromeExtension } from "./_chrome_extension";

interface LoadedExtensionElement {
  getText(): Promise<string>;
  waitForExist(): Promise<void>;
}

interface LoadedExtensionBrowser {
  $(selector: string): LoadedExtensionElement;
  capabilities: { "goog:chromeOptions"?: { debuggerAddress?: string } };
  execute<Result>(script: () => Result): Promise<Result>;
  /**
   * WebdriverIO 9 exposes the browser under test as a flag; the
   * `getCapabilities()` call earlier versions had no longer exists.
   */
  isFirefox: boolean;
  installAddOn(path: string | undefined, temporary: boolean): Promise<string>;
  url(destination: string | undefined): Promise<void>;
}

declare const browser: LoadedExtensionBrowser;
declare const chrome: {
  permissions?: { contains?: unknown };
  scripting?: { executeScript?: unknown };
};

const EXTENSION_NAME = "PrintStash Model Importer";
const FIREFOX_ADDON_ID = "printstash-model-importer@printstash.local";

describe("loaded extension", () => {
  it("installs the manifest and opens a popup extension context", async () => {
    if (browser.isFirefox) {
      const addOnId = await browser.installAddOn(process.env.PRINTSTASH_EXTENSION_XPI, true);

      // Firefox reads the id straight out of the manifest, so a mismatch here
      // means the built XPI is not the extension this repo describes.
      assert.equal(addOnId, FIREFOX_ADDON_ID);
      await browser.url("about:debugging#/runtime/this-firefox");
      await browser.$("body").waitForExist();
      assert.match(await browser.$("body").getText(), new RegExp(EXTENSION_NAME));
      await browser.url(process.env.PRINTSTASH_EXTENSION_POPUP_URL);
    } else {
      const extensionId = await installChromeExtension(
        browser,
        process.env.PRINTSTASH_EXTENSION_DIST as string,
      );

      // Chrome assigns the id, and `Extensions.loadUnpacked` returns it — so the
      // popup URL is derived rather than discovered, and an install that failed
      // shows up as an install error instead of as a missing DOM node.
      assert.match(extensionId, /^[a-p]{32}$/);
      await browser.url(`chrome-extension://${extensionId}/popup.html`);
    }

    await browser.$("#connection-status").waitForExist();
    const apis = await browser.execute(() => ({
      permissions: typeof chrome.permissions?.contains,
      scripting: typeof chrome.scripting?.executeScript,
    }));

    assert.deepEqual(apis, { permissions: "function", scripting: "function" });
  });
});
