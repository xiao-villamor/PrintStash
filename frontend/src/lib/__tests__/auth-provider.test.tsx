/*
 * The session, as the rest of the app sees it.
 *
 * A stored token is a claim, not a fact — it can be revoked, expired, or
 * belong to a user who has since been disabled. So the provider confirms it
 * with the server on mount and clears the login when the server disagrees.
 * Trusting local storage instead leaves the app rendering an admin's UI for
 * somebody whose account was turned off, until the first write 403s.
 *
 * `loading` is what the shell waits on, and it must be false immediately when
 * there is nothing to confirm: a visitor with no session would otherwise sit
 * behind a spinner waiting for a request that is never made.
 *
 * Signing in is two calls, and the second decides who the user *is* — the token
 * response carries no id or admin flag. If that call fails the login is thrown
 * away rather than left half-applied, because a session that believes it is a
 * non-admin with id 0 is worse than no session at all.
 */

import "@testing-library/jest-dom/vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "@/lib/auth-provider";
import { useAuth, type AuthApi } from "@/lib/auth-context";
import { clearLogin, storeLogin } from "@/lib/auth-store";
import type { UserRead } from "@/types";

function aUser(over: Partial<UserRead> = {}): UserRead {
  return {
    id: 7,
    username: "maker",
    email: null,
    is_superuser: false,
    is_active: true,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...over,
  };
}

function stubApi(over: Partial<AuthApi> = {}): AuthApi {
  return {
    login: vi.fn<AuthApi["login"]>().mockResolvedValue({
      access_token: "not-a-real-token",
      token_type: "bearer",
    }),
    logout: vi.fn<AuthApi["logout"]>().mockResolvedValue(undefined),
    getMe: vi.fn<AuthApi["getMe"]>().mockResolvedValue(aUser()),
    ...over,
  };
}

/** Renders the session state as text, plus buttons for each transition. */
function Probe() {
  const { user, loading, login, logout, refresh } = useAuth();
  return (
    <div>
      <p>{loading ? "loading" : user ? `signed in as ${user.username}` : "signed out"}</p>
      <button type="button" onClick={() => void login("maker", "hunter2").catch(() => {})}>
        sign in
      </button>
      <button type="button" onClick={() => void logout().catch(() => {})}>
        sign out
      </button>
      <button type="button" onClick={() => void refresh().catch(() => {})}>
        refresh
      </button>
    </div>
  );
}

function renderProvider(over: Partial<AuthApi> = {}) {
  const api = stubApi(over);
  const result = render(
    <AuthProvider api={api}>
      <Probe />
    </AuthProvider>,
  );
  return { ...result, api };
}

/** A session already in browser storage, as a returning visit would have. */
function withStoredSession() {
  storeLogin("not-a-real-token", {
    id: 7,
    username: "maker",
    email: null,
    is_superuser: false,
  });
}

