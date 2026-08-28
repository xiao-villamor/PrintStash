/*
 * The frame every page renders inside, and the last gate in front of it.
 *
 * Two jobs, both invisible when they work. The first is the auth boundary: a
 * visitor with no session is sent to the login form rather than shown the
 * chrome, because a nav rail full of links that will all bounce them reads as
 * the app being broken.
 *
 * The second is RBAC over routes. A member reaching /printers or /statistics is
 * put back on the vault: those pages are admin-only, and hiding the nav links is
 * not enough — a bookmark, a shared URL and the back button all arrive without
 * ever touching the nav.
 *
 * Login and setup are deliberately chromeless. They are the two screens where
 * there is no session yet, so the nav would advertise pages the visitor cannot
 * open.
 *
 * The document title is the app's only presence in a tab strip full of them, so
 * it tracks the route rather than staying "PrintStash" forever.
 */

import "@testing-library/jest-dom/vitest";
import { screen, waitFor } from "@testing-library/react";
import { useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "@/components/app-shell";
import {
  adminSession,
  json,
  memberSession,
  renderApp,
  type RenderAppOptions,
} from "@/test-support/render";

/**
 * The shell redirects by navigating, and a `MemoryRouter` never touches
 * `window.location` — so where it sent the user is only observable from inside
 * the router.
 */
function WhereAmI() {
  return <p>at {useLocation().pathname}</p>;
}

function renderShell(options: RenderAppOptions = {}) {
  const { routes = {}, ...rest } = options;
  return renderApp(
    <AppShell>
      <WhereAmI />
    </AppShell>,
    {
      routes: {
        "GET /api/v1/inbox": json([]),
        "GET /api/v1/models/stats": json({ model_count: 0, file_count: 0, total_size_bytes: 0 }),
        ...routes,
      },
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

describe("AppShell", () => {
  describe("a signed-in user", () => {
    it("renders the page inside the frame", async () => {
      renderShell();

      expect(await screen.findByText("at /")).toBeInTheDocument();
    });

    it("puts the chrome around it", async () => {
      renderShell();

      await screen.findByText("at /");
      expect(screen.getAllByRole("navigation")).not.toHaveLength(0);
    });

    it("leaves them where they asked to be", async () => {
      renderShell({ at: "/settings" });

      expect(await screen.findByText("at /settings")).toBeInTheDocument();
    });
  });

  describe("a visitor with no session", () => {
    it("sends them to the login form", async () => {
      renderShell({ auth: adminSession({ user: null }) });

      expect(await screen.findByText("at /login")).toBeInTheDocument();
    });

    it("shows them no chrome on the way", async () => {
      // A nav rail full of links that will all bounce them is worse than the
      // bare form.
      renderShell({ auth: adminSession({ user: null }) });

      await screen.findByText("at /login");
      expect(screen.queryByRole("navigation")).toBeNull();
    });

    it("leaves the setup wizard chromeless too", async () => {
      // There is no session yet, so there is nothing for a nav rail to link to.
      renderShell({ at: "/setup", auth: adminSession({ user: null }) });

      await screen.findByText("at /setup");
      expect(screen.queryByRole("navigation")).toBeNull();
    });

    it("does not bounce a visitor already on the login form", async () => {
      renderShell({ at: "/login", auth: adminSession({ user: null }) });

      expect(await screen.findByText("at /login")).toBeInTheDocument();
    });
  });

  describe("an admin-only route", () => {
    it("lets an admin onto the printers page", async () => {
      renderShell({ at: "/printers" });

      expect(await screen.findByText("at /printers")).toBeInTheDocument();
    });

    it("puts a member back on the vault", async () => {
      // Hiding the nav link is not enough: a bookmark, a shared URL and the
      // back button all arrive without ever touching the nav.
      renderShell({ at: "/printers", auth: memberSession() });

      expect(await screen.findByText("at /")).toBeInTheDocument();
    });

    it("keeps a member off the statistics page too", async () => {
      renderShell({ at: "/statistics", auth: memberSession() });

      expect(await screen.findByText("at /")).toBeInTheDocument();
    });

    it("leaves a member on the pages that are theirs", async () => {
      renderShell({ at: "/settings", auth: memberSession() });

      expect(await screen.findByText("at /settings")).toBeInTheDocument();
    });
  });

  describe("the browser tab", () => {
    it("names the vault", async () => {
      renderShell();

      await screen.findByText("at /");
      expect(document.title).toContain("PrintStash");
    });

    it("names the page the user is on", async () => {
      // A tab strip of identical titles is a tab strip nobody can navigate.
      renderShell({ at: "/settings" });

      await waitFor(() => expect(document.title).toContain("Settings"));
    });

    it("names a model page after the model section", async () => {
      renderShell({ at: "/models/1" });

      await waitFor(() => expect(document.title).toContain("Model"));
    });
  });
});
