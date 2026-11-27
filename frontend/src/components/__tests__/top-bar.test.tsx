/** The topbar exposes route context and launches workbench actions. */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CollectionsPaneHost, CollectionsPaneProvider } from "@/components/collections-pane";
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

      await waitFor(() =>
        expect(
          app.requests().filter((request) => request.url.includes("/collections/search")),
        ).toHaveLength(1),
      );
      expect(app.requests().filter((r) => r.url.includes("/collections/search"))).toHaveLength(1);
    });

    it("leaves Upload to the Library header on the Library itself", () => {
      renderTopBar({ at: "/", auth: memberSession() });

      expect(screen.queryByRole("button", { name: "Upload" })).not.toBeInTheDocument();
    });

    it("keeps Upload in the top bar on other screens", () => {
      renderTopBar({ at: "/printers", auth: memberSession() });

      expect(screen.getByRole("button", { name: "Upload" })).toBeVisible();
    });
  });

  describe("search", () => {
    it("opens Library search from a secondary screen on mobile", async () => {
      const user = userEvent.setup();
      const app = renderApp(
        <CollectionsPaneProvider>
          <TopBar />
          <CollectionsPaneHost />
          <CurrentLocation />
        </CollectionsPaneProvider>,
        {
          at: "/settings",
          matchesMedia: (query) => query === "(prefers-reduced-motion: reduce)",
        },
      );

      expect(app.requests().some((request) => request.url.includes("/search/status"))).toBe(false);
      await user.click(screen.getByRole("button", { name: "Search" }));

      await user.type(
        await screen.findByRole("searchbox", { name: "Search library" }),
        "benchy{Enter}",
      );
      await waitFor(() =>
        expect(screen.getByTestId("location")).toHaveTextContent("/?v=models&q=benchy"),
      );
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
