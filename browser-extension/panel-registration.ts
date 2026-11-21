import { MARKETPLACE_ORIGINS } from "./source-providers.ts";

export interface PanelRegistrationApi {
  storage: {
    get(keys: string[]): Promise<Record<string, unknown>>;
    set(values: Record<string, boolean>): Promise<void>;
    remove(keys: string[]): Promise<void>;
  };
  permissions: { contains(details: { origins: string[] }): Promise<boolean> };
  scripting: {
    getRegisteredContentScripts(filter: { ids: string[] }): Promise<Array<{ id: string }>>;
    registerContentScripts(
      scripts: Array<{
        id: string;
        matches: string[];
        js: string[];
        runAt: "document_idle";
        persistAcrossSessions: boolean;
      }>,
    ): Promise<void>;
    unregisterContentScripts(filter: { ids: string[] }): Promise<void>;
  };
}
function panelIdentity(origin: string) {
  if (!MARKETPLACE_ORIGINS.includes(origin)) throw new Error("Unsupported marketplace permission.");
  const id = `printstash-${new URL(origin.replace(/\*$/, "")).hostname}`;
  return { id, key: `panel-enabled:${id}` };
}
export async function isMarketplacePanelEnabled(
  storage: Pick<PanelRegistrationApi["storage"], "get">,
  origin: string,
): Promise<boolean> {
  const { key } = panelIdentity(origin);
  return (await storage.get([key]))[key] === true;
}

export async function registerMarketplacePanel(
  api: PanelRegistrationApi,
  origin: string,
): Promise<boolean> {
  const { id, key } = panelIdentity(origin);
  if (!(await api.permissions.contains({ origins: [origin] }))) return false;
  const existing = await api.scripting.getRegisteredContentScripts({ ids: [id] });
  if (!existing.length)
    await api.scripting.registerContentScripts([
      {
        id,
        matches: [origin],
        js: ["marketplace.js"],
        runAt: "document_idle",
        persistAcrossSessions: true,
      },
    ]);
  await api.storage.set({ [key]: true });
  return true;
}
export async function restoreMarketplacePanels(api: PanelRegistrationApi): Promise<void> {
  const saved = await api.storage.get(
    MARKETPLACE_ORIGINS.map((origin) => panelIdentity(origin).key),
  );
  for (const origin of MARKETPLACE_ORIGINS) {
    const { id, key } = panelIdentity(origin);
    if (saved[key] !== true) continue;
    if (!(await api.permissions.contains({ origins: [origin] }))) {
      await api.scripting.unregisterContentScripts({ ids: [id] });
      await api.storage.remove([key]);
    } else await registerMarketplacePanel(api, origin);
  }
}

interface PanelIntentStorage {
  get(keys: string[]): Promise<Record<string, unknown>>;
  set(values: Record<string, number>): Promise<void>;
  remove(keys: string[]): Promise<void>;
}

const PERMISSION_INTENT_MS = 120_000;

/** Complete a user-requested activation even if Chrome closes the requesting popup. */
export function marketplacePanelActivation(
  api: PanelRegistrationApi,
  intent: PanelIntentStorage,
  inject: (origin: string) => Promise<void>,
) {
  const installing = new Map<string, Promise<boolean>>();
  const keyFor = (origin: string) => `panel-permission:${panelIdentity(origin).id}`;

  function install(origin: string) {
    const result = (installing.get(origin) ?? Promise.resolve(false))
      .catch(() => false)
      .then(async () => {
        if (!(await registerMarketplacePanel(api, origin))) return false;
        await intent.remove([keyFor(origin)]);
        await inject(origin);
        return true;
      });
    installing.set(origin, result);
    const clear = () => {
      if (installing.get(origin) === result) installing.delete(origin);
    };
    void result.then(clear, clear);
    return result;
  }

  return {
    async request(origin: string) {
      const key = keyFor(origin);
      await intent.set({ [key]: Date.now() + PERMISSION_INTENT_MS });
      return install(origin);
    },
    async permissionAdded(origins: string[]) {
      for (const origin of origins) {
        if (!MARKETPLACE_ORIGINS.includes(origin)) continue;
        const key = keyFor(origin);
        const expiry = (await intent.get([key]))[key];
        if (typeof expiry !== "number") continue;
        if (Date.now() < expiry) await install(origin);
        else await intent.remove([key]);
      }
    },
  };
}
