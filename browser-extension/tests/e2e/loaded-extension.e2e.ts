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
  addInitScript(script: () => void): Promise<{ remove(): Promise<void> }>;
  keys(value: string): Promise<void>;
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
  storage: {
    local: {
      set(values: Record<string, string>): Promise<void>;
      get(keys: string[]): Promise<Record<string, string | null>>;
    };
  };
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

  it("uses a fixed toolbar width with a fluid embedded panel", async () => {
    const layouts = await browser.execute(async () => {
      const frame = document.createElement("iframe");
      // Keep the fixture out of the popup's flex layout so it cannot shrink.
      frame.style.position = "fixed";
      frame.style.width = "300px";
      frame.style.height = "650px";
      frame.style.border = "0";
      frame.src = new URL("?panel=1", location.href).href;
      const loaded = new Promise<void>((resolve) =>
        frame.addEventListener("load", () => resolve()),
      );
      document.body.append(frame);
      await loaded;
      const panel = frame.contentDocument;
      const width = (element: HTMLElement | null | undefined) =>
        element?.getBoundingClientRect().width;
      const result = {
        toolbar: [width(document.documentElement), width(document.body)],
        toolbarMinimum: [document.documentElement, document.body].map(
          (el) => getComputedStyle(el).minWidth,
        ),
        toolbarMaximum: [document.documentElement, document.body].map(
          (el) => getComputedStyle(el).maxWidth,
        ),
        panelFrame: width(frame),
        panelFillsViewport: Boolean(
          panel &&
          width(panel.documentElement) === panel.documentElement.clientWidth &&
          width(panel.body) === panel.documentElement.clientWidth,
        ),
        titleLines: getComputedStyle(document.querySelector("#page") as HTMLElement)
          .webkitLineClamp,
        headerDivider: getComputedStyle(document.querySelector("header") as HTMLElement)
          .borderBottomWidth,
        panelBorder: getComputedStyle(document.querySelector("#connection-panel") as HTMLElement)
          .borderTopWidth,
        hasDisclosure: Boolean(document.querySelector("details, summary")),
      };
      frame.remove();
      return result;
    });
    assert.deepEqual(layouts, {
      toolbar: [360, 360],
      toolbarMinimum: ["360px", "360px"],
      toolbarMaximum: ["none", "none"],
      panelFrame: 300,
      panelFillsViewport: true,
      titleLines: "2",
      headerDivider: "1px",
      panelBorder: "0px",
      hasDisclosure: false,
    });
  });

  it("keeps capture status and Import visible with a tall picker", async () => {
    const layout = await browser.execute(async () => {
      function measure(doc: Document) {
        const content = doc.querySelector<HTMLElement>("#capture-content")!;
        const capture = doc.querySelector<HTMLElement>("#capture")!;
        const status = doc.querySelector<HTMLElement>("#status")!;
        const tallPicker = doc.createElement("div");
        tallPicker.style.blockSize = "1200px";
        content.append(tallPicker);
        capture.hidden = false;
        status.hidden = false;
        const bottom = Math.min(
          doc.body.getBoundingClientRect().bottom,
          doc.documentElement.clientHeight,
        );
        const initial = capture.getBoundingClientRect();
        content.scrollTop = content.scrollHeight;
        const scrolled = capture.getBoundingClientRect();
        const statusBounds = status.getBoundingClientRect();
        return {
          scrolls: content.scrollTop > 0,
          fixed: scrolled.top === initial.top && scrolled.top >= 0 && scrolled.bottom <= bottom,
          statusVisible:
            statusBounds.top >= content.getBoundingClientRect().bottom &&
            statusBounds.bottom <= bottom,
          geometry: { bottom, initialTop: initial.top, top: scrolled.top, end: scrolled.bottom },
        };
      }
      async function measureInFrame(width: number, height: number, search: string) {
        const frame = document.createElement("iframe");
        frame.style.position = "fixed";
        frame.style.width = `${width}px`;
        frame.style.height = `${height}px`;
        frame.style.border = "0";
        frame.src = new URL(`popup.html${search}`, location.href).href;
        const loaded = new Promise<void>((resolve) =>
          frame.addEventListener("load", () => resolve(), { once: true }),
        );
        document.body.append(frame);
        try {
          await loaded;
          return measure(frame.contentDocument!);
        } finally {
          frame.remove();
        }
      }
      const toolbar = await measureInFrame(360, 600, "");
      const panel = await measureInFrame(300, 400, "?panel=1");
      return { toolbar, panel };
    });
    const visible = { scrolls: true, fixed: true, statusVisible: true };
    for (const [surface, { geometry, ...visibility }] of Object.entries(layout)) {
      assert.deepEqual(visibility, visible, `${surface}: ${JSON.stringify(geometry)}`);
    }
  });

  it("shows import as the only view in the embedded panel", async () => {
    const views = await browser.execute(async () => {
      const frame = document.createElement("iframe");
      frame.src = new URL("?panel=1", location.href).href;
      const loaded = new Promise<void>((resolve) =>
        frame.addEventListener("load", () => resolve(), { once: true }),
      );
      document.body.append(frame);
      try {
        await loaded;
        const panel = frame.contentDocument!;
        const status = panel.querySelector("#connection-status")!;
        if (status.getAttribute("data-state") === "checking") {
          await new Promise<void>((resolve) => {
            const observer = new MutationObserver(() => {
              if (status.getAttribute("data-state") === "checking") return;
              observer.disconnect();
              resolve();
            });
            observer.observe(status, { attributeFilter: ["data-state"] });
          });
        }
        return {
          toolbar: Array.from(document.querySelectorAll('[role="tab"]'), (tab) =>
            tab.textContent?.trim(),
          ),
          panelTabs: panel.querySelectorAll('[role="tab"]').length,
          panelViews: Array.from(
            panel.querySelectorAll(".popup-view:not([hidden])"),
            (view) => view.id,
          ),
          setupVisible: panel.querySelector("#connection-panel")?.checkVisibility(),
        };
      } finally {
        frame.remove();
      }
    });
    assert.deepEqual(views, {
      toolbar: ["Capture", "History", "Settings"],
      panelTabs: 0,
      panelViews: ["view-capture"],
      setupVisible: true,
    });
  });

  it("switches credential fields using the keyboard", async () => {
    await browser.waitUntil(
      async () => (await browser.$("#connection-title").getText()) === "Not connected",
    );
    await browser.execute(() =>
      document.querySelector<HTMLInputElement>("#pairing-method")?.focus(),
    );

    await browser.keys("ArrowRight");

    const fields = await browser.execute(() => ({
      active: document.activeElement?.id,
      pairing: document.querySelector("#pairing-code")?.checkVisibility(),
      username: document.querySelector("#username")?.checkVisibility(),
      key: document.querySelector("#key")?.checkVisibility(),
    }));
    assert.deepEqual(fields, {
      active: "api-key-method",
      pairing: false,
      username: true,
      key: true,
    });
  });

  it("selects launcher settings with the keyboard in the installed popup", async () => {
    await browser.execute(() => document.querySelector<HTMLButtonElement>("#tab-capture")?.focus());
    await browser.keys("ArrowRight");
    await browser.keys("ArrowRight");
    assert.equal(await browser.execute(() => document.activeElement?.id), "tab-settings");

    await browser.$('label:has([name="launcher-variant"][value="icon-and-label"])').click();
    await browser.$('label:has([name="launcher-corner"][value="bottom-left"])').click();
    await browser.waitUntil(() =>
      browser.execute(() => {
        const preview = document.querySelector<HTMLElement>("#launcher-preview");
        return (
          preview?.dataset.variant === "icon-and-label" &&
          preview.dataset.corner === "bottom-left" &&
          document.body.scrollWidth === 360
        );
      }),
    );
    await browser.$('label:has([name="launcher-variant"][value="edge-tab"])').click();
    await browser.waitUntil(() =>
      browser.execute(() => {
        return (
          document.querySelector<HTMLElement>("#launcher-preview")?.dataset.variant ===
            "edge-tab" && document.body.scrollWidth === 360
        );
      }),
    );
    await browser.$("#tab-capture").click();
  });

  it("renders only the configured launcher label", async () => {
    await browser.$("#tab-settings").click();
    for (const [variant, label] of [
      ["icon", ""],
      ["icon-and-label", "Import"],
      ["edge-tab", ""],
    ]) {
      await browser.$(`label:has([name="launcher-variant"][value="${variant}"])`).click();
      const preview = `#launcher-preview[data-variant="${variant}"] .launcher`;
      await browser.$(preview).waitForExist();
      const state = await browser.execute((selector) => {
        const button = document.querySelector(selector)!;
        return {
          hasHoverLabel: button.textContent?.includes("Import to PrintStash"),
          visibleText: Array.from(button.children)
            .filter((child) => child.checkVisibility({ visibilityProperty: true }))
            .map((child) => child.textContent)
            .join(""),
          tooltip: button.getAttribute("title"),
          accessibleName: button.getAttribute("aria-label"),
        };
      }, preview);
      assert.deepEqual(state, {
        hasHoverLabel: false,
        visibleText: label,
        tooltip: null,
        accessibleName: "Import to PrintStash",
      });
    }
    await browser.$("#tab-capture").click();
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
    it("sends only the images selected during marketplace review", async () => {
      const writes: { gallery: { name: string }[] }[] = [];
      const server = createServer(async (request, response) => {
        const path = request.url?.split("?")[0];
        response.setHeader("Content-Type", "application/json");
        if (path === "/api/v1/health") {
          response.end(
            JSON.stringify({ status: "ok", name: "PrintStash", capture_import_intent: true }),
          );
        } else if (path?.endsWith("/capture-capabilities")) {
          response.end(
            JSON.stringify({
              owner_user_id: 9,
              import_intent: true,
              resumable_slots: true,
              resumable_gallery: true,
            }),
          );
        } else if (path?.endsWith("/capture-source-status")) {
          response.end(
            JSON.stringify(
              writes.length
                ? [{ id: 90, state: "completed", completion: "complete", resulting_model_id: 91 }]
                : [],
            ),
          );
        } else if (path?.endsWith("/capture-sessions")) {
          const chunks: Buffer[] = [];
          for await (const chunk of request) chunks.push(chunk);
          writes.push(JSON.parse(Buffer.concat(chunks).toString()));
          response.end(
            JSON.stringify({ item: { id: 90, state: "completed" }, slots: [], recoverable: false }),
          );
        } else if (path?.endsWith("/capture-status")) {
          response.end(
            JSON.stringify({ state: "completed", completion: "complete", resulting_model_id: 91 }),
          );
        } else {
          response.writeHead(404).end("{}");
        }
      });
      await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
      const address = server.address();
      assert.ok(address && typeof address !== "string");
      const vault = `http://127.0.0.1:${address.port}`;
      const popupUrl = await browser.execute(async (base) => {
        await chrome.storage.local.set({ vault: base, deviceCredential: "gallery-test-device" });
        return new URL("popup.html", location.href).href;
      }, vault);
      // Only the marketplace responses are synthetic. The installed popup
      // makes the real capture HTTP request and reloads the stored receipt.
      const fixture = await browser.addInitScript(() => {
        if (location.protocol !== "chrome-extension:" || location.pathname !== "/popup.html")
          return;
        const tab = {
          id: 42,
          title: "Adjustable enclosure",
          url: "https://www.thingiverse.com/thing:12345/files",
        };
        Object.assign(chrome.tabs, { query: async () => [tab] });
        Object.assign(chrome.permissions!, { contains: async () => true });
        Object.assign(chrome.scripting!, {
          executeScript: async ({ args = [] }: { args?: unknown[] }) => {
            const [params] = args;
            if (params === "thingiverse") {
              return [
                {
                  result: [1, 2, 3].map(
                    (index) => `https://cdn.thingiverse.com/image-${index}.png`,
                  ),
                },
              ];
            }
            if (params && typeof params === "object" && "sourceItemId" in params) {
              return [
                {
                  result: {
                    ok: true,
                    files: [
                      {
                        id: "101",
                        filename: "enclosure.stl",
                        fileType: "stl",
                        url: "https://www.thingiverse.com/download:101",
                      },
                    ],
                  },
                },
              ];
            }
            return [{ result: { pageTitle: tab.title, jsonLd: [] } }];
          },
        });
      });
      try {
        await browser.url(popupUrl);
        await browser.waitUntil(
          async () => (await browser.$("#capture").getText()) === "Review files and images",
        );
        await browser.$("#capture").click();
        const secondImage = browser.$(".gallery-option:nth-child(2) input");
        await secondImage.waitForExist();
        assert.equal(writes.length, 0);
        await secondImage.click();
        await browser.$(".gallery-cover select").click();
        await browser.keys("End");
        await browser.keys("Enter");
        await browser.$("#capture").click();
        await browser.waitUntil(
          async () => (await browser.$("#open-inbox").getText()) === "Open in PrintStash",
        );
        assert.deepEqual(
          writes.map(({ gallery }) => gallery.map(({ name }) => name)),
          [["source-image-3", "source-image-1"]],
        );
        await browser.url(popupUrl);
        await browser.waitUntil(
          async () => (await browser.$("#status-title-text").getText()) === "Already in PrintStash",
        );
      } finally {
        await fixture.remove();
        server.closeAllConnections();
        await new Promise<void>((resolve, reject) =>
          server.close((error) => (error ? reject(error) : resolve())),
        );
      }
    });

    it("cancels native connection verification before publication", async () => {
      let heldResponse: ServerResponse | undefined;
      let requestClosed = false;
      const server = createServer((request, response) => {
        if (request.url === "/second/api/v1/browser-pairings/claim") {
          heldResponse = response;
          response.on("close", () => {
            requestClosed = true;
          });
          request.resume();
          return;
        }
        response.setHeader("Content-Type", "application/json");
        response.end(JSON.stringify({ status: "ok", name: "PrintStash" }));
      });
      await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
      const address = server.address();
      assert.ok(address && typeof address !== "string");
      const vault = `http://127.0.0.1:${address.port}`;
      try {
        const popupUrl = await browser.execute(async (base) => {
          await chrome.storage.local.set({ vault: base, deviceCredential: "device-a" });
          return new URL("popup.html", location.href).href;
        }, vault);
        await browser.url(popupUrl);
        await browser.waitUntil(
          async () => (await browser.$("#connection-title").getText()) === "Connected",
        );
        await browser.$("#tab-settings").click();
        const settingsSize = await browser.execute(() => ({
          width: document.body.scrollWidth,
          height: document.body.scrollHeight,
        }));
        assert.equal(settingsSize.width, 360);
        assert.ok(settingsSize.height <= 600, "Settings and its footer fit the toolbar popup");
        await browser.$("#tab-capture").click();
        await browser.$("#edit-connection").click();
        await browser.$("#vault").setValue(`${vault}/second`);
        await browser.$("#pairing-code").setValue("test-cancelled-code");
        await browser.$("#marketplace-access-row label").click();
        await browser.$("#connect").click();
        await browser.waitUntil(async () => heldResponse !== undefined);
        await browser.$("#cancel-edit").click();
        await browser.waitUntil(async () => requestClosed);
        heldResponse?.end(JSON.stringify({ credential: "device-b" }));
        const state = await browser.execute(async () => ({
          stored: await chrome.storage.local.get(["vault", "deviceCredential"]),
          title: document.querySelector("#connection-title")?.textContent,
          statusHidden: document.querySelector<HTMLElement>("#status")?.hidden,
          formHidden: document.querySelector<HTMLElement>("#connection-panel")?.hidden,
        }));
        assert.deepEqual(state.stored, { vault, deviceCredential: "device-a" });
        assert.equal(state.title, "Connected");
        assert.equal(state.statusHidden, true);
        assert.equal(state.formHidden, true);
      } finally {
        heldResponse?.destroy();
        server.closeAllConnections();
        await new Promise<void>((resolve, reject) =>
          server.close((error) => (error ? reject(error) : resolve())),
        );
      }
    });

    it("retires an active native capture when the connection changes", async () => {
      const writes: { path: string; authorization: string | undefined }[] = [];
      let heldResponse: ServerResponse | undefined;
      let heldRequestClosed = false;
      let healthReads = 0;
      let rejectCapture = false;
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
        if (path === "/second/api/v1/inbox") {
          if (rejectCapture) {
            response.writeHead(503).end(JSON.stringify({ detail: "Import unavailable." }));
            return;
          }
          response.end(JSON.stringify({ id: 2 }));
          return;
        }
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
        assert.deepEqual(
          await browser.execute(() =>
            [...document.querySelectorAll("#capture-preferences :is(input, select, button)")]
              .filter((control) => control.checkVisibility())
              .map((control) => control.id),
          ),
          ["capture-collection", "capture-tags"],
        );
        await browser.$("#capture").click();
        await browser.waitUntil(async () => heldResponse !== undefined);
        assert.deepEqual(writes, [{ path: "/api/v1/inbox", authorization: "Bearer device-a" }]);

        await browser.$("#edit-connection").click();
        await browser.$("#vault").setValue(`${vault}/second`);
        await browser.$("#pairing-code").setValue("new-device-code");
        await browser.$("#marketplace-access-row label").click();
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
          async () => (await browser.$("#status-title").getText()) === "Sent to Pending Imports",
        );
        assert.equal(await browser.$("#open-inbox").getText(), "Review in PrintStash");
        assert.equal(
          await browser.execute(() =>
            document.querySelector("#capture-preferences")?.checkVisibility(),
          ),
          false,
        );
        const inboxUrl = await browser.execute(
          () => document.querySelector<HTMLElement>("#open-inbox")?.dataset.url,
        );
        assert.equal(inboxUrl, `${vault}/second/inbox/2`);
        assert.deepEqual(writes, [
          { path: "/api/v1/inbox", authorization: "Bearer device-a" },
          { path: "/second/api/v1/browser-pairings/claim", authorization: undefined },
          { path: "/second/api/v1/inbox", authorization: "Bearer device-b" },
        ]);
        rejectCapture = true;
        await browser.$("#edit-connection").click();
        await browser.$("#cancel-edit").click();
        await browser.$("#capture").click();
        await browser.waitUntil(
          async () => (await browser.$("#status-details").getText()) === "Code: capture_failed",
        );
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
