/*
 * The one piece of chrome that tells a user their session went away.
 *
 * A 401 arrives asynchronously, in the middle of whatever the user was doing —
 * a save, a batch, a printer command — and the request that raised it has
 * already failed silently by the time anything renders. Without this banner the
 * page simply stops working, which reads as the app being broken rather than as
 * the user being signed out.
 *
 * Two situations produce the same status code and need different words. A user
 * whose session expired had one and lost it; a user who never had one is being
 * asked to sign in for the first time. Telling the second group their session
 * expired sends them looking for a session they never had.
 *
 * The bootstrap probe is the exception: the app asks who you are on every load,
 * and a signed-out visitor's 401 there is expected. Toasting it would greet
 * every anonymous visitor with a warning about nothing.
 */

import "@testing-library/jest-dom/vitest";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthBanner } from "@/components/auth-banner";
import { clearLogin, storeLogin } from "@/lib/auth-store";
import { renderApp } from "@/test-support/render";

/** The event the api client fires when the server refuses a stored credential. */
function serverRefusedTheCredential() {
  act(() => {
    window.dispatchEvent(new Event("printstash:unauthorized"));
  });
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AuthBanner", () => {
  describe("when nothing has gone wrong", () => {
    it("stays out of the way", () => {
      renderApp(<AuthBanner />);

      expect(screen.queryByRole("button", { name: "Dismiss" })).toBeNull();
    });
  });

  describe("when the server refuses a credential", () => {
    it("tells a signed-out visitor to sign in", async () => {
      // They never had a session, so "expired" would send them looking for one
      // they never had.
      renderApp(<AuthBanner />);
      clearLogin();

      serverRefusedTheCredential();

      expect(
        await screen.findByText("An action requires authentication. Sign in to continue."),
      ).toBeInTheDocument();
    });

    it("tells a signed-in user their session expired", async () => {
      renderApp(<AuthBanner />);
      storeLogin(
        "",
        { id: 1, username: "admin", email: null, is_superuser: true },
        { silent: true },
      );

      serverRefusedTheCredential();

      expect(
        await screen.findByText("Your session has expired. Please sign in again."),
      ).toBeInTheDocument();
    });

    it("offers a way to sign in again", async () => {
      renderApp(<AuthBanner />);
      clearLogin();

      serverRefusedTheCredential();

      expect(await screen.findByRole("link", { name: "Sign in" })).toHaveAttribute(
        "href",
        "/login",
      );
    });

    it("offers no sign-in link to somebody who still has a session", async () => {
      // They are signed in; the credential the server refused is a stale token,
      // and sending them to the form again is the wrong instruction.
      renderApp(<AuthBanner />);
      storeLogin(
        "",
        { id: 1, username: "admin", email: null, is_superuser: true },
        { silent: true },
      );

      serverRefusedTheCredential();

      await screen.findByText("Your session has expired. Please sign in again.");
      expect(screen.queryByRole("link", { name: "Sign in" })).toBeNull();
    });

    it("points at settings, where the credentials live", async () => {
      renderApp(<AuthBanner />);
      clearLogin();

      serverRefusedTheCredential();

      expect(await screen.findByRole("link", { name: "Settings" })).toHaveAttribute(
        "href",
        "/settings",
      );
    });

    it("says nothing louder than the banner on the first refusal", async () => {
      // The app probes who you are on every load, so an anonymous visitor's
      // first 401 is expected — toasting it warns about nothing.
      renderApp(<AuthBanner />);
      clearLogin();

      serverRefusedTheCredential();

      await screen.findByRole("button", { name: "Dismiss" });
      expect(screen.queryByText("Authentication required — sign in to continue.")).toBeNull();
    });

    it("toasts a refusal that follows the bootstrap probe", async () => {
      // By the second one the user is doing something, and a banner above the
      // fold is easy to miss mid-task.
      renderApp(<AuthBanner />);
      clearLogin();
      serverRefusedTheCredential();

      serverRefusedTheCredential();

      expect(
        await screen.findByText("Authentication required — sign in to continue."),
      ).toBeInTheDocument();
    });
  });

  describe("dismissing it", () => {
    it("lets the user put it away", async () => {
      const user = userEvent.setup();
      renderApp(<AuthBanner />);
      clearLogin();
      serverRefusedTheCredential();

      await user.click(await screen.findByRole("button", { name: "Dismiss" }));

      expect(screen.queryByRole("button", { name: "Dismiss" })).toBeNull();
    });

    it("takes itself away once the user signs back in", async () => {
      // Leaving it up over a working session is a warning about a problem that
      // is already fixed.
      renderApp(<AuthBanner />);
      clearLogin();
      serverRefusedTheCredential();
      await screen.findByRole("button", { name: "Dismiss" });

      act(() => {
        storeLogin("t", { id: 1, username: "admin", email: null, is_superuser: true });
      });

      await waitFor(() => expect(screen.queryByRole("button", { name: "Dismiss" })).toBeNull());
    });
  });
});