beforeEach(() => {
  window.localStorage.clear();
  clearLogin();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AuthProvider", () => {
  describe("a visitor with no stored session", () => {
    it("is ready at once", () => {
      // There is nothing to confirm, so waiting would put a spinner in front of
      // a request that is never made.
      renderProvider();

      expect(screen.getByText("signed out")).toBeInTheDocument();
    });

    it("asks the server nothing", () => {
      const { api } = renderProvider();

      expect(api.getMe).not.toHaveBeenCalled();
    });
  });

  describe("a returning visit", () => {
    it("waits before claiming who the user is", () => {
      // A stored token is a claim, not a fact.
      withStoredSession();
      renderProvider({
        getMe: vi.fn<AuthApi["getMe"]>().mockReturnValue(new Promise(() => {})),
      });

      expect(screen.getByText("loading")).toBeInTheDocument();
    });

    it("confirms the stored session with the server", async () => {
      withStoredSession();
      const { api } = renderProvider();

      await waitFor(() => expect(api.getMe).toHaveBeenCalled());
    });

    it("adopts the identity the server reported", async () => {
      // The server is the authority on the admin flag; local storage is not.
      withStoredSession();
      renderProvider({
        getMe: vi.fn<AuthApi["getMe"]>().mockResolvedValue(aUser({ username: "admin" })),
      });

      expect(await screen.findByText("signed in as admin")).toBeInTheDocument();
    });

    it("clears a session the server no longer honours", async () => {
      // Otherwise the app renders for an account that was disabled, until the
      // first write 403s.
      withStoredSession();
      renderProvider({
        getMe: vi.fn<AuthApi["getMe"]>().mockRejectedValue(new Error("HTTP 401")),
      });

      expect(await screen.findByText("signed out")).toBeInTheDocument();
    });

    it("stops waiting even when the check fails", async () => {
      withStoredSession();
      renderProvider({
        getMe: vi.fn<AuthApi["getMe"]>().mockRejectedValue(new Error("HTTP 500")),
      });

      await waitFor(() => expect(screen.queryByText("loading")).toBeNull());
    });
  });

  describe("signing in", () => {
    it("exchanges the credentials for a token", async () => {
      const { api } = renderProvider();

      screen.getByRole("button", { name: "sign in" }).click();

      await waitFor(() =>
        expect(api.login).toHaveBeenCalledWith({
          username: "maker",
          password: "hunter2",
          remember_me: false,
        }),
      );
    });

    it("asks who the token belongs to", async () => {
      // The token response carries no id and no admin flag, so the second call
      // is what decides what the user may do.
      const { api } = renderProvider();

      screen.getByRole("button", { name: "sign in" }).click();

      await waitFor(() => expect(api.getMe).toHaveBeenCalled());
    });

    it("signs the user in under the identity the server gave", async () => {
      renderProvider();

      screen.getByRole("button", { name: "sign in" }).click();

      expect(await screen.findByText("signed in as maker")).toBeInTheDocument();
    });

    it("throws the login away when the identity call fails", async () => {
      // A session that believes it is a non-admin with id 0 is worse than no
      // session at all.
      renderProvider({
        getMe: vi.fn<AuthApi["getMe"]>().mockRejectedValue(new Error("HTTP 500")),
      });

      screen.getByRole("button", { name: "sign in" }).click();

      await waitFor(() => expect(screen.getByText("signed out")).toBeInTheDocument());
    });
  });

  describe("signing out", () => {
    it("tells the server", async () => {
      const { api } = renderProvider();

      screen.getByRole("button", { name: "sign out" }).click();

      await waitFor(() => expect(api.logout).toHaveBeenCalled());
    });

    it("ends the local session even when the server call fails", async () => {
      // The user asked to be signed out; leaving them signed in because a
      // network call failed is the opposite of what they asked for.
      withStoredSession();
      renderProvider({
        logout: vi.fn<AuthApi["logout"]>().mockRejectedValue(new Error("offline")),
      });
      await screen.findByText("signed in as maker");

      screen.getByRole("button", { name: "sign out" }).click();

      await waitFor(() => expect(screen.getByText("signed out")).toBeInTheDocument());
    });
  });

  describe("refreshing", () => {
    it("re-reads the identity from the server", async () => {
      // This is how an OIDC round trip lands: the cookie is already set, and
      // the app has to find out who it now belongs to.
      const { api } = renderProvider();

      screen.getByRole("button", { name: "refresh" }).click();

      await waitFor(() => expect(api.getMe).toHaveBeenCalled());
    });

    it("signs the user in from the refreshed identity", async () => {
      renderProvider();

      screen.getByRole("button", { name: "refresh" }).click();

      expect(await screen.findByText("signed in as maker")).toBeInTheDocument();
    });

    it("clears the session when the refresh is refused", async () => {
      withStoredSession();
      renderProvider({
        getMe: vi
          .fn<AuthApi["getMe"]>()
          .mockResolvedValueOnce(aUser())
          .mockRejectedValue(new Error("HTTP 401")),
      });
      await screen.findByText("signed in as maker");

      screen.getByRole("button", { name: "refresh" }).click();

      await waitFor(() => expect(screen.getByText("signed out")).toBeInTheDocument());
    });
  });
});
