/*
 * The only page a signed-out visitor can reach.
 *
 * Two things make it worth its own file. First, "why am I here?" — landing on
 * the login form is the same screen whether the user opened the tab, was thrown
 * out by an expired session, or came back from an identity provider that
 * refused them. Those are different situations and the page has to say which,
 * because a user who does not know their session expired assumes the app broke.
 * The expiry notice is also consumed on read: it explains *this* landing, not
 * every subsequent one.
 *
 * Second, the failure copy. A wrong password is the user's problem to fix and
 * says so plainly; anything else is the server's, and repeating "invalid
 * username or password" for a 500 sends people to reset a password that was
 * always correct.
 *
 * SSO is offered only when the server has it configured — a button leading to an
 * identity provider nobody set up is a dead end — and an "?oidc=success" landing
 * is already mid-exchange, so the form must be busy from the first paint rather
 * than inviting a second sign-in over the top of it.
 */

import "@testing-library/jest-dom/vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import LoginPage from "@/pages/login";
import { adminSession, json, renderApp, type RenderAppOptions } from "@/test-support/render";
import type { AuthState } from "@/lib/auth-context";
import type { AuthProvidersRead } from "@/types";

const NO_SSO: AuthProvidersRead = { oidc_enabled: false, oidc_display_name: "" };
const WITH_SSO: AuthProvidersRead = { oidc_enabled: true, oidc_display_name: "Authentik" };

/** Signed out — the only state in which this page is the right screen. */
function signedOut(over: Partial<AuthState> = {}): AuthState {
  return adminSession({ user: null, ...over });
}

function renderLogin(options: RenderAppOptions & { providers?: AuthProvidersRead } = {}) {
  const { providers = NO_SSO, auth = signedOut(), routes = {}, ...rest } = options;
  return renderApp(<LoginPage />, {
    auth,
    routes: { "GET /api/v1/auth/providers": json(providers), ...routes },
    ...rest,
  });
}

/**
 * The OIDC outcome is read from `window.location.search`, not from the router:
 * the identity provider redirects the whole browser back here, so the query
 * string is the only thing carrying the result.
 */
function landOn(search: string) {
  window.history.replaceState({}, "", `/login${search}`);
}

const realLocation = window.location;

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
  landOn("");
});

afterEach(() => {
  Object.defineProperty(window, "location", { configurable: true, value: realLocation });
  vi.unstubAllGlobals();
});

