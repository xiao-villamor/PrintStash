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
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useState } from "react";

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
      <span>{user?.is_superuser ? "administrator" : "member"}</span>
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
  it("discards private component state on a new session", async () => {
    function PrivateDraft() {
      const [draft, setDraft] = useState("");
      return (
        <input
          aria-label="Private draft"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
        />
      );
    }
    withStoredSession();
    render(
      <AuthProvider api={stubApi()}>
        <Probe />
        <PrivateDraft />
      </AuthProvider>,
    );
    await screen.findByText("signed in as maker");
    fireEvent.change(screen.getByRole("textbox", { name: "Private draft" }), {
      target: { value: "Previous scope draft" },
    });

    await act(async () => withStoredSession());

    expect(screen.getByRole("textbox", { name: "Private draft" })).toHaveValue("");
  });

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

  it("keeps session validation pending through unrelated storage events", async () => {
    withStoredSession();
    let deliver: (user: UserRead) => void = () => {};
    renderProvider({
      getMe: () =>
        new Promise<UserRead>((resolve) => {
          deliver = resolve;
        }),
    });
    act(() =>
      window.dispatchEvent(
        new StorageEvent("storage", { key: "printstash.locale", newValue: "es" }),
      ),
    );
    expect(screen.getByText("loading")).toBeVisible();
    await act(async () => deliver(aUser()));
    expect(screen.getByText("signed in as maker")).toBeVisible();
  });

  it("accepts same-user auth changes after session validation", async () => {
    withStoredSession();
    renderProvider();
    expect(await screen.findByText("signed in as maker")).toBeVisible();
    act(() => storeLogin("", { id: 7, username: "maker", email: null, is_superuser: true }));
    expect(screen.getByText("administrator")).toBeVisible();
    act(() => clearLogin());
    expect(screen.getByText("signed out")).toBeVisible();
  });

  it("ignores bootstrap identity from a previous session", async () => {
    withStoredSession();
    let deliver: (user: UserRead) => void = () => {};
    renderProvider({
      getMe: () =>
        new Promise<UserRead>((resolve) => {
          deliver = resolve;
        }),
    });
    act(() => clearLogin());
    expect(screen.getByText("signed out")).toBeVisible();
    act(() => storeLogin("", { id: 9, username: "new-owner", email: null, is_superuser: false }));
    await act(async () => deliver(aUser()));
    expect(screen.getByText("signed in as new-owner")).toBeVisible();
    expect(screen.queryByText("signed in as maker")).toBeNull();
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

/** A late lifecycle operation must not replace or clear a newer verified session. */
describe("session transition races", () => {
  it("retains verified identity after stale refresh", async () => {
    withStoredSession();
    const refresh = Promise.withResolvers<UserRead>();
    const getMe = vi
      .fn<AuthApi["getMe"]>()
      .mockResolvedValueOnce(aUser())
      .mockReturnValueOnce(refresh.promise);
    renderProvider({ getMe });
    expect(await screen.findByText("signed in as maker")).toBeVisible();
    screen.getByRole("button", { name: "refresh" }).click();
    await waitFor(() => expect(getMe).toHaveBeenCalledTimes(2));
    act(() => storeLogin("", { id: 9, username: "new-owner", email: null, is_superuser: false }));
    await act(async () => refresh.resolve(aUser()));
    expect(screen.getByText("signed in as new-owner")).toBeVisible();
  });

  it("retains verified identity after stale logout", async () => {
    withStoredSession();
    const logout = Promise.withResolvers<void>();
    renderProvider({ logout: () => logout.promise });
    expect(await screen.findByText("signed in as maker")).toBeVisible();
    screen.getByRole("button", { name: "sign out" }).click();
    act(() => storeLogin("", { id: 9, username: "new-owner", email: null, is_superuser: false }));
    await act(async () => logout.resolve());
    expect(screen.getByText("signed in as new-owner")).toBeVisible();
  });

  it("rejects stale login identity publication", async () => {
    const me = Promise.withResolvers<UserRead>();
    const getMe = vi.fn<AuthApi["getMe"]>().mockReturnValue(me.promise);
    renderProvider({ getMe });
    screen.getByRole("button", { name: "sign in" }).click();
    await waitFor(() => expect(getMe).toHaveBeenCalledOnce());
    act(() => storeLogin("", { id: 9, username: "new-owner", email: null, is_superuser: false }));
    await act(async () => me.resolve(aUser()));
    expect(screen.getByText("signed in as new-owner")).toBeVisible();
  });
});

/** Retiring a cookie session is immediate; identity is published only after verification. */
describe("auth transition boundaries", () => {
  it("retires the displayed session while logout awaits the server", async () => {
    withStoredSession();
    const logout = Promise.withResolvers<void>();
    renderProvider({ logout: () => logout.promise });
    expect(await screen.findByText("signed in as maker")).toBeVisible();
    await act(async () => screen.getByRole("button", { name: "sign out" }).click());
    expect(screen.getByText("signed out")).toBeVisible();
    await act(async () => logout.resolve());
  });

  it("publishes no provisional identity during login verification", async () => {
    const me = Promise.withResolvers<UserRead>();
    const getMe = vi.fn<AuthApi["getMe"]>().mockReturnValue(me.promise);
    renderProvider({ getMe });
    screen.getByRole("button", { name: "sign in" }).click();
    await waitFor(() => expect(getMe).toHaveBeenCalledOnce());
    expect(window.localStorage.getItem("printstash.user")).toBeNull();
    await act(async () => me.resolve(aUser()));
    expect(screen.getByText("signed in as maker")).toBeVisible();
  });
});
