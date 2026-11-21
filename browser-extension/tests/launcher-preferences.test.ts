import { fakeBrowser } from "@webext-core/fake-browser";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { launcherPreferences } from "../launcher-preferences.ts";

const thingiverse = "https://www.thingiverse.com/*";

describe("launcher preferences", () => {
  beforeEach(() => fakeBrowser.reset());

  it("defaults to the enabled icon at the bottom right", async () => {
    await expect(launcherPreferences(fakeBrowser.storage).read()).resolves.toEqual({
      variant: "icon",
      corner: "bottom-right",
      enabled: true,
      hiddenSites: new Set(),
    });
  });

  it("round-trips settings through the sync area", async () => {
    const preferences = launcherPreferences(fakeBrowser.storage);
    await preferences.setVariant("edge-tab");
    await preferences.setCorner("top-left");
    await preferences.setEnabled(false);
    await preferences.setSiteHidden(thingiverse, true);

    await expect(launcherPreferences(fakeBrowser.storage).read()).resolves.toEqual({
      variant: "edge-tab",
      corner: "top-left",
      enabled: false,
      hiddenSites: new Set([thingiverse]),
    });
    expect(await fakeBrowser.storage.local.get()).toEqual({});
  });

  it("round-trips through local storage if sync is unavailable", async () => {
    const storage = {
      ...fakeBrowser.storage,
      sync: {
        ...fakeBrowser.storage.sync,
        get: vi.fn().mockRejectedValue(new Error("Sync is unavailable")),
        set: vi.fn().mockRejectedValue(new Error("Sync is unavailable")),
      },
    };
    await launcherPreferences(storage).setVariant("icon-and-label");

    await expect(launcherPreferences(storage).read()).resolves.toMatchObject({
      variant: "icon-and-label",
    });
  });

  it("keeps the local fallback when a stale synchronized value exists", async () => {
    const preferences = launcherPreferences(fakeBrowser.storage);
    await preferences.setVariant("icon");
    const storage = {
      ...fakeBrowser.storage,
      sync: {
        ...fakeBrowser.storage.sync,
        set: vi.fn().mockRejectedValue(new Error("Sync quota exceeded")),
      },
    };
    await launcherPreferences(storage).setVariant("edge-tab");

    await expect(launcherPreferences(storage).read()).resolves.toMatchObject({
      variant: "edge-tab",
    });
  });

  it("resets invalid saved values to the defaults", async () => {
    await fakeBrowser.storage.sync.set({
      "launcher.variant": "large",
      "launcher.corner": "center",
      "launcher.enabled": "true",
      "launcher.hidden:www.thingiverse.com": "yes",
    });
    await expect(launcherPreferences(fakeBrowser.storage).read()).resolves.toEqual({
      variant: "icon",
      corner: "bottom-right",
      enabled: true,
      hiddenSites: new Set(),
    });
  });

  it("rejects site visibility edits for an unsupported origin", () => {
    expect(() =>
      launcherPreferences(fakeBrowser.storage).setSiteHidden("https://example.com/*", true),
    ).toThrow("Unsupported launcher site.");
  });
});
