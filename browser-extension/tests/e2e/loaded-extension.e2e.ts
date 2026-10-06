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
import { createServer, type ServerResponse } from "node:http";

import { installChromeExtension } from "./_chrome_extension";

interface LoadedExtensionElement {
  getText(): Promise<string>;
  waitForExist(): Promise<void>;
  click(): Promise<void>;
  setValue(value: string): Promise<void>;
}

interface LoadedExtensionBrowser {
  $(selector: string): LoadedExtensionElement;
  webExtensionInstall(parameters: {
    extensionData: { type: "path"; path: string };
  }): Promise<{ extension: string }>;
  execute<Result, Arguments extends unknown[]>(
    script: (...args: Arguments) => Result,
    ...args: Arguments
  ): Promise<Awaited<Result>>;
  /**
   * WebdriverIO 9 exposes the browser under test as a flag; the
   * `getCapabilities()` call earlier versions had no longer exists.
   */
  isFirefox: boolean;
  installAddOn(path: string | undefined, temporary: boolean): Promise<string>;
  url(destination: string | undefined): Promise<void>;
  getWindowHandles(): Promise<string[]>;
  switchToWindow(handle: string): Promise<void>;
  getUrl(): Promise<string>;
  waitUntil(condition: () => Promise<boolean>): Promise<void>;
}

declare const browser: LoadedExtensionBrowser;
declare const chrome: {
  permissions?: { contains?: unknown };
  scripting?: { executeScript?: unknown };
  storage: { local: { set(values: Record<string, string>): Promise<void> } };
  tabs: { create(options: { url: string; active: boolean }): Promise<{ id?: number }> };
};

const EXTENSION_NAME = "PrintStash";
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

  it("opens packaged help from the installed popup", async () => {
    const previousHandles = await browser.getWindowHandles();

    await browser.$("#help-link").click();
    await browser.waitUntil(
      async () => (await browser.getWindowHandles()).length > previousHandles.length,
    );
    const handle = (await browser.getWindowHandles()).find(
      (value) => !previousHandles.includes(value),
    );
    assert.ok(handle, "Help must open in its own tab so the popup's form is preserved");
    await browser.switchToWindow(handle);

    assert.match(await browser.getUrl(), /^(?:chrome|moz)-extension:\/\/[^/]+\/help\.html$/);
    assert.equal(await browser.$("h1").getText(), "PrintStash");
    assert.equal(await browser.$("#privacy").getText(), "Privacy policy");
    assert.match(
      await browser.$("body").getText(),
      /Source-site cookies and session credentials are never copied/,
    );
  });

  if (process.env.PRINTSTASH_EXTENSION_BROWSER_NAME !== "firefox") {
    it("retires an active native capture when the connection changes", async () => {
      const writes: { path: string; authorization: string | undefined }[] = [];
      let heldResponse: ServerResponse | undefined;
      let heldRequestClosed = false;
      let healthReads = 0;
      const server = createServer((request, response) => {
        const path = request.url || "";
        if (path === "/api/v1/health") healthReads += 1;
        if (request.method === "POST") {
          writes.push({ path, authorization: request.headers.authorization });
        }
        if (path === "/api/v1/inbox") {
          heldResponse = response;
          response.on("close", () => {
            heldRequestClosed = true;
          });
          request.resume();
          return;
        }
        response.setHeader("Content-Type", "application/json");
        response.end(JSON.stringify({ status: "ok", name: "PrintStash", credential: "device-b" }));
      });
      await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
      const address = server.address();
      assert.ok(address && typeof address !== "string");
      const vault = `http://127.0.0.1:${address.port}`;
      try {
        const previousHandles = await browser.getWindowHandles();
        await browser.execute(async (base) => {
          await chrome.storage.local.set({ vault: base, deviceCredential: "device-a" });
          await chrome.tabs.create({ url: `${base}/part.stl`, active: true });
          // Initialize against the real active source tab before the driver
          // focuses this popup page. This uses native browser APIs throughout.
          await chrome.tabs.create({
            url: new URL("popup.html", location.href).href,
            active: false,
          });
        }, vault);
        await browser.waitUntil(async () => healthReads > 0);
        let popupHandle: string | undefined;
        await browser.waitUntil(async () => {
          for (const handle of await browser.getWindowHandles()) {
            if (previousHandles.includes(handle)) continue;
            await browser.switchToWindow(handle);
            if (/^chrome-extension:\/\/[^/]+\/popup\.html$/.test(await browser.getUrl())) {
              popupHandle = handle;
              return true;
            }
          }
          return false;
        });
        assert.ok(popupHandle, "The installed extension must open its packaged popup context");
        await browser.switchToWindow(popupHandle);
        await browser.waitUntil(
          async () => (await browser.$("#connection-title").getText()) === "Connected",
        );
        await browser.$("#capture").click();
        await browser.waitUntil(async () => heldResponse !== undefined);
        assert.deepEqual(writes, [{ path: "/api/v1/inbox", authorization: "Bearer device-a" }]);

        await browser.$("#edit-connection").click();
        await browser.$("#vault").setValue(`${vault}/second`);
        await browser.$("#pairing-code").setValue("new-device-code");
        await browser.$("#connect").click();
        await browser.waitUntil(async () => heldRequestClosed);
        await browser.waitUntil(
          async () =>
            (await browser.$("#status-message").getText()) ===
            "Connection verified. This browser is ready to import.",
        );
        heldResponse?.end(JSON.stringify({ id: 1 }));
        const state = await browser.execute(() => ({
          status: document.querySelector("#status-message")?.textContent,
          inboxHidden: document.querySelector<HTMLElement>("#open-inbox")?.hidden,
        }));
        assert.equal(state.status, "Connection verified. This browser is ready to import.");
        assert.equal(state.inboxHidden, true);
        await browser.$("#capture").click();
        await browser.waitUntil(
          async () =>
            (await browser.$("#status-message").getText()) ===
            "Model from Direct file sent to Pending Imports.",
        );
        const inboxUrl = await browser.execute(
          () => document.querySelector<HTMLElement>("#open-inbox")?.dataset.url,
        );
        assert.equal(inboxUrl, `${vault}/second/inbox`);
        assert.deepEqual(writes, [
          { path: "/api/v1/inbox", authorization: "Bearer device-a" },
          { path: "/second/api/v1/browser-pairings/claim", authorization: undefined },
          { path: "/second/api/v1/inbox", authorization: "Bearer device-b" },
        ]);
      } finally {
        heldResponse?.destroy();
        server.closeAllConnections();
        await new Promise<void>((resolve, reject) =>
          server.close((error) => (error ? reject(error) : resolve())),
        );
      }
    });
  }
});
