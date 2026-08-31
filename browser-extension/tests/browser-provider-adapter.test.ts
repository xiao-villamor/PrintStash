import { describe, expect, it, vi } from "vitest";

import {
  createBrowserProviderAdapter,
  type BrowserExtensionApi,
} from "../browser-provider-adapter.ts";

describe("BrowserProviderAdapter", () => {
  it("exposes the popup's storage, permission, tab, and scripting seams without changing their results", async () => {
    const adapter: BrowserExtensionApi = {
      runtime: {
        getManifest: vi.fn().mockReturnValue({ version: "0.12.1" }),
      },
      storage: {
        local: {
          get: vi.fn().mockResolvedValue({ vault: "https://prints.example.com" }),
          set: vi.fn().mockResolvedValue(undefined),
          remove: vi.fn().mockResolvedValue(undefined),
        },
      },
      permissions: {
        contains: vi.fn().mockResolvedValue(true),
        request: vi.fn().mockResolvedValue(true),
        remove: vi.fn().mockResolvedValue(true),
      },
      tabs: {
        query: vi
          .fn()
          .mockResolvedValue([{ id: 9, url: "https://www.printables.com/model/9-safe" }]),
        create: vi.fn().mockResolvedValue(undefined),
      },
      scripting: {
        executeScript: vi.fn().mockResolvedValue([{ result: { pageTitle: "Safe" } }]),
      },
    };

    const browser = createBrowserProviderAdapter(adapter);

    await expect(browser.storage.get(["vault"])).resolves.toEqual({
      vault: "https://prints.example.com",
    });
    await expect(
      browser.permissions.request({ origins: ["https://prints.example.com/*"] }),
    ).resolves.toBe(true);
    await expect(browser.tabs.query({ active: true, currentWindow: true })).resolves.toEqual([
      { id: 9, url: "https://www.printables.com/model/9-safe" },
    ]);
    await expect(browser.scripting.executeScript({ target: { tabId: 9 } })).resolves.toEqual([
      { result: { pageTitle: "Safe" } },
    ]);
  });
});
