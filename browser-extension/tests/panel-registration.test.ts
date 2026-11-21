import { fakeBrowser } from "@webext-core/fake-browser";
/** Site activation survives extension updates without activating other granted hosts. */
import { describe, expect, it } from "vitest";
import {
  marketplacePanelActivation,
  registerMarketplacePanel,
  restoreMarketplacePanels,
  type PanelRegistrationApi,
} from "../panel-registration.ts";
function registrationFixture(granted: string[]) {
  const values: Record<string, boolean> = {};
  const scripts = new Map<string, { id: string; matches: string[] }>();
  const api: PanelRegistrationApi = {
    storage: {
      get: async (keys) =>
        Object.fromEntries(keys.filter((k) => k in values).map((k) => [k, values[k]])),
      set: async (next) => {
        Object.assign(values, next);
      },
      remove: async (keys) => {
        keys.forEach((k) => delete values[k]);
      },
    },
    permissions: {
      contains: async ({ origins }) => origins.every((origin) => granted.includes(origin)),
    },
    scripting: {
      getRegisteredContentScripts: async ({ ids }) => ids.flatMap((id) => scripts.get(id) ?? []),
      registerContentScripts: async (rows) => {
        rows.forEach((row) => scripts.set(row.id, row));
      },
      unregisterContentScripts: async ({ ids }) => {
        ids.forEach((id) => scripts.delete(id));
      },
    },
  };
  return { api, values, scripts };
}
describe("registerMarketplacePanel", () => {
  it("requires an explicit site permission", async () => {
    const { api, scripts, values } = registrationFixture([]);
    expect(await registerMarketplacePanel(api, "https://thangs.com/*")).toBe(false);
    expect([...scripts.values()]).toEqual([]);
    expect(values).toEqual({});
  });
});
describe("restoreMarketplacePanels", () => {
  it("restores only a previously enabled site after an extension update", async () => {
    const { api, scripts } = registrationFixture([
      "https://thangs.com/*",
      "https://makerworld.com/*",
    ]);
    await registerMarketplacePanel(api, "https://thangs.com/*");
    scripts.clear();
    await restoreMarketplacePanels(api);
    expect([...scripts.values()].map((row) => row.matches)).toEqual([["https://thangs.com/*"]]);
  });
  it("forgets a revoked site", async () => {
    const granted = ["https://thangs.com/*"];
    const { api, scripts, values } = registrationFixture(granted);
    await registerMarketplacePanel(api, granted[0]);
    granted.length = 0;
    await restoreMarketplacePanels(api);
    expect([...scripts.values()]).toEqual([]);
    expect(values).toEqual({});
  });
});

describe("marketplacePanelActivation", () => {
  it("activates the current site after permission arrives without the toolbar popup", async () => {
    fakeBrowser.reset();
    const origin = "https://www.thingiverse.com/*";
    const granted: string[] = [];
    const { api, scripts } = registrationFixture(granted);
    const injected: string[] = [];
    const activation = marketplacePanelActivation(
      api,
      fakeBrowser.storage.session,
      async (site) => {
        injected.push(site);
      },
    );

    expect(await activation.request(origin)).toBe(false);
    granted.push(origin);
    await activation.permissionAdded(granted);

    expect(injected).toEqual([origin]);
    expect([...scripts.values()].map((entry) => entry.matches)).toEqual([[origin]]);
  });

  it("does not activate a grant without a matching user request", async () => {
    fakeBrowser.reset();
    const origin = "https://www.thingiverse.com/*";
    const { api, scripts } = registrationFixture([origin]);
    const activation = marketplacePanelActivation(api, fakeBrowser.storage.session, async () => {});

    await activation.permissionAdded([origin]);

    expect(scripts.size).toBe(0);
  });
});
