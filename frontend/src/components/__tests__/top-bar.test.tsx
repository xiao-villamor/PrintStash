/*
 * The desktop header: the logo, the model search, and the account menu.
 *
 * Two things here are stateful in ways a render test is the only way to catch.
 *
 * The search box is owned by the URL, not by the input. A `?q=` in the address
 * bar — from a back navigation, a bookmark, or a shared link — has to land in the
 * box, and typing has to end up back in the URL after a debounce. Break either
 * direction and the results stop matching what the user sees they searched for.
 * Search on other pages must open the existing results route with the entered term.
 *
 * The account menu is the desktop half of the navigation, and it carries the same
 * prefix-matched active state and admin-only filtering as the mobile bar. A route
 * added to one shell and not the other is invisible on the platform nobody tested
 * on.
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TopBar } from "@/components/top-bar";
import { type AuthState } from "@/lib/auth-context";
import { useCollectionSearch } from "@/lib/queries";
import { queryKeys } from "@/lib/query-client";
import { aCollection, aModel } from "@/test-support/factories";
import { collectionTreeRoutes } from "@/test-support/collection-tree";
import { json, memberSession, renderApp, type RenderAppOptions } from "@/test-support/render";
import { searchStatus } from "@/test-support/search";
import { usePathname, useSearchParams } from "@/lib/navigation";

const fetchMock = vi.fn<typeof fetch>();

function session(overrides: Partial<AuthState> = {}): AuthState {
  return {
    user: { id: 1, username: "admin", email: null, is_superuser: true },
    loading: false,
    login: async () => {},
    logout: async () => {},
    refresh: async () => {},
    ...overrides,
  };
}

/** Renders the current location, so a navigation or a URL write is observable. */
function CurrentLocation() {
  const search = useSearchParams().toString();
  return <output data-testid="location">{`${usePathname()}${search ? `?${search}` : ""}`}</output>;
}