describe("LoginPage", () => {
  describe("the form", () => {
    it("welcomes the visitor", () => {
      renderLogin();

      expect(screen.getByText("Welcome back")).toBeInTheDocument();
    });

    it("asks for a username", () => {
      renderLogin();

      expect(screen.getByLabelText("Username")).toBeInTheDocument();
    });

    it("asks for a password", () => {
      renderLogin();

      expect(screen.getByLabelText("Password")).toBeInTheDocument();
    });

    it("offers to remember the device", () => {
      renderLogin();

      expect(screen.getByLabelText("Remember me")).toBeInTheDocument();
    });

    it("says credentials never leave the server", () => {
      // The reassurance is the product's whole pitch; losing it is a
      // self-hosting promise quietly dropped.
      renderLogin();

      expect(
        screen.getByText("Your credentials stay with your self-hosted server."),
      ).toBeInTheDocument();
    });
  });

  describe("signing in", () => {
    it("submits what the visitor typed", async () => {
      const user = userEvent.setup();
      const login = vi.fn<AuthState["login"]>().mockResolvedValue(undefined);
      renderLogin({ auth: signedOut({ login }) });
      await user.type(screen.getByLabelText("Username"), "maker");
      await user.type(screen.getByLabelText("Password"), "hunter2");

      await user.click(screen.getByRole("button", { name: "Sign in" }));

      await waitFor(() => expect(login).toHaveBeenCalledWith("maker", "hunter2", false));
    });

    it("asks to be remembered when the visitor said so", async () => {
      // "Remember me" is the difference between a session cookie and a token
      // that survives a reboot; dropping it silently logs people out nightly.
      const user = userEvent.setup();
      const login = vi.fn<AuthState["login"]>().mockResolvedValue(undefined);
      renderLogin({ auth: signedOut({ login }) });
      await user.type(screen.getByLabelText("Username"), "maker");
      await user.type(screen.getByLabelText("Password"), "hunter2");
      await user.click(screen.getByLabelText("Remember me"));

      await user.click(screen.getByRole("button", { name: "Sign in" }));

      await waitFor(() => expect(login).toHaveBeenCalledWith("maker", "hunter2", true));
    });

    it("says the credentials were wrong for a 401", async () => {
      const user = userEvent.setup();
      renderLogin({
        auth: signedOut({
          login: vi.fn<AuthState["login"]>().mockRejectedValue(new Error("HTTP 401: nope")),
        }),
      });
      await user.type(screen.getByLabelText("Username"), "maker");
      await user.type(screen.getByLabelText("Password"), "wrong");

      await user.click(screen.getByRole("button", { name: "Sign in" }));

      expect(await screen.findByRole("alert")).toHaveTextContent("Invalid username or password.");
    });

    it("reports the server's own failure rather than blaming the password", async () => {
      // Repeating "invalid username or password" for a 500 sends people to
      // reset a password that was always correct.
      const user = userEvent.setup();
      renderLogin({
        auth: signedOut({
          login: vi.fn<AuthState["login"]>().mockRejectedValue(new Error("HTTP 503: down")),
        }),
      });
      await user.type(screen.getByLabelText("Username"), "maker");
      await user.type(screen.getByLabelText("Password"), "hunter2");

      await user.click(screen.getByRole("button", { name: "Sign in" }));

      expect(await screen.findByRole("alert")).toHaveTextContent("HTTP 503: down");
    });

    it("marks the fields as the thing that failed", async () => {
      // Screen-reader users get the error announced against the input, not as
      // loose text somewhere on the page.
      const user = userEvent.setup();
      renderLogin({
        auth: signedOut({
          login: vi.fn<AuthState["login"]>().mockRejectedValue(new Error("HTTP 401: nope")),
        }),
      });
      await user.type(screen.getByLabelText("Username"), "maker");
      await user.type(screen.getByLabelText("Password"), "wrong");

      await user.click(screen.getByRole("button", { name: "Sign in" }));

      await waitFor(() =>
        expect(screen.getByLabelText("Username")).toHaveAttribute("aria-invalid", "true"),
      );
    });
  });

  describe("why the visitor is here", () => {
    it("explains a session that expired", async () => {
      window.sessionStorage.setItem("printstash.session-expired", "1");

      renderLogin();

      expect(await screen.findByRole("status")).toHaveTextContent(
        "Session expired. Sign in again to continue.",
      );
    });

    it("says nothing about expiry on an ordinary visit", () => {
      renderLogin();

      expect(screen.queryByRole("status")).toBeNull();
    });

    it("clears the expiry notice once it has been shown", () => {
      // It explains *this* landing. Left set, it reappears on every future
      // sign-in and stops meaning anything.
      window.sessionStorage.setItem("printstash.session-expired", "1");

      renderLogin();

      expect(window.sessionStorage.getItem("printstash.session-expired")).toBeNull();
    });

    it("explains an identity provider that refused", async () => {
      landOn("?oidc_error=access_denied");

      renderLogin();

      expect(await screen.findByRole("alert")).toHaveTextContent(
        "Single sign-on failed. Try again or use local login.",
      );
    });
  });

  describe("single sign-on", () => {
    it("offers nothing when the server has no provider configured", async () => {
      // A button leading to an identity provider nobody set up is a dead end.
      renderLogin();

      await waitFor(() => expect(screen.queryByText("or")).toBeNull());
    });

    it("names the provider the server configured", async () => {
      renderLogin({ providers: WITH_SSO });

      expect(
        await screen.findByRole("button", { name: "Sign in with Authentik" }),
      ).toBeInTheDocument();
    });

    it("sends the visitor to the provider", async () => {
      const user = userEvent.setup();
      const assign = vi.fn<(url: string) => void>();
      Object.defineProperty(window, "location", {
        configurable: true,
        value: { href: realLocation.href, origin: realLocation.origin, search: "", assign },
      });
      renderLogin({ providers: WITH_SSO });

      await user.click(await screen.findByRole("button", { name: "Sign in with Authentik" }));

      expect(assign).toHaveBeenCalledWith(expect.stringContaining("/api/v1/auth/oidc/login"));
    });

    it("still offers local sign-in alongside it", async () => {
      // The provider going down must not lock the operator out of their own
      // self-hosted server.
      renderLogin({ providers: WITH_SSO });

      await screen.findByRole("button", { name: "Sign in with Authentik" });
      expect(screen.getByRole("button", { name: "Sign in" })).toBeEnabled();
    });
  });

  describe("coming back from the provider", () => {
    it("picks up the session the provider granted", async () => {
      const refresh = vi.fn<AuthState["refresh"]>().mockResolvedValue(undefined);
      landOn("?oidc=success");

      renderLogin({ auth: signedOut({ refresh }) });

      await waitFor(() => expect(refresh).toHaveBeenCalled());
    });

    it("holds the form busy while the exchange finishes", () => {
      // The landing is already mid-exchange; an inviting form would let the
      // visitor sign in a second time over the top of it.
      landOn("?oidc=success");

      renderLogin({
        auth: signedOut({
          refresh: vi.fn<AuthState["refresh"]>().mockReturnValue(new Promise(() => {})),
        }),
      });

      expect(screen.getByRole("button", { name: "Sign in" })).toBeDisabled();
    });

    it("falls back to the form when the exchange fails", async () => {
      const refresh = vi.fn<AuthState["refresh"]>().mockRejectedValue(new Error("boom"));
      landOn("?oidc=success");

      renderLogin({ auth: signedOut({ refresh }) });

      expect(await screen.findByRole("alert")).toHaveTextContent(
        "Single sign-on failed. Try again or use local login.",
      );
    });
  });

  describe("a visitor who is already signed in", () => {
    it("shows no form at all", () => {
      // Offering a sign-in to somebody with a session is how a user ends up
      // wondering whether they were logged out.
      renderLogin({ auth: adminSession() });

      expect(screen.queryByLabelText("Username")).toBeNull();
    });
  });

  describe("in Spanish", () => {
    it("renders the form in the chosen language", () => {
      renderLogin({ locale: "es" });

      expect(screen.getByText("Te damos la bienvenida")).toBeInTheDocument();
    });
  });
});
