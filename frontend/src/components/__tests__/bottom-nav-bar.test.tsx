/*
 * The mobile navigation bar — the only way around the app on a phone.
 *
 * Five slots, and the fifth is always "More", so the destination list has to be
 * split rather than crushed: the first four visible entries become tabs and the
 * rest fold into a sheet. Which four depends on the user, because the admin-only
 * entries are filtered out first — so a non-admin's bar is a different bar, and a
 * regression there either hides a destination from everybody or shows a
 * non-admin a page that 403s.
 *
 * Active state is a prefix match, which is what lets an inbox *detail* page keep
 * the Pending tab lit. It also has to reach through the fold: when the active
 * destination lives in the sheet, "More" is what carries the highlight, or the
 * user appears to have navigated out of the section they are in.
 *
 * The labels come from the i18n context rather than from a mount-time read. The
 * shell never remounts — the locale is switched from a settings page inside it —
 * so a bar that cached its labels stays in the old language until a reload, on
 * the one component that is always on screen.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BottomNavBar } from "@/components/bottom-nav-bar";
import { LocaleToggle } from "@/components/locale-toggle";
import { AuthContext, type AuthState } from "@/lib/auth-context";
import { I18nProvider } from "@/lib/i18n";
import { usePathname } from "@/lib/navigation";

const fetchMock = vi.fn<typeof fetch>();

function session(overrides: Partial<AuthState["user"]> = {}): AuthState {
  return {
    user: { id: 1, username: "admin", email: null, is_superuser: true, ...overrides },
    loading: false,
    login: async () => {},
    logout: async () => {},
    refresh: async () => {},
  };
}

/** Renders the current route, so a click's effect is observable. */
function CurrentPath() {
  return <output data-testid="current-path">{usePathname()}</output>;
}

function renderNav({
  at = "/",
  auth = session(),
  extra = null,
}: {
  at?: string;
  auth?: AuthState;
  extra?: React.ReactNode;
} = {}) {
  return render(
    <MemoryRouter initialEntries={[at]}>
      <AuthContext.Provider value={auth}>
        <I18nProvider>
          {extra}
          <BottomNavBar />
          <CurrentPath />
        </I18nProvider>
      </AuthContext.Provider>
    </MemoryRouter>,
  );
}

/** The pending-import rows the bar's badge is counted from. */
function pendingImports(...states: string[]) {
  return states.map((state, index) => ({ id: index + 1, state }));
}

function respondWith(rows: unknown[]) {
  fetchMock.mockResolvedValue(
    new Response(JSON.stringify(rows), {
      status: 200,
      headers: { "content-type": "application/json" },
    }),
  );
}

