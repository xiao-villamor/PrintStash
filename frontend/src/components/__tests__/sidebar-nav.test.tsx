/*
 * The rail that says where you are and what you are allowed to reach.
 *
 * Two things here are not decoration. The admin-only destinations are absent
 * rather than disabled — a member who clicks Printers gets bounced back, and a
 * link that exists only to reject you reads as the app being broken. And the
 * pending-imports badge is tagged with the account it was counted for: an inbox
 * total is per-user, so showing the previous account's number after a switch
 * tells the new user they have work that belongs to somebody else.
 *
 * The active highlight is how a user knows which page they are on. The vault is
 * the exception the rule needs: its href is "/", which prefix-matches every URL
 * in the app, so it has to match exactly or every page looks like the vault.
 */

import "@testing-library/jest-dom/vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SidebarNav } from "@/components/sidebar-nav";
import {
  adminSession,
  json,
  memberSession,
  renderApp,
  type RenderAppOptions,
} from "@/test-support/render";

/** One queued import, trimmed to the fields the badge counts. */
function anInboxItem(state = "review") {
  return { id: 1, state, source_kind: "url", display_title: "Benchy", results: [] };
}

function renderNav(options: RenderAppOptions & { pending?: unknown[] } = {}) {
  const { pending = [], routes = {}, ...rest } = options;
  return renderApp(<SidebarNav />, {
    routes: { "GET /api/v1/inbox": json(pending), ...routes },
    ...rest,
  });
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("SidebarNav", () => {
  describe("where it can take you", () => {
    it("links to the vault", () => {
      renderNav();

      expect(screen.getByRole("link", { name: /Vault/i })).toHaveAttribute("href", "/");
    });

    it("links to the inbox", () => {
      renderNav();

      expect(screen.getByRole("link", { name: /Pending/i })).toHaveAttribute("href", "/inbox");
    });

    it("links to settings", () => {
      renderNav();

      expect(screen.getByRole("link", { name: /Settings/i })).toHaveAttribute("href", "/settings");
    });

    it("sends the wiki link out to the docs site", () => {
      // It leaves the app, so it is a plain anchor rather than a router link —
      // routing to it would 404 inside the SPA.
      renderNav();

      expect(screen.getByRole("link", { name: /Wiki/i })).toHaveAttribute(
        "href",
        expect.stringContaining("http"),
      );
    });
  });

  describe("what a member may reach", () => {
    it("offers printers to an admin", () => {
      renderNav({ auth: adminSession() });

      expect(screen.getByRole("link", { name: /Printers/i })).toBeInTheDocument();
    });

    it("leaves printers out for a member", () => {
      // A member who clicks it is bounced straight back, which reads as the app
      // being broken rather than as the page being off-limits.
      renderNav({ auth: memberSession() });

      expect(screen.queryByRole("link", { name: /Printers/i })).toBeNull();
    });
  });

  describe("the pending-imports badge", () => {
    it("counts what is waiting", async () => {
      renderNav({ pending: [anInboxItem(), anInboxItem()] });

      expect(await screen.findByText("2")).toBeInTheDocument();
    });

    it("leaves out imports the user already dismissed", async () => {
      // A dismissed import is done with; counting it keeps a badge on screen
      // over work nobody is going to do.
      renderNav({ pending: [anInboxItem(), anInboxItem("dismissed")] });

      expect(await screen.findByText("1")).toBeInTheDocument();
    });

    it("shows no badge when nothing is waiting", async () => {
      renderNav();

      await waitFor(() => expect(screen.queryByText("0")).toBeNull());
    });

    it("counts nothing for a signed-out visitor", async () => {
      // The inbox is per-account; a count with no account behind it is somebody
      // else's.
      const { requests } = renderNav({
        auth: adminSession({ user: null }),
        pending: [anInboxItem()],
      });

      await waitFor(() =>
        expect(requests().some((call) => call.url.includes("inbox"))).toBe(false),
      );
    });
  });

  describe("who is signed in", () => {
    it("names the signed-in user", () => {
      renderNav();

      expect(screen.getByText("admin")).toBeInTheDocument();
    });

    it("offers a way out", async () => {
      const user = userEvent.setup();
      const logout = vi.fn<() => Promise<void>>().mockResolvedValue(undefined);
      renderNav({ auth: adminSession({ logout }) });

      await user.click(screen.getByTitle("Sign out"));

      expect(logout).toHaveBeenCalled();
    });

    it("offers a way in to a signed-out visitor", () => {
      renderNav({ auth: adminSession({ user: null }) });

      expect(screen.getByRole("link", { name: /Sign in/i })).toHaveAttribute("href", "/login");
    });
  });
});
