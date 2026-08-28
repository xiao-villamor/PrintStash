/*
 * The model page's fallback path: fetching the model the server could not.
 *
 * Reads require a session, and the server render has no token — so on a cold
 * load the page arrives with no model and has to fetch one with the browser's
 * stored credentials. The three ways that can fail are three different
 * instructions, and collapsing them wastes the user's time: a 404 means stop
 * looking, a 401/403 means ask for access, and anything else means try again.
 * "Couldn't load this model" for a deleted model sends somebody hunting for a
 * model that no longer exists.
 *
 * Nothing is rendered while the fetch is in flight, because an empty detail page
 * is indistinguishable from a model with nothing in it.
 */

import "@testing-library/jest-dom/vitest";
import { screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ModelDetailClientView } from "@/components/model-detail/client-view";
import { json, renderApp, type RenderAppOptions } from "@/test-support/render";
import type { ModelRead } from "@/types";

const FROZEN_NOW = "2026-01-01T00:00:00Z";

function aModel(over: Partial<ModelRead> = {}): ModelRead {
  return {
    id: 1,
    name: "Benchy",
    slug: "benchy",
    hash: "a".repeat(64),
    collection: null,
    collection_id: null,
    description: null,
    source_url: null,
    effective_role: "admin",
    tags: [],
    thumbnail_url: null,
    created_at: FROZEN_NOW,
    updated_at: FROZEN_NOW,
    files: [],
    starred: false,
    ...over,
  };
}

function renderView(options: RenderAppOptions & { initialModel?: ModelRead | null } = {}) {
  const { initialModel = null, routes = {}, ...rest } = options;
  return renderApp(<ModelDetailClientView id={1} initialModel={initialModel} />, {
    routes: {
      "GET /api/v1/models/1/print-jobs": json([]),
      "GET /api/v1/models/1/printer-files": json([]),
      "GET /api/v1/printers": json([]),
      "GET /api/v1/collections": json([]),
      "GET /api/v1/tags": json([]),
      ...routes,
    },
    ...rest,
  });
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ModelDetailClientView", () => {
  describe("when the server already had the model", () => {
    it("renders it without asking again", async () => {
      // The server fetched it; fetching a second time is a wasted round trip on
      // the slowest part of the page.
      const { requests } = renderView({ initialModel: aModel() });

      expect(await screen.findByText("Benchy")).toBeInTheDocument();
      expect(requests().some((call) => call.url === "/api/v1/models/1")).toBe(false);
    });
  });

  describe("when the server could not", () => {
    it("shows nothing while it fetches", () => {
      // An empty detail page is indistinguishable from a model with nothing in
      // it.
      renderView({
        routes: { "GET /api/v1/models/1": () => json(aModel()) },
      });

      expect(screen.queryByText("Benchy")).toBeNull();
    });

    it("fetches the model with the browser's own session", async () => {
      renderView({ routes: { "GET /api/v1/models/1": json(aModel()) } });

      expect(await screen.findByText("Benchy")).toBeInTheDocument();
    });
  });

  describe("when the fetch fails", () => {
    it("says a deleted model is gone rather than broken", async () => {
      // "Couldn't load this model" sends somebody hunting for a model that no
      // longer exists.
      renderView({ routes: { "GET /api/v1/models/1": json({ detail: "not_found" }, 404) } });

      expect(await screen.findByText("Model not found")).toBeInTheDocument();
    });

    it("explains that a deleted model is not coming back", async () => {
      renderView({ routes: { "GET /api/v1/models/1": json({ detail: "not_found" }, 404) } });

      expect(
        await screen.findByText("This model doesn’t exist or has been deleted."),
      ).toBeInTheDocument();
    });

    it("asks for a session when there is none", async () => {
      renderView({
        routes: { "GET /api/v1/models/1": json({ detail: "not_authenticated" }, 401) },
      });

      expect(await screen.findByText("Sign in to view this model")).toBeInTheDocument();
    });

    it("names access, not identity, when the session is not enough", async () => {
      // A 403 is a collection the user is not in; telling them to sign in sends
      // them round a loop that changes nothing.
      renderView({ routes: { "GET /api/v1/models/1": json({ detail: "forbidden" }, 403) } });

      expect(
        await screen.findByText("This model lives in a collection you need access to."),
      ).toBeInTheDocument();
    });

    it("invites a retry for a server error", async () => {
      renderView({ routes: { "GET /api/v1/models/1": json({ detail: "boom" }, 500) } });

      expect(await screen.findByText("Couldn’t load this model")).toBeInTheDocument();
    });
  });
});
