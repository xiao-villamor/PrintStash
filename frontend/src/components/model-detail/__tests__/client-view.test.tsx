/** The SPA route shares one authorized Model read and distinguishes recovery from lost access. */

import "@testing-library/jest-dom/vitest";
import { act, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import userEvent from "@testing-library/user-event";
import { queryKeys } from "@/lib/query-client";
import { ModelDetailClientView } from "@/components/model-detail/client-view";
import { json, renderApp, type RenderAppOptions } from "@/test-support/render";
import type { ModelRead } from "@/types";

const FROZEN_NOW = "2026-01-01T00:00:00Z";

function aModel(over: Partial<ModelRead> = {}): ModelRead {
  return {
    edit_epoch: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    edit_version: 1,
    id: 1,
    name: "Benchy",
    slug: "benchy",
    hash: "a".repeat(64),
    collection: null,
    collection_id: null,
    collection_label: null,
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
  it("retries a failed initial read without navigation", async () => {
    const user = userEvent.setup();
    const view = renderView({ routes: { "GET /api/v1/models/1": json({ detail: "boom" }, 500) } });
    await screen.findByText("Couldn’t load this model");
    view.route({ "GET /api/v1/models/1": json(aModel()) });

    await user.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByText("Benchy")).toBeVisible();
  });

  it("hides a cached Model after access is denied", async () => {
    const { client } = renderView({
      initialModel: aModel(),
      routes: { "GET /api/v1/models/1": json({ detail: "forbidden" }, 403) },
    });
    await screen.findByText("Benchy");

    await act(async () => {
      await client.invalidateQueries({ queryKey: queryKeys.model(1), exact: true });
    });

    expect(
      await screen.findByText("This model lives in a collection you need access to."),
    ).toBeVisible();
    expect(screen.queryByText("Benchy")).toBeNull();
  });

  it("retains displayed Model during a transient read failure", async () => {
    const { client } = renderView({
      initialModel: aModel(),
      routes: { "GET /api/v1/models/1": json({ detail: "boom" }, 500) },
    });
    await screen.findByText("Benchy");

    await act(async () => {
      await client.invalidateQueries({ queryKey: queryKeys.model(1), exact: true });
    });

    expect(screen.getByText("Benchy")).toBeVisible();
    expect(await screen.findByRole("button", { name: "Retry" })).toBeVisible();
  });

  it("retires a pending read when the route closes", async () => {
    let requestSignal: AbortSignal | null | undefined;
    const view = renderView({
      routes: {
        "GET /api/v1/models/1": (_url, init) => {
          requestSignal = init?.signal;
          return new Promise<Response>(() => {});
        },
      },
    });
    await waitFor(() => expect(requestSignal).toBeDefined());

    view.unmount();

    expect(requestSignal?.aborted).toBe(true);
  });

  describe("when navigation already provided the Model", () => {
    it("renders it without asking again", async () => {
      // A fresh authorized snapshot does not need another round trip.
      const { requests } = renderView({ initialModel: aModel() });

      expect(await screen.findByText("Benchy")).toBeInTheDocument();
      expect(requests().some((call) => call.url === "/api/v1/models/1")).toBe(false);
    });

    it("loads a member Model when navigation changes the route id", async () => {
      const view = renderView({
        initialModel: aModel(),
        routes: {
          "GET /api/v1/models/2": json(aModel({ id: 2, name: "Handle", slug: "handle" })),
          "GET /api/v1/models/2/print-jobs": json([]),
          "GET /api/v1/models/2/printer-files": json([]),
        },
      });

      expect(await screen.findByText("Benchy")).toBeInTheDocument();
      view.rerender(<ModelDetailClientView key={2} id={2} initialModel={null} />);

      expect(await screen.findByText("Handle")).toBeInTheDocument();
      expect(screen.queryByText("Benchy")).toBeNull();
    });
  });

  describe("when navigation needs the Model", () => {
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
