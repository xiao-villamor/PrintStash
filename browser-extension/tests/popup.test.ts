import { readFile } from "node:fs/promises";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fakeBrowser } from "@webext-core/fake-browser";

const popupHtml = await readFile("entrypoints/popup/index.html", "utf8");
const popupCss = await readFile("popup.css", "utf8");
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

function cssBlock(selector: string): string {
  const selectorIndex = popupCss.indexOf(selector);
  const blockStart = popupCss.indexOf("{", selectorIndex);
  const blockEnd = popupCss.indexOf("}", blockStart);
  if (selectorIndex < 0 || blockStart < 0 || blockEnd < 0) {
    throw new Error(`Missing CSS block for ${selector}`);
  }
  return popupCss.slice(blockStart + 1, blockEnd);
}

async function settle() {
  await new Promise((resolve) => setTimeout(resolve, 0));
  await new Promise((resolve) => setTimeout(resolve, 0));
}

describe("popup browser adapters", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  beforeEach(() => {
    vi.resetModules();
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

  it("shows the packaged release version in the popup header", async () => {
    await import("../popup.ts");

    expect(element("#runtime-marker").textContent).toBe(`Version ${extensionVersion}`);
    expect(element("#runtime-marker").hidden).toBe(false);
    expect(document.querySelector("header")?.textContent).not.toContain("protocol");
    expect(document.querySelector("header")?.textContent).not.toContain("diagnostics");
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

  it("reports a safe code when visible capture execution rejects or times out", async () => {
    vi.stubGlobal("__PRINTSTASH_CAPTURE_TIMEOUT_MS__", 5);
    await fakeBrowser.storage.local.set({
      vault: "https://prints.example.com",
      username: "owner",
      apiKey: "psk_vault_secret",
    });
    const fetchImpl = vi.fn(async (url: string) => {
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

  it("keeps candidate checkboxes compact instead of inheriting text-input dimensions", () => {
    const checkboxStyles = cssBlock('.candidate-option input[type="checkbox"]');
    expect(checkboxStyles).toMatch(/width:\s*16px/);
    expect(checkboxStyles).toMatch(/height:\s*16px/);
    expect(checkboxStyles).toMatch(/min-height:\s*16px/);
    expect(checkboxStyles).toMatch(/flex:\s*0 0 16px/);
    expect(checkboxStyles).toMatch(/padding:\s*0/);
  });

  it("restores settings and checks the vault through fake storage and permission APIs", async () => {
    await fakeBrowser.storage.local.set({
      vault: "https://prints.example.com",
      username: "owner",
      apiKey: "psk_vault_secret",
    });
    const fetchImpl = vi.fn(async (url) => {
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

  it("falls back to a local Printables file without metadata-only capture", async () => {
    await fakeBrowser.storage.local.set({
      vault: "https://prints.example.com",
      username: "owner",
      apiKey: "psk_vault_secret",
    });
    const fetchImpl = vi.fn(async (url, options: RequestInit = {}) => {
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
    expect(element("#status").textContent).toContain("sent to Pending Imports");
  });

  it("fails closed to a local Printables file when capture acquisition is unusable", async () => {
    await fakeBrowser.storage.local.set({
      vault: "https://prints.example.com",
      username: "owner",
      apiKey: "psk_vault_secret",
    });
    const fetchImpl = vi.fn(async (url: string) => {
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
    expect(fetchImpl).toHaveBeenCalledTimes(4);
    expect(
      fetchImpl.mock.calls.filter(([url]) => url === "https://api.printables.com/graphql/"),
    ).toHaveLength(1);
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
    expect(linkCalls).toHaveLength(2);
    const linkCall = linkCalls[1];
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
    expect(element("#status").textContent).toContain("sent to Pending Imports");
    expect(element("#status").textContent).not.toContain("code:");
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
    expect(element("#status").textContent).toContain("MakerWorld packages sent to Pending Imports");
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
      .mockResolvedValueOnce([{ result: { ok: false, code: "challenge" } }]);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
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
    expect(element("#status-title").textContent).toBe("Automatic download blocked");
    expect(element("#status-message").textContent).toBe(
      "MakerWorld did not authorize the automatic download. Download the selected 3MF from MakerWorld, then attach it below.",
    );
    expect(element("#status-message").textContent).not.toContain("user_file_required");
    expect(element("#status-code").textContent).toBe("makerworld_links_failed · challenge");
    expect(requiredElement("#status-details", HTMLDetailsElement).open).toBe(false);
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

    expect(fakeBrowser.scripting.executeScript).toHaveBeenCalledTimes(3);
    expect(button("#capture").disabled).toBe(false);
    expect(element("#status").textContent).toContain("Select Thingiverse files");
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
    expect(fetchImpl).toHaveBeenCalledTimes(7);
    expect(fetchImpl).not.toHaveBeenCalledWith(
      "https://www.thingiverse.com/download:991002",
      expect.anything(),
    );
    expect(element("#status").textContent).toContain(
      "Selected Thingiverse files sent to Pending Imports",
    );
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
    expect(element("#status").textContent).toContain("sent to Pending Imports");
  });
});
