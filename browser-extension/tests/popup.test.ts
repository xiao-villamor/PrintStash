/** Defends the popup's user-visible states through real DOM events and browser boundary fakes. */
import { UploadRequest } from "./support/upload-request.ts";
import { readFile } from "node:fs/promises";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fakeBrowser } from "@webext-core/fake-browser";

const popupHtml = await readFile("entrypoints/popup/index.html", "utf8");
const extensionVersion = JSON.parse(await readFile("package.json", "utf8")).version;

function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function requiredElement<T extends Element>(selector: string, constructor: { new (): T }): T {
  const element = document.querySelector(selector);
  if (!(element instanceof constructor)) throw new Error(`Missing ${selector}`);
  return element;
}

const button = (selector: string) => requiredElement(selector, HTMLButtonElement);
const element = (selector: string) => requiredElement(selector, HTMLElement);

function stringBody(options: RequestInit): string {
  if (typeof options.body !== "string") throw new TypeError("Expected a string request body");
  return options.body;
}

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  const promise = new Promise<T>((settle) => {
    resolve = settle;
  });
  return { promise, resolve };
}

async function settle() {
  await new Promise((resolve) => setTimeout(resolve, 0));
  await new Promise((resolve) => setTimeout(resolve, 0));
}

describe("popup browser adapters", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  beforeEach(() => {
    vi.resetModules();
    UploadRequest.instances = [];
    vi.stubGlobal("XMLHttpRequest", UploadRequest);
    fakeBrowser.reset();
    document.documentElement.innerHTML = popupHtml;
    vi.stubGlobal("chrome", fakeBrowser);
    vi.stubGlobal("browser", fakeBrowser);
    fakeBrowser.tabs.query = vi.fn().mockResolvedValue([
      {
        id: 42,
        title: "3DBenchy",
        url: "https://www.printables.com/model/3161-3d-benchy/files",
      },
    ]);
    fakeBrowser.permissions.contains = vi.fn().mockResolvedValue(true);
    fakeBrowser.permissions.request = vi.fn().mockResolvedValue(true);
    fakeBrowser.permissions.remove = vi.fn().mockResolvedValue(true);
    fakeBrowser.scripting.executeScript = vi.fn();
    fakeBrowser.tabs.create = vi.fn();
    fakeBrowser.runtime.getManifest = vi.fn().mockReturnValue({ version: extensionVersion });
  });

  it("shows literal collection suggestions without persisting private labels", async () => {
    await fakeBrowser.storage.local.set({
      vault: "https://vault.test",
      deviceCredential: "device-secret",
    });
    const requests: string[] = [];
    vi.stubGlobal("fetch", async (input: RequestInfo | URL) => {
      const url = String(input);
      requests.push(url);
      if (url.includes("/capture-destinations?"))
        return response([{ id: 7, label: "Parts / <img src=x>" }]);
      if (url.endsWith("/health")) return response({ status: "ok", name: "PrintStash" });
      return response({});
    });
    await import("../popup.ts");
    await settle();
    const collection = requiredElement("#capture-collection", HTMLInputElement);
    collection.value = "Parts";
    collection.dispatchEvent(new Event("input"));
    await vi.waitFor(() => expect(element("#collection-options").children).toHaveLength(2));
    expect(requests).toContain("https://vault.test/api/v1/inbox/capture-destinations?q=Parts");
    const list = element("#collection-options");
    expect([...list.children].map((option) => option.textContent)).toEqual([
      "Choose in Pending Imports",
      "Parts / <img src=x>",
    ]);
    expect(list.querySelector("img")).toBeNull();
    expect(JSON.stringify(await fakeBrowser.storage.local.get(null))).not.toContain("Parts");
  });

  it("ignores suggestions returned after disconnecting", async () => {
    await fakeBrowser.storage.local.set({
      vault: "https://vault.test",
      deviceCredential: "device-secret",
    });
    const pending = deferred<Response>();
    const lookup = deferred<void>();
    vi.stubGlobal("fetch", async (input: RequestInfo | URL) => {
      if (String(input).includes("/capture-destinations?")) {
        lookup.resolve();
        return pending.promise;
      }
      if (String(input).endsWith("/health")) return response({ status: "ok", name: "PrintStash" });
      return response({});
    });
    await import("../popup.ts");
    await settle();
    requiredElement("#capture-collection", HTMLInputElement).focus();
    await lookup.promise;
    button("#edit-connection").click();
    button("#disconnect").click();
    await settle();
    pending.resolve(response([{ id: 7, label: "Old account" }]));
    await settle();
    expect(element("#collection-options").children).toHaveLength(0);
    expect(element("#connection-title").textContent).toBe("Not connected");
  });

  it("submits the keyboard-selected collection ID from the debounced combobox", async () => {
    const pageUrl = "https://models.example.com/bracket.stl";
    fakeBrowser.tabs.query = vi.fn().mockResolvedValue([{ url: pageUrl }]);
    await fakeBrowser.storage.local.set(pairedDevice);
    const searches: string[] = [];
    const writes: unknown[] = [];
    vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input));
      if (url.pathname.endsWith("/health")) return readyVault();
      if (url.pathname.endsWith("/capture-destinations")) {
        searches.push(url.searchParams.get("q") ?? "");
        return response([{ id: 7, label: "Parts / Brackets" }]);
      }
      if (init) writes.push(JSON.parse(stringBody(init)));
      return response({ id: 42 });
    });
    await import("../popup.ts");
    await vi.waitFor(() => expect(button("#capture").disabled).toBe(false));
    const input = requiredElement("#capture-collection", HTMLInputElement);
    expect(input.placeholder).toBe("Choose in Pending Imports");
    input.focus();
    for (const query of ["P", "Pa", "Parts"]) {
      input.value = query;
      input.dispatchEvent(new Event("input"));
    }
    await vi.waitFor(() => expect(element("#collection-options").children).toHaveLength(2));
    expect(searches).toEqual(["Parts"]);
    for (const key of ["ArrowDown", "ArrowDown", "Enter"]) {
      input.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true }));
    }
    expect(input.value).toBe("Parts / Brackets");
    expect(input.getAttribute("aria-expanded")).toBe("false");
    button("#capture").click();
    await vi.waitFor(() => expect(writes).toEqual([expect.objectContaining({ collection_id: 7 })]));
  });

  it("shows only a compact empty state for an unsupported page", async () => {
    fakeBrowser.tabs.query = vi.fn().mockResolvedValue([{ url: "https://example.com" }]);
    await fakeBrowser.storage.local.set(pairedDevice);
    vi.stubGlobal("fetch", async () => readyVault());

    await import("../popup.ts");
    await vi.waitFor(() => expect(element("#import-panel").hidden).toBe(false));

    expect(element("#unsupported-page").hidden).toBe(false);
    expect(element("#unsupported-page").textContent).toContain("Thingiverse or Printables");
    expect(element("#page-context").hidden).toBe(true);
    expect(element("#capture-preferences").hidden).toBe(true);
    expect(button("#capture").hidden).toBe(true);
  });

  it("puts the panel action beside the marketplace source badge", async () => {
    await fakeBrowser.storage.local.set(pairedDevice);
    vi.stubGlobal("fetch", async () => readyVault());

    await import("../popup.ts");
    await vi.waitFor(() => expect(element("#source-actions button").hidden).toBe(false));

    expect(element("#source").parentElement).toBe(element("#source-actions"));
    expect(element("#source-actions button").textContent).toBe("Enable panel on this site");
    expect(document.querySelector("details, summary")).toBeNull();
  });

  it("defaults the browser name from Chrome and the platform", async () => {
    vi.spyOn(navigator, "userAgent", "get").mockReturnValue(
      "Mozilla/5.0 (X11; Linux x86_64) Chrome/154.0",
    );
    await import("../popup.ts");

    expect(requiredElement("#device-name", HTMLInputElement).value).toBe("Chrome on Linux");
  });

  it("focuses the inline browser-name conflict while remaining disconnected", async () => {
    vi.stubGlobal("fetch", async (url: string) =>
      url.endsWith("/claim")
        ? response({ detail: "browser_device_name_in_use" }, 409)
        : readyVault(),
    );
    await import("../popup.ts");
    await vi.waitFor(() => expect(element("#connection-title").textContent).toBe("Not connected"));
    requiredElement("#vault", HTMLInputElement).value = pairedDevice.vault;
    requiredElement("#pairing-code", HTMLInputElement).value = "test-code";
    const name = requiredElement("#device-name", HTMLInputElement);

    button("#connect").click();
    await vi.waitFor(() => expect(element("#device-name-error").hidden).toBe(false));

    expect(element("#device-name-error").textContent).toContain("name is already paired");
    expect(name.getAttribute("aria-invalid")).toBe("true");
    expect(document.activeElement).toBe(name);
    expect(element("#connection-title").textContent).toBe("Not connected");
    expect(await fakeBrowser.storage.local.get()).toEqual({});
    name.dispatchEvent(new Event("input"));
    expect(element("#device-name-error").hidden).toBe(true);
  });

  it("moves the selected popup tab with arrow keys", async () => {
    await import("../popup.ts");
    await settle();
    const capture = button("#tab-capture");
    const history = button("#tab-history");

    capture.focus();
    capture.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));

    expect(history.getAttribute("aria-selected")).toBe("true");
    expect(document.activeElement).toBe(history);
    expect(element("#view-history").hidden).toBe(false);
    expect(element("#view-capture").hidden).toBe(true);
    expect(capture.tabIndex).toBe(-1);
  });

  it("persists an explicitly selected popup tab", async () => {
    await import("../popup.ts");
    await settle();

    button("#tab-settings").click();

    await vi.waitFor(async () =>
      expect(await fakeBrowser.storage.local.get("popupTab")).toEqual({ popupTab: "settings" }),
    );
  });

  it("restores the last selected tab on an unsupported page", async () => {
    fakeBrowser.tabs.query = vi.fn().mockResolvedValue([{ url: "https://example.com/" }]);
    await fakeBrowser.storage.local.set({ popupTab: "settings" });

    await import("../popup.ts");

    await vi.waitFor(() =>
      expect(button("#tab-settings").getAttribute("aria-selected")).toBe("true"),
    );
  });

  it("opens Capture first on a supported page", async () => {
    await fakeBrowser.storage.local.set({ ...pairedDevice, popupTab: "settings" });
    vi.stubGlobal("fetch", async () => readyVault());

    await import("../popup.ts");
    await vi.waitFor(() => expect(element("#connection-title").textContent).toBe("Connected"));

    expect(button("#tab-capture").getAttribute("aria-selected")).toBe("true");
    expect(await fakeBrowser.storage.local.get("popupTab")).toEqual({ popupTab: "settings" });
  });

  it("opens the listed capture from toolbar History", async () => {
    await fakeBrowser.storage.local.set(pairedDevice);
    vi.stubGlobal("fetch", async (url: string) =>
      url.endsWith("/health")
        ? readyVault()
        : response([{ id: 47, state: "review", completion: null }]),
    );

    await import("../popup.ts");
    await vi.waitFor(() => expect(element("#connection-title").textContent).toBe("Connected"));
    button("#tab-history").click();
    await vi.waitFor(() => expect(element("#history-list").hidden).toBe(false));

    expect(element("#history-list").textContent).toContain("#47Pending");
    button('#history-list button[aria-label="Open capture #47"]').click();
    expect(fakeBrowser.tabs.create).toHaveBeenCalledWith({
      url: "https://prints.example.com/inbox/47",
    });
  });

  it("explains when the current model has no capture history", async () => {
    await fakeBrowser.storage.local.set(pairedDevice);
    vi.stubGlobal("fetch", async (url: string) =>
      url.endsWith("/health") ? readyVault() : response([]),
    );
    await import("../popup.ts");
    await vi.waitFor(() => expect(element("#connection-title").textContent).toBe("Connected"));

    button("#tab-history").click();

    await vi.waitFor(() =>
      expect(element("#history-message").textContent).toBe("No previous captures of this page."),
    );
    expect(element("#history-list").hidden).toBe(true);
  });

  it("updates the launcher preview from Settings", async () => {
    await import("../popup.ts");
    await settle();
    button("#tab-settings").click();

    requiredElement('[name="launcher-variant"][value="edge-tab"]', HTMLInputElement).click();
    requiredElement('[name="launcher-corner"][value="top-left"]', HTMLInputElement).click();

    const preview = element("#launcher-preview");
    await vi.waitFor(() => expect(preview.dataset.variant).toBe("edge-tab"));
    expect(preview.dataset.corner).toBe("top-left");
  });

  it("restores a hidden site from Settings", async () => {
    const origin = "https://www.thingiverse.com/*";
    fakeBrowser.tabs.query = vi
      .fn()
      .mockResolvedValue([{ url: "https://www.thingiverse.com/thing:763622" }]);
    await fakeBrowser.storage.local.set({
      ...pairedDevice,
      "panel-enabled:printstash-www.thingiverse.com": true,
    });
    const { launcherPreferences } = await import("../launcher-preferences.ts");
    const preferences = launcherPreferences(fakeBrowser.storage);
    await preferences.setSiteHidden(origin, true);
    vi.stubGlobal("fetch", async () => readyVault());
    await import("../popup.ts");
    await vi.waitFor(() => expect(button("#launcher-site").hidden).toBe(false));
    button("#tab-settings").click();
    expect(button("#launcher-site").textContent).toBe("Show on this site");

    button("#launcher-site").click();

    await vi.waitFor(() => expect(button("#launcher-site").textContent).toBe("Hide on this site"));
    expect((await preferences.read()).hiddenSites.has(origin)).toBe(false);
  });

  it("imports a direct URL through the supported server without opening review", async () => {
    const url = "https://models.example.com/clean.stl";
    fakeBrowser.tabs.query = vi.fn().mockResolvedValue([{ url, title: "clean.stl" }]);
    await fakeBrowser.storage.local.set(pairedDevice);
    const writes: Record<string, unknown>[] = [];
    vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(input)).pathname;
      if (path.endsWith("/health"))
        return response({ status: "ok", name: "PrintStash", capture_import_intent: true });
      if (path.endsWith("/capture-capabilities")) return response({ owner_user_id: 9 });
      if (path.endsWith("/capture-source-status")) return response([]);
      if (path.endsWith("/capture-status"))
        return response({
          state: "completed",
          completion: "complete",
          resulting_model_id: 37,
          collection_name: "Brackets",
        });
      if (init) writes.push(JSON.parse(stringBody(init)));
      return response({ id: 12 });
    });
    await import("../popup.ts");
    await vi.waitFor(() => {
      expect(button("#capture").textContent).toBe("Import");
      expect(button("#capture").disabled).toBe(false);
    });

    button("#capture").click();
    await vi.waitFor(() => expect(button("#open-inbox").textContent).toBe("Open in PrintStash"));

    expect(writes).toEqual([expect.objectContaining({ url, on_ready: "import" })]);
    expect(element("#status-title-text").textContent).toBe("Imported to Brackets");
    expect(button("#open-inbox").dataset.url).toBe("https://prints.example.com/models/37");
  });

  it("opens an already imported capture before sending another", async () => {
    fakeBrowser.tabs.query = vi
      .fn()
      .mockResolvedValue([{ url: "https://models.example.com/clean.stl" }]);
    await fakeBrowser.storage.local.set(pairedDevice);
    vi.stubGlobal("fetch", async (input: RequestInfo | URL) => {
      const path = new URL(String(input)).pathname;
      if (path.endsWith("/health"))
        return response({ status: "ok", name: "PrintStash", capture_import_intent: true });
      if (path.endsWith("/capture-capabilities")) return response({ owner_user_id: 9 });
      return response([
        { id: 12, state: "completed", completion: "complete", resulting_model_id: 37 },
      ]);
    });
    await import("../popup.ts");
    await vi.waitFor(() =>
      expect(element("#status-title-text").textContent).toBe("Already in PrintStash"),
    );

    expect(button("#capture").hidden).toBe(true);
    expect(button("#open-inbox").dataset.url).toBe("https://prints.example.com/models/37");
    expect(button("#import-again").hidden).toBe(false);
  });

  it("offers to resume an interrupted browser upload without creating a duplicate", async () => {
    fakeBrowser.tabs.query = vi
      .fn()
      .mockResolvedValue([{ url: "https://www.printables.com/model/3161-3d-benchy" }]);
    await fakeBrowser.storage.local.set(pairedDevice);
    vi.stubGlobal("fetch", async (input: RequestInfo | URL) => {
      const path = new URL(String(input)).pathname;
      if (path.endsWith("/health"))
        return response({ status: "ok", name: "PrintStash", capture_import_intent: true });
      if (path.endsWith("/capture-capabilities")) return response({ owner_user_id: 9 });
      if (path.endsWith("/12/capture-upload")) return response({ recoverable: true });
      return response([{ id: 12, state: "captured", completion: null }]);
    });
    await import("../popup.ts");
    await vi.waitFor(() => expect(button("#capture").textContent).toBe("Resume import"));
    expect(button("#capture").disabled).toBe(false);
    expect(button("#open-inbox").hidden).toBe(true);
  });

  it("shows the packaged release version in the popup footer", async () => {
    await import("../popup.ts");

    expect(element("#runtime-marker").textContent).toBe(`Version ${extensionVersion}`);
    expect(element("#runtime-marker").closest("footer")).toBe(element(".popup-footer"));
    expect(document.querySelector("header")?.textContent).not.toContain("protocol");
    expect(document.querySelector("header")?.textContent).not.toContain("diagnostics");
  });

  it("pairs a second browser with the chosen device name", async () => {
    const requests: Array<{ name: string }> = [];
    vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/browser-pairings/claim")) {
        requests.push(JSON.parse(String(init?.body)));
        return response({ credential: "second-device" });
      }
      return response({ status: "ok", name: "PrintStash" });
    });
    await import("../popup.ts");
    await settle();
    requiredElement("#vault", HTMLInputElement).value = "http://localhost:8000";
    requiredElement("#pairing-code", HTMLInputElement).value = "new-code";
    requiredElement("#device-name", HTMLInputElement).value = "MakerWorld validation";
    button("#connect").click();
    await settle();
    expect(requests).toEqual([{ code: "new-code", name: "MakerWorld validation" }]);
    expect(element("#connection-title").textContent).toBe("Connected");
  });

  it.each([
    { input: "localhost:8000", expected: "http://localhost:8000" },
    { input: "127.0.0.1:3000", expected: "http://127.0.0.1:3000" },
    { input: "192.168.1.20:8000", expected: "http://192.168.1.20:8000" },
    { input: "https://prints.example.com", expected: "https://prints.example.com" },
  ])(
    "submits supported Vault addresses through native form validation: $input",
    async ({ input, expected }) => {
      vi.stubGlobal(
        "fetch",
        vi.fn<typeof fetch>(async () =>
          response({
            status: "ok",
            name: "PrintStash",
            credential: "test-device-credential",
          }),
        ),
      );
      await import("../popup.ts");
      await settle();
      requiredElement("#vault", HTMLInputElement).value = input;
      requiredElement("#pairing-code", HTMLInputElement).value = "test-pairing-code";

      button("#connect").click();
      await settle();

      expect(element("#connection-title").textContent).toBe("Connected");
      expect(element("#page-context").hidden).toBe(false);
      expect(await fakeBrowser.storage.local.get(["vault", "deviceCredential"])).toEqual({
        vault: expected,
        deviceCredential: "test-device-credential",
      });
    },
  );

  it.each(["stored", "new pairing"])(
    "displays paired browser readiness without inventing a user role: %s",
    async (mode) => {
      if (mode === "stored") {
        await fakeBrowser.storage.local.set({
          vault: "https://prints.example.com",
          deviceCredential: "device-secret",
        });
      }
      const fetchImpl = vi.fn<typeof fetch>(async (input) => {
        if (String(input).endsWith("/health"))
          return response({ status: "ok", name: "PrintStash" });
        if (String(input).endsWith("/browser-pairings/claim"))
          return response({ credential: "device-secret", device: { id: 3, name: "Browser" } });
        throw new Error("Unexpected readiness request");
      });
      vi.stubGlobal("fetch", fetchImpl);
      await import("../popup.ts");
      await settle();
      if (mode === "new pairing") {
        requiredElement("#vault", HTMLInputElement).value = "https://prints.example.com";
        requiredElement("#pairing-code", HTMLInputElement).value = "pairing-code";
        button("#connect").click();
        await settle();
      }

      expect(element("#connection-title").textContent).toBe("Connected");
      expect(element("#connection-detail").textContent).toBe("Paired browser · prints.example.com");
      expect(element("#import-panel").hidden).toBe(false);
      expect(button("#capture").disabled).toBe(false);
      expect(fetchImpl.mock.calls.map(([url]) => String(url))).toEqual([
        "https://prints.example.com/api/v1/health",
        ...(mode === "new pairing"
          ? ["https://prints.example.com/api/v1/browser-pairings/claim"]
          : []),
      ]);
    },
  );

  it.each([
    { isSuperuser: true, role: "Admin" },
    { isSuperuser: false, role: "Member" },
  ])("preserves the verified legacy account role: $role", async ({ isSuperuser, role }) => {
    await fakeBrowser.storage.local.set({
      vault: "https://prints.example.com",
      username: "owner",
      apiKey: "legacy-key",
    });
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>(async (input) => {
        if (String(input).endsWith("/health"))
          return response({ status: "ok", name: "PrintStash" });
        if (String(input).endsWith("/login")) return response({ access_token: "vault-jwt" });
        if (String(input).endsWith("/me"))
          return response({ username: "owner", is_superuser: isSuperuser });
        throw new Error("Unexpected account request");
      }),
    );
    await import("../popup.ts");
    await settle();

    expect(element("#connection-title").textContent).toBe("Connected");
    expect(element("#connection-detail").textContent).toBe(`owner · prints.example.com · ${role}`);
    expect(button("#capture").disabled).toBe(false);
  });

  it("keeps an empty Vault address required", () => {
    const input = requiredElement("#vault", HTMLInputElement);

    input.value = "";

    expect(input.checkValidity()).toBe(false);
    expect(input.validity.valueMissing).toBe(true);
  });

  it("keeps first-run setup focused on pairing", async () => {
    await import("../popup.ts");
    await settle();

    expect(element("#connection-panel").hidden).toBe(false);
    expect(element("#page-context").hidden).toBe(true);
    expect(element("#import-panel").hidden).toBe(true);
    expect(requiredElement("#pairing-method", HTMLInputElement).checked).toBe(true);
  });

  it.each([
    { method: "pairing", selector: "#pairing-method", credentials: ["pairing-code"] },
    { method: "API key", selector: "#api-key-method", credentials: ["username", "key"] },
  ])("puts the browser name last in $method setup", async ({ selector, credentials }) => {
    await import("../popup.ts");
    await settle();

    requiredElement(selector, HTMLInputElement).click();

    const inputs = element("#connection-form").querySelectorAll("input");
    expect(
      [...inputs].filter((input) => !input.closest("[hidden]")).map((input) => input.id),
    ).toEqual(["vault", "pairing-method", "api-key-method", ...credentials, "device-name"]);
  });

  it("describes the disconnected state using server terminology", async () => {
    await import("../popup.ts");

    await vi.waitFor(() =>
      expect(element("#connection-detail").textContent).toBe(
        "Connect a PrintStash server to start importing.",
      ),
    );
  });

  it.each([
    {
      method: "API key",
      previous: "#pairing-method",
      selector: "#api-key-method",
      visible: "#api-key-fields",
      hidden: "#pairing-fields",
    },
    {
      method: "pairing code",
      previous: "#api-key-method",
      selector: "#pairing-method",
      visible: "#pairing-fields",
      hidden: "#api-key-fields",
    },
  ])(
    "shows credential fields for the selected $method method",
    async ({ previous, selector, visible, hidden }) => {
      await import("../popup.ts");
      await settle();
      requiredElement(previous, HTMLInputElement).click();

      requiredElement(selector, HTMLInputElement).click();

      expect(element(visible).hidden).toBe(false);
      expect(element(hidden).hidden).toBe(true);
    },
  );

  it("shows the API key method for prepared legacy setup", async () => {
    const vault = "https://prints.example.com";
    const now = Date.parse("2026-01-01T00:00:00Z");
    vi.spyOn(Date, "now").mockReturnValue(now);
    fakeBrowser.tabs.query = vi.fn().mockResolvedValue([{ id: 42, url: vault }]);
    fakeBrowser.permissions.contains = vi.fn().mockResolvedValue(false);
    fakeBrowser.scripting.executeScript = vi.fn().mockResolvedValue([
      {
        result: JSON.stringify({
          version: 1,
          vault,
          username: "owner",
          apiKey: "test-key",
          expiresAt: now + 60_000,
        }),
      },
    ]);

    await import("../popup.ts");
    await settle();

    expect(requiredElement("#api-key-method", HTMLInputElement).checked).toBe(true);
    expect(element("#api-key-fields").hidden).toBe(false);
    expect(requiredElement("#username", HTMLInputElement).value).toBe("owner");
    expect(button("#connect").textContent).toBe("Finish setup");
    expect(element("#status-message").textContent).toBe(
      "Setup received. Choose Finish setup to approve access to this server.",
    );
  });

  const pairedDevice = { vault: "https://prints.example.com", deviceCredential: "test-device" };
  const readyVault = () => response({ status: "ok", name: "PrintStash" });

  it("identifies the server when connection permission is denied", async () => {
    fakeBrowser.permissions.request = vi.fn().mockResolvedValue(false);
    await import("../popup.ts");
    await settle();
    requiredElement("#vault", HTMLInputElement).value = pairedDevice.vault;
    requiredElement("#pairing-code", HTMLInputElement).value = "test-pairing-code";

    button("#connect").click();

    await vi.waitFor(() =>
      expect(element("#connection-feedback").textContent).toBe(
        "Permission to contact this PrintStash server was not granted.",
      ),
    );
  });

  it("asks to reconnect the server when a saved permission is missing", async () => {
    await fakeBrowser.storage.local.set(pairedDevice);
    fakeBrowser.permissions.contains = vi.fn().mockResolvedValue(false);

    await import("../popup.ts");

    await vi.waitFor(() =>
      expect(element("#connection-feedback").textContent).toBe(
        "Reconnect this server to restore its browser permission.",
      ),
    );
  });

  it.each([
    {
      state: "checking",
      label: "Checking…",
      stored: pairedDevice,
      health: () => deferred<Response>().promise,
    },
    { state: "disconnected", label: "Not connected", stored: {}, health: readyVault },
    { state: "connected", label: "Connected", stored: pairedDevice, health: readyVault },
    {
      state: "error",
      label: "Connection problem",
      stored: pairedDevice,
      health: () => response({}, 503),
    },
  ])("announces $state in the header pill", async ({ state, label, stored, health }) => {
    await fakeBrowser.storage.local.set(stored);
    vi.stubGlobal("fetch", async () => health());

    await import("../popup.ts");

    await vi.waitFor(() => expect(element("#connection-status").dataset.state).toBe(state));
    expect(
      element("header #connection-status[aria-live='polite'] #connection-title").textContent,
    ).toBe(label);
  });

  it("shows capture suggestions when connected", async () => {
    await fakeBrowser.storage.local.set(pairedDevice);
    vi.stubGlobal("fetch", async () => readyVault());

    await import("../popup.ts");
    await vi.waitFor(() => expect(element("#import-panel").hidden).toBe(false));

    const suggestions = requiredElement("#capture-preferences", HTMLFieldSetElement);
    expect(suggestions.disabled).toBe(false);
    expect(suggestions.querySelector("legend")?.textContent).toBe("Suggestions for review");
    expect(
      [...suggestions.querySelectorAll("input, select, button")]
        .filter((item) => !item.closest("[hidden]"))
        .map((item) => item.id),
    ).toEqual(["capture-collection", "capture-tags", "capture-cover"]);
  });

  it("visibly labels each suggestion input", async () => {
    await fakeBrowser.storage.local.set(pairedDevice);
    vi.stubGlobal("fetch", async () => readyVault());

    await import("../popup.ts");
    await vi.waitFor(() => expect(element("#import-panel").hidden).toBe(false));

    const controls = document.querySelectorAll<HTMLInputElement | HTMLSelectElement>(
      "#capture-preferences input, #capture-preferences select",
    );

    expect(
      [...controls].map((control) =>
        [...(control.labels ?? [])]
          .filter((label) => !label.closest("[hidden], .visually-hidden"))
          .map((label) => label.textContent?.trim()),
      ),
    ).toEqual([["Collection"], ["Tags"], ["Add cover image"]]);
  });

  describe.each([
    { id: "manual-file", nameId: "manual-file-name", action: "Choose file", ext: "stl" },
    { id: "capture-cover", nameId: "capture-cover-name", action: "Add cover image", ext: "png" },
  ])("$id picker", ({ id, nameId, action, ext }) => {
    it("labels the visually hidden native input", () => {
      const input = requiredElement(`#${id}.visually-hidden[type="file"]`, HTMLInputElement);

      expect(input.labels?.item(0)?.textContent).toBe(action);
    });

    it("shows the selected filename as text", async () => {
      await import("../popup.ts");
      const input = requiredElement(`#${id}`, HTMLInputElement);
      const file = new File(["file"], `<part>.${ext}`);
      Object.defineProperty(input, "files", { value: [file] });

      input.dispatchEvent(new Event("change"));

      expect(element(`#${nameId}`).textContent).toBe(file.name);
    });

    it("restores the empty filename after the selection clears", async () => {
      await import("../popup.ts");
      const input = requiredElement(`#${id}`, HTMLInputElement);
      const files = [new File(["file"], `part.${ext}`)];
      Object.defineProperty(input, "files", { value: files });
      input.dispatchEvent(new Event("change"));

      files.pop();
      input.dispatchEvent(new Event("change"));

      if (id === "capture-cover") {
        expect(element(`#${nameId}`).hidden).toBe(true);
        expect(button("#remove-cover").hidden).toBe(true);
      } else {
        expect(element(`#${nameId}`).textContent).toBe("No file selected");
      }
    });
  });

  it("clears the cover through its Remove action", async () => {
    await fakeBrowser.storage.local.set(pairedDevice);
    vi.stubGlobal("fetch", async () => readyVault());
    await import("../popup.ts");
    await vi.waitFor(() => expect(button("#capture").disabled).toBe(false));
    const input = requiredElement("#capture-cover", HTMLInputElement);
    const files = [new File(["image"], "cover.png", { type: "image/png" })];
    Object.defineProperty(input, "files", { value: files });
    const clear = vi.spyOn(input, "value", "set").mockImplementation(() => {
      files.length = 0;
    });
    input.dispatchEvent(new Event("change"));
    expect(button("#remove-cover").hidden).toBe(false);

    button("#remove-cover").click();

    expect(clear).toHaveBeenCalledWith("");
    expect(button("#remove-cover").hidden).toBe(true);
    expect(element("#capture-cover-name").hidden).toBe(true);
  });

  it("shows the allowed cover formats when validation fails", async () => {
    await import("../popup.ts");
    await settle();
    const input = requiredElement("#capture-cover", HTMLInputElement);
    Object.defineProperty(input, "files", {
      value: [new File(["mesh"], "part.stl", { type: "model/stl" })],
    });

    input.dispatchEvent(new Event("change"));

    expect(element("#cover-error").hidden).toBe(false);
    expect(element("#cover-error").textContent).toBe(
      "Choose a JPEG, PNG or WebP cover up to 15 MiB.",
    );
    expect(input.getAttribute("aria-invalid")).toBe("true");
  });

  it.each([
    { label: "setup", state: "disconnected", stored: {}, action: "Pair browser" },
    { label: "capture", state: "connected", stored: pairedDevice, action: "Send to PrintStash" },
  ])("shows a single primary action during $label", async ({ state, stored, action }) => {
    await fakeBrowser.storage.local.set(stored);
    vi.stubGlobal("fetch", async () => readyVault());

    await import("../popup.ts");
    await vi.waitFor(() => expect(element("#connection-status").dataset.state).toBe(state));

    const buttons = document.querySelectorAll<HTMLButtonElement>("button.primary");
    expect(
      [...buttons].filter((item) => !item.closest("[hidden]")).map((item) => item.textContent),
    ).toEqual([action]);
  });

  describe("capture feedback", () => {
    beforeEach(async () => {
      await fakeBrowser.storage.local.set({
        vault: "https://prints.example.com",
        deviceCredential: "test-device",
      });
      fakeBrowser.tabs.query = vi
        .fn()
        .mockResolvedValue([{ id: 42, title: "Part", url: "https://models.example.com/part.stl" }]);
    });

    it("shows the direct filename with its host as a subtitle", async () => {
      fakeBrowser.tabs.query = vi.fn().mockResolvedValue([
        {
          title: "Browser-generated page title",
          url: "https://models.example.com/my%20part.stl",
        },
      ]);
      vi.stubGlobal("fetch", async () => readyVault());
      await import("../popup.ts");
      await vi.waitFor(() => expect(element("#page-context").hidden).toBe(false));

      expect(element("#page").textContent).toBe("my part.stl");
      expect(element("#page-host").textContent).toBe("models.example.com");
      expect(element("#page-host").hidden).toBe(false);
    });

    it("offers manual attachment when PrintStash refuses a private direct URL", async () => {
      const privateUrl = "http://192.168.1.42/part.stl";
      fakeBrowser.tabs.query = vi.fn().mockResolvedValue([{ url: privateUrl }]);
      const uploads: FormData[] = [];
      vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
        if (url.endsWith("/health")) return readyVault();
        if (url.endsWith("/browser-upload") && init?.body instanceof FormData) {
          uploads.push(init.body);
          return response({ id: 53 }, 201);
        }
        return response({ detail: "url_target_not_public" }, 400);
      });
      await import("../popup.ts");
      await vi.waitFor(() => expect(button("#capture").disabled).toBe(false));

      button("#capture").click();
      await vi.waitFor(() => expect(element("#manual-file-panel").hidden).toBe(false));
      expect(element("#status-message").textContent).toBe(
        "PrintStash can't download from local or private addresses. Download the file and attach it instead.",
      );
      expect(element("#status-code").textContent).toBe("url_target_not_public");

      const input = requiredElement("#manual-file", HTMLInputElement);
      const file = new File(["mesh"], "part.stl", { type: "model/stl" });
      Object.defineProperty(input, "files", { value: { item: () => file, 0: file, length: 1 } });
      input.dispatchEvent(new Event("change"));
      button("#capture").click();
      await vi.waitFor(() =>
        expect(button("#open-inbox").dataset.url).toBe("https://prints.example.com/inbox/53"),
      );
      expect(uploads).toHaveLength(1);
      expect(uploads[0].get("source_url")).toBe(privateUrl);
      expect(await (uploads[0].get("file") as File).text()).toBe("mesh");
    });

    it("offers review from the capture confirmation", async () => {
      vi.stubGlobal("fetch", async (url: string) =>
        url.endsWith("/health")
          ? response({ status: "ok", name: "PrintStash" })
          : response({ id: 42 }),
      );
      await import("../popup.ts");
      await vi.waitFor(() => expect(button("#capture").disabled).toBe(false));

      button("#capture").click();
      await vi.waitFor(() => expect(element("#status").dataset.kind).toBe("success"));

      expect(
        element("#status[role='status'][aria-live='polite'] #status-title").textContent?.trim(),
      ).toBe("Sent to Pending Imports");
      expect(element("#status-message").hidden).toBe(true);
      const review = button("#open-inbox");
      expect(review.textContent).toBe("Review in PrintStash");
      expect(element("#import-panel").hidden).toBe(true);
      expect(button("#capture").hidden).toBe(true);
      expect(document.activeElement).toBe(review);
      expect(review.hidden).toBe(false);
      review.click();
      expect(fakeBrowser.tabs.create).toHaveBeenCalledWith({
        url: "https://prints.example.com/inbox/42",
      });
    });

    it("renders a decorative SVG check on success", async () => {
      vi.stubGlobal("fetch", async (url: string) =>
        url.endsWith("/health") ? readyVault() : response({ id: 42 }),
      );
      await import("../popup.ts");
      await vi.waitFor(() => expect(button("#capture").disabled).toBe(false));

      button("#capture").click();
      await vi.waitFor(() => expect(element("#status").dataset.kind).toBe("success"));

      expect(
        document.querySelector('#status-title:not([hidden]) > svg[aria-hidden="true"] path'),
      ).toBeInstanceOf(SVGElement);
    });

    it.each([
      ["capture_not_pending", "capture_not_pending"],
      ["Import failed.", "capture_failed"],
      [["validation detail"], "capture_failed"],
    ])("shows the safe server diagnostic for %s", async (detailValue, expectedCode) => {
      vi.stubGlobal("fetch", async (url: string) =>
        url.endsWith("/health")
          ? response({ status: "ok", name: "PrintStash" })
          : response({ detail: detailValue }, 503),
      );
      await import("../popup.ts");
      await vi.waitFor(() => expect(button("#capture").disabled).toBe(false));

      button("#capture").click();
      await vi.waitFor(() => expect(element("#status").dataset.kind).toBe("error"));

      expect(element("#status-message").hidden).toBe(false);
      const details = element("#status-details");
      expect(details.hidden).toBe(false);
      expect(details.previousElementSibling).toBe(element("#status-message"));
      expect(details.textContent?.trim()).toBe(`Code: ${expectedCode}`);
      expect(element("#status-code").textContent).toBe(expectedCode);
    });
  });

  it("asks users to keep the popup open during a transfer", async () => {
    await fakeBrowser.storage.local.set({
      vault: "https://prints.example.com",
      deviceCredential: "test-device-credential",
    });
    fakeBrowser.tabs.query = vi
      .fn()
      .mockResolvedValue([
        { id: 42, title: "Part.stl", url: "https://models.example.com/part.stl" },
      ]);
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>(async () => response({ status: "ok", name: "PrintStash", id: 1 })),
    );
    await import("../popup.ts");
    await settle();

    button("#capture").click();

    expect(element("#import-hint").textContent).toContain(
      "Keep this popup open until it finishes.",
    );
    expect(button("#capture").disabled).toBe(true);
    await settle();
  });

  it.each([
    {
      vault: "http://[",
      code: "test-code",
      method: "#pairing-method",
      message: "Vault URL is invalid.",
    },
    {
      vault: "ftp://prints.example.com",
      code: "test-code",
      method: "#pairing-method",
      message: "Vault URL must use HTTP or HTTPS.",
    },
    {
      vault: "https://prints.example.com",
      code: "",
      method: "#pairing-method",
      message: "Enter a pairing code.",
    },
    {
      vault: "https://prints.example.com",
      code: "unused-pairing-code",
      method: "#api-key-method",
      message: "Enter your username and named API key.",
    },
  ])("explains invalid connection input: $message", async ({ vault, code, method, message }) => {
    const fetchImpl = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetchImpl);
    await import("../popup.ts");
    await settle();
    requiredElement(method, HTMLInputElement).click();
    requiredElement("#vault", HTMLInputElement).value = vault;
    requiredElement("#pairing-code", HTMLInputElement).value = code;

    button("#connect").click();
    await settle();

    expect(element("#connection-title").textContent).toBe("Not connected");
    expect(element("#connection-feedback").textContent).toBe(message);
    expect(fetchImpl).not.toHaveBeenCalled();
    expect(await fakeBrowser.storage.local.get()).toEqual({});
  });

  it("exposes local help from the popup", () => {
    const link = requiredElement("#help-link", HTMLAnchorElement);

    expect(link.getAttribute("href")).toBe("help.html");
    expect(link.target).toBe("_blank");
    expect(link.rel).toContain("noopener");
    expect(link.textContent).toBe("Help & privacy");
  });

  it("places recovery guidance before the fallback controls", () => {
    const status = element("#status");
    const manualFilePanel = element("#manual-file-panel");

    expect(status.compareDocumentPosition(manualFilePanel) & Node.DOCUMENT_POSITION_FOLLOWING).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
  });

  it("reports a safe code and falls back when Printables permission checking times out", async () => {
    vi.stubGlobal("__PRINTSTASH_CAPTURE_TIMEOUT_MS__", 5);
    await fakeBrowser.storage.local.set({
      vault: "https://prints.example.com",
      username: "owner",
      apiKey: "psk_vault_secret",
    });
    fakeBrowser.permissions.contains = vi.fn(({ origins }: { origins: string[] }) =>
      origins[0] === "https://api.printables.com/*"
        ? new Promise<boolean>(() => {})
        : Promise.resolve(true),
    );
    const fetchImpl = vi.fn(async (url: string) => {
      if (url.endsWith("/capture-capabilities")) return response({}, 404);
      if (url.endsWith("/health")) return response({ status: "ok", name: "PrintStash" });
      if (url.endsWith("/login")) return response({ access_token: "vault-jwt" });
      if (url.endsWith("/me")) return response({ username: "owner", is_superuser: false });
      throw new Error("unexpected provider request");
    });
    vi.stubGlobal("fetch", fetchImpl);
    fakeBrowser.scripting.executeScript = vi.fn().mockResolvedValue([{ result: null }]);

    await import("../popup.ts");
    await settle();
    vi.mocked(fakeBrowser.scripting.executeScript).mockClear();
    fetchImpl.mockClear();
    button("#capture").click();
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(element("#manual-file-panel").hidden).toBe(false);
    expect(element("#status").textContent).toContain("capture_permission_contains_timeout");
    expect(fetchImpl.mock.calls.some(([url]) => url.includes("api.printables.com"))).toBe(false);
    expect(fakeBrowser.scripting.executeScript).not.toHaveBeenCalled();
  });

  it("allows time to read the native provider permission prompt", async () => {
    await fakeBrowser.storage.local.set({
      vault: "https://prints.example.com",
      username: "owner",
      apiKey: "psk_test",
    });
    const permission = deferred<boolean>();
    fakeBrowser.permissions.contains = vi.fn(
      async ({ origins }: { origins: string[] }) => origins[0] !== "https://api.printables.com/*",
    );
    fakeBrowser.permissions.request = vi.fn(() => permission.promise);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.endsWith("/health")) return response({ status: "ok", name: "PrintStash" });
        if (url.endsWith("/login")) return response({ access_token: "test-jwt" });
        if (url.endsWith("/me")) return response({ username: "owner", is_superuser: false });
        throw new Error("unexpected request");
      }),
    );
    await import("../popup.ts");
    await settle();
    vi.useFakeTimers();
    try {
      button("#capture").click();
      await vi.advanceTimersByTimeAsync(11_000);
      expect(button("#capture").disabled).toBe(true);
      expect(element("#status").textContent).not.toContain("timed out");
      permission.resolve(false);
      await vi.advanceTimersByTimeAsync(0);
      expect(button("#capture").disabled).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });

  it("reports a safe code when visible capture execution rejects or times out", async () => {
    vi.stubGlobal("__PRINTSTASH_CAPTURE_TIMEOUT_MS__", 5);
    await fakeBrowser.storage.local.set({
      vault: "https://prints.example.com",
      username: "owner",
      apiKey: "psk_vault_secret",
    });
    const fetchImpl = vi.fn(async (url: string) => {
      if (url.endsWith("/capture-capabilities")) return response({}, 404);
      if (url.endsWith("/health")) return response({ status: "ok", name: "PrintStash" });
      if (url.endsWith("/login")) return response({ access_token: "vault-jwt" });
      if (url.endsWith("/me")) return response({ username: "owner", is_superuser: false });
      throw new Error("unexpected provider request");
    });
    vi.stubGlobal("fetch", fetchImpl);
    fakeBrowser.scripting.executeScript = vi.fn().mockResolvedValue([{ result: null }]);
    await import("../popup.ts");
    await settle();
    vi.mocked(fakeBrowser.scripting.executeScript).mockClear();
    fakeBrowser.scripting.executeScript = vi
      .fn()
      .mockRejectedValue(
        new Error("provider payload https://api.printables.com/graphql/ Bearer secret-token"),
      );
    button("#capture").click();
    await settle();

    expect(element("#status").textContent).toContain("capture_visible_capture_failed");
    expect(element("#status").textContent).not.toContain("secret-token");
  });

  it("reports a safe timeout code when visible capture execution hangs", async () => {
    vi.stubGlobal("__PRINTSTASH_CAPTURE_TIMEOUT_MS__", 5);
    await fakeBrowser.storage.local.set({
      vault: "https://prints.example.com",
      username: "owner",
      apiKey: "psk_vault_secret",
    });
    const fetchImpl = vi.fn(async (url: string) => {
      if (url.endsWith("/capture-capabilities")) return response({}, 404);
      if (url.endsWith("/health")) return response({ status: "ok", name: "PrintStash" });
      if (url.endsWith("/login")) return response({ access_token: "vault-jwt" });
      if (url.endsWith("/me")) return response({ username: "owner", is_superuser: false });
      throw new Error("unexpected provider request");
    });
    vi.stubGlobal("fetch", fetchImpl);
    fakeBrowser.scripting.executeScript = vi.fn().mockResolvedValue([{ result: null }]);
    await import("../popup.ts");
    await settle();
    vi.mocked(fakeBrowser.scripting.executeScript).mockClear();
    vi.mocked(fakeBrowser.scripting.executeScript).mockImplementation(
      () => new Promise<never>(() => {}),
    );
    button("#capture").click();
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(element("#status").textContent).toContain("capture_visible_capture_timeout");
  });

  it("reports provider-specific metadata stage codes without exposing provider payloads", async () => {
    vi.stubGlobal("__PRINTSTASH_CAPTURE_TIMEOUT_MS__", 5);
    await fakeBrowser.storage.local.set({
      vault: "https://prints.example.com",
      username: "owner",
      apiKey: "psk_vault_secret",
    });
    const printablesFetch = vi.fn(async (url: string) => {
      if (url.endsWith("/capture-capabilities")) return response({}, 404);
      if (url.endsWith("/health")) return response({ status: "ok", name: "PrintStash" });
      if (url.endsWith("/login")) return response({ access_token: "vault-jwt" });
      if (url.endsWith("/me")) return response({ username: "owner", is_superuser: false });
      if (url === "https://api.printables.com/graphql/")
        return response({ error: "provider secret" }, 400);
      throw new Error("unexpected request");
    });
    vi.stubGlobal("fetch", printablesFetch);
    fakeBrowser.scripting.executeScript = vi
      .fn()
      .mockResolvedValueOnce([{ result: null }])
      .mockResolvedValueOnce([{ result: { pageTitle: "3DBenchy", jsonLd: [] } }]);
    await import("../popup.ts");
    await settle();
    button("#capture").click();
    await settle();

    expect(element("#status").textContent).toContain("printables_metadata_http");
    expect(element("#status").textContent).not.toContain("provider secret");
  });

  it("reports MakerWorld metadata stage failures with a stable code", async () => {
    vi.stubGlobal("__PRINTSTASH_CAPTURE_TIMEOUT_MS__", 5);
    await fakeBrowser.storage.local.set({
      vault: "https://prints.example.com",
      username: "owner",
      apiKey: "psk_vault_secret",
    });
    fakeBrowser.tabs.query = vi
      .fn()
      .mockResolvedValue([
        { id: 42, title: "Calibration cube", url: "https://makerworld.com/en/models/1234-cube" },
      ]);
    const fetchImpl = vi.fn(async (url: string) => {
      if (url.endsWith("/capture-capabilities")) return response({}, 404);
      if (url.endsWith("/health")) return response({ status: "ok", name: "PrintStash" });
      if (url.endsWith("/login")) return response({ access_token: "vault-jwt" });
      if (url.endsWith("/me")) return response({ username: "owner", is_superuser: false });
      throw new Error("unexpected MakerWorld metadata request");
    });
    vi.stubGlobal("fetch", fetchImpl);
    fakeBrowser.scripting.executeScript = vi
      .fn()
      .mockResolvedValueOnce([{ result: null }])
      .mockResolvedValueOnce([{ result: { pageTitle: "Calibration cube", jsonLd: [] } }])
      .mockResolvedValueOnce([{ result: { ok: false, code: "request_failed" } }]);
    await import("../popup.ts");
    await settle();
    button("#capture").click();
    await settle();
    expect(element("#status").textContent).toContain("makerworld_metadata_http");
  });

  it("fetches Printables metadata from the extension context with narrow permission and no MAIN metadata seam", async () => {
    await fakeBrowser.storage.local.set({
      vault: "https://prints.example.com",
      username: "owner",
      apiKey: "psk_vault_secret",
    });
    fakeBrowser.permissions.contains = vi.fn(
      async ({ origins }: { origins: string[] }) => origins[0] !== "https://api.printables.com/*",
    );
    fakeBrowser.tabs.query = vi.fn().mockResolvedValue([
      {
        id: 42,
        title: "3DBenchy",
        url: "https://printables.com/model/3161-3d-benchy/files",
      },
    ]);
    const metadata = {
      data: {
        print: {
          id: "3161",
          name: "3DBenchy",
          license: { name: "CC BY-NC 4.0" },
          stls: [{ id: "stl-1", name: "benchy.stl", fileSize: 4 }],
          gcodes: [{ id: "gcode-1", name: "benchy.gcode", fileSize: 4 }],
        },
      },
    };
    const fetchImpl = vi.fn(async (url: string, _options: RequestInit = {}) => {
      if (url.endsWith("/capture-capabilities")) return response({}, 404);
      if (url.endsWith("/health")) return response({ status: "ok", name: "PrintStash" });
      if (url.endsWith("/login")) return response({ access_token: "vault-jwt" });
      if (url.endsWith("/me")) return response({ username: "owner", is_superuser: false });
      if (url === "https://api.printables.com/graphql/") return response(metadata);
      throw new Error(`Unexpected Printables request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchImpl);
    fakeBrowser.scripting.executeScript = vi
      .fn()
      .mockResolvedValueOnce([{ result: null }])
      .mockResolvedValueOnce([{ result: { pageTitle: "3DBenchy", jsonLd: [] } }])
      .mockRejectedValueOnce(new TypeError("Failed to fetch"));

    await import("../popup.ts");
    await settle();
    button("#capture").click();
    for (let attempt = 0; attempt < 6; attempt += 1) await settle();

    expect(element("#candidate-panel").hidden).toBe(false);
    expect(document.querySelectorAll("#candidate-list input")).toHaveLength(2);
    expect(fakeBrowser.permissions.request).toHaveBeenCalledWith({
      origins: ["https://api.printables.com/*"],
    });
    const metadataCall = fetchImpl.mock.calls.find(
      ([url]) => url === "https://api.printables.com/graphql/",
    );
    if (!metadataCall?.[1]) throw new Error("Missing Printables metadata request");
    expect(metadataCall[1].credentials).toBe("omit");
    expect(metadataCall[1].headers).not.toHaveProperty("Authorization");
    expect(JSON.stringify(metadataCall[1])).not.toContain("psk_vault_secret");
    const metadataExecutes = vi
      .mocked(fakeBrowser.scripting.executeScript)
      .mock.calls.filter(
        ([details]) =>
          Array.isArray(details.args) &&
          details.args[0] !== null &&
          typeof details.args[0] === "object" &&
          "query" in details.args[0],
      );
    expect(metadataExecutes).toHaveLength(0);
    expect(
      vi
        .mocked(fakeBrowser.scripting.executeScript)
        .mock.calls.some(
          ([details]) =>
            Array.isArray(details.args) &&
            details.args[0] !== null &&
            typeof details.args[0] === "object" &&
            "groups" in details.args[0],
        ),
    ).toBe(false);
  });

  it("checks Printables API permission before page extraction or metadata fetch", async () => {
    await fakeBrowser.storage.local.set({
      vault: "https://prints.example.com",
      username: "owner",
      apiKey: "psk_vault_secret",
    });
    const events: string[] = [];
    fakeBrowser.permissions.contains = vi.fn(async ({ origins }: { origins: string[] }) => {
      events.push(`contains:${origins[0]}`);
      return origins[0] !== "https://api.printables.com/*";
    });
    fakeBrowser.permissions.request = vi.fn(async ({ origins }: { origins: string[] }) => {
      events.push(`request:${origins[0]}`);
      return true;
    });
    const fetchImpl = vi.fn(async (url: string) => {
      events.push(`fetch:${url}`);
      if (url.endsWith("/capture-capabilities")) return response({}, 404);
      if (url.endsWith("/health")) return response({ status: "ok", name: "PrintStash" });
      if (url.endsWith("/login")) return response({ access_token: "vault-jwt" });
      if (url.endsWith("/me")) return response({ username: "owner", is_superuser: false });
      if (url === "https://api.printables.com/graphql/")
        return response({
          data: {
            print: {
              id: "3161",
              name: "3DBenchy",
              license: { name: "CC BY-NC 4.0" },
              stls: [{ id: "stl-1", name: "benchy.stl", fileSize: 4 }],
            },
          },
        });
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchImpl);
    fakeBrowser.scripting.executeScript = vi.fn(async () => {
      events.push("executeScript");
      return [{ frameId: 0, result: { pageTitle: "3DBenchy", jsonLd: [] } }];
    });

    await import("../popup.ts");
    await settle();
    events.length = 0;
    vi.mocked(fakeBrowser.scripting.executeScript).mockClear();
    fetchImpl.mockClear();
    button("#capture").click();
    for (let attempt = 0; attempt < 6; attempt += 1) await settle();

    const firstExecute = events.indexOf("executeScript");
    expect(firstExecute).toBeGreaterThanOrEqual(0);
    expect(events.slice(0, firstExecute)).toEqual([
      "contains:https://api.printables.com/*",
      "request:https://api.printables.com/*",
    ]);
    expect(element("#candidate-panel").hidden).toBe(false);
  });

  it("falls directly to manual Printables attachment when API permission is denied", async () => {
    await fakeBrowser.storage.local.set({
      vault: "https://prints.example.com",
      username: "owner",
      apiKey: "psk_vault_secret",
    });
    fakeBrowser.permissions.contains = vi.fn(
      async ({ origins }: { origins: string[] }) => origins[0] !== "https://api.printables.com/*",
    );
    fakeBrowser.permissions.request = vi.fn().mockResolvedValue(false);
    const fetchImpl = vi.fn(async (url: string) => {
      if (url.endsWith("/capture-capabilities")) return response({}, 404);
      if (url.endsWith("/health")) return response({ status: "ok", name: "PrintStash" });
      if (url.endsWith("/login")) return response({ access_token: "vault-jwt" });
      if (url.endsWith("/me")) return response({ username: "owner", is_superuser: false });
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchImpl);
    fakeBrowser.scripting.executeScript = vi.fn();

    await import("../popup.ts");
    await settle();
    vi.mocked(fakeBrowser.scripting.executeScript).mockClear();
    fetchImpl.mockClear();
    button("#capture").click();
    for (let attempt = 0; attempt < 6; attempt += 1) await settle();

    expect(element("#manual-file-panel").hidden).toBe(false);
    expect(fakeBrowser.scripting.executeScript).not.toHaveBeenCalled();
    expect(
      fetchImpl.mock.calls.some(([url]) => url === "https://api.printables.com/graphql/"),
    ).toBe(false);
  });

  it("fetches MakerWorld metadata through the MAIN seam with four unchecked live candidates", async () => {
    await fakeBrowser.storage.local.set({
      vault: "https://prints.example.com",
      username: "owner",
      apiKey: "psk_vault_secret",
    });
    fakeBrowser.tabs.query = vi.fn().mockResolvedValue([
      {
        id: 42,
        title: "Calibration cube",
        url: "https://makerworld.com/en/models/1234-calibration-cube",
      },
    ]);
    const fetchImpl = vi.fn(async (url: string, _options: RequestInit = {}) => {
      if (url.endsWith("/capture-capabilities")) return response({}, 404);
      if (url.endsWith("/health")) return response({ status: "ok", name: "PrintStash" });
      if (url.endsWith("/login")) return response({ access_token: "vault-jwt" });
      if (url.endsWith("/me")) return response({ username: "owner", is_superuser: false });
      if (url.includes("design-service/design")) {
        throw new Error(`Unexpected extension-context MakerWorld metadata request: ${url}`);
      }
      throw new Error(`Unexpected MakerWorld request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchImpl);
    fakeBrowser.scripting.executeScript = vi
      .fn()
      .mockResolvedValueOnce([{ result: null }])
      .mockResolvedValueOnce([{ result: { pageTitle: "Calibration cube", jsonLd: [] } }])
      .mockResolvedValueOnce([
        {
          result: {
            ok: true,
            metadata: {
              fixtureVersion: "makerworld-design-service-v1",
              sourceItemId: "1234",
              source: { title: "Calibration cube", creatorName: "Maker" },
              files: [
                {
                  id: "instance-default",
                  filename: "cube—高.3mf",
                  fileType: "other",
                  sizeBytes: 4,
                },
                { id: "instance-alt", filename: "cube-alt.3mf", fileType: "other", sizeBytes: 4 },
                {
                  id: "instance-third",
                  filename: "cube-third.3mf",
                  fileType: "other",
                  sizeBytes: 4,
                },
                {
                  id: "instance-fourth",
                  filename: "cube-fourth.3mf",
                  fileType: "other",
                  sizeBytes: 4,
                },
              ],
            },
          },
        },
      ]);

    await import("../popup.ts");
    await settle();
    button("#capture").click();
    for (let attempt = 0; attempt < 6; attempt += 1) await settle();

    expect(element("#candidate-panel").hidden).toBe(false);
    expect(document.querySelectorAll("#candidate-list input")).toHaveLength(4);
    expect(
      [...document.querySelectorAll<HTMLInputElement>("#candidate-list input")].every(
        (input) => !input.checked,
      ),
    ).toBe(true);
    const metadataExecutes = vi
      .mocked(fakeBrowser.scripting.executeScript)
      .mock.calls.filter(
        ([details]) =>
          Array.isArray(details.args) &&
          details.args[0] !== null &&
          typeof details.args[0] === "object" &&
          "endpoint" in details.args[0] &&
          String(details.args[0].endpoint).includes("design-service/design"),
      );
    expect(metadataExecutes).toHaveLength(1);
    expect(metadataExecutes[0]?.[0].args?.[0]).toMatchObject({
      sourceItemId: "1234",
      fixtureVersion: "makerworld-design-service-v1",
    });
    expect(fetchImpl.mock.calls.some(([url]) => url.includes("design-service/design"))).toBe(false);
    expect(fakeBrowser.permissions.request).not.toHaveBeenCalledWith({
      origins: ["https://makerworld.com/*"],
    });
    expect(
      vi
        .mocked(fakeBrowser.scripting.executeScript)
        .mock.calls.some(
          ([details]) =>
            Array.isArray(details.args) &&
            details.args[0] !== null &&
            typeof details.args[0] === "object" &&
            "selectedIds" in details.args[0],
        ),
    ).toBe(false);
  });

  it("requests MakerOnline download access before starting verification", async () => {
    await fakeBrowser.storage.local.set({
      vault: "https://prints.example.com",
      username: "owner",
      apiKey: "psk_test",
    });
    fakeBrowser.tabs.query = vi
      .fn()
      .mockResolvedValue([
        { id: 42, title: "Clip", url: "https://www.makeronline.com/en/model/Clip/100.html" },
      ]);
    vi.stubGlobal("fetch", async (url: string) => {
      if (url.endsWith("/capture-capabilities")) return response({}, 404);
      if (url.endsWith("/login")) return response({ access_token: "test-token" });
      if (url.endsWith("/me")) return response({ username: "owner", is_superuser: false });
      return response({ status: "ok", name: "PrintStash" });
    });
    fakeBrowser.scripting.executeScript = vi
      .fn()
      .mockResolvedValueOnce([{ result: null }])
      .mockResolvedValueOnce([{ result: { pageTitle: "Clip", jsonLd: [] } }])
      .mockResolvedValueOnce([
        {
          result: {
            ok: true,
            kind: "metadata",
            source: { title: "Clip" },
            profiles: [
              {
                id: "200",
                filename: "clip.3mf",
                size: 1000,
                profile: { id: "200", name: "Fine", origin: "provider" },
              },
            ],
          },
        },
      ]);
    await import("../popup.ts");
    await settle();
    button("#capture").click();
    await vi.waitFor(() => expect(element("#candidate-panel").hidden).toBe(false));
    requiredElement("#candidate-list input", HTMLInputElement).checked = true;
    fakeBrowser.permissions.contains = vi.fn().mockResolvedValue(false);
    fakeBrowser.permissions.request = vi.fn().mockResolvedValue(false);
    vi.mocked(fakeBrowser.scripting.executeScript).mockClear();

    button("#capture").click();
    await vi.waitFor(() =>
      expect(element("#status-message").textContent).toContain("Permission to download"),
    );

    expect(fakeBrowser.permissions.request).toHaveBeenCalledWith({
      origins: ["https://acop-prod-private.s3-accelerate.amazonaws.com/*"],
    });
    expect(fakeBrowser.scripting.executeScript).not.toHaveBeenCalled();
  });

  it("restores settings and checks the vault through fake storage and permission APIs", async () => {
    await fakeBrowser.storage.local.set({
      vault: "https://prints.example.com",
      username: "owner",
      apiKey: "psk_vault_secret",
    });
    const fetchImpl = vi.fn(async (url) => {
      if (url.endsWith("/capture-capabilities")) return response({}, 404);
      if (url.endsWith("/health")) return response({ status: "ok", name: "PrintStash" });
      if (url.endsWith("/login")) return response({ access_token: "vault-jwt" });
      return response({ username: "owner", is_superuser: false });
    });
    vi.stubGlobal("fetch", fetchImpl);

    await import("../popup.ts");
    await settle();

    expect(fakeBrowser.permissions.contains).toHaveBeenCalledWith({
      origins: ["https://prints.example.com/*"],
    });
    expect(element("#connection-title").textContent).toBe("Connected");
    expect(button("#capture").disabled).toBe(false);
    expect(fetchImpl).toHaveBeenCalledTimes(3);
  });

  describe("connection lifecycle", () => {
    const previous = { vault: "https://vault-a.example.com", deviceCredential: "device-a" };
    const nextVault = "https://vault-b.example.com";

    async function openConnectionEditor() {
      await fakeBrowser.storage.local.set(previous);
      await import("../popup.ts");
      await settle();
      button("#edit-connection").click();
      requiredElement("#vault", HTMLInputElement).value = nextVault;
      requiredElement("#pairing-code", HTMLInputElement).value = "test-new-code";
    }

    it.each([200, 400])(
      "preserves the previous connection after cancelled verification: %s",
      async (status) => {
        const claim = deferred<Response>();
        let claimStarted = false;
        let requestSignal: AbortSignal | null | undefined;
        vi.stubGlobal(
          "fetch",
          vi.fn<typeof fetch>(async (url, options) => {
            if (String(url).endsWith("/claim")) {
              claimStarted = true;
              requestSignal = options?.signal;
              return claim.promise;
            }
            return response({ status: "ok", name: "PrintStash" });
          }),
        );
        await openConnectionEditor();
        button("#connect").click();
        await vi.waitFor(() => expect(claimStarted).toBe(true));
        expect(button("#cancel-edit").hidden).toBe(false);
        expect(button("#cancel-edit").disabled).toBe(false);
        button("#cancel-edit").click();
        claim.resolve(response({ credential: "device-b" }, status));
        await settle();
        expect(await fakeBrowser.storage.local.get()).toEqual(previous);
        expect(element("#connection-detail").textContent).toContain("vault-a.example.com");
        expect(element("#connection-panel").hidden).toBe(true);
        expect(element("#status").hidden).toBe(true);
        expect(requestSignal?.aborted).toBe(true);
        expect(fakeBrowser.permissions.remove).not.toHaveBeenCalledWith({
          origins: ["https://vault-a.example.com/*"],
        });
        expect(fakeBrowser.permissions.remove).toHaveBeenCalledWith({
          origins: ["https://vault-b.example.com/*"],
        });
      },
    );

    it("stops a cancelled health check before sending credentials", async () => {
      const health = deferred<Response>();
      const requests: string[] = [];
      vi.stubGlobal(
        "fetch",
        vi.fn<typeof fetch>(async (url) => {
          requests.push(String(url));
          if (String(url).startsWith(nextVault)) return health.promise;
          return response({ status: "ok", name: "PrintStash" });
        }),
      );
      await openConnectionEditor();
      button("#connect").click();
      await settle();
      button("#cancel-edit").click();
      health.resolve(response({ status: "ok", name: "PrintStash", credential: "device-b" }));
      await settle();
      expect(requests.filter((url) => url.startsWith(nextVault))).toEqual([
        `${nextVault}/api/v1/health`,
      ]);
      expect(await fakeBrowser.storage.local.get()).toEqual(previous);
    });

    it("keeps publication exclusive until storage settles", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn<typeof fetch>(async () =>
          response({ status: "ok", name: "PrintStash", credential: "device-b" }),
        ),
      );
      await openConnectionEditor();
      const publication = deferred<void>();
      const originalSet = fakeBrowser.storage.local.set.bind(fakeBrowser.storage.local);
      vi.spyOn(fakeBrowser.storage.local, "set").mockImplementation(async (values) => {
        await publication.promise;
        await originalSet(values);
      });
      button("#connect").click();
      await settle();
      expect(button("#cancel-edit").disabled).toBe(true);
      button("#cancel-edit").click();
      expect(element("#connection-title").textContent).toBe("Checking…");
      publication.resolve(undefined);
      await settle();
      expect(await fakeBrowser.storage.local.get()).toEqual({
        vault: nextVault,
        deviceCredential: "device-b",
        deviceName: "Browser",
      });
      expect(element("#connection-detail").textContent).toContain("vault-b.example.com");
    });

    it("preserves the previous connection when publication fails", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn<typeof fetch>(async () =>
          response({ status: "ok", name: "PrintStash", credential: "device-b" }),
        ),
      );
      await openConnectionEditor();
      vi.spyOn(fakeBrowser.storage.local, "set").mockRejectedValue(
        new Error("Storage unavailable"),
      );
      button("#connect").click();
      await settle();
      expect(await fakeBrowser.storage.local.get()).toEqual(previous);
      expect(element("#connection-detail").textContent).toContain("vault-a.example.com");
      expect(element("#status").textContent).toContain("Connection was not updated.");
      expect(button("#cancel-edit").disabled).toBe(false);
      expect(button("#connect").disabled).toBe(false);
    });

    it("ignores a cancelled response body after headers arrive", async () => {
      let body: ReadableStreamDefaultController<Uint8Array> | undefined;
      const stream = new ReadableStream<Uint8Array>({
        start(controller) {
          body = controller;
        },
      });
      let claimStarted = false;
      vi.stubGlobal(
        "fetch",
        vi.fn<typeof fetch>(async (url) => {
          if (String(url).endsWith("/claim")) {
            claimStarted = true;
            return new Response(stream, { headers: { "Content-Type": "application/json" } });
          }
          return response({ status: "ok", name: "PrintStash" });
        }),
      );
      await openConnectionEditor();
      button("#connect").click();
      await vi.waitFor(() => expect(claimStarted).toBe(true));
      button("#cancel-edit").click();
      body?.enqueue(new TextEncoder().encode(JSON.stringify({ credential: "device-b" })));
      body?.close();
      await settle();
      expect(await fakeBrowser.storage.local.get()).toEqual(previous);
      expect(element("#status").hidden).toBe(true);
      expect(element("#connection-detail").textContent).toContain("vault-a.example.com");
    });

    it("finishes cancelled permission cleanup before a same-origin retry", async () => {
      const permission = deferred<boolean>();
      const cleanup = deferred<boolean>();
      const grantedOrigins = new Set(["https://vault-a.example.com/*"]);
      fakeBrowser.permissions.request = vi.fn(async ({ origins }) => {
        await permission.promise;
        for (const origin of origins ?? []) grantedOrigins.add(origin);
        return true;
      });
      fakeBrowser.permissions.remove = vi.fn(async ({ origins }) => {
        await cleanup.promise;
        for (const origin of origins ?? []) grantedOrigins.delete(origin);
        return true;
      });
      const requests: string[] = [];
      vi.stubGlobal(
        "fetch",
        vi.fn<typeof fetch>(async (url) => {
          requests.push(String(url));
          return response({ status: "ok", name: "PrintStash", credential: "device-b" });
        }),
      );
      await openConnectionEditor();
      button("#connect").click();
      button("#cancel-edit").click();
      permission.resolve(true);
      await settle();
      expect(button("#edit-connection").disabled).toBe(true);
      expect(requests).toEqual([`${previous.vault}/api/v1/health`]);
      cleanup.resolve(true);
      await settle();
      expect(grantedOrigins).toEqual(new Set(["https://vault-a.example.com/*"]));
      button("#edit-connection").click();
      requiredElement("#vault", HTMLInputElement).value = nextVault;
      requiredElement("#pairing-code", HTMLInputElement).value = "test-retry-code";
      button("#connect").click();
      await settle();
      expect(grantedOrigins).toEqual(new Set(["https://vault-b.example.com/*"]));
      expect((await fakeBrowser.storage.local.get()).deviceCredential).toBe("device-b");
    });

    it.each(["pairing", "api-key"])(
      "replaces the previous credential kind in one publication: %s",
      async (kind) => {
        const oldCredential =
          kind === "pairing"
            ? { vault: previous.vault, username: "old-user", apiKey: "test-old-key" }
            : previous;
        await fakeBrowser.storage.local.set(oldCredential);
        vi.stubGlobal(
          "fetch",
          vi.fn<typeof fetch>(async (url) => {
            if (String(url).endsWith("/login")) return response({ access_token: "test-jwt" });
            if (String(url).endsWith("/me"))
              return response({ username: "test-user", is_superuser: false });
            return response({ status: "ok", name: "PrintStash", credential: "device-b" });
          }),
        );
        await import("../popup.ts");
        await settle();
        button("#edit-connection").click();
        requiredElement(
          `#${kind === "pairing" ? "pairing" : "api-key"}-method`,
          HTMLInputElement,
        ).click();
        requiredElement("#vault", HTMLInputElement).value = nextVault;
        requiredElement("#pairing-code", HTMLInputElement).value = "test-code";
        requiredElement("#username", HTMLInputElement).value = "test-user";
        requiredElement("#key", HTMLInputElement).value = "test-new-key";
        button("#connect").click();
        await settle();
        const expected =
          kind === "pairing"
            ? { vault: nextVault, deviceCredential: "device-b", deviceName: "Browser" }
            : { vault: nextVault, username: "test-user", apiKey: "test-new-key" };
        expect(await fakeBrowser.storage.local.get()).toEqual(expected);
        vi.resetModules();
        UploadRequest.instances = [];
        vi.stubGlobal("XMLHttpRequest", UploadRequest);
        document.documentElement.innerHTML = popupHtml;
        await import("../popup.ts");
        await settle();
        expect(element("#connection-title").textContent).toBe("Connected");
        expect(element("#connection-detail").textContent).toContain("vault-b.example.com");
      },
    );

    it.each([
      { label: "denied permission", granted: false, status: 200 },
      { label: "rejected pairing", granted: true, status: 400 },
    ])(
      "preserves the previous connection after a failed update: $label",
      async ({ granted, status }) => {
        vi.stubGlobal(
          "fetch",
          vi.fn<typeof fetch>(async (url) =>
            String(url).endsWith("/claim")
              ? response({ detail: "invalid_code" }, status)
              : response({ status: "ok", name: "PrintStash" }),
          ),
        );
        await openConnectionEditor();
        fakeBrowser.permissions.request = vi.fn().mockResolvedValue(granted);
        button("#connect").click();
        await settle();
        expect(await fakeBrowser.storage.local.get()).toEqual(previous);
        expect(element("#connection-detail").textContent).toContain("vault-a.example.com");
        expect(
          granted ? element("#connection-feedback").textContent : element("#status").textContent,
        ).toContain(granted ? "pairing code is invalid" : "Connection was not updated.");
        expect(button("#connect").disabled).toBe(false);
        expect(button("#cancel-edit").disabled).toBe(false);
      },
    );

    it("locks setup while prepared setup is pending", async () => {
      const prepared = deferred<Awaited<ReturnType<typeof fakeBrowser.scripting.executeScript>>>();
      fakeBrowser.scripting.executeScript = vi.fn().mockReturnValue(prepared.promise);
      await import("../popup.ts");
      await settle();
      expect(button("#connect").disabled).toBe(true);
      prepared.resolve([{ frameId: 0, result: null }]);
      await settle();
      expect(button("#connect").disabled).toBe(false);
    });

    it("locks setup while initialization reads are pending", async () => {
      const settings = deferred<Record<string, never>>();
      vi.spyOn(fakeBrowser.storage.local, "get").mockReturnValue(settings.promise);
      await import("../popup.ts");
      expect(button("#connect").disabled).toBe(true);
      settings.resolve({});
      await settle();
      expect(button("#connect").disabled).toBe(false);
    });

    it("locks connection controls during disconnect", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn<typeof fetch>(async () => response({ status: "ok", name: "PrintStash" })),
      );
      await openConnectionEditor();
      const removal = deferred<void>();
      const originalRemove = fakeBrowser.storage.local.remove.bind(fakeBrowser.storage.local);
      vi.spyOn(fakeBrowser.storage.local, "remove").mockImplementation(async (keys) => {
        await removal.promise;
        await originalRemove(keys);
      });
      button("#disconnect").click();
      expect(button("#cancel-edit").disabled).toBe(true);
      expect(button("#connect").disabled).toBe(true);
      expect(button("#disconnect").disabled).toBe(true);
      removal.resolve(undefined);
      await settle();
      expect(await fakeBrowser.storage.local.get()).toEqual({ vault: previous.vault });
      expect(element("#connection-title").textContent).toBe("Not connected");
      expect(button("#connect").disabled).toBe(false);
    });

    it("keeps the previous connection when disconnect storage fails", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn<typeof fetch>(async () => response({ status: "ok", name: "PrintStash" })),
      );
      await openConnectionEditor();
      vi.spyOn(fakeBrowser.storage.local, "remove").mockRejectedValue(
        new Error("Storage unavailable"),
      );
      button("#disconnect").click();
      await settle();
      expect(await fakeBrowser.storage.local.get()).toEqual(previous);
      expect(element("#status").textContent).toContain(
        "Couldn't remove the stored browser credential.",
      );
      expect(button("#connect").disabled).toBe(false);
      expect(button("#cancel-edit").disabled).toBe(false);
      expect(button("#disconnect").disabled).toBe(false);
    });
  });

  it("opens the Imports settings section used for browser pairing", async () => {
    await fakeBrowser.storage.local.set({
      vault: "https://prints.example.com",
      deviceCredential: "device-secret",
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => response({ status: "ok", name: "PrintStash" })),
    );

    await import("../popup.ts");
    await settle();
    button("#open-api-settings").click();

    expect(fakeBrowser.tabs.create).toHaveBeenCalledWith({
      url: "https://prints.example.com/settings?section=imports",
    });
  });

  it("restores a paired device credential and clears it on disconnect", async () => {
    await fakeBrowser.storage.local.set({
      vault: "https://prints.example.com",
      deviceCredential: "device-secret",
    });
    const fetchImpl = vi.fn(async (url) => {
      if (url.endsWith("/capture-capabilities")) return response({}, 404);
      if (url.endsWith("/health")) return response({ status: "ok", name: "PrintStash" });
      return response({}, 404);
    });
    vi.stubGlobal("fetch", fetchImpl);
    await import("../popup.ts");
    for (let attempt = 0; attempt < 4; attempt += 1) await settle();
    const stored = await fakeBrowser.storage.local.get([
      "vault",
      "deviceCredential",
      "username",
      "apiKey",
    ]);
    expect(stored).toMatchObject({
      vault: "https://prints.example.com",
      deviceCredential: "device-secret",
    });
    expect(stored.username).toBeUndefined();
    expect(stored.apiKey).toBeUndefined();
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    button("#edit-connection").click();
    button("#disconnect").click();
    await settle();
    expect(await fakeBrowser.storage.local.get("deviceCredential")).toEqual({});
    expect(fakeBrowser.permissions.remove).toHaveBeenCalledWith({
      origins: ["https://prints.example.com/*"],
    });
  });

  describe("disconnect host permissions", () => {
    async function connectSavedDevice(vault: string) {
      await fakeBrowser.storage.local.set({ vault, deviceCredential: "device-secret" });
      vi.stubGlobal(
        "fetch",
        vi.fn<typeof fetch>(async () => response({ status: "ok", name: "PrintStash" })),
      );
      await import("../popup.ts");
      await vi.waitFor(() => expect(element("#connection-title").textContent).toBe("Connected"));
      vi.mocked(fakeBrowser.permissions.contains).mockClear();
      button("#edit-connection").click();
    }

    it.each([
      { vault: "http://192.168.1.20:8000", origin: "http://192.168.1.20/*" },
      { vault: "http://prints.local:8000", origin: "http://prints.local/*" },
      { vault: "http://printstash:8000", origin: "http://printstash/*" },
      { vault: "http://vault.localhost:8000", origin: "http://vault.localhost/*" },
      { vault: "http://[fd00::1]:8000", origin: "http://[fd00::1]/*" },
      { vault: "http://127.0.0.2:8000", origin: "http://127.0.0.2/*" },
      { vault: "https://localhost:8443", origin: "https://localhost/*" },
      { vault: "https://127.0.0.1:8443", origin: "https://127.0.0.1/*" },
      { vault: "https://[::1]:8443", origin: "https://[::1]/*" },
    ])("removes optional local vault access on disconnect: $vault", async ({ vault, origin }) => {
      await connectSavedDevice(vault);
      vi.mocked(fakeBrowser.permissions.contains).mockResolvedValue(false);

      button("#disconnect").click();
      await vi.waitFor(() =>
        expect(element("#connection-title").textContent).toBe("Not connected"),
      );

      expect(await fakeBrowser.storage.local.get()).toEqual({ vault });
      expect(fakeBrowser.permissions.remove).toHaveBeenCalledExactlyOnceWith({ origins: [origin] });
      expect(fakeBrowser.permissions.contains).toHaveBeenCalledExactlyOnceWith({
        origins: [origin],
      });
      expect(element("#status-message").textContent).toBe(
        "Disconnected. The stored browser credential and server permission were removed from this browser.",
      );
    });

    it.each(["http://localhost:8000", "http://127.0.0.1:8000", "http://[::1]:8000"])(
      "explains retained built-in loopback access: %s",
      async (vault) => {
        await connectSavedDevice(vault);

        button("#disconnect").click();
        await vi.waitFor(() =>
          expect(element("#connection-title").textContent).toBe("Not connected"),
        );

        expect(await fakeBrowser.storage.local.get()).toEqual({ vault });
        expect(fakeBrowser.permissions.remove).not.toHaveBeenCalled();
        expect(fakeBrowser.permissions.contains).not.toHaveBeenCalled();
        expect(element("#status-message").textContent).toBe(
          "Disconnected. The stored browser credential was removed; built-in loopback access contains no credentials.",
        );
      },
    );

    it.each(["retained", "rejected"])(
      "explains an optional local permission that the browser retained: %s",
      async (outcome) => {
        const vault = "http://192.168.1.20:8000";
        await connectSavedDevice(vault);
        if (outcome === "rejected")
          vi.mocked(fakeBrowser.permissions.remove).mockRejectedValue(
            new Error("Permission removal failed"),
          );
        else vi.mocked(fakeBrowser.permissions.remove).mockResolvedValue(false);

        button("#disconnect").click();
        await vi.waitFor(() =>
          expect(element("#connection-title").textContent).toBe("Not connected"),
        );

        expect(await fakeBrowser.storage.local.get()).toEqual({ vault });
        expect(fakeBrowser.permissions.remove).toHaveBeenCalledExactlyOnceWith({
          origins: ["http://192.168.1.20/*"],
        });
        expect(element("#status-message").textContent).toBe(
          "Disconnected and removed the stored browser credential, but Chrome kept the server permission. Remove it from the extension's site access settings.",
        );
      },
    );
  });

  describe("capture authentication retirement", () => {
    const stages = ["slot_create", "slot_upload", "slot_finalize"] as const;
    const stored = { vault: "https://vault-a.example.com", deviceCredential: "test-credential-a" };

    async function startRichFailure(stage: (typeof stages)[number], status: number, hold = false) {
      await fakeBrowser.storage.local.set(stored);
      fakeBrowser.scripting.executeScript = vi
        .fn()
        .mockResolvedValue([{ frameId: 0, result: { pageTitle: "Part", jsonLd: [] } }]);
      const held = deferred<Response>();
      const requests: { url: string; method: string; authorization: string | null }[] = [];
      let rejectionEnabled = false;
      let rejectionReached = false;
      vi.stubGlobal(
        "fetch",
        vi.fn<typeof fetch>(async (input, options = {}) => {
          const url = String(input);
          requests.push({
            url,
            method: options.method ?? "GET",
            authorization: new Headers(options.headers).get("Authorization"),
          });
          if (url.endsWith("/capture-capabilities")) return response({}, 404);
          if (url.endsWith("/health")) return response({ status: "ok", name: "PrintStash" });
          if (url.endsWith("/claim")) return response({ credential: "test-credential-b" });
          if (url.includes("api.printables.com")) return response({}, 503);
          if (options.method === "DELETE") return new Response(null, { status: 204 });
          const currentStage = url.endsWith("/capture-upload-slots")
            ? "slot_create"
            : options.method === "PUT"
              ? "slot_upload"
              : "slot_finalize";
          if (rejectionEnabled && currentStage === stage) {
            rejectionReached = true;
            return hold
              ? held.promise
              : response(
                  { detail: status === 401 ? "invalid_browser_credential" : "insufficient_scope" },
                  status,
                );
          }
          if (currentStage === "slot_create") {
            const body = JSON.parse(stringBody(options));
            return response({
              item: { id: 44 },
              slots: body.files.map(
                (file: {
                  id: string;
                  filename: string;
                  media_type: string;
                  size_bytes: number;
                  sha256: string;
                }) => ({ ...file, id: "slot-a", role: "file", source_file_id: file.id }),
              ),
            });
          }
          return currentStage === "slot_finalize"
            ? response({ id: 44, state: "review" })
            : new Response(null, { status: 204 });
        }),
      );
      await import("../popup.ts");
      await vi.waitFor(() => expect(element("#connection-title").textContent).toBe("Connected"));
      async function sendManualFile() {
        button("#capture").click();
        await vi.waitFor(() => expect(element("#manual-file-panel").hidden).toBe(false));
        const input = requiredElement("#manual-file", HTMLInputElement);
        const file = new File(["mesh"], "part.stl", { type: "model/stl" });
        Object.defineProperty(input, "files", {
          configurable: true,
          value: { 0: file, length: 1, item: (index: number) => (index === 0 ? file : null) },
        });
        button("#capture").click();
      }
      await sendManualFile();
      await vi.waitFor(() =>
        expect(element("#status").textContent).toContain("Sent to Pending Imports"),
      );
      expect(button("#open-inbox").hidden).toBe(false);
      rejectionEnabled = true;
      button("#edit-connection").click();
      button("#cancel-edit").click();
      await sendManualFile();
      await vi.waitFor(() => expect(rejectionReached).toBe(true));
      return { held, requests };
    }

    it.each(stages)(
      "retires the connection after rich capture authentication failure: %s",
      async (stage) => {
        await startRichFailure(stage, 401);
        await settle();
        expect(element("#connection-title").textContent).toBe("Connection problem");
        expect(element("#connection-detail").textContent).toBe(
          "Reconnect PrintStash to continue importing.",
        );
        expect(element("#connection-panel").hidden).toBe(false);
        expect(button("#capture").disabled).toBe(true);
        expect(button("#open-inbox").hidden).toBe(true);
        expect(element("#manual-file-panel").hidden).toBe(true);
        expect(element("#candidate-panel").hidden).toBe(true);
        expect(await fakeBrowser.storage.local.get()).toEqual(stored);
      },
    );

    it.each(stages)(
      "keeps the connection after rich capture permission failure: %s",
      async (stage) => {
        await startRichFailure(stage, 403);
        await settle();
        expect(element("#connection-title").textContent).toBe("Connected");
        expect(element("#status").textContent).toContain("insufficient_scope");
        expect(button("#capture").disabled).toBe(false);
        expect(element("#manual-file-panel").hidden).toBe(false);
        expect(await fakeBrowser.storage.local.get()).toEqual(stored);
      },
    );

    it("ignores retired rich authentication failures", async () => {
      const { held, requests } = await startRichFailure("slot_upload", 401, true);
      button("#edit-connection").click();
      requiredElement("#vault", HTMLInputElement).value = "https://vault-b.example.com";
      requiredElement("#pairing-code", HTMLInputElement).value = "test-code-b";
      button("#connect").click();
      await vi.waitFor(() =>
        expect(element("#connection-detail").textContent).toContain("vault-b.example.com"),
      );
      held.resolve(response({ detail: "invalid_browser_credential" }, 401));
      await settle();
      expect(element("#connection-title").textContent).toBe("Connected");
      expect(element("#status").textContent).toContain("Connection verified");
      expect(
        requests.filter(
          (request) =>
            request.url.startsWith("https://vault-b.example.com") && request.authorization !== null,
        ),
      ).toEqual([]);
      expect((await fakeBrowser.storage.local.get()).deviceCredential).toBe("test-credential-b");
    });

    it("retires the connection after direct capture authentication failure", async () => {
      await fakeBrowser.storage.local.set(stored);
      fakeBrowser.tabs.query = vi
        .fn()
        .mockResolvedValue([{ id: 42, title: "Part", url: "https://models.example.com/part.stl" }]);
      vi.stubGlobal(
        "fetch",
        vi.fn<typeof fetch>(async (url) =>
          String(url).endsWith("/health")
            ? response({ status: "ok", name: "PrintStash" })
            : response({ detail: "invalid_browser_credential" }, 401),
        ),
      );
      await import("../popup.ts");
      await settle();
      button("#capture").click();
      await settle();
      expect(element("#connection-title").textContent).toBe("Connection problem");
      expect(element("#connection-detail").textContent).toBe(
        "Reconnect PrintStash to continue importing.",
      );
      expect(button("#capture").disabled).toBe(true);
      expect(await fakeBrowser.storage.local.get()).toEqual(stored);
    });
  });

  it("falls back to a local Printables file without metadata-only capture", async () => {
    await fakeBrowser.storage.local.set({
      vault: "https://prints.example.com",
      username: "owner",
      apiKey: "psk_vault_secret",
    });
    const fetchImpl = vi.fn(async (url, options: RequestInit = {}) => {
      if (url.endsWith("/capture-capabilities")) return response({}, 404);
      if (url.endsWith("/health")) return response({ status: "ok", name: "PrintStash" });
      if (url.endsWith("/login")) return response({ access_token: "vault-jwt" });
      if (url.endsWith("/me")) return response({ username: "owner", is_superuser: false });
      if (url.endsWith("/capture-upload-slots")) {
        const body = JSON.parse(stringBody(options));
        return response(
          {
            item: { id: 22 },
            slots: [
              {
                id: "slot-printables-manual",
                role: "file",
                source_file_id: "3161:benchy.3mf",
                filename: "benchy.3mf",
                media_type: "model/3mf",
                size_bytes: 4,
                sha256: body.files[0].sha256,
              },
            ],
          },
          201,
        );
      }
      if (url.endsWith("/capture-upload-slots/slot-printables-manual"))
        return new Response(null, { status: 204 });
      if (url.endsWith("/capture-upload-finalize")) return response({ id: 22, state: "review" });
      throw new Error(`Unexpected metadata-only capture: ${url}`);
    });
    vi.stubGlobal("fetch", fetchImpl);
    fakeBrowser.scripting.executeScript = vi.fn().mockResolvedValue([
      {
        result: {
          pageTitle: "3DBenchy",
          jsonLd: [
            JSON.stringify({
              name: "3DBenchy",
              image: "data:image/png;base64,secret",
              contentUrl: "https://media.printables.com/files/benchy.3mf?signature=signed-secret",
            }),
          ],
        },
      },
    ]);

    await import("../popup.ts");
    await settle();
    button("#capture").click();
    for (let attempt = 0; attempt < 4; attempt += 1) await settle();

    expect(element("#manual-file-panel").hidden).toBe(false);
    expect(element("#status").textContent).toContain("Choose a downloaded Printables file");
    expect(fetchImpl.mock.calls.some(([url]) => url.endsWith("/api/v1/inbox"))).toBe(false);

    const input = requiredElement("#manual-file", HTMLInputElement);
    const file = new File(["mesh"], "benchy.3mf", { type: "model/3mf" });
    Object.defineProperty(input, "files", {
      configurable: true,
      value: { 0: file, length: 1, item: (index: number) => (index === 0 ? file : null) },
    });
    button("#capture").click();
    for (let attempt = 0; attempt < 10; attempt += 1) await settle();

    const createCall = fetchImpl.mock.calls.find(([url]) => url.endsWith("/capture-upload-slots"));
    if (createCall === undefined || createCall[1] === undefined)
      throw new Error("Missing durable slot creation request");
    const createBody = stringBody(createCall[1]);
    expect(JSON.parse(createBody)).toMatchObject({
      capture_source: {
        provider: "printables",
        canonical_url: "https://www.printables.com/model/3161-3d-benchy/files",
        source_item_id: "3161",
        fields: { title: { value: "3DBenchy", origin: "confirmed" } },
      },
      files: [
        {
          id: "3161:benchy.3mf",
          filename: "benchy.3mf",
          size_bytes: 4,
          sha256: expect.stringMatching(/^[a-f0-9]{64}$/),
        },
      ],
    });
    expect(createBody).not.toContain("psk_vault_secret");
    expect(createBody).not.toContain("vault-jwt");
    expect(createBody).not.toContain("base64");
    expect(createBody).not.toContain("signed-secret");
    expect(fetchImpl.mock.calls.map(([url]) => url)).toEqual([
      "https://prints.example.com/api/v1/health",
      "https://prints.example.com/api/v1/auth/login",
      "https://prints.example.com/api/v1/auth/me",
      "https://api.printables.com/graphql/",
      "https://prints.example.com/api/v1/inbox/capture-upload-slots",
      "https://prints.example.com/api/v1/inbox/capture-upload-slots/slot-printables-manual",
      "https://prints.example.com/api/v1/inbox/22/capture-upload-finalize",
    ]);
    expect(element("#status").textContent).toContain("Sent to Pending Imports");
  });

  it("fails closed to a local Printables file when capture acquisition is unusable", async () => {
    await fakeBrowser.storage.local.set({
      vault: "https://prints.example.com",
      username: "owner",
      apiKey: "psk_vault_secret",
    });
    const fetchImpl = vi.fn(async (url: string) => {
      if (url.endsWith("/capture-capabilities")) return response({}, 404);
      if (url.endsWith("/health")) return response({ status: "ok", name: "PrintStash" });
      if (url.endsWith("/login")) return response({ access_token: "vault-jwt" });
      if (url.endsWith("/me")) return response({ username: "owner", is_superuser: false });
      if (url.endsWith("/inbox")) return response({ detail: "user_file_required" }, 400);
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchImpl);
    fakeBrowser.scripting.executeScript = vi
      .fn()
      .mockResolvedValueOnce([{ result: null }])
      .mockResolvedValueOnce([{ result: { pageTitle: "3DBenchy", jsonLd: "not-an-array" } }]);

    await import("../popup.ts");
    await settle();
    button("#capture").click();
    for (let attempt = 0; attempt < 4; attempt += 1) await settle();

    expect(element("#manual-file-panel").hidden).toBe(false);
    expect(element("#status").textContent).toContain("Choose a downloaded Printables file");
    expect(fetchImpl.mock.calls.some(([url]) => url.endsWith("/api/v1/inbox"))).toBe(false);
  });

  it("bounds JSON-LD scripts before returning page metadata to the popup", async () => {
    await fakeBrowser.storage.local.set({
      vault: "https://prints.example.com",
      username: "owner",
      apiKey: "psk_vault_secret",
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.endsWith("/capture-capabilities")) return response({}, 404);
        if (url.endsWith("/health")) return response({ status: "ok", name: "PrintStash" });
        if (url.endsWith("/login")) return response({ access_token: "vault-jwt" });
        if (url.endsWith("/me")) return response({ username: "owner", is_superuser: false });
        return response({ id: 22, state: "captured" }, 202);
      }),
    );
    fakeBrowser.scripting.executeScript = vi
      .fn()
      .mockResolvedValue([{ result: { pageTitle: "3DBenchy", jsonLd: [] } }]);
    await import("../popup.ts");
    for (let attempt = 0; attempt < 4; attempt += 1) await settle();
    button("#capture").click();
    for (let attempt = 0; attempt < 4; attempt += 1) await settle();

    const executeScript = vi.mocked(fakeBrowser.scripting.executeScript);
    const invocation = executeScript.mock.calls.find(([details]) => {
      const args = details.args;
      return (
        Array.isArray(args) &&
        typeof args[0] === "object" &&
        args[0] !== null &&
        "maxScripts" in args[0] &&
        "maxScriptBytes" in args[0] &&
        "maxTotalBytes" in args[0]
      );
    });
    const details = invocation?.[0];
    if (!details?.func || !details.args?.[0]) throw new Error("Missing JSON-LD collection script");
    const collect = details.func as (limits: {
      maxScripts: number;
      maxScriptBytes: number;
      maxTotalBytes: number;
    }) => { jsonLd: string[] };
    const limits = details.args[0] as {
      maxScripts: number;
      maxScriptBytes: number;
      maxTotalBytes: number;
    };
    const appendJsonLd = (count: number, text: string) => {
      for (let index = 0; index < count; index += 1) {
        const script = document.createElement("script");
        script.type = "application/ld+json";
        script.textContent = text;
        document.body.append(script);
      }
    };
    const clearJsonLd = () => {
      document.querySelectorAll('script[type="application/ld+json"]').forEach((script) => {
        script.remove();
      });
    };

    appendJsonLd(limits.maxScripts + 1, '{"name":"hostile"}');
    expect(collect(limits).jsonLd).toEqual([]);
    clearJsonLd();

    const aggregateText = "x".repeat(Math.floor(limits.maxTotalBytes / limits.maxScripts) + 1);
    appendJsonLd(limits.maxScripts, aggregateText);
    expect(aggregateText.length).toBeLessThan(limits.maxScriptBytes);
    expect(collect(limits).jsonLd).toEqual([]);
    clearJsonLd();

    appendJsonLd(1, "x".repeat(limits.maxScriptBytes + 1));
    expect(collect(limits).jsonLd).toEqual([]);
  });

  it("requires Printables file confirmation before using durable upload slots", async () => {
    await fakeBrowser.storage.local.set({
      vault: "https://prints.example.com",
      username: "owner",
      apiKey: "psk_vault_secret",
    });
    fakeBrowser.scripting.executeScript = vi
      .fn()
      .mockResolvedValueOnce([{ result: null }])
      .mockResolvedValueOnce([{ result: { pageTitle: "3DBenchy", jsonLd: [] } }])
      .mockRejectedValueOnce(new TypeError("Failed to fetch"));
    const fetchImpl = vi.fn(async (url: string, options: RequestInit = {}) => {
      if (url.endsWith("/capture-capabilities")) return response({}, 404);
      if (url.endsWith("/health")) return response({ status: "ok", name: "PrintStash" });
      if (url.endsWith("/login")) return response({ access_token: "vault-jwt" });
      if (url.endsWith("/me")) return response({ username: "owner", is_superuser: false });
      if (url === "https://api.printables.com/graphql/") {
        const requestBody = JSON.parse(stringBody(options));
        if (requestBody.query.includes("mutation")) {
          return response({
            data: {
              getDownloadLink: {
                ok: true,
                output: {
                  files: [
                    {
                      id: "file-3mf",
                      link: "https://media.printables.com/files/benchy.3mf?signature=signed-secret",
                    },
                  ],
                },
              },
            },
          });
        }
        return response({
          data: {
            print: {
              id: "3161",
              images: [],
              name: "3DBenchy",
              description:
                "<p>Live <strong>description</strong></p><p>Second paragraph</p><script>document.cookie = 'session-cookie'</script>",
              summary: "Fallback summary",
              datePublished: "2026-08-20T10:20:30Z",
              modified: "2026-08-21T11:22:33Z",
              user: { id: "ada-7", publicUsername: "Ada Maker", handle: "ada-maker" },
              tags: [{ name: " Calibration " }, { name: "boat" }, { name: "boat" }],
              contentUrl: "https://media.printables.com/files/secret.3mf?signature=signed-secret",
              sessionCookie: "session-cookie",
              license: { name: "CC BY-NC 4.0" },
              otherFiles: [{ id: "file-3mf", name: "benchy.3mf", fileSize: 4 }],
              stls: [{ id: "file-stl", name: "benchy.stl", fileSize: 4 }],
            },
          },
        });
      }
      if (url.startsWith("https://media.printables.com/")) {
        return new Response("mesh", {
          status: 200,
          headers: { "Content-Type": "model/3mf" },
        });
      }
      if (url.endsWith("/capture-upload-slots")) {
        const body = typeof options.body === "string" ? JSON.parse(options.body) : null;
        return response(
          {
            item: { id: 51 },
            slots: [
              {
                id: "slot-printables",
                role: "file",
                source_file_id: "file-3mf",
                filename: "benchy.3mf",
                media_type: "model/3mf",
                size_bytes: 4,
                sha256: body.files[0].sha256,
              },
            ],
          },
          201,
        );
      }
      if (url.endsWith("/capture-upload-slots/slot-printables")) {
        return new Response(null, { status: 204 });
      }
      if (url.endsWith("/capture-upload-finalize")) return response({ id: 51, state: "ready" });
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchImpl);

    await import("../popup.ts");
    await settle();
    button("#capture").click();
    for (let attempt = 0; attempt < 4; attempt += 1) await settle();

    expect(element("#candidate-panel").hidden).toBe(false);
    expect(element("#candidate-panel legend").textContent).toBe("Select Printables files");
    expect(button("#capture").textContent).toBe("Confirm and upload selected files");
    expect(fetchImpl).toHaveBeenCalledTimes(5);
    expect(
      fetchImpl.mock.calls.filter(([url]) => url === "https://api.printables.com/graphql/"),
    ).toHaveLength(2);
    expect(
      vi.mocked(fakeBrowser.scripting.executeScript).mock.calls.filter(([details]) => {
        const args = details.args;
        return (
          Array.isArray(args) &&
          args[0] !== null &&
          typeof args[0] === "object" &&
          "groups" in args[0]
        );
      }),
    ).toHaveLength(0);
    const candidates = document.querySelectorAll<HTMLInputElement>("#candidate-list input");
    expect(candidates).toHaveLength(2);
    candidates[0]?.click();

    button("#capture").click();
    for (let attempt = 0; attempt < 10; attempt += 1) await settle();

    expect(
      fetchImpl.mock.calls.filter(([url]) => url.startsWith("https://media.printables.com/")),
    ).toHaveLength(1);
    const linkCalls = fetchImpl.mock.calls.filter(
      ([url]) => url === "https://api.printables.com/graphql/",
    );
    expect(linkCalls).toHaveLength(3);
    const linkCall = linkCalls[2];
    if (!linkCall?.[1]) throw new Error("Missing extension-context Printables link request");
    expect(linkCall[1].credentials).toBe("omit");
    expect(linkCall[1].headers).not.toHaveProperty("Authorization");
    expect(JSON.parse(stringBody(linkCall[1]))).toMatchObject({
      variables: { files: [{ fileType: "other", ids: ["file-3mf"] }] },
    });
    expect(
      vi
        .mocked(fakeBrowser.scripting.executeScript)
        .mock.calls.some(
          ([details]) =>
            Array.isArray(details.args) &&
            details.args[0] !== null &&
            typeof details.args[0] === "object" &&
            "groups" in details.args[0],
        ),
    ).toBe(false);
    const createCall = fetchImpl.mock.calls.find(([url]) => url.endsWith("/capture-upload-slots"));
    if (createCall === undefined || createCall[1] === undefined) {
      throw new Error(
        `Missing durable slot creation request: ${fetchImpl.mock.calls.map(([url]) => url).join(", ")}; status=${element("#status").textContent}`,
      );
    }
    const createBody = stringBody(createCall[1]);
    expect(JSON.parse(createBody)).toMatchObject({
      capture_source: {
        provider: "printables",
        source_item_id: "3161",
        fields: {
          description: {
            value: "Live description\nSecond paragraph",
            origin: "confirmed",
          },
          creator_name: { value: "Ada Maker", origin: "confirmed" },
          creator_id: { value: "ada-7", origin: "confirmed" },
          creator_url: {
            value: "https://www.printables.com/@ada-maker",
            origin: "confirmed",
          },
          published_at: { value: "2026-08-20T10:20:30Z", origin: "confirmed" },
          updated_at: { value: "2026-08-21T11:22:33Z", origin: "confirmed" },
        },
        tags: ["calibration", "boat"],
      },
      files: [
        {
          id: "file-3mf",
          filename: "benchy.3mf",
          size_bytes: 4,
          sha256: expect.stringMatching(/^[a-f0-9]{64}$/),
        },
      ],
    });
    expect(createBody).not.toContain("signed-secret");
    expect(createBody).not.toContain("session-cookie");
    expect(createBody).not.toContain("document.cookie");
    expect(fetchImpl.mock.calls.some(([url]) => url.endsWith("/api/v1/inbox"))).toBe(false);
    expect(JSON.stringify(await fakeBrowser.storage.local.get())).not.toContain("signed-secret");
    expect(fetchImpl.mock.calls.some(([url]) => url.endsWith("/capture-upload-finalize"))).toBe(
      true,
    );
    expect(element("#status").textContent).toContain("Sent to Pending Imports");
    expect(element("#status").textContent).not.toContain("code:");
  });

  it.each([
    {
      name: "requests file-host permission before sequential Printables downloads",
      alreadyGranted: false,
      grant: true,
    },
    { name: "uses an existing Printables file-host permission", alreadyGranted: true, grant: true },
    {
      name: "stops Printables downloads when file-host permission is denied",
      alreadyGranted: false,
      grant: false,
    },
  ])("$name", async ({ alreadyGranted, grant }) => {
    await fakeBrowser.storage.local.set({
      vault: "https://prints.example.com",
      username: "owner",
      apiKey: "psk_vault_secret",
    });
    fakeBrowser.scripting.executeScript = vi
      .fn()
      .mockResolvedValueOnce([{ result: null }])
      .mockResolvedValueOnce([{ result: { pageTitle: "3DBenchy", jsonLd: [] } }])
      .mockRejectedValueOnce(new TypeError("Failed to fetch"));
    let permissionPending = false;
    let hostGranted = alreadyGranted;
    const fileHostRequests: string[][] = [];
    fakeBrowser.permissions.contains = vi.fn(
      async ({ origins }: { origins: string[] }) =>
        !origins.includes("https://files.printables.com/*") || hostGranted,
    );
    fakeBrowser.permissions.request = vi.fn(async ({ origins }: { origins: string[] }) => {
      if (!origins.includes("https://files.printables.com/*")) return true;
      fileHostRequests.push(origins);
      if (permissionPending) throw new Error("Another permission prompt is already open");
      permissionPending = true;
      await new Promise((resolve) => setTimeout(resolve, 0));
      permissionPending = false;
      hostGranted = grant;
      return grant;
    });
    const fetchImpl = vi.fn(async (url: string, options: RequestInit = {}) => {
      if (url.endsWith("/capture-capabilities")) return response({}, 404);
      if (url.endsWith("/health")) return response({ status: "ok", name: "PrintStash" });
      if (url.endsWith("/login")) return response({ access_token: "vault-jwt" });
      if (url.endsWith("/me")) return response({ username: "owner", is_superuser: false });
      if (url === "https://api.printables.com/graphql/") {
        if (JSON.parse(stringBody(options)).query.includes("mutation")) {
          return response({
            data: {
              getDownloadLink: {
                ok: true,
                output: {
                  files: [
                    { id: "first", link: "https://files.printables.com/first.3mf?token=secret" },
                    { id: "second", link: "https://files.printables.com/second.3mf?token=secret" },
                  ].filter((file) =>
                    JSON.stringify(JSON.parse(stringBody(options)).variables.files).includes(
                      file.id,
                    ),
                  ),
                },
              },
            },
          });
        }
        return response({
          data: {
            print: {
              id: "3161",
              images: [],
              name: "3DBenchy",
              otherFiles: [
                { id: "first", name: "first.3mf", fileSize: 4 },
                { id: "second", name: "second.3mf", fileSize: 4 },
              ],
            },
          },
        });
      }
      if (url.startsWith("https://files.printables.com/")) {
        return new Response("mesh", { status: 200, headers: { "Content-Type": "model/3mf" } });
      }
      if (url.endsWith("/capture-upload-slots")) {
        const files = JSON.parse(stringBody(options)).files as {
          id: string;
          filename: string;
          size_bytes: number;
          sha256: string;
        }[];
        return response(
          {
            item: { id: 51 },
            slots: files.map((file, index) => ({
              id: `slot-${index}`,
              role: "file",
              source_file_id: file.id,
              filename: file.filename,
              media_type: "model/3mf",
              size_bytes: file.size_bytes,
              sha256: file.sha256,
            })),
          },
          201,
        );
      }
      if (url.includes("/capture-upload-slots/slot-")) return new Response(null, { status: 204 });
      if (url.endsWith("/capture-upload-finalize")) return response({ id: 51, state: "ready" });
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchImpl);

    await import("../popup.ts");
    await settle();
    button("#capture").click();
    for (let attempt = 0; attempt < 4; attempt += 1) await settle();
    expect(document.querySelectorAll("#candidate-list input")).toHaveLength(2);
    button("#capture").click();
    for (let attempt = 0; attempt < 12; attempt += 1) await settle();

    expect(fileHostRequests).toEqual(alreadyGranted ? [] : [["https://files.printables.com/*"]]);
    if (grant || alreadyGranted) {
      expect(element("#status").textContent).toContain("Sent to Pending Imports");
    } else {
      expect(element("#status").textContent).toContain("Permission to download");
      expect(fetchImpl.mock.calls.some(([url]) => url.endsWith("/capture-upload-slots"))).toBe(
        false,
      );
    }
  });

  it("enumerates MakerWorld packages, requires explicit subset confirmation, and uses fresh links plus durable slots", async () => {
    await fakeBrowser.storage.local.set({
      vault: "https://prints.example.com",
      username: "owner",
      apiKey: "psk_vault_secret",
    });
    fakeBrowser.tabs.query = vi.fn().mockResolvedValue([
      {
        id: 42,
        title: "Calibration cube",
        url: "https://makerworld.com/en/models/1234-calibration-cube",
      },
    ]);
    fakeBrowser.scripting.executeScript = vi
      .fn()
      .mockResolvedValueOnce([{ result: null }])
      .mockResolvedValueOnce([{ result: { pageTitle: "Calibration cube", jsonLd: [] } }])
      .mockResolvedValueOnce([
        {
          result: {
            ok: true,
            metadata: {
              fixtureVersion: "makerworld-design-service-v1",
              sourceItemId: "1234",
              source: { title: "Calibration cube", creatorName: "Maker" },
              files: [
                {
                  id: "instance-default",
                  filename: "cube—高.3mf",
                  fileType: "other",
                  sizeBytes: 4,
                },
                { id: "instance-alt", filename: "cube-alt.3mf", fileType: "other", sizeBytes: 4 },
                {
                  id: "instance-third",
                  filename: "cube-third.3mf",
                  fileType: "other",
                  sizeBytes: 4,
                },
                {
                  id: "instance-fourth",
                  filename: "cube-fourth.3mf",
                  fileType: "other",
                  sizeBytes: 4,
                },
              ],
            },
          },
        },
      ])
      .mockResolvedValueOnce([{ result: [] }])
      .mockResolvedValueOnce([
        {
          result: {
            ok: true,
            links: [
              {
                id: "instance-alt",
                url: "https://makerworld.bblmw.com/files/cube-alt.3mf?signature=signed-secret",
              },
            ],
          },
        },
      ]);
    const fetchImpl = vi.fn(async (url: string, options: RequestInit = {}) => {
      if (url.endsWith("/capture-capabilities")) return response({}, 404);
      if (url.endsWith("/health")) return response({ status: "ok", name: "PrintStash" });
      if (url.endsWith("/login")) return response({ access_token: "vault-jwt" });
      if (url.endsWith("/me")) return response({ username: "owner", is_superuser: false });
      if (url === "https://makerworld.com/api/v1/design-service/design/1234")
        return response({
          id: "1234",
          title: "Calibration cube",
          designCreator: "Maker",
          instances: [
            { id: "instance-default", title: "cube—高.3mf", fileSize: 4 },
            { id: "instance-alt", title: "cube-alt.3mf", fileSize: 4 },
            { id: "instance-third", title: "cube-third.3mf", fileSize: 4 },
            { id: "instance-fourth", title: "cube-fourth.3mf", fileSize: 4 },
          ],
        });
      if (url.startsWith("https://makerworld.bblmw.com/")) {
        return new Response("mesh", {
          status: 200,
          headers: { "Content-Type": "model/3mf" },
        });
      }
      if (url.endsWith("/capture-upload-slots")) {
        const body = JSON.parse(stringBody(options));
        return response(
          {
            item: { id: 72 },
            slots: [
              {
                id: "slot-makerworld",
                role: "file",
                source_file_id: "instance-alt",
                filename: "cube-alt.3mf",
                media_type: "model/3mf",
                size_bytes: 4,
                sha256: body.files[0].sha256,
              },
            ],
          },
          201,
        );
      }
      if (url.endsWith("/capture-upload-slots/slot-makerworld"))
        return new Response(null, { status: 204 });
      if (url.endsWith("/capture-upload-finalize")) return response({ id: 72, state: "review" });
      throw new Error(`Unexpected MakerWorld request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchImpl);

    await import("../popup.ts");
    await settle();
    button("#capture").click();
    for (let attempt = 0; attempt < 5; attempt += 1) await settle();

    expect(element("#candidate-panel").hidden).toBe(false);
    expect(element("#candidate-panel legend").textContent).toBe("Select MakerWorld packages");
    const candidates = document.querySelectorAll<HTMLInputElement>("#candidate-list input");
    expect(candidates).toHaveLength(4);
    expect([...candidates].every((input) => !input.checked)).toBe(true);
    candidates[1]?.click();
    button("#capture").click();
    for (let attempt = 0; attempt < 12; attempt += 1) await settle();

    const executeScript = vi.mocked(fakeBrowser.scripting.executeScript);
    const linkRequest = executeScript.mock.calls.find(([details]) => {
      const args = details.args;
      return (
        Array.isArray(args) && args[0] && typeof args[0] === "object" && "selectedIds" in args[0]
      );
    });
    expect(linkRequest?.[0].args?.[0]).toMatchObject({ selectedIds: ["instance-alt"] });
    expect(fetchImpl.mock.calls.some(([url]) => url.endsWith("/api/v1/inbox"))).toBe(false);
    expect(fetchImpl.mock.calls.some(([url]) => url.endsWith("/browser-upload"))).toBe(false);
    const createCall = fetchImpl.mock.calls.find(([url]) => url.endsWith("/capture-upload-slots"));
    if (!createCall?.[1]) throw new Error("Missing MakerWorld durable slot request");
    const createBody = stringBody(createCall[1]);
    expect(JSON.parse(createBody)).toMatchObject({
      capture_source: { provider: "makerworld", source_item_id: "1234" },
      files: [{ id: "instance-alt", filename: "cube-alt.3mf", size_bytes: 4 }],
    });
    expect(createBody).not.toContain("signed-secret");
    expect(JSON.stringify(await fakeBrowser.storage.local.get())).not.toContain("signed-secret");
    expect(element("#status").textContent).toContain("Sent to Pending Imports");
    expect(element("#status").textContent).not.toContain("code:");
  });

  it("routes MakerWorld auth failure straight to the local-file picker without retry", async () => {
    await fakeBrowser.storage.local.set({
      vault: "https://prints.example.com",
      username: "owner",
      apiKey: "psk_vault_secret",
    });
    fakeBrowser.tabs.query = vi.fn().mockResolvedValue([
      {
        id: 42,
        title: "Calibration cube",
        url: "https://makerworld.com/en/models/1234-calibration-cube",
      },
    ]);
    fakeBrowser.scripting.executeScript = vi
      .fn()
      .mockResolvedValueOnce([{ result: null }])
      .mockResolvedValueOnce([{ result: { pageTitle: "Calibration cube", jsonLd: [] } }])
      .mockResolvedValueOnce([{ result: { ok: false, code: "auth_required" } }]);
    const fetchImpl = vi.fn(async (url: string) => {
      if (url.endsWith("/capture-capabilities")) return response({}, 404);
      if (url.endsWith("/health")) return response({ status: "ok", name: "PrintStash" });
      if (url.endsWith("/login")) return response({ access_token: "vault-jwt" });
      if (url.endsWith("/me")) return response({ username: "owner", is_superuser: false });
      if (url === "https://makerworld.com/api/v1/design-service/design/1234")
        return response({}, 403);
      throw new Error(`Unexpected MakerWorld capture retry: ${url}`);
    });
    vi.stubGlobal("fetch", fetchImpl);

    await import("../popup.ts");
    await settle();
    button("#capture").click();
    for (let attempt = 0; attempt < 6; attempt += 1) await settle();

    expect(element("#manual-file-panel").hidden).toBe(false);
    expect(element("#status").textContent).toContain("Sign in to MakerWorld");
    expect(fetchImpl).toHaveBeenCalledTimes(3);
    expect(fetchImpl.mock.calls.some(([url]) => url.endsWith("/api/v1/inbox"))).toBe(false);
    expect(fetchImpl.mock.calls.some(([url]) => url.endsWith("/browser-upload"))).toBe(false);
    expect(
      vi
        .mocked(fakeBrowser.scripting.executeScript)
        .mock.calls.some(
          ([details]) =>
            Array.isArray(details.args) &&
            details.args[0] &&
            typeof details.args[0] === "object" &&
            "selectedIds" in details.args[0],
        ),
    ).toBe(false);
  });

  it("presents a MakerWorld challenge as a manual recovery", async () => {
    await fakeBrowser.storage.local.set({
      vault: "https://prints.example.com",
      username: "owner",
      apiKey: "psk_vault_secret",
    });
    fakeBrowser.tabs.query = vi.fn().mockResolvedValue([
      {
        id: 42,
        title: "Design Headphone Stand Swing",
        url: "https://makerworld.com/en/models/1574312-design-headphone-stand-swing-quickprint",
      },
    ]);
    fakeBrowser.scripting.executeScript = vi
      .fn()
      .mockResolvedValueOnce([{ result: null }])
      .mockResolvedValueOnce([
        {
          result: { pageTitle: "Design Headphone Stand Swing", jsonLd: [] },
        },
      ])
      .mockResolvedValueOnce([
        {
          result: {
            ok: true,
            metadata: {
              fixtureVersion: "makerworld-design-service-v1",
              sourceItemId: "1574312",
              source: { title: "Design Headphone Stand Swing" },
              files: [
                {
                  id: "1656140",
                  filename: "0.24mm layer, 3 walls, 15% infill.3mf",
                  fileType: "other",
                },
              ],
            },
          },
        },
      ])
      .mockResolvedValueOnce([{ result: [] }])
      .mockResolvedValueOnce([{ result: { ok: false, code: "challenge" } }]);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.endsWith("/capture-capabilities")) return response({}, 404);
        if (url.endsWith("/health")) return response({ status: "ok", name: "PrintStash" });
        if (url.endsWith("/login")) return response({ access_token: "vault-jwt" });
        if (url.endsWith("/me")) return response({ username: "owner", is_superuser: false });
        throw new Error(`Unexpected MakerWorld challenge request: ${url}`);
      }),
    );

    await import("../popup.ts");
    await settle();
    button("#capture").click();
    for (let attempt = 0; attempt < 6; attempt += 1) await settle();
    document.querySelector<HTMLInputElement>("#candidate-list input")?.click();
    button("#capture").click();
    for (let attempt = 0; attempt < 6; attempt += 1) await settle();

    expect(element("#manual-file-panel").hidden).toBe(false);
    expect(element("#status-title").textContent?.trim()).toBe("Automatic download blocked");
    expect(element("#status-message").textContent).toBe(
      "MakerWorld did not authorize the automatic download. Download the selected 3MF from MakerWorld, then attach it below.",
    );
    expect(element("#status-message").textContent).not.toContain("user_file_required");
    expect(element("#status-code").textContent).toBe("makerworld_links_failed · challenge");
    expect(element("#status-details").hidden).toBe(false);
  });

  it("stops on a changed Printables file contract and directs manual attachment without retry", async () => {
    await fakeBrowser.storage.local.set({
      vault: "https://prints.example.com",
      username: "owner",
      apiKey: "psk_vault_secret",
    });
    fakeBrowser.scripting.executeScript = vi.fn().mockResolvedValue([
      {
        result: {
          pageTitle: "Changed Printables page",
          jsonLd: [
            JSON.stringify({
              name: "Changed model",
              distribution: [{ download: "https://media.printables.com/files/unsupported.3mf" }],
            }),
          ],
        },
      },
    ]);
    const fetchImpl = vi.fn(async (url: string) => {
      if (url.endsWith("/capture-capabilities")) return response({}, 404);
      if (url.endsWith("/health")) return response({ status: "ok", name: "PrintStash" });
      if (url.endsWith("/login")) return response({ access_token: "vault-jwt" });
      if (url.endsWith("/me")) return response({ username: "owner", is_superuser: false });
      throw new Error(`Unexpected capture retry: ${url}`);
    });
    vi.stubGlobal("fetch", fetchImpl);

    await import("../popup.ts");
    await settle();
    button("#capture").click();
    for (let attempt = 0; attempt < 4; attempt += 1) await settle();

    expect(element("#status").textContent).toContain("attach it in Pending Imports");
    expect(fetchImpl).toHaveBeenCalledTimes(4);
    expect(element("#candidate-panel").hidden).toBe(true);
  });

  it("uploads only the selected Thingiverse files through durable slots", async () => {
    fakeBrowser.tabs.query = vi.fn().mockResolvedValue([
      {
        id: 42,
        title: "Whistle",
        url: "https://www.thingiverse.com/thing:763622/files",
      },
    ]);
    fakeBrowser.scripting.executeScript = vi.fn(async (details) =>
      details.func?.name === "requestThingiverseFilesInMainWorld"
        ? [
            {
              frameId: 0,
              result: {
                ok: true,
                files: [
                  {
                    id: "991001",
                    filename: "whistle.stl",
                    fileType: "stl",
                    url: "https://www.thingiverse.com/download:991001",
                  },
                  {
                    id: "991002",
                    filename: "whistle-source.scad",
                    fileType: "other",
                    url: "https://www.thingiverse.com/download:991002",
                  },
                ],
              },
            },
          ]
        : [
            {
              frameId: 0,
              result: {
                pageTitle: "Whistle",
                jsonLd: [JSON.stringify({ name: "Whistle", author: "Ada" })],
              },
            },
          ],
    );
    await fakeBrowser.storage.local.set({
      vault: "https://prints.example.com",
      username: "owner",
      apiKey: "psk_vault_secret",
    });
    const fetchImpl = vi.fn(async (url: string, options: RequestInit = {}) => {
      if (url.endsWith("/capture-capabilities")) return response({}, 404);
      if (url.endsWith("/health")) return response({ status: "ok", name: "PrintStash" });
      if (url.endsWith("/login")) return response({ access_token: "vault-jwt" });
      if (url.endsWith("/me")) return response({ username: "owner", is_superuser: false });
      if (url === "https://www.thingiverse.com/download:991001") {
        const file = new Response("mesh", {
          status: 200,
          headers: { "Content-Length": "4", "Content-Type": "model/stl" },
        });
        Object.defineProperty(file, "url", {
          configurable: true,
          value: "https://cdn.thingiverse.com/assets/991001/whistle.stl",
        });
        return file;
      }
      if (url.endsWith("/capture-upload-slots")) {
        const body = JSON.parse(stringBody(options));
        return response(
          {
            item: { id: 61 },
            slots: [
              {
                id: "slot-thingiverse",
                role: "file",
                source_file_id: "thingiverse:763622:file:991001",
                filename: "whistle.stl",
                media_type: "model/stl",
                size_bytes: 4,
                sha256: body.files[0].sha256,
              },
            ],
          },
          201,
        );
      }
      if (url.endsWith("/capture-upload-slots/slot-thingiverse")) {
        return new Response(null, { status: 204 });
      }
      if (url.endsWith("/capture-upload-finalize")) return response({ id: 61, state: "ready" });
      throw new Error(`Unexpected Thingiverse URL retry: ${url}`);
    });
    vi.stubGlobal("fetch", fetchImpl);

    await import("../popup.ts");
    for (let attempt = 0; attempt < 6; attempt += 1) await settle();
    expect(button("#capture").disabled).toBe(false);
    button("#capture").click();
    for (let attempt = 0; attempt < 6; attempt += 1) await settle();

    expect(fakeBrowser.scripting.executeScript).toHaveBeenCalledTimes(4);
    expect(button("#capture").disabled).toBe(false);
    expect(element("#status").hidden).toBe(true);
    expect(fetchImpl).toHaveBeenCalledTimes(3);
    expect(element("#candidate-panel").hidden).toBe(false);
    expect(element("#candidate-panel legend").textContent).toBe("Select Thingiverse files");
    const checkboxes = [...document.querySelectorAll<HTMLInputElement>("#candidate-list input")];
    expect(checkboxes).toHaveLength(2);
    checkboxes[1].checked = false;
    button("#capture").click();
    for (let attempt = 0; attempt < 8; attempt += 1) await settle();

    const createCall = fetchImpl.mock.calls.find(([url]) => url.endsWith("/capture-upload-slots"));
    if (createCall === undefined || createCall[1] === undefined) {
      throw new Error(
        `Missing Thingiverse slot request: ${fetchImpl.mock.calls.map(([url]) => url).join(", ")}; status=${element("#status").textContent}`,
      );
    }
    expect(JSON.parse(stringBody(createCall[1]))).toMatchObject({
      capture_source: { provider: "thingiverse", source_item_id: "763622" },
      files: [{ id: "thingiverse:763622:file:991001", filename: "whistle.stl", size_bytes: 4 }],
    });
    expect(fetchImpl).toHaveBeenCalledTimes(8);
    expect(fetchImpl).not.toHaveBeenCalledWith(
      "https://www.thingiverse.com/download:991002",
      expect.anything(),
    );
    expect(element("#status").textContent).toContain("Sent to Pending Imports");
  });

  it("offers manual Thingiverse attachment when file links are unavailable", async () => {
    fakeBrowser.tabs.query = vi.fn().mockResolvedValue([
      {
        id: 42,
        title: "Cable Mount",
        url: "https://www.thingiverse.com/thing:7401604/files",
      },
    ]);
    fakeBrowser.scripting.executeScript = vi.fn(async (details) =>
      details.func?.name === "requestThingiverseFilesInMainWorld"
        ? [{ frameId: 0, result: { ok: false, code: "contract_changed" } }]
        : [
            {
              frameId: 0,
              result: {
                pageTitle: "Cable Mount",
                jsonLd: [JSON.stringify({ name: "Cable Mount", author: "INFINITY_D" })],
              },
            },
          ],
    );
    await fakeBrowser.storage.local.set({
      vault: "https://prints.example.com",
      username: "owner",
      apiKey: "psk_vault_secret",
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.endsWith("/capture-capabilities")) return response({}, 404);
        if (url.endsWith("/health")) return response({ status: "ok", name: "PrintStash" });
        if (url.endsWith("/login")) return response({ access_token: "vault-jwt" });
        if (url.endsWith("/me")) return response({ username: "owner", is_superuser: false });
        throw new Error(`Unexpected Thingiverse request: ${url}`);
      }),
    );

    await import("../popup.ts");
    for (let attempt = 0; attempt < 6; attempt += 1) await settle();
    expect(button("#capture").disabled).toBe(false);
    button("#capture").click();
    for (let attempt = 0; attempt < 12; attempt += 1) await settle();

    expect(fakeBrowser.scripting.executeScript).toHaveBeenCalledTimes(3);
    expect(button("#capture").disabled).toBe(false);
    expect(element("#manual-file-panel").hidden).toBe(false);
    expect(element("#import-hint").textContent).toBe("Automatic download was unavailable.");
    expect(element("#status").textContent).toContain(
      "Thingiverse returned file information PrintStash could not safely use",
    );
    expect(element("#status-code").textContent).toContain(
      "thingiverse_links_failed · contract_changed",
    );
  });

  it("shows a safe diagnostic when Thingiverse omits its file list", async () => {
    fakeBrowser.tabs.query = vi.fn().mockResolvedValue([
      {
        id: 42,
        title: "Thingiverse - The community for Open Hardware",
        url: "https://www.thingiverse.com/thing:7398551/files",
      },
    ]);
    fakeBrowser.scripting.executeScript = vi.fn(async (details) =>
      details.func?.name === "requestThingiverseFilesInMainWorld"
        ? [
            {
              frameId: 0,
              result: { ok: false, code: "contract_changed", reason: "files_missing" },
            },
          ]
        : [
            {
              frameId: 0,
              result: {
                pageTitle: "Thingiverse - The community for Open Hardware",
                jsonLd: [],
              },
            },
          ],
    );
    await fakeBrowser.storage.local.set({
      vault: "https://prints.example.com",
      username: "owner",
      apiKey: "psk_vault_secret",
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.endsWith("/capture-capabilities")) return response({}, 404);
        if (url.endsWith("/health")) return response({ status: "ok", name: "PrintStash" });
        if (url.endsWith("/login")) return response({ access_token: "vault-jwt" });
        if (url.endsWith("/me")) return response({ username: "owner", is_superuser: false });
        throw new Error(`Unexpected Thingiverse request: ${url}`);
      }),
    );

    await import("../popup.ts");
    for (let attempt = 0; attempt < 6; attempt += 1) await settle();
    button("#capture").click();
    for (let attempt = 0; attempt < 12; attempt += 1) await settle();

    expect(element("#status").textContent).toContain(
      "Thingiverse did not expose its file list. Refresh the Files page, then try again",
    );
    expect(element("#status-code").textContent).toContain(
      "thingiverse_links_failed · files_missing",
    );
  });

  it("identifies unusable Thingiverse file data without exposing provider content", async () => {
    fakeBrowser.tabs.query = vi.fn().mockResolvedValue([
      {
        id: 42,
        title: "Black Hole Lamp by NAM_3 - Thingiverse",
        url: "https://www.thingiverse.com/thing:7398551/files",
      },
    ]);
    fakeBrowser.scripting.executeScript = vi.fn(async (details) =>
      details.func?.name === "requestThingiverseFilesInMainWorld"
        ? [
            {
              frameId: 0,
              result: { ok: false, code: "contract_changed", reason: "invalid_file_data" },
            },
          ]
        : [
            {
              frameId: 0,
              result: {
                pageTitle: "Black Hole Lamp by NAM_3 - Thingiverse",
                jsonLd: [],
              },
            },
          ],
    );
    await fakeBrowser.storage.local.set({
      vault: "https://prints.example.com",
      username: "owner",
      apiKey: "psk_vault_secret",
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.endsWith("/capture-capabilities")) return response({}, 404);
        if (url.endsWith("/health")) return response({ status: "ok", name: "PrintStash" });
        if (url.endsWith("/login")) return response({ access_token: "vault-jwt" });
        if (url.endsWith("/me")) return response({ username: "owner", is_superuser: false });
        throw new Error(`Unexpected Thingiverse request: ${url}`);
      }),
    );

    await import("../popup.ts");
    for (let attempt = 0; attempt < 6; attempt += 1) await settle();
    button("#capture").click();
    for (let attempt = 0; attempt < 12; attempt += 1) await settle();

    expect(element("#status").textContent).toContain(
      "Thingiverse returned file entries without a safe download link",
    );
    expect(element("#status-code").textContent).toContain(
      "thingiverse_links_failed · invalid_file_data",
    );
    expect(element("#status").textContent).not.toContain("private-model");
    expect(element("#status").textContent).not.toContain("secret");
  });

  it("explains a blocked Thingiverse file service without blaming the visible page", async () => {
    fakeBrowser.tabs.query = vi.fn().mockResolvedValue([
      {
        id: 42,
        title: "Cable Mount",
        url: "https://www.thingiverse.com/thing:7401604/files",
      },
    ]);
    fakeBrowser.scripting.executeScript = vi.fn(async (details) =>
      details.func?.name === "requestThingiverseFilesInMainWorld"
        ? [{ frameId: 0, result: { ok: false, code: "challenge" } }]
        : [
            {
              frameId: 0,
              result: {
                pageTitle: "Cable Mount",
                jsonLd: [JSON.stringify({ name: "Cable Mount", author: "INFINITY_D" })],
              },
            },
          ],
    );
    await fakeBrowser.storage.local.set({
      vault: "https://prints.example.com",
      username: "owner",
      apiKey: "psk_vault_secret",
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.endsWith("/capture-capabilities")) return response({}, 404);
        if (url.endsWith("/health")) return response({ status: "ok", name: "PrintStash" });
        if (url.endsWith("/login")) return response({ access_token: "vault-jwt" });
        if (url.endsWith("/me")) return response({ username: "owner", is_superuser: false });
        throw new Error(`Unexpected Thingiverse request: ${url}`);
      }),
    );

    await import("../popup.ts");
    for (let attempt = 0; attempt < 6; attempt += 1) await settle();
    button("#capture").click();
    for (let attempt = 0; attempt < 12; attempt += 1) await settle();

    expect(element("#manual-file-panel").hidden).toBe(false);
    expect(element("#status").textContent).toContain(
      "Thingiverse blocked access to its file list. Refresh this page and try again",
    );
    expect(element("#status").textContent).not.toContain("Complete it in this tab");
  });

  it("uploads a user-selected Cults file through slots, PUT, and finalize without URL capture or retry", async () => {
    fakeBrowser.tabs.query = vi.fn().mockResolvedValue([
      {
        id: 42,
        title: "Cult cube",
        url: "https://cults3d.com/en/3d-model/art/cult-cube",
      },
    ]);
    fakeBrowser.scripting.executeScript = vi.fn().mockResolvedValue([
      {
        result: {
          pageTitle: "Cult cube",
          jsonLd: [JSON.stringify({ name: "Cult cube", author: "Ada" })],
        },
      },
    ]);
    await fakeBrowser.storage.local.set({
      vault: "https://prints.example.com",
      username: "owner",
      apiKey: "psk_vault_secret",
    });
    const fetchImpl = vi.fn(async (url: string, options: RequestInit = {}) => {
      if (url.endsWith("/capture-capabilities")) return response({}, 404);
      if (url.endsWith("/health")) return response({ status: "ok", name: "PrintStash" });
      if (url.endsWith("/login")) return response({ access_token: "vault-jwt" });
      if (url.endsWith("/me")) return response({ username: "owner", is_superuser: false });
      if (url.endsWith("/capture-upload-slots")) {
        const body = JSON.parse(stringBody(options));
        return response(
          {
            item: { id: 62 },
            slots: [
              {
                id: "slot-cults",
                role: "file",
                source_file_id: "cult-cube:cult-cube.stl",
                filename: "cult-cube.stl",
                media_type: "model/stl",
                size_bytes: 4,
                sha256: body.files[0].sha256,
              },
            ],
          },
          201,
        );
      }
      if (url.endsWith("/capture-upload-slots/slot-cults"))
        return new Response(null, { status: 204 });
      if (url.endsWith("/capture-upload-finalize")) return response({ id: 62, state: "ready" });
      throw new Error(`Unexpected Cults URL POST/retry: ${url}`);
    });
    vi.stubGlobal("fetch", fetchImpl);

    await import("../popup.ts");
    await settle();
    button("#capture").click();
    for (let attempt = 0; attempt < 4; attempt += 1) await settle();

    expect(element("#manual-file-panel").hidden).toBe(false);
    const input = requiredElement("#manual-file", HTMLInputElement);
    const file = new File(["mesh"], "cult-cube.stl", { type: "model/stl" });
    Object.defineProperty(input, "files", {
      configurable: true,
      value: { 0: file, length: 1, item: (index: number) => (index === 0 ? file : null) },
    });
    button("#capture").click();
    for (let attempt = 0; attempt < 8; attempt += 1) await settle();

    const captureRequests = fetchImpl.mock.calls.map(([url]) => url as string);
    expect(captureRequests).toEqual([
      "https://prints.example.com/api/v1/health",
      "https://prints.example.com/api/v1/auth/login",
      "https://prints.example.com/api/v1/auth/me",
      "https://prints.example.com/api/v1/inbox/capture-upload-slots",
      "https://prints.example.com/api/v1/inbox/capture-upload-slots/slot-cults",
      "https://prints.example.com/api/v1/inbox/62/capture-upload-finalize",
    ]);
    expect(element("#status").textContent).toContain("Sent to Pending Imports");
  });

  it.each(["visible_page", "thingiverse_files", "printables_metadata"])(
    "retires the fallback after held %s metadata is cancelled",
    async (stage) => {
      fakeBrowser.tabs.query = vi.fn().mockResolvedValue([
        {
          id: 42,
          title: "Whistle",
          url:
            stage === "printables_metadata"
              ? "https://www.printables.com/model/3161-3d-benchy/files"
              : "https://www.thingiverse.com/thing:763622/files",
        },
      ]);
      let holdCapture = false;
      let releaseMetadata: (() => void) | undefined;
      let metadataSignal: AbortSignal | null | undefined;
      fakeBrowser.scripting.executeScript = vi.fn(async (details) => {
        const fileRead = details.func?.name === "requestThingiverseFilesInMainWorld";
        const visibleRead =
          Array.isArray(details.args) &&
          details.args[0] &&
          typeof details.args[0] === "object" &&
          "maxScripts" in details.args[0];
        if (
          holdCapture &&
          ((stage === "visible_page" && visibleRead) || (stage === "thingiverse_files" && fileRead))
        ) {
          await new Promise<void>((resolve) => {
            releaseMetadata = resolve;
          });
        }
        return [{ frameId: 0, result: { pageTitle: "Whistle", jsonLd: [] } }];
      });
      await fakeBrowser.storage.local.set({
        vault: "https://vault-a.example.com",
        deviceCredential: "credential-a",
      });
      const fetchImpl = vi.fn<typeof fetch>(async (input, options = {}) => {
        const url = String(input);
        if (url === "https://api.printables.com/graphql/") {
          metadataSignal = options.signal;
          return new Promise<Response>((resolve) => {
            releaseMetadata = () => resolve(response({}));
          });
        }
        if (url.endsWith("/capture-capabilities")) return response({}, 404);
        if (url.endsWith("/health")) return response({ status: "ok", name: "PrintStash" });
        if (url.endsWith("/browser-pairings/claim"))
          return response({ credential: "credential-b" });
        throw new Error(`Unexpected request ${url}`);
      });
      vi.stubGlobal("fetch", fetchImpl);
      await import("../popup.ts");
      await vi.waitFor(() => expect(element("#connection-title").textContent).toBe("Connected"));
      holdCapture = true;
      button("#capture").click();
      await vi.waitFor(() => expect(releaseMetadata).toBeTypeOf("function"));
      button("#edit-connection").click();
      requiredElement("#vault", HTMLInputElement).value = "https://vault-b.example.com";
      requiredElement("#pairing-code", HTMLInputElement).value = "pair-b";
      button("#connect").click();
      await vi.waitFor(() =>
        expect(element("#connection-detail").textContent).toContain("vault-b.example.com"),
      );
      releaseMetadata?.();
      await settle();

      if (stage === "printables_metadata") expect(metadataSignal?.aborted).toBe(true);
      expect(element("#manual-file-panel").hidden).toBe(true);
      expect(element("#candidate-panel").hidden).toBe(true);
      expect(button("#capture").textContent).toBe("Send to PrintStash");
      expect(element("#status").textContent).toContain("Connection verified");
      expect(fetchImpl.mock.calls.some(([input]) => String(input).includes("/inbox"))).toBe(false);
    },
  );

  async function startHeldThingiverseCapture() {
    fakeBrowser.tabs.query = vi
      .fn()
      .mockResolvedValue([
        { id: 42, title: "Whistle", url: "https://www.thingiverse.com/thing:763622/files" },
      ]);
    fakeBrowser.scripting.executeScript = vi.fn(async (details) =>
      details.func?.name === "requestThingiverseFilesInMainWorld"
        ? [
            {
              frameId: 0,
              result: {
                ok: true,
                files: [
                  {
                    id: "991001",
                    filename: "whistle.stl",
                    fileType: "stl",
                    url: "https://www.thingiverse.com/download:991001",
                  },
                ],
              },
            },
          ]
        : [{ frameId: 0, result: { pageTitle: "Whistle", jsonLd: [] } }],
    );
    await fakeBrowser.storage.local.set({
      vault: "https://vault-a.example.com",
      deviceCredential: "credential-a",
    });
    const downloads: Array<{ signal: AbortSignal | null; release: (response: Response) => void }> =
      [];
    const fetchImpl = vi.fn<typeof fetch>(async (input, options = {}) => {
      const url = String(input);
      if (url.endsWith("/capture-capabilities")) return response({}, 404);
      if (url.endsWith("/health")) return response({ status: "ok", name: "PrintStash" });
      if (url.endsWith("/browser-pairings/claim")) return response({ credential: "credential-b" });
      if (url === "https://www.thingiverse.com/download:991001") {
        return new Promise<Response>((release) => {
          downloads.push({ signal: options.signal ?? null, release });
        });
      }
      if (url.endsWith("/capture-upload-slots")) return response({ item: { id: 99 }, slots: [] });
      throw new Error(`Unexpected request ${url}`);
    });
    vi.stubGlobal("fetch", fetchImpl);
    await import("../popup.ts");
    await vi.waitFor(() => expect(element("#connection-title").textContent).toBe("Connected"));
    button("#capture").click();
    await vi.waitFor(() => expect(element("#candidate-panel").hidden).toBe(false));
    button("#capture").click();
    await vi.waitFor(() => expect(downloads).toHaveLength(1));
    const releaseDownload = (index: number) => {
      const download = downloads[index];
      if (!download) throw new Error("Download not started");
      const downloaded = new Response("mesh", {
        headers: { "Content-Length": "4", "Content-Type": "model/stl" },
      });
      Object.defineProperty(downloaded, "url", {
        value: "https://cdn.thingiverse.com/assets/991001/whistle.stl",
      });
      download.release(downloaded);
    };
    return { fetchImpl, downloads, releaseDownload };
  }

  it("retires a held provider download when switching vaults", async () => {
    const { fetchImpl, downloads, releaseDownload } = await startHeldThingiverseCapture();

    button("#edit-connection").click();
    requiredElement("#vault", HTMLInputElement).value = "https://vault-b.example.com";
    requiredElement("#pairing-code", HTMLInputElement).value = "pair-b";
    button("#connect").click();
    await vi.waitFor(async () =>
      expect((await fakeBrowser.storage.local.get("vault")).vault).toBe(
        "https://vault-b.example.com",
      ),
    );
    releaseDownload(0);
    await vi.waitFor(() => expect(button("#capture").disabled).toBe(false));
    await settle();
    expect(
      fetchImpl.mock.calls.filter(([input]) => String(input).includes("/capture-upload")),
    ).toEqual([]);
    expect(downloads[0]?.signal?.aborted).toBe(true);
    expect(element("#connection-detail").textContent).toContain("vault-b.example.com");
    expect(element("#status").textContent).not.toContain("Sent to Pending Imports");
    expect(element("#status").textContent).toContain("Connection verified");
  });

  it("preserves disconnect feedback after a retired download completes", async () => {
    const { fetchImpl, downloads, releaseDownload } = await startHeldThingiverseCapture();
    button("#edit-connection").click();
    button("#disconnect").click();
    await vi.waitFor(() => expect(element("#connection-title").textContent).toBe("Not connected"));
    releaseDownload(0);
    await settle();
    expect(downloads[0]?.signal?.aborted).toBe(true);
    expect(fetchImpl.mock.calls.some(([input]) => String(input).includes("/capture-upload"))).toBe(
      false,
    );
    expect(element("#status").textContent).toContain("Disconnected");
    expect(button("#capture").disabled).toBe(true);
  });

  it("keeps the newer capture busy after the retired download completes", async () => {
    const { fetchImpl, downloads, releaseDownload } = await startHeldThingiverseCapture();
    button("#edit-connection").click();
    requiredElement("#vault", HTMLInputElement).value = "https://vault-b.example.com";
    requiredElement("#pairing-code", HTMLInputElement).value = "pair-b";
    button("#connect").click();
    await vi.waitFor(async () =>
      expect((await fakeBrowser.storage.local.get("vault")).vault).toBe(
        "https://vault-b.example.com",
      ),
    );
    button("#capture").click();
    await vi.waitFor(() => expect(element("#candidate-panel").hidden).toBe(false));
    button("#capture").click();
    await vi.waitFor(() => expect(downloads).toHaveLength(2));
    releaseDownload(0);
    await settle();
    expect(button("#capture").disabled).toBe(true);
    expect(downloads[1]?.signal?.aborted).toBe(false);
    expect(fetchImpl.mock.calls.some(([input]) => String(input).includes("/capture-upload"))).toBe(
      false,
    );
    expect(element("#import-hint").textContent).toContain("Keep this popup open");
    button("#edit-connection").click();
    button("#disconnect").click();
    await vi.waitFor(() => expect(element("#connection-title").textContent).toBe("Not connected"));
    releaseDownload(1);
    await settle();
    expect(downloads[1]?.signal?.aborted).toBe(true);
  });
});
