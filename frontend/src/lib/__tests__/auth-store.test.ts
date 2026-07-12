import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  consumeSessionExpired,
  emitUnauthorized,
  getToken,
  onUnauthorized,
  storeLogin,
} from "@/lib/auth-store";

describe("session expiry", () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
  });

  it("expires an established session once across concurrent 401 responses", () => {
    const listener = vi.fn();
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
});
