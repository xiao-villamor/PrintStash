/** The connection form survives the popup closing while the user copies a code. */
import { describe, expect, it } from "vitest";
import {
  clearConnectionDraft,
  holdPendingConnection,
  readConnectionDraft,
  saveConnectionDraft,
  takePendingConnection,
} from "../connection-draft.ts";

function memoryStorage() {
  const values = new Map<string, unknown>();
  return {
    values,
    async get(keys: string[]) {
      return Object.fromEntries(keys.map((key) => [key, values.get(key)]));
    },
    async set(changes: Record<string, unknown>) {
      for (const [key, value] of Object.entries(changes)) values.set(key, value);
    },
    async remove(keys: string[]) {
      for (const key of keys) values.delete(key);
    },
  };
}

describe("connection draft", () => {
  it("restores the typed server, method, browser name and username", async () => {
    const storage = memoryStorage();
    const draft = {
      vault: "192.168.7.160:3470",
      method: "api-key" as const,
      deviceName: "Chrome on Linux",
      username: "printer",
    };
    await saveConnectionDraft(storage, draft);
    expect(await readConnectionDraft(storage)).toEqual(draft);
  });

  it("never stores a pairing code or API key", async () => {
    const storage = memoryStorage();
    await saveConnectionDraft(storage, {
      vault: "prints.example",
      method: "pairing",
      deviceName: "Chrome on Linux",
      username: "",
      // Extra fields from a careless caller must not reach storage.
      ...({ pairingCode: "secret-code", apiKey: "psk_secret" } as object),
    } as Parameters<typeof saveConnectionDraft>[1]);
    expect(JSON.stringify([...storage.values])).not.toMatch(/secret/);
  });

  it("forgets an empty form and a cleared draft", async () => {
    const storage = memoryStorage();
    await saveConnectionDraft(storage, {
      vault: "prints.example",
      method: "pairing",
      deviceName: "",
      username: "",
    });
    await saveConnectionDraft(storage, {
      vault: " ",
      method: "pairing",
      deviceName: "",
      username: "",
    });
    expect(await readConnectionDraft(storage)).toBeNull();
    await saveConnectionDraft(storage, {
      vault: "prints.example",
      method: "pairing",
      deviceName: "",
      username: "",
    });
    await clearConnectionDraft(storage);
    expect(await readConnectionDraft(storage)).toBeNull();
  });

  it("ignores a malformed stored value", async () => {
    const storage = memoryStorage();
    storage.values.set("connectionDraft", "not an object");
    expect(await readConnectionDraft(storage)).toBeNull();
  });

  describe("a connection waiting on the browser prompt", () => {
    const held = {
      vault: "http://nas.lan:3470",
      candidates: ["http://nas.lan:3470", "https://nas.lan:3470"],
      pairingCode: "one-time",
      deviceName: "Chrome",
      expiresAt: 2_000,
    };

    it("is returned once", async () => {
      const session = memoryStorage();
      await holdPendingConnection(session, held);
      expect(await takePendingConnection(session, 1_000)).toEqual(held);
      expect(await takePendingConnection(session, 1_000)).toBeNull();
    });

    it("expires", async () => {
      const session = memoryStorage();
      await holdPendingConnection(session, held);
      expect(await takePendingConnection(session, 2_000)).toBeNull();
      expect(session.values.size).toBe(0);
    });

    it("ignores a held value without addresses", async () => {
      const session = memoryStorage();
      session.values.set("pendingConnection", { ...held, candidates: [] });
      expect(await takePendingConnection(session, 1_000)).toBeNull();
    });
  });
});
