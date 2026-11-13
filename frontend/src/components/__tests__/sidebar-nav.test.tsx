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
import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { KeyboardShortcuts } from "@/components/keyboard-shortcuts";
import { SidebarNav } from "@/components/sidebar-nav";
import { usePathname, useSearchParams } from "@/lib/navigation";
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

function CurrentLocation() {
  const pathname = usePathname();
  const search = useSearchParams().toString();
  return (
    <output data-testid="rail-location">
      {pathname}
      {search ? `?${search}` : ""}
    </output>
  );
}

function renderNav(options: RenderAppOptions & { pending?: unknown[] } = {}) {
  const { pending = [], routes = {}, ...rest } = options;
  return renderApp(
    <>
      <SidebarNav />
      <KeyboardShortcuts />
      <CurrentLocation />
    </>,
    {
      routes: { "GET /api/v1/inbox": json(pending), ...routes },
      ...rest,
    },
  );
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("SidebarNav", () => {
  describe("where it can take you", () => {
    it("keeps Builds out of the main rail", () => {
      renderNav();
      expect(screen.queryByRole("link", { name: /Builds/i })).not.toBeInTheDocument();
      expect(screen.getByRole("link", { name: /Profiles/i })).toHaveAttribute("href", "/profiles");
    });

    it("links to the vault", () => {
      renderNav();

      expect(screen.getByRole("link", { name: "Collections" })).toHaveAttribute("href", "/");
    });

    it("opens Favorites using the existing library filter", () => {
      renderNav();

      expect(screen.getByRole("link", { name: "Favorites" })).toHaveAttribute(
        "href",
        "/?favorites=true",
      );
    });

    it("navigates to Favorites through the client router", async () => {
      const user = userEvent.setup();
      renderNav({ at: "/inbox" });
      await user.click(screen.getByRole("link", { name: "Favorites" }));
      await waitFor(() =>
        expect(screen.getByTestId("rail-location")).toHaveTextContent("/?favorites=true"),
      );
    });

    it("leaves modified link clicks to the browser", () => {
      renderNav({ at: "/inbox" });
      const link = screen.getByRole("link", { name: "Favorites" });
      let intercepted: boolean | undefined;
      document.addEventListener(
        "click",
        (event) => {
          intercepted = event.defaultPrevented;
          event.preventDefault();
        },
        { once: true },
      );
      fireEvent.click(link, { button: 0, ctrlKey: true });
      expect(intercepted).toBe(false);
      expect(screen.getByTestId("rail-location")).toHaveTextContent("/inbox");
    });

    it("opens Trash through its settings section", () => {
      renderNav({ auth: adminSession() });

      expect(screen.getByRole("link", { name: "Trash" })).toHaveAttribute(
        "href",
        "/settings?section=trash",
      );
    });

    it("links to the inbox", () => {
      renderNav();

      expect(screen.getByRole("link", { name: /Pending/i })).toHaveAttribute("href", "/inbox");
    });

    it("links to settings from the account menu", async () => {
      const user = userEvent.setup();
      renderNav();
      await user.click(screen.getByRole("button", { name: "My workspace" }));

      expect(screen.getByRole("menuitem", { name: "Settings" })).toHaveAttribute(
        "href",
        "/settings",
      );
    });

    it("sends the wiki link out to the docs site", async () => {
      const user = userEvent.setup();
      renderNav();
      await user.click(screen.getByRole("button", { name: "My workspace" }));

      expect(screen.getByRole("menuitem", { name: /Docs/ })).toHaveAttribute(
        "href",
        "https://www.printstash.org/docs/",
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

  describe("the current destination", () => {
    it("marks Favorites when the filter is active", () => {
      renderNav({ at: "/?favorites=true" });

      expect(screen.getByRole("link", { name: "Favorites" })).toHaveAttribute(
        "aria-current",
        "page",
      );
      expect(screen.getByRole("link", { name: "Collections" })).not.toHaveAttribute("aria-current");
    });

    it("keeps Pending reachable from a nested inbox route", () => {
      renderNav({ at: "/inbox/41" });

      expect(screen.getByRole("link", { name: "Pending" })).toHaveAttribute("href", "/inbox");
    });

    it("does not mark the vault on a different route", () => {
      renderNav({ at: "/inbox" });

      expect(screen.getByRole("link", { name: "Collections" })).not.toHaveAttribute("aria-current");
    });

    it("returns the brand link to the remembered collection", () => {
      localStorage.setItem("printstash.last.collection", "Calibration/Boats");
      renderNav({ at: "/settings" });

      expect(screen.getByRole("link", { name: "PrintStash" })).toHaveAttribute(
        "href",
        "/?c=Calibration%2FBoats",
      );
    });

    it("returns focus to the account button after Escape", async () => {
      const user = userEvent.setup();
      renderNav();
      const account = screen.getByRole("button", { name: "My workspace" });
      await user.click(account);

      await user.keyboard("{Escape}");

      await waitFor(() => expect(screen.queryByRole("menu")).not.toBeInTheDocument());
      expect(account).toHaveFocus();
    });
  });

  describe("keyboard help", () => {
    it("lists the supported library shortcut", async () => {
      const user = userEvent.setup();
      renderNav();

      await user.click(screen.getByRole("button", { name: "Keyboard shortcuts" }));

      expect(screen.getByRole("dialog", { name: "Keyboard shortcuts" })).toHaveTextContent(
        "Search panel",
      );
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

      await user.click(screen.getByRole("button", { name: "My workspace" }));
      await user.click(screen.getByRole("menuitem", { name: "Log out" }));

      expect(logout).toHaveBeenCalled();
    });

    it("offers a way in to a signed-out visitor", () => {
      renderNav({ auth: adminSession({ user: null }) });

      expect(screen.getByRole("link", { name: /Sign in/i })).toHaveAttribute("href", "/login");
    });
  });
});
