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
