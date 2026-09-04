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
  onUnauthorized,
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
