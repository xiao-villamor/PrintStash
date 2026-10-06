/*
 * The one place the app decides a session is over.
 *
 * Every request goes through the same client, so a token that expired produces a
 * burst of 401s at once — a list page fires half a dozen. If each one triggered
 * the expiry handler the user would get a stack of "session expired" toasts and,
 * worse, a refresh storm. So expiry is latched: the first 401 on an established
 * session ends it, and the rest are absorbed.
 *
 * The inverse matters as much. A *rejected login* is a 401 too, and treating it
 * as an expired session would fire the expiry path for a user who was never
 * signed in — clearing state they do not have and showing them a message about
 * a session instead of about their password.
 *
 * The last case is the security one: the access token never lands anywhere a
 * script can read it. An earlier release kept it in `localStorage`, so this is a
 * regression guard, not a hypothetical.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  consumeSessionExpired,
  emitUnauthorized,
  getToken,
  clearLogin,
  onUnauthorized,
  onAuthChange,
  storeLogin,
} from "@/lib/auth-store";

describe("expireSession", () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
  });

  it("expires an established session once across concurrent 401 responses", () => {
    const listener = vi.fn<() => void>();
    const off = onUnauthorized(listener);
    storeLogin("expired-token", {
      id: 1,
      username: "admin",
      email: null,
      is_superuser: true,
    });

    emitUnauthorized();
    emitUnauthorized();
    emitUnauthorized();

    expect(getToken()).toBeNull();
    expect(listener).toHaveBeenCalledTimes(1);
    expect(consumeSessionExpired()).toBe(true);
    expect(consumeSessionExpired()).toBe(false);
    off();
  });

  it("does not treat a rejected login as an expired session", () => {
    emitUnauthorized();

    expect(consumeSessionExpired()).toBe(false);
  });

  it("never persists a browser-readable access token", () => {
    storeLogin("sensitive-jwt", {
      id: 1,
      username: "admin",
      email: null,
      is_superuser: true,
    });

    expect(localStorage.getItem("printstash.token")).toBeNull();
    expect(sessionStorage.getItem("printstash.token")).toBeNull();
    expect(getToken()).toBeNull();
  });
});

describe("auth change notifications", () => {
  it("ignores unrelated storage events while recognizing cross-tab sessions", () => {
    const listener = vi.fn<() => void>();
    const off = onAuthChange(listener);
    try {
      window.dispatchEvent(new StorageEvent("storage", { key: "printstash.locale" }));
      window.dispatchEvent(new StorageEvent("storage", { key: "printstash.theme" }));
      expect(listener).not.toHaveBeenCalled();
      window.dispatchEvent(new StorageEvent("storage", { key: "printstash.user" }));
      expect(listener).toHaveBeenCalledTimes(1);
      window.dispatchEvent(new StorageEvent("storage", { key: null }));
      expect(listener).toHaveBeenCalledTimes(2);
    } finally {
      off();
    }
    window.dispatchEvent(new StorageEvent("storage", { key: "printstash.user" }));
    expect(listener).toHaveBeenCalledTimes(2);
  });
});

/** Storage is metadata persistence, never the session retirement mechanism. */
describe("session retirement", () => {
  it("retires sessions when browser storage is unavailable", () => {
    const changes = vi.fn<() => void>();
    const off = onAuthChange(changes);
    const remove = vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
      throw new DOMException("Blocked", "SecurityError");
    });
    try {
      clearLogin();
      expect(changes).toHaveBeenCalledOnce();
    } finally {
      remove.mockRestore();
      off();
    }
  });

  it("retires bootstrap when validated identity changes", () => {
    storeLogin("", { id: 7, username: "maker", email: null, is_superuser: false });
    const changes = vi.fn<() => void>();
    const off = onAuthChange(changes);
    try {
      storeLogin(
        "",
        { id: 9, username: "new-owner", email: null, is_superuser: false },
        { silent: true },
      );
      expect(changes).toHaveBeenCalledOnce();
    } finally {
      off();
    }
  });

  it("keeps same-session metadata refresh silent", () => {
    storeLogin("", { id: 7, username: "maker", email: null, is_superuser: false });
    const changes = vi.fn<() => void>();
    const off = onAuthChange(changes);
    try {
      storeLogin(
        "",
        { id: 7, username: "maker", email: null, is_superuser: true },
        { silent: true },
      );
      expect(changes).not.toHaveBeenCalled();
    } finally {
      off();
    }
  });
});
