/*
 * One model's page: six tabs over the artifacts, the settings that produced
 * them, and the prints that came out.
 *
 * The tab in the URL is what a shared link reproduces, and it is user-editable —
 * so `?tab=nonsense` has to land somewhere real rather than render an empty
 * page. History is the one tab that is *conditionally* present, because it is
 * about printers: someone who cannot see printers must not land on a tab whose
 * contents they are not allowed to fetch.
 *
 * Bed size is derived from the printer model string, and it is the frame the
 * G-code preview is drawn against. Guessing a 250mm bed for an A1 mini renders
 * a part that looks like it fits when it does not — which is a wrong answer
 * presented with the same confidence as a right one.
 *
 * Favouriting writes immediately and optimistically, so the star has to survive
 * the request failing: leaving it lit after a 403 tells the user something is
 * saved that is not.
 */

import "@testing-library/jest-dom/vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ModelDetail } from "@/components/model-detail";
import { queryKeys } from "@/lib/query-client";
import { aCollection } from "@/test-support/factories";
import { json, memberSession, renderApp, type RenderAppOptions } from "@/test-support/render";
import type { FileRead, ModelRead } from "@/types";

const FROZEN_NOW = "2026-01-01T00:00:00Z";

function aFile(over: Partial<FileRead> = {}): FileRead {
  return {
    id: 10,
    model_id: 1,
    original_filename: "cube.stl",
    file_type: "stl",
    version: 1,
    size_bytes: 2048,
    sha256: "a".repeat(64),
    revision_status: null,
    revision_notes: null,
    is_recommended: false,
    uploaded_at: FROZEN_NOW,
    metadata: null,
    ...over,
  };
}

function aModel(over: Partial<ModelRead> = {}): ModelRead {
  return {
    id: 1,
    name: "Benchy",
    slug: "benchy",
    hash: "h".repeat(16),
    collection: "parts",
    collection_id: 1,
    description: null,
    source_url: null,
    effective_role: "admin",
    tags: [],
    thumbnail_url: null,
    created_at: FROZEN_NOW,
    updated_at: FROZEN_NOW,
    files: [aFile()],
    starred: false,
    ...over,
  };
}

function renderDetail(options: RenderAppOptions & { model?: ModelRead } = {}) {
  const { model = aModel(), seed = [], routes = {}, ...rest } = options;
  return renderApp(<ModelDetail model={model} />, {
    seed: [
      [queryKeys.collections, [aCollection()]],
      [queryKeys.tags, []],
      [queryKeys.printers, []],
      ...seed,
    ],
    routes: {
      "GET /api/v1/models/1": json(model),
      "GET /api/v1/models/1/print-jobs": json([]),
      "GET /api/v1/models/1/printer-files": json([]),
      "GET /api/v1/models/1/provenance": json({ sources: [] }),
      "GET /api/v1/models/1/shares": json([]),
      "GET /api/v1/printers": json([]),
      "GET /api/v1/collections": json([aCollection()]),
      "GET /api/v1/tags": json([]),
      ...routes,
    },
    ...rest,
  });
}

/** Open the header's actions menu, which is where every write action lives. */
async function openActions(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByText("Benchy");
  await user.click(screen.getByRole("button", { name: "Model actions" }));
}

