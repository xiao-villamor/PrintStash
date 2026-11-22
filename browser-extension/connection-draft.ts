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

/**
 * A connection waiting on Chrome's permission prompt.
 *
 * Chrome closes the toolbar popup when that prompt opens, ending the attempt
 * before the user can allow it. The attempt is held, with its single-use
 * pairing code or API key, in session storage: memory only, never written to
 * disk, unreadable by content scripts, and kept for two minutes. Reopening
 * the popup after allowing finishes the connection and forgets it.
 */
export interface PendingConnection {
  vault: string;
  candidates: string[];
  pairingCode?: string;
  deviceName?: string;
  username?: string;
  apiKey?: string;
  expiresAt: number;
}

const PENDING_KEY = "pendingConnection";
export const PENDING_CONNECTION_MS = 2 * 60 * 1000;

export async function holdPendingConnection(
  session: DraftStorage,
  pending: PendingConnection,
): Promise<void> {
  await session.set({ [PENDING_KEY]: pending });
}

/** Return the held connection once, if it has not expired. */
export async function takePendingConnection(
  session: DraftStorage,
  now = Date.now(),
): Promise<PendingConnection | null> {
  const stored = (await session.get([PENDING_KEY]))[PENDING_KEY];
  await session.remove([PENDING_KEY]);
  if (!stored || typeof stored !== "object") return null;
  const held = stored as Record<string, unknown>;
  const candidates = Array.isArray(held.candidates)
    ? held.candidates.filter((value): value is string => typeof value === "string")
    : [];
  if (
    typeof held.vault !== "string" ||
    !candidates.length ||
    typeof held.expiresAt !== "number" ||
    held.expiresAt <= now
  )
    return null;
  const optional = (name: string) =>
    typeof held[name] === "string" ? { [name]: held[name] as string } : {};
  return {
    vault: held.vault,
    candidates,
    expiresAt: held.expiresAt,
    ...optional("pairingCode"),
    ...optional("deviceName"),
    ...optional("username"),
    ...optional("apiKey"),
  };
}