beforeEach(() => {
  localStorage.setItem("printstash.locale", "en");
  fetchMock.mockReset();
  respondWith([]);
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("BottomNavBar", () => {
  describe("destinations", () => {
    it("shows four tabs plus More", () => {
      renderNav();

      expect(screen.getAllByRole("link").map((tab) => tab.textContent)).toEqual([
        "Vault",
        "Pending",
        "Printers",
        "Profiles",
      ]);
      expect(screen.getByRole("button", { name: "More" })).toBeInTheDocument();
    });

    it("drops the admin-only destinations for an ordinary user", () => {
      renderNav({ auth: session({ is_superuser: false }) });

      expect(screen.getAllByRole("link").map((tab) => tab.textContent)).toEqual([
        "Vault",
        "Pending",
        "Profiles",
        "Settings",
      ]);
    });

    it("links Pending at the inbox route", () => {
      renderNav();

      expect(screen.getByRole("link", { name: "Pending" })).toHaveAttribute("href", "/inbox");
    });

    it("navigates when a tab is tapped", async () => {
      const user = userEvent.setup();
      renderNav();

      await user.click(screen.getByRole("link", { name: "Pending" }));

      expect(screen.getByTestId("current-path")).toHaveTextContent("/inbox");
    });
  });

  describe("active destination", () => {
    it("marks the tab for the current route", () => {
      renderNav({ at: "/inbox" });

      expect(screen.getByRole("link", { name: "Pending" })).toHaveAttribute("aria-current", "page");
    });

    it("marks Pending for a nested inbox route", () => {
      renderNav({ at: "/inbox/41" });

      expect(screen.getByRole("link", { name: "Pending" })).toHaveAttribute("aria-current", "page");
    });

    it("keeps Vault unmarked away from the root", () => {
      // Vault is the one entry whose href is a prefix of every route, so it is
      // matched exactly. Prefix-matching it would light it up on every page.
      renderNav({ at: "/inbox" });

      expect(screen.getByRole("link", { name: "Vault" })).not.toHaveAttribute("aria-current");
    });

    it("marks More when the active destination folded into the sheet", () => {
      renderNav({ at: "/statistics" });

      expect(screen.getByRole("button", { name: "More" })).toHaveAttribute("aria-current", "page");
    });
  });

  describe("pending-import badge", () => {
    it("counts the imports still waiting", async () => {
      respondWith(pendingImports("pending", "needs_input"));

      renderNav();

      expect(await screen.findByText("2")).toBeInTheDocument();
    });

    it("ignores the ones the user dismissed", async () => {
      respondWith(pendingImports("pending", "dismissed", "dismissed"));

      renderNav();

      expect(await screen.findByText("1")).toBeInTheDocument();
    });

    it("caps the count so the badge cannot grow the tab", async () => {
      respondWith(pendingImports(...Array.from({ length: 120 }, () => "pending")));

      renderNav();

      expect(await screen.findByText("99+")).toBeInTheDocument();
    });

    it("shows no badge when nothing is waiting", async () => {
      renderNav();

      await waitFor(() => expect(fetchMock).toHaveBeenCalled());
      expect(screen.queryByText("0")).toBeNull();
    });

    it("survives a failed count rather than blanking the bar", async () => {
      fetchMock.mockRejectedValue(new Error("offline"));

      renderNav();

      await waitFor(() => expect(fetchMock).toHaveBeenCalled());
      expect(screen.getByRole("link", { name: "Vault" })).toBeInTheDocument();
    });
  });

  describe("the More sheet", () => {
    it("opens on tap", async () => {
      const user = userEvent.setup();
      renderNav();

      await user.click(screen.getByRole("button", { name: "More" }));

      expect(screen.getByRole("dialog", { name: "More" })).toBeInTheDocument();
    });

    it("holds the destinations that did not fit", async () => {
      const user = userEvent.setup();
      renderNav();

      await user.click(screen.getByRole("button", { name: "More" }));

      const sheet = screen.getByRole("dialog", { name: "More" });
      expect(
        within(sheet)
          .getAllByRole("link")
          .map((link) => link.textContent),
      ).toEqual(["Stats", "Settings", "Wiki"]);
    });

    it("sends an external destination to a plain link", async () => {
      // The wiki lives on another origin, so it must not go through the router.
      const user = userEvent.setup();
      renderNav();

      await user.click(screen.getByRole("button", { name: "More" }));

      expect(screen.getByRole("link", { name: "Wiki" })).toHaveAttribute(
        "href",
        "https://xiao-villamor.github.io/PrintStash/",
      );
    });

    it("closes when the user dismisses it", async () => {
      const user = userEvent.setup();
      renderNav();
      await user.click(screen.getByRole("button", { name: "More" }));

      await user.click(screen.getByRole("button", { name: "Close" }));

      await waitFor(() => expect(screen.queryByRole("dialog", { name: "More" })).toBeNull());
    });

    it("closes when a destination inside it is chosen", async () => {
      const user = userEvent.setup();
      renderNav();
      await user.click(screen.getByRole("button", { name: "More" }));

      await user.click(screen.getByRole("link", { name: "Settings" }));

      expect(screen.getByTestId("current-path")).toHaveTextContent("/settings");
    });

    it("offers the signed-in user's name", async () => {
      const user = userEvent.setup();
      renderNav();

      await user.click(screen.getByRole("button", { name: "More" }));

      expect(screen.getByRole("dialog", { name: "More" })).toHaveTextContent("admin");
    });

    it("signs the user out", async () => {
      const logout = vi.fn<() => Promise<void>>().mockResolvedValue(undefined);
      const user = userEvent.setup();
      renderNav({ auth: { ...session(), logout } });
      await user.click(screen.getByRole("button", { name: "More" }));

      await user.click(screen.getByRole("button", { name: "Log out" }));

      expect(logout).toHaveBeenCalledTimes(1);
    });

    it("sends the signed-out user to the login page", async () => {
      const user = userEvent.setup();
      renderNav({ auth: { ...session(), logout: async () => {} } });
      await user.click(screen.getByRole("button", { name: "More" }));

      await user.click(screen.getByRole("button", { name: "Log out" }));

      await waitFor(() => expect(screen.getByTestId("current-path")).toHaveTextContent("/login"));
    });

    it("expands the task list on request", async () => {
      const user = userEvent.setup();
      renderNav();
      await user.click(screen.getByRole("button", { name: "More" }));

      await user.click(screen.getByRole("button", { name: /Tasks/ }));

      expect(screen.getByRole("button", { name: /Tasks/ })).toHaveAttribute(
        "aria-expanded",
        "true",
      );
    });
  });

  describe("localization", () => {
    it("relabels itself when the locale changes under it", async () => {
      const user = userEvent.setup();
      renderNav({ extra: <LocaleToggle /> });
      expect(screen.getByText("Vault")).toBeInTheDocument();

      await user.click(screen.getByRole("button", { name: /Language/ }));

      expect(screen.getByText("Bóveda")).toBeInTheDocument();
    });
  });
});
