/**
 * What the user typed into the connection form, kept while the popup closes.
 *
 * Chrome closes the toolbar popup as soon as focus leaves it, which is exactly
 * what happens when the user switches to PrintStash to copy a pairing code.
 * Secrets (pairing code, API key) are never kept: the user pastes them anyway,
 * and a closed popup must not leave a credential behind in storage.
 */
export interface ConnectionDraft {
  vault: string;
  method: "pairing" | "api-key";
  deviceName: string;
  username: string;
}

interface DraftStorage {
  get(keys: string[]): Promise<Record<string, unknown>>;
  set(values: Record<string, unknown>): Promise<void>;
  remove(keys: string[]): Promise<void>;
}

const KEY = "connectionDraft";

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

export async function readConnectionDraft(storage: DraftStorage): Promise<ConnectionDraft | null> {
  const stored = (await storage.get([KEY]))[KEY];
  if (!stored || typeof stored !== "object") return null;
  const draft = stored as Record<string, unknown>;
  const method = draft.method === "api-key" ? "api-key" : "pairing";
  const result: ConnectionDraft = {
    vault: text(draft.vault),
    method,
    deviceName: text(draft.deviceName),
    username: text(draft.username),
  };
  return result.vault || result.username ? result : null;
}

export async function saveConnectionDraft(
  storage: DraftStorage,
  draft: ConnectionDraft,
): Promise<void> {
  if (!draft.vault.trim() && !draft.username.trim()) {
    await storage.remove([KEY]);
    return;
  }
  const { vault, method, deviceName, username } = draft;
  await storage.set({ [KEY]: { vault, method, deviceName, username } });
}

export async function clearConnectionDraft(storage: DraftStorage): Promise<void> {
  await storage.remove([KEY]);
}