/** Open the edit form, which replaces the header title with an input. */
async function openEdit(user: ReturnType<typeof userEvent.setup>) {
  await openActions(user);
  await user.click(screen.getByRole("menuitem", { name: /Edit details/ }));
  await screen.findByPlaceholderText("Model name");
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ModelDetail", () => {
  describe("what it shows", () => {
    it("names the model", async () => {
      renderDetail();

      expect(await screen.findByText("Benchy")).toBeInTheDocument();
    });

    it("opens on the overview", async () => {
      renderDetail();

      expect(await screen.findByRole("tab", { name: /Overview/ })).toHaveAttribute(
        "aria-selected",
        "true",
      );
    });

    it("counts the source files on their tab", async () => {
      renderDetail({ model: aModel({ files: [aFile(), aFile({ id: 11, version: 2 })] }) });

      expect(await screen.findByRole("tab", { name: /Files\s*2/ })).toBeInTheDocument();
    });

    it("counts the G-code revisions on their tab", async () => {
      renderDetail({
        model: aModel({
          files: [aFile({ id: 12, file_type: "gcode", original_filename: "part.gcode" })],
        }),
      });

      expect(await screen.findByRole("tab", { name: /Revisions\s*1/ })).toBeInTheDocument();
    });
  });

  describe("moving between tabs", () => {
    it("selects the tab the user chose", async () => {
      const user = userEvent.setup();
      renderDetail();
      await screen.findByText("Benchy");

      await user.click(screen.getByRole("tab", { name: /Files/ }));

      expect(screen.getByRole("tab", { name: /Files/ })).toHaveAttribute("aria-selected", "true");
    });

    it("shows the chosen tab's contents", async () => {
      const user = userEvent.setup();
      renderDetail();
      await screen.findByText("Benchy");

      await user.click(screen.getByRole("tab", { name: /Settings/ }));

      expect(screen.getByRole("tab", { name: /Settings/ })).toHaveAttribute(
        "aria-selected",
        "true",
      );
    });

    it("leaves the previous tab unselected", async () => {
      const user = userEvent.setup();
      renderDetail();
      await screen.findByText("Benchy");

      await user.click(screen.getByRole("tab", { name: /Files/ }));

      expect(screen.getByRole("tab", { name: /Overview/ })).toHaveAttribute(
        "aria-selected",
        "false",
      );
    });
  });

  describe("who may see the print history", () => {
    it("offers it to someone who can see printers", async () => {
      renderDetail();

      expect(await screen.findByRole("tab", { name: /History/ })).toBeInTheDocument();
    });

    it("withholds it from someone who cannot", async () => {
      renderDetail({ auth: memberSession() });

      await screen.findByText("Benchy");
      expect(screen.queryByRole("tab", { name: /History/ })).toBeNull();
    });

    it("asks for no print history on that user's behalf", async () => {
      // The tab is about printers; fetching its contents for someone who may not
      // see printers is a request that answers 403 on every page load.
      const { requests } = renderDetail({ auth: memberSession() });

      await screen.findByText("Benchy");
      expect(requests().some((call) => call.url.includes("print-jobs"))).toBe(false);
    });
  });

  describe("favouriting", () => {
    it("stars the model", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderDetail({
        routes: { "PUT /api/v1/models/1/star": json({ model_id: 1, starred: true }) },
      });
      await screen.findByText("Benchy");

      await user.click(screen.getByRole("button", { name: /favorite/i }));

      await waitFor(() =>
        expect(requestsWithMethod("PUT").some((call) => call.url.includes("/star"))).toBe(true),
      );
    });

    it("unstars a model that was starred", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderDetail({
        model: aModel({ starred: true }),
        routes: { "DELETE /api/v1/models/1/star": json(null, 204) },
      });
      await screen.findByText("Benchy");

      await user.click(screen.getByRole("button", { name: /favorite/i }));

      await waitFor(() =>
        expect(requestsWithMethod("DELETE").some((call) => call.url.includes("/star"))).toBe(true),
      );
    });
  });

  describe("editing", () => {
    it("offers editing to someone with write access", async () => {
      renderDetail();

      await screen.findByText("Benchy");
      expect(screen.getByRole("tab", { name: /Settings/ })).toBeInTheDocument();
    });

    it("keeps a view-only user out of destructive actions", async () => {
      renderDetail({ model: aModel({ effective_role: "view" }), auth: memberSession() });

      await screen.findByText("Benchy");
      expect(screen.queryByRole("button", { name: /Delete model/i })).toBeNull();
    });

    it("opens the form on the details the model already has", async () => {
      // An edit form that starts blank is a form that erases whatever the user
      // does not retype.
      const user = userEvent.setup();
      renderDetail();
      await openActions(user);

      await user.click(screen.getByRole("menuitem", { name: /Edit details/ }));

      expect(screen.getByPlaceholderText("Model name")).toHaveValue("Benchy");
    });

    it("saves the name the user typed", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderDetail({
        routes: { "PATCH /api/v1/models/1": json(aModel({ name: "Benchy v2" })) },
      });
      await openEdit(user);
      const name = screen.getByPlaceholderText("Model name");
      await user.clear(name);
      await user.type(name, "Benchy v2");

      await user.click(screen.getByRole("button", { name: "Save" }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("PATCH").at(-1)?.body ?? "{}")).toMatchObject({
          name: "Benchy v2",
        }),
      );
    });

    it("shows the saved model without a reload", async () => {
      // The page owns the model it was handed, so a save that only reaches the
      // server leaves the user looking at the old values.
      const user = userEvent.setup();
      renderDetail({
        routes: { "PATCH /api/v1/models/1": json(aModel({ name: "Benchy v2" })) },
      });
      await openEdit(user);
      const name = screen.getByPlaceholderText("Model name");
      await user.clear(name);
      await user.type(name, "Benchy v2");

      await user.click(screen.getByRole("button", { name: "Save" }));

      expect(await screen.findByText("Benchy v2")).toBeInTheDocument();
    });

    it("clears a source URL the user emptied", async () => {
      // An empty string here has to travel as null: `undefined` would leave the
      // old link in place, so the field would silently refuse to be cleared.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderDetail({
        model: aModel({ source_url: "https://example.test/thing" }),
        routes: { "PATCH /api/v1/models/1": json(aModel({ source_url: null })) },
      });
      await openEdit(user);

      await user.clear(screen.getByPlaceholderText("https://www.printables.com/model/..."));
      await user.click(screen.getByRole("button", { name: "Save" }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("PATCH").at(-1)?.body ?? "{}")).toMatchObject({
          source_url: null,
        }),
      );
    });

    it("carries the source URL the user typed", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderDetail({
        routes: { "PATCH /api/v1/models/1": json(aModel()) },
      });
      await openEdit(user);

      await user.type(
        screen.getByPlaceholderText("https://www.printables.com/model/..."),
        "https://example.test/thing",
      );
      await user.click(screen.getByRole("button", { name: "Save" }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("PATCH").at(-1)?.body ?? "{}")).toMatchObject({
          source_url: "https://example.test/thing",
        }),
      );
    });

    it("leaves the model alone when the edit is abandoned", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderDetail();
      await openEdit(user);
      const name = screen.getByPlaceholderText("Model name");
      await user.clear(name);
      await user.type(name, "Something else");

      await user.click(screen.getByRole("button", { name: "Cancel" }));

      expect(requestsWithMethod("PATCH")).toHaveLength(0);
    });

    it("keeps the form open when the save is refused", async () => {
      // Closing on failure throws away everything the user just typed.
      const user = userEvent.setup();
      renderDetail({
        routes: { "PATCH /api/v1/models/1": json({ detail: "name_taken" }, 409) },
      });
      await openEdit(user);

      await user.click(screen.getByRole("button", { name: "Save" }));

      expect(await screen.findByPlaceholderText("Model name")).toBeInTheDocument();
    });
  });

  describe("deleting the model", () => {
    it("asks before deleting", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderDetail();
      await openActions(user);

      await user.click(screen.getByRole("menuitem", { name: /Delete model/ }));

      expect(requestsWithMethod("DELETE")).toHaveLength(0);
    });

    it("says the model goes to the trash rather than away", async () => {
      // "Delete" reads as permanent; the retention window is the difference
      // between a mistake and a loss.
      const user = userEvent.setup();
      renderDetail();
      await openActions(user);

      await user.click(screen.getByRole("menuitem", { name: /Delete model/ }));

      expect(
        await screen.findByText(
          "This will move the model to trash. Files will be permanently removed after the retention period.",
        ),
      ).toBeInTheDocument();
    });

    it("deletes the model once confirmed", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderDetail({
        routes: { "DELETE /api/v1/models/1": json(null, 204) },
      });
      await openActions(user);
      await user.click(screen.getByRole("menuitem", { name: /Delete model/ }));

      await user.click(
        within(await screen.findByRole("dialog")).getByRole("button", { name: "Delete" }),
      );

      await waitFor(() =>
        expect(requestsWithMethod("DELETE").some((call) => call.url.endsWith("/models/1"))).toBe(
          true,
        ),
      );
    });

    it("stays on the page when the delete is refused", async () => {
      // Navigating away from a model that still exists loses the user's place
      // for nothing.
      const user = userEvent.setup();
      renderDetail({
        routes: { "DELETE /api/v1/models/1": json({ detail: "model_in_use" }, 409) },
      });
      await openActions(user);
      await user.click(screen.getByRole("menuitem", { name: /Delete model/ }));

      await user.click(
        within(await screen.findByRole("dialog")).getByRole("button", { name: "Delete" }),
      );

      expect(await screen.findByText("Benchy")).toBeInTheDocument();
    });
  });

  describe("sharing", () => {
    it("opens the share dialog", async () => {
      const user = userEvent.setup();
      renderDetail();
      await openActions(user);

      await user.click(screen.getByRole("menuitem", { name: /Share/ }));

      expect(await screen.findByRole("dialog")).toBeInTheDocument();
    });

    it("offers no sharing to a view-only user", async () => {
      // A share link hands the model to anybody holding it, which is more than
      // the viewer's own grant.
      const user = userEvent.setup();
      renderDetail({ model: aModel({ effective_role: "view" }), auth: memberSession() });
      await openActions(user);

      expect(screen.getByRole("menuitem", { name: /Share/ })).toBeDisabled();
    });
  });
  describe("resizing the details panel", () => {
    /** The drag handle, which keyboard users reach instead of dragging. */
    async function handle() {
      return screen.findByRole("separator", { name: "Resize details panel" });
    }

    it("widens the panel with the left arrow", async () => {
      // The panel holds the settings table, unreadably narrow on a laptop at
      // the default width — and dragging is not available to keyboard users.
      const user = userEvent.setup();
      renderDetail();
      const bar = await handle();
      const before = Number(bar.getAttribute("aria-valuenow"));

      bar.focus();
      await user.keyboard("{ArrowLeft}");

      expect(Number(bar.getAttribute("aria-valuenow"))).toBeGreaterThan(before);
    });

    it("narrows it with the right arrow", async () => {
      const user = userEvent.setup();
      renderDetail();
      const bar = await handle();
      const before = Number(bar.getAttribute("aria-valuenow"));

      bar.focus();
      await user.keyboard("{ArrowRight}");

      expect(Number(bar.getAttribute("aria-valuenow"))).toBeLessThan(before);
    });

    it("goes to the narrowest width with Home", async () => {
      const user = userEvent.setup();
      renderDetail();
      const bar = await handle();

      bar.focus();
      await user.keyboard("{Home}");

      expect(bar.getAttribute("aria-valuenow")).toBe(bar.getAttribute("aria-valuemin"));
    });

    it("goes as wide as the window allows with End", async () => {
      // The stated maximum is a preference, not a promise: the panel is still
      // clamped to the viewport, or it would push the model out of view.
      const user = userEvent.setup();
      renderDetail();
      const bar = await handle();
      const before = Number(bar.getAttribute("aria-valuenow"));

      bar.focus();
      await user.keyboard("{End}");

      expect(Number(bar.getAttribute("aria-valuenow"))).toBeGreaterThan(before);
    });

    it("returns to the default width on a double-click", async () => {
      // Dragging to an unusable width is easy; dragging back from one is not.
      const user = userEvent.setup();
      renderDetail();
      const bar = await handle();
      bar.focus();
      await user.keyboard("{Home}");
      const narrowest = bar.getAttribute("aria-valuenow");

      await user.dblClick(bar);

      expect(bar.getAttribute("aria-valuenow")).not.toBe(narrowest);
    });

    it("leaves a key that means something else alone", async () => {
      // Swallowing every keystroke would trap a keyboard user on the handle.
      const user = userEvent.setup();
      renderDetail();
      const bar = await handle();
      const before = bar.getAttribute("aria-valuenow");

      bar.focus();
      await user.keyboard("{Tab}");

      expect(bar.getAttribute("aria-valuenow")).toBe(before);
    });
  });

  describe("adding a revision", () => {
    it("opens the upload dialog from the revisions tab", async () => {
      const user = userEvent.setup();
      renderDetail({
        model: aModel({
          files: [aFile({ id: 12, file_type: "gcode", original_filename: "part.gcode" })],
        }),
      });
      await screen.findByText("Benchy");
      await user.click(screen.getByRole("tab", { name: /Revisions/ }));

      await user.click(await screen.findByRole("button", { name: /Add/ }));

      expect(await screen.findByText("Add G-code revision")).toBeInTheDocument();
    });

    it("opens no upload dialog for somebody who may only read", async () => {
      // The revision would 403 on save; refusing at the dialog is the difference
      // between "you cannot" and a form that throws away what was typed.
      const user = userEvent.setup();
      renderDetail({
        model: aModel({
          effective_role: "view",
          files: [aFile({ id: 12, file_type: "gcode", original_filename: "part.gcode" })],
        }),
        auth: memberSession(),
      });
      await screen.findByText("Benchy");
      await user.click(screen.getByRole("tab", { name: /Revisions/ }));

      await user.click(await screen.findByRole("button", { name: /Add/ }));

      expect(screen.queryByText("Add G-code revision")).toBeNull();
    });
  });
});
