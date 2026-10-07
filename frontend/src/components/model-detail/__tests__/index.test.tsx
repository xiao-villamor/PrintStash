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
 * Favouriting publishes the confirmed server state. A failed write must leave
 * the earlier star visible rather than claim something was saved.
 */

import "@testing-library/jest-dom/vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ModelDetail } from "@/components/model-detail";
import { queryKeys } from "@/lib/query-client";
import { aCollection } from "@/test-support/factories";
import { collectionTreeRoutes } from "@/test-support/collection-tree";
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
    tags: [],
    ...over,
  };
}

function aModel(over: Partial<ModelRead> = {}): ModelRead {
  return {
    edit_epoch: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    edit_version: 1,
    id: 1,
    name: "Benchy",
    slug: "benchy",
    hash: "h".repeat(16),
    collection: "parts",
    collection_id: 1,
    collection_label: "Parts",
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
    seed: [[queryKeys.tags, []], [queryKeys.printers, []], ...seed],
    routes: {
      "GET /api/v1/models/1": json(model),
      "GET /api/v1/models/1/print-jobs": json([]),
      "GET /api/v1/models/1/printer-files": json([]),
      "GET /api/v1/models/1/provenance": json({ edit_version: 1, sources: [] }),
      "GET /api/v1/models/1/shares": json([]),
      "GET /api/v1/printers": json([]),
      ...collectionTreeRoutes([aCollection()]),
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
  describe("remote Model ownership", () => {
    it("shows a refreshed authorized Model", async () => {
      const { client } = renderDetail();
      await screen.findByText("Benchy");

      act(() =>
        client.setQueryData(queryKeys.model(1), aModel({ name: "Updated Model", edit_version: 4 })),
      );

      expect(await screen.findByText("Updated Model")).toBeVisible();
      expect(screen.queryByText("Benchy")).toBeNull();
    });

    it("preserves the editing base across a background refresh", async () => {
      const user = userEvent.setup();
      const headers: string[] = [];
      const { client } = renderDetail({
        routes: {
          "PATCH /api/v1/models/1": (_url, init) => {
            headers.push(new Headers(init?.headers).get("If-Match") ?? "missing");
            return json({ detail: "edit_conflict" }, 412);
          },
        },
      });
      await openEdit(user);
      await user.clear(screen.getByPlaceholderText("Model name"));
      await user.type(screen.getByPlaceholderText("Model name"), "My draft");

      act(() =>
        client.setQueryData(
          queryKeys.model(1),
          aModel({
            name: "Remote edit",
            edit_epoch: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            edit_version: 4,
          }),
        ),
      );
      await user.click(screen.getByRole("button", { name: "Save" }));

      expect(headers).toEqual(['"model-1-eaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-v1"']);
      expect(screen.getByPlaceholderText("Model name")).toHaveValue("My draft");
    });

    it("keeps a confirmed edit after an older read finishes", async () => {
      const user = userEvent.setup();
      let release!: (response: Response) => void;
      const oldRead = new Promise<Response>((resolve) => {
        release = resolve;
      });
      const { client, requests } = renderDetail({
        routes: {
          "GET /api/v1/models/1": () => oldRead,
          "PATCH /api/v1/models/1": json(aModel({ name: "Confirmed edit", edit_version: 4 })),
        },
      });
      await openEdit(user);
      await user.clear(screen.getByPlaceholderText("Model name"));
      await user.type(screen.getByPlaceholderText("Model name"), "Confirmed edit");
      void client.invalidateQueries({ queryKey: queryKeys.model(1), exact: true });
      await waitFor(() =>
        expect(requests().some((r) => r.url === "/api/v1/models/1" && r.method === "GET")).toBe(
          true,
        ),
      );

      await user.click(screen.getByRole("button", { name: "Save" }));
      expect(await screen.findByText("Confirmed edit")).toBeVisible();
      await act(async () => {
        release(json(aModel()));
        await oldRead;
      });

      expect(screen.getByText("Confirmed edit")).toBeVisible();
      expect(client.getQueryData<ModelRead>(queryKeys.model(1))?.name).toBe("Confirmed edit");
    });
  });

  describe("collection navigation", () => {
    it("shows the detail label without loading the whole collection tree", async () => {
      const { requests } = renderDetail({
        model: aModel({ collection: "parts/brackets", collection_label: "Parts/Brackets" }),
      });

      expect(await screen.findByText("Parts/Brackets")).toBeVisible();
      expect(
        requests().filter(
          (call) => new URL(call.url, "http://test").pathname === "/api/v1/collections",
        ),
      ).toEqual([]);
    });

    it("offers editable destinations through collection search", async () => {
      const user = userEvent.setup();
      const { requests } = renderDetail();
      await openEdit(user);

      await user.click(screen.getByRole("button", { name: "Parts" }));
      expect(await screen.findByRole("option", { name: /Parts/ })).toBeVisible();
      expect(
        requests().some(
          (call) => new URL(call.url, "http://test").searchParams.get("min_role") === "edit",
        ),
      ).toBe(true);
    });

    it.each([
      { label: "containing collection", collection: "parts", expected: "/?c=parts" },
      {
        label: "nested collection with special characters",
        collection: "Wall mounts/Backpack & gear #2",
        expected: "/?c=Wall%20mounts%2FBackpack%20%26%20gear%20%232",
      },
      { label: "root for an uncollected model", collection: null, expected: "/" },
    ])("returns to $label", async ({ collection, expected }) => {
      renderDetail({ model: aModel({ collection }) });

      expect(await screen.findByRole("link", { name: "Back" })).toHaveAttribute("href", expected);
    });
  });

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

    it("offers the original DXF download without promising a drawing preview", async () => {
      const user = userEvent.setup();
      renderDetail({
        model: aModel({ files: [aFile({ file_type: "dxf", original_filename: "plate.dxf" })] }),
      });

      expect(
        await screen.findByText(
          "DXF preview is not supported yet. Download the original file below.",
        ),
      ).toBeInTheDocument();
      await user.click(screen.getByRole("tab", { name: /Files/ }));
      expect(await screen.findByText("plate.dxf")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /Download/ })).toBeInTheDocument();
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
    it("preserves a Model draft on edit conflict", async () => {
      const user = userEvent.setup();
      const app = renderDetail({
        routes: {
          "PATCH /api/v1/models/1": json({ detail: "edit_conflict" }, 412),
          "GET /api/v1/models/1": json(aModel({ name: "Other editor", edit_version: 7 })),
        },
      });
      await openEdit(user);
      await user.clear(screen.getByPlaceholderText("Model name"));
      await user.type(screen.getByPlaceholderText("Model name"), "My draft");

      await user.click(screen.getByRole("button", { name: "Save" }));

      expect(
        await screen.findByText(
          "This item changed elsewhere. Your draft has been kept. Review the latest version before saving again.",
        ),
      ).toBeVisible();
      expect(screen.getByPlaceholderText("Model name")).toHaveValue("My draft");
      expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
      await user.click(screen.getByRole("button", { name: "Review latest version" }));
      expect(await screen.findByRole("dialog", { name: "Latest saved version" })).toHaveTextContent(
        "Other editor",
      );
      expect(app.requestsWithMethod("PATCH")).toHaveLength(1);
    });

    it("saves a retained Model draft only after explicit version review", async () => {
      const user = userEvent.setup();
      const app = renderDetail({
        routes: {
          "PATCH /api/v1/models/1": json({ detail: "edit_conflict" }, 412),
          "GET /api/v1/models/1": json(
            aModel({
              name: "Other editor",
              edit_epoch: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
              edit_version: 7,
            }),
          ),
        },
      });
      await openEdit(user);
      await user.clear(screen.getByPlaceholderText("Model name"));
      await user.type(screen.getByPlaceholderText("Model name"), "My draft");
      await user.click(screen.getByRole("button", { name: "Save" }));
      await user.click(await screen.findByRole("button", { name: "Review latest version" }));
      await screen.findByRole("dialog", { name: "Latest saved version" });
      let version: string | null = null;
      app.route({
        "PATCH /api/v1/models/1": (_url, init) => {
          version = new Headers(init?.headers).get("If-Match");
          return json(
            aModel({
              name: "My draft",
              edit_epoch: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
              edit_version: 8,
            }),
          );
        },
      });

      await user.click(screen.getByRole("button", { name: "Save my draft against this version" }));

      expect(await screen.findByText("My draft")).toBeVisible();
      expect(version).toBe('"model-1-ebbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-v7"');
      expect(app.requestsWithMethod("PATCH")).toHaveLength(2);
    });

    it("replaces a Model draft with the reviewed version", async () => {
      const user = userEvent.setup();
      const app = renderDetail({
        routes: {
          "PATCH /api/v1/models/1": json({ detail: "edit_conflict" }, 412),
          "GET /api/v1/models/1": json(aModel({ name: "Other editor", edit_version: 7 })),
        },
      });
      await openEdit(user);
      await user.click(screen.getByRole("button", { name: "Save" }));
      await user.click(await screen.findByRole("button", { name: "Review latest version" }));
      await screen.findByRole("dialog", { name: "Latest saved version" });

      await user.click(screen.getByRole("button", { name: "Use latest version" }));

      expect(screen.getByPlaceholderText("Model name")).toHaveValue("Other editor");
      expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
      expect(app.requestsWithMethod("PATCH")).toHaveLength(1);
    });

    it("keeps a conflicted Model draft when review is dismissed", async () => {
      const user = userEvent.setup();
      renderDetail({
        routes: {
          "PATCH /api/v1/models/1": json({ detail: "edit_conflict" }, 412),
          "GET /api/v1/models/1": json(aModel({ name: "Other editor", edit_version: 7 })),
        },
      });
      await openEdit(user);
      await user.click(screen.getByRole("button", { name: "Save" }));
      await user.click(await screen.findByRole("button", { name: "Review latest version" }));
      await screen.findByRole("dialog", { name: "Latest saved version" });

      await user.click(screen.getByRole("button", { name: "Keep my draft" }));

      expect(screen.getByPlaceholderText("Model name")).toHaveValue("Benchy");
      expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    });

    it("requires another review when a reviewed version becomes stale", async () => {
      const user = userEvent.setup();
      const app = renderDetail({
        routes: {
          "PATCH /api/v1/models/1": json({ detail: "edit_conflict" }, 412),
          "GET /api/v1/models/1": json(aModel({ name: "Other editor", edit_version: 7 })),
        },
      });
      await openEdit(user);
      await user.click(screen.getByRole("button", { name: "Save" }));
      await user.click(await screen.findByRole("button", { name: "Review latest version" }));
      await screen.findByRole("dialog", { name: "Latest saved version" });

      await user.click(screen.getByRole("button", { name: "Save my draft against this version" }));

      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(screen.getByPlaceholderText("Model name")).toHaveValue("Benchy");
      expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
      expect(app.requestsWithMethod("PATCH")).toHaveLength(2);
    });

    it("prevents retry after review reveals lost edit access", async () => {
      const user = userEvent.setup();
      const app = renderDetail({
        routes: {
          "PATCH /api/v1/models/1": json({ detail: "edit_conflict" }, 412),
          "GET /api/v1/models/1": json(aModel({ effective_role: "view", edit_version: 7 })),
        },
      });
      await openEdit(user);
      await user.click(screen.getByRole("button", { name: "Save" }));
      await user.click(await screen.findByRole("button", { name: "Review latest version" }));
      await screen.findByRole("dialog", { name: "Latest saved version" });

      expect(
        screen.getByRole("button", { name: "Save my draft against this version" }),
      ).toBeDisabled();
      expect(app.requestsWithMethod("PATCH")).toHaveLength(1);
    });

    it("offers direct tag editing in the header", async () => {
      renderDetail();

      expect(await screen.findByRole("button", { name: "Add tags to Benchy" })).toBeVisible();
    });

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
        routes: { "PATCH /api/v1/models/1": json(aModel({ name: "Benchy v2", edit_version: 2 })) },
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

    it("refuses an empty model name", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderDetail();
      await openEdit(user);

      await user.clear(screen.getByPlaceholderText("Model name"));

      expect(screen.getByPlaceholderText("Model name")).toHaveAttribute("aria-invalid", "true");
      expect(screen.getByText("Model name is required.")).toBeVisible();
      expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
      expect(requestsWithMethod("PATCH")).toHaveLength(0);
    });

    it("shows the saved model without a reload", async () => {
      // The page owns the model it was handed, so a save that only reaches the
      // server leaves the user looking at the old values.
      const user = userEvent.setup();
      renderDetail({
        routes: { "PATCH /api/v1/models/1": json(aModel({ name: "Benchy v2", edit_version: 2 })) },
      });
      await openEdit(user);
      const name = screen.getByPlaceholderText("Model name");
      await user.clear(name);
      await user.type(name, "Benchy v2");

      await user.click(screen.getByRole("button", { name: "Save" }));

      expect(await screen.findByText("Benchy v2")).toBeInTheDocument();
    });

    it("clears a description the user emptied", async () => {
      const user = userEvent.setup();
      const app = renderDetail({
        model: aModel({ description: "Previous description" }),
        routes: {
          "PATCH /api/v1/models/1": json(aModel({ description: null, edit_version: 2 })),
        },
      });
      await openEdit(user);

      await user.clear(screen.getByPlaceholderText("Optional description"));
      await user.click(screen.getByRole("button", { name: "Save" }));

      expect(JSON.parse(app.requestsWithMethod("PATCH")[0].body)).toHaveProperty(
        "description",
        null,
      );
    });

    it("clears all Model tags", async () => {
      const user = userEvent.setup();
      const app = renderDetail({
        model: aModel({ tags: ["old-tag"] }),
        routes: {
          "PATCH /api/v1/models/1": json(aModel({ tags: [], edit_version: 2 })),
        },
      });
      await openEdit(user);

      await user.click(screen.getByPlaceholderText("Search or create — press Enter"));
      await user.keyboard("{Backspace}");
      await user.click(screen.getByRole("button", { name: "Save" }));

      expect(JSON.parse(app.requestsWithMethod("PATCH")[0].body)).toHaveProperty("tags", []);
    });

    it.each(["transport", "server"] as const)(
      "blocks another Model write after an unconfirmed response (%s)",
      async (failure) => {
        const user = userEvent.setup();
        const failures = {
          transport: () => {
            throw new TypeError("Failed to fetch");
          },
          server: () => json({ detail: "unavailable" }, 503),
        };
        const app = renderDetail({ routes: { "PATCH /api/v1/models/1": failures[failure] } });
        await openEdit(user);

        await user.click(screen.getByRole("button", { name: "Save" }));

        expect(
          await screen.findByText(
            "The save was not confirmed. Review the latest version before retrying.",
          ),
        ).toBeVisible();
        expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
        expect(screen.getByPlaceholderText("Model name")).toHaveValue("Benchy");
        expect(app.requestsWithMethod("PATCH")).toHaveLength(1);
      },
    );

    it("keeps an unconfirmed draft blocked when review fails", async () => {
      const user = userEvent.setup();
      const app = renderDetail({
        routes: {
          "PATCH /api/v1/models/1": json({ detail: "unavailable" }, 503),
          "GET /api/v1/models/1": json({ detail: "unavailable" }, 503),
        },
      });
      await openEdit(user);
      await user.click(screen.getByRole("button", { name: "Save" }));

      await user.click(await screen.findByRole("button", { name: "Review latest version" }));
      await waitFor(() =>
        expect(screen.getByRole("button", { name: "Review latest version" })).not.toBeDisabled(),
      );

      expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
      expect(screen.getByPlaceholderText("Model name")).toHaveValue("Benchy");
      expect(app.requestsWithMethod("PATCH")).toHaveLength(1);
    });

    it("adopts the reviewed result of an unconfirmed save without rewriting", async () => {
      const user = userEvent.setup();
      const app = renderDetail({
        routes: {
          "PATCH /api/v1/models/1": json({ detail: "unavailable" }, 503),
          "GET /api/v1/models/1": json(aModel({ name: "Saved draft", edit_version: 7 })),
        },
      });
      await openEdit(user);
      await user.clear(screen.getByPlaceholderText("Model name"));
      await user.type(screen.getByPlaceholderText("Model name"), "Saved draft");
      await user.click(screen.getByRole("button", { name: "Save" }));
      await user.click(await screen.findByRole("button", { name: "Review latest version" }));
      await screen.findByRole("dialog", { name: "Latest saved version" });

      await user.click(screen.getByRole("button", { name: "Use latest version" }));

      expect(app.requestsWithMethod("PATCH")).toHaveLength(1);
      expect(app.client.getQueryData<ModelRead>(queryKeys.model(1))).toMatchObject({
        name: "Saved draft",
        edit_epoch: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        edit_version: 7,
      });
      expect(screen.getByPlaceholderText("Model name")).toHaveValue("Saved draft");
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

    it("sends the root collection when the user chooses None", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderDetail({
        routes: {
          "PATCH /api/v1/models/1": json(aModel({ collection: null, collection_id: null })),
        },
      });
      await openEdit(user);

      await user.click(screen.getByRole("button", { name: "Parts" }));
      await user.click(screen.getByRole("option", { name: "None" }));
      await user.click(screen.getByRole("button", { name: "Save" }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("PATCH").at(-1)?.body ?? "{}")).toMatchObject({
          collection: "",
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

      await user.click(await screen.findByRole("button", { name: /^Add$/ }));

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