function renderTopBar({
  at = "/",
  auth = session(),
  matchesMedia,
  routes = {},
  seed,
}: Pick<RenderAppOptions, "at" | "auth" | "matchesMedia" | "routes" | "seed"> = {}) {
  return renderApp(
    <>
      <TopBar />
      <CurrentLocation />
    </>,
    {
      at,
      auth,
      matchesMedia,
      seed,
      routes: {
        "GET /api/v1/search/status": json(searchStatus()),
        "GET /api/v1/jobs": json([]),
        ...routes,
      },
    },
  );
}

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem("printstash.locale", "en");
  fetchMock.mockReset();
  fetchMock.mockResolvedValue(
    new Response("[]", { status: 200, headers: { "content-type": "application/json" } }),
  );
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("TopBar", () => {
  it("reflects the Library inspector owner state", async () => {
    renderTopBar();
    fireEvent(
      window,
      new CustomEvent("printstash:library-inspector-state", {
        detail: { available: true, open: true },
      }),
    );
    expect(screen.getByRole("button", { name: "Show or hide details" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    fireEvent(
      window,
      new CustomEvent("printstash:library-inspector-state", {
        detail: { available: false, open: false },
      }),
    );
    expect(screen.queryByRole("button", { name: "Show or hide details" })).not.toBeInTheDocument();
  });
  it("ignores malformed inspector events", () => {
    renderTopBar();
    fireEvent(
      window,
      new CustomEvent("printstash:library-inspector-state", {
        detail: { available: "yes", open: true },
      }),
    );
    expect(screen.queryByRole("button", { name: "Show or hide details" })).not.toBeInTheDocument();
  });
  it("labels an unknown page in the breadcrumb", () => {
    renderTopBar({ at: "/page-that-does-not-exist" });

    const breadcrumb = screen.getByRole("navigation", { name: "Breadcrumbs" });
    expect(within(breadcrumb).getByText("Page not found")).toHaveAttribute("aria-current", "page");
  });

  describe("model breadcrumb", () => {
    it.each([
      { collection: null, expected: null },
      { collection: "parts/brackets", expected: "parts/brackets" },
      { collection: "parts/A & B #1", expected: "parts/A & B #1" },
    ])("returns to the model's containing collection: $collection", ({ collection, expected }) => {
      const app = renderTopBar({
        at: "/models/1",
        seed: [[queryKeys.model(1), aModel({ name: "Garden bracket", collection })]],
      });

      expect(
        within(screen.getByRole("navigation", { name: "Breadcrumbs" })).getByText("Garden bracket"),
      ).toBeVisible();
      const link = screen.getByRole("link", { name: "Back" });
      const target = new URL(link.getAttribute("href") ?? "", "http://test");
      expect(target.pathname).toBe("/");
      expect(target.searchParams.get("c")).toBe(expected);
      expect(app.requests().some((request) => request.url.includes("/api/v1/models/1"))).toBe(
        false,
      );
    });

    it("preserves a safe Library origin", () => {
      renderTopBar({
        at: "/models/1?return=%2F%3Fq%3Dplanter",
        seed: [[queryKeys.model(1), aModel({ collection: "parts" })]],
      });

      expect(screen.getByRole("link", { name: "Back" })).toHaveAttribute("href", "/?q=planter");
    });

    it("keeps the return link on narrow screens", () => {
      renderTopBar({
        at: "/models/1",
        matchesMedia: () => false,
        seed: [[queryKeys.model(1), aModel({ collection: "parts/brackets" })]],
      });

      const target = new URL(
        screen.getByRole("link", { name: "Back" }).getAttribute("href") ?? "",
        "http://test",
      );
      expect(target.searchParams.get("c")).toBe("parts/brackets");
    });

    it("keeps the return link for a trailing-slash model URL", () => {
      renderTopBar({
        at: "/models/1/",
        matchesMedia: () => false,
        seed: [[queryKeys.model(1), aModel({ collection: "parts" })]],
      });

      const target = new URL(
        screen.getByRole("link", { name: "Back" }).getAttribute("href") ?? "",
        "http://test",
      );
      expect(target.searchParams.get("c")).toBe("parts");
    });
  });

  describe("global upload", () => {
    it("keeps Upload disabled for a member with no editable collection", async () => {
      const app = renderTopBar({
        at: "/settings",
        auth: memberSession(),
        routes: collectionTreeRoutes([aCollection({ effective_role: "view" })]),
      });

      await waitFor(() =>
        expect(app.requests().some((request) => request.url.includes("/collections/search"))).toBe(
          true,
        ),
      );
      expect(screen.getByRole("button", { name: "Upload" })).toBeDisabled();
    });

    it("opens the existing upload intent for a member with edit access", async () => {
      const user = userEvent.setup();
      renderTopBar({
        at: "/settings",
        auth: memberSession(),
        routes: collectionTreeRoutes([aCollection({ effective_role: "edit" })]),
      });

      const upload = screen.getByRole("button", { name: "Upload" });
      await waitFor(() => expect(upload).toBeEnabled());
      await user.click(upload);
      await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("upload=1"));
    });

    it("shares the Library permission probe rather than issuing a second request", async () => {
      function LibraryProbe() {
        useCollectionSearch("", "edit");
        return null;
      }
      const app = renderApp(
        <>
          <TopBar />
          <LibraryProbe />
        </>,
        {
          at: "/",
          auth: memberSession(),
          routes: {
            ...collectionTreeRoutes([aCollection({ effective_role: "edit" })]),
            "GET /api/v1/search/status": json(searchStatus()),
          },
        },
      );

      await waitFor(() => expect(screen.getByRole("button", { name: "Upload" })).toBeEnabled());
      expect(
        app.requests().filter((request) => request.url.includes("/collections/search")),
      ).toHaveLength(1);
    });
  });

  describe("search", () => {
    it("shows the search box on the vault", () => {
      renderTopBar();

      expect(screen.getByRole("searchbox", { name: "Search library" })).toBeInTheDocument();
    });

    it("starts a search from another page", async () => {
      const user = userEvent.setup();
      renderTopBar({ at: "/settings" });
      const box = screen.getByRole("searchbox", { name: "Search library" });

      await user.type(box, "benchy{Enter}");

      await waitFor(() =>
        expect(screen.getByTestId("location")).toHaveTextContent("/search?q=benchy"),
      );
    });

    it("does not read search capability on another page before the field is used", async () => {
      const app = renderTopBar({ at: "/settings" });

      await waitFor(() =>
        expect(screen.getByRole("searchbox", { name: "Search library" })).toBeVisible(),
      );
      expect(app.requests().some((request) => request.url.includes("/search/status"))).toBe(false);
    });

    it("starts from the term already in the URL", () => {
      renderTopBar({ at: "/?q=benchy" });

      expect(screen.getByRole("searchbox", { name: "Search library" })).toHaveValue("benchy");
    });

    it("writes what the user typed back to the URL", async () => {
      const user = userEvent.setup();
      renderTopBar();

      await user.type(screen.getByRole("searchbox", { name: "Search library" }), "benchy");

      await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("/?q=benchy"));
    });

    it("offers a clear affordance once there is a term", () => {
      renderTopBar({ at: "/?q=benchy" });

      expect(screen.getByRole("button", { name: "Clear search" })).toBeInTheDocument();
    });

    it("drops the term from the URL when the search is cleared", async () => {
      const user = userEvent.setup();
      renderTopBar({ at: "/?q=benchy" });

      await user.click(screen.getByRole("button", { name: "Clear search" }));

      await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("/"));
      expect(screen.getByRole("searchbox", { name: "Search library" })).toHaveValue("");
    });

    it("focuses the box when the user presses slash", async () => {
      const user = userEvent.setup();
      renderTopBar();

      await user.keyboard("/");

      expect(screen.getByRole("searchbox", { name: "Search library" })).toHaveFocus();
    });

    it("leaves slash alone while the user is typing in the box", async () => {
      // Otherwise the shortcut swallows the character in the one field it is
      // meant to serve.
      const user = userEvent.setup();
      renderTopBar();
      const box = screen.getByRole("searchbox", { name: "Search library" });
      await user.click(box);

      await user.keyboard("a/b");

      expect(box).toHaveValue("a/b");
    });
  });

  it("keeps Similar models out of the desktop header", () => {
    renderTopBar();
    expect(screen.queryByRole("link", { name: "Similar models" })).toBeNull();
  });

  describe("home link", () => {
    it("returns to the vault root by default", () => {
      renderTopBar({ at: "/settings" });

      expect(
        within(screen.getByRole("navigation", { name: "Breadcrumbs" })).getByRole("link", {
          name: "PrintStash",
        }),
      ).toHaveAttribute("href", "/");
    });

    it("returns to the collection the user was last in", () => {
      // The logo is the way back to browsing, so dropping the remembered folder
      // means every trip through settings costs the user their place.
      localStorage.setItem("printstash.last.collection", "Calibration/Boats");

      renderTopBar({ at: "/settings" });

      expect(
        within(screen.getByRole("navigation", { name: "Breadcrumbs" })).getByRole("link", {
          name: "PrintStash",
        }),
      ).toHaveAttribute("href", "/?c=Calibration%2FBoats");
    });
  });

  describe("task tray", () => {
    it("opens the task list", async () => {
      const user = userEvent.setup();
      renderTopBar();

      await user.click(screen.getByRole("button", { name: "Notifications" }));

      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });
  });
});
