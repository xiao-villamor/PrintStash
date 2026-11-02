import { NO_PREVIEWS } from "@/types/previews";
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
import { useLocation, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ModelDetail } from "@/components/model-detail";
import { LibraryItemLink } from "@/features/library/navigation";
import { knownOrigin, useLibraryEntry } from "@/features/library/navigation-state";
import { queryKeys } from "@/lib/query-client";
import { aCollection, aFilePlate, aMetadata } from "@/test-support/factories";
import { similarityStatus } from "@/test-support/similarity";
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
    geometric_sha256: null,
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
    previews: NO_PREVIEWS,
    has_custom_cover: false,
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
    derivatives_revision: "0",
    ...over,
  };
}

function DetailLocation() {
  const location = useLocation();
  const navigate = useNavigate();
  return (
    <>
      <output aria-label="Current detail location">{location.pathname + location.search}</output>
      <button onClick={() => navigate("/models/1?tab=files")}>Open Files URL</button>
      <button onClick={() => navigate(-1)}>Previous detail</button>
    </>
  );
}

function LibraryOriginProbe() {
  const location = useLocation();
  const origin = useLibraryEntry("/?c=parts", location.pathname === "/");
  return (
    <>
      <LibraryItemLink href="/models/1" origin={origin}>
        Open model from library
      </LibraryItemLink>
      <output aria-label="Registered library origin">
        {knownOrigin(location.state)?.href ?? "none"}
      </output>
    </>
  );
}

function renderDetail(
  options: RenderAppOptions & { model?: ModelRead; libraryOrigin?: boolean } = {},
) {
  const { model = aModel(), seed = [], routes = {}, libraryOrigin = false, ...rest } = options;
  return renderApp(
    <>
      <ModelDetail model={model} />
      <DetailLocation />
      {libraryOrigin && <LibraryOriginProbe />}
    </>,
    {
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
        "GET /api/v1/similarity/status": json(similarityStatus()),
        "GET /api/v1/similarity/candidates": json({ items: [], next_cursor: null }),
        ...routes,
      },
      ...rest,
    },
  );
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
  describe("quick name editing", () => {
    it("keeps a single model heading click in read mode", async () => {
      const user = userEvent.setup();
      renderDetail();

      await user.click(await screen.findByRole("heading", { name: "Benchy", level: 1 }));

      expect(screen.queryByRole("textbox", { name: "Model name" })).toBeNull();
    });

    it("focuses the model name after a heading double-click", async () => {
      const user = userEvent.setup();
      renderDetail();

      await user.dblClick(await screen.findByRole("heading", { name: "Benchy", level: 1 }));

      const name = screen.getByRole("textbox", { name: "Model name" });
      expect(name).toHaveValue("Benchy");
      expect(name).toHaveFocus();
    });

    it("saves a name opened from the keyboard rename button", async () => {
      const user = userEvent.setup();
      const app = renderDetail({
        routes: { "PATCH /api/v1/models/1": json(aModel({ name: "New Benchy", edit_version: 2 })) },
      });
      const pencil = await screen.findByRole("button", { name: "Rename Benchy" });
      pencil.focus();
      await user.keyboard("{Enter}");
      const name = screen.getByRole("textbox", { name: "Model name" });
      await user.clear(name);
      await user.type(name, "New Benchy");
      await user.click(screen.getByRole("button", { name: "Save" }));

      expect(await screen.findByRole("heading", { name: "New Benchy", level: 1 })).toBeVisible();
      expect(JSON.parse(app.requestsWithMethod("PATCH")[0].body ?? "{}")).toEqual({
        name: "New Benchy",
      });
    });

    it("hides quick rename for a read-only model", async () => {
      const user = userEvent.setup();
      renderDetail({ model: aModel({ effective_role: "view" }), auth: memberSession() });

      await user.dblClick(await screen.findByRole("heading", { name: "Benchy", level: 1 }));

      expect(screen.queryByRole("button", { name: "Rename Benchy" })).toBeNull();
      expect(screen.queryByRole("textbox", { name: "Model name" })).toBeNull();
    });

    it("keeps header actions visible during title editing", async () => {
      const user = userEvent.setup();
      renderDetail();
      await user.click(await screen.findByRole("button", { name: "Rename Benchy" }));

      expect(screen.getByRole("dialog", { name: "Rename Benchy" })).not.toHaveAttribute(
        "aria-modal",
        "true",
      );
      expect(screen.getByRole("heading", { level: 1, name: "Benchy" })).toBeVisible();
      expect(screen.getByRole("button", { name: "Add tags to Benchy" })).toBeVisible();
      expect(screen.getByRole("button", { name: "Favorite" })).toBeVisible();
      expect(screen.getByRole("button", { name: "Model actions" })).toBeVisible();
      expect(screen.queryByPlaceholderText("Add a description…")).not.toBeInTheDocument();
    });

    it.each(["Cancel", "Escape", "outside"])(
      "discards a title draft through %s",
      async (dismiss) => {
        const user = userEvent.setup();
        const app = renderDetail();
        await user.click(await screen.findByRole("button", { name: "Rename Benchy" }));
        await user.clear(screen.getByRole("textbox", { name: "Model name" }));
        await user.type(screen.getByRole("textbox", { name: "Model name" }), "Discarded title");
        if (dismiss === "Cancel") await user.click(screen.getByRole("button", { name: "Cancel" }));
        else if (dismiss === "Escape") await user.keyboard("{Escape}");
        else await user.click(screen.getByRole("tab", { name: "Overview" }));

        await waitFor(() =>
          expect(screen.queryByRole("dialog", { name: "Rename Benchy" })).not.toBeInTheDocument(),
        );
        expect(screen.getByRole("heading", { level: 1, name: "Benchy" })).toBeVisible();
        expect(app.requestsWithMethod("PATCH")).toHaveLength(0);
        await user.click(screen.getByRole("button", { name: "Rename Benchy" }));
        expect(screen.getByRole("textbox", { name: "Model name" })).toHaveValue("Benchy");
      },
    );

    it("prevents an empty title save inside the editor", async () => {
      const user = userEvent.setup();
      const app = renderDetail();
      await user.click(await screen.findByRole("button", { name: "Rename Benchy" }));
      await user.clear(screen.getByRole("textbox", { name: "Model name" }));
      await user.keyboard("{Enter}");

      const editor = within(screen.getByRole("dialog", { name: "Rename Benchy" }));
      expect(editor.getByRole("alert")).toHaveTextContent("Model name is required.");
      expect(editor.getByRole("button", { name: "Save" })).toBeDisabled();
      expect(app.requestsWithMethod("PATCH")).toHaveLength(0);
    });

    it("retains the title editing base after background metadata changes", async () => {
      const user = userEvent.setup();
      let version: string | null = null;
      const app = renderDetail({
        routes: {
          "PATCH /api/v1/models/1": (_url, init) => {
            version = new Headers(init?.headers).get("If-Match");
            return json({ detail: "edit_conflict" }, 412);
          },
        },
      });
      await user.click(await screen.findByRole("button", { name: "Rename Benchy" }));
      await user.clear(screen.getByRole("textbox", { name: "Model name" }));
      await user.type(screen.getByRole("textbox", { name: "Model name" }), "My title");
      act(() =>
        app.client.setQueryData(
          queryKeys.model(1),
          aModel({
            name: "Remote title",
            tags: ["New tag"],
            description: "Remote description",
            edit_version: 4,
          }),
        ),
      );
      await user.click(screen.getByRole("button", { name: "Save" }));

      expect(await screen.findByRole("button", { name: "Review latest version" })).toBeVisible();
      expect(screen.getByRole("textbox", { name: "Model name" })).toHaveValue("My title");
      expect(version).toBe('"model-1-eaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-v1"');
      expect(JSON.parse(app.requestsWithMethod("PATCH")[0].body)).toEqual({ name: "My title" });
    });

    it("reviews a conflicting title inside its anchored editor", async () => {
      const user = userEvent.setup();
      const app = renderDetail({
        routes: {
          "PATCH /api/v1/models/1": json({ detail: "edit_conflict" }, 412),
          "GET /api/v1/models/1": json(aModel({ name: "Remote title", edit_version: 7 })),
        },
      });
      await user.click(await screen.findByRole("button", { name: "Rename Benchy" }));
      await user.clear(screen.getByRole("textbox", { name: "Model name" }));
      await user.type(screen.getByRole("textbox", { name: "Model name" }), "My title");
      await user.click(screen.getByRole("button", { name: "Save" }));
      await user.click(await screen.findByRole("button", { name: "Review latest version" }));

      expect(await screen.findByText("Remote title")).toBeVisible();
      expect(screen.getAllByRole("dialog")).toHaveLength(1);
      expect(screen.getByRole("dialog", { name: "Rename Benchy" })).toHaveTextContent(
        "Remote title",
      );
      expect(screen.getByRole("textbox", { name: "Model name" })).toHaveValue("My title");
      expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
      let version: string | null = null;
      app.route({
        "PATCH /api/v1/models/1": (_url, init) => {
          version = new Headers(init?.headers).get("If-Match");
          return json(aModel({ name: "My title", edit_version: 8 }));
        },
      });
      await user.click(screen.getByRole("button", { name: "Save my draft against this version" }));
      expect(await screen.findByRole("heading", { level: 1, name: "My title" })).toBeVisible();
      expect(version).toBe('"model-1-eaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-v7"');
      expect(JSON.parse(app.requestsWithMethod("PATCH")[1].body)).toEqual({ name: "My title" });
    });

    it("adopts a reviewed title without another write", async () => {
      const user = userEvent.setup();
      const app = renderDetail({
        routes: {
          "PATCH /api/v1/models/1": json({ detail: "unavailable" }, 503),
          "GET /api/v1/models/1": json(aModel({ name: "Saved title", edit_version: 7 })),
        },
      });
      await user.click(await screen.findByRole("button", { name: "Rename Benchy" }));
      await user.click(screen.getByRole("button", { name: "Save" }));
      await user.click(await screen.findByRole("button", { name: "Review latest version" }));
      await user.click(await screen.findByRole("button", { name: "Use latest version" }));

      expect(screen.getByRole("textbox", { name: "Model name" })).toHaveValue("Saved title");
      expect(screen.getByRole("heading", { level: 1, name: "Saved title" })).toBeVisible();
      expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
      expect(app.requestsWithMethod("PATCH")).toHaveLength(1);
    });
    it("preserves a pending title save through repeated edit gestures", async () => {
      const user = userEvent.setup();
      let release!: (response: Response) => void;
      const pending = new Promise<Response>((resolve) => {
        release = resolve;
      });
      const app = renderDetail({ routes: { "PATCH /api/v1/models/1": () => pending } });
      await user.click(await screen.findByRole("button", { name: "Rename Benchy" }));
      await user.clear(screen.getByRole("textbox", { name: "Model name" }));
      await user.type(screen.getByRole("textbox", { name: "Model name" }), "Saved title");
      await user.click(screen.getByRole("button", { name: "Save" }));
      await user.dblClick(screen.getByRole("heading", { level: 1, name: "Benchy" }));
      expect(screen.getByRole("textbox", { name: "Model name" })).toHaveValue("Saved title");
      expect(screen.getByRole("textbox", { name: "Model name" })).toBeDisabled();
      await user.click(screen.getByRole("button", { name: "Model actions" }));
      await user.click(screen.getByRole("menuitem", { name: "Edit details" }));
      expect(screen.getByRole("textbox", { name: "Model name" })).toBeDisabled();
      expect(app.requestsWithMethod("PATCH")).toHaveLength(1);
      await act(async () => {
        release(json(aModel({ name: "Saved title", edit_version: 2 })));
        await pending;
      });
      expect(await screen.findByRole("heading", { level: 1, name: "Saved title" })).toBeVisible();
    });

    it("retires the title editor when write access is revoked", async () => {
      const user = userEvent.setup();
      let release!: (response: Response) => void;
      const pending = new Promise<Response>((resolve) => {
        release = resolve;
      });
      const app = renderDetail({ routes: { "PATCH /api/v1/models/1": () => pending } });
      await user.click(await screen.findByRole("button", { name: "Rename Benchy" }));
      await user.click(screen.getByRole("button", { name: "Save" }));
      act(() =>
        app.client.setQueryData(
          queryKeys.model(1),
          aModel({ effective_role: "view", edit_version: 3 }),
        ),
      );
      await waitFor(() =>
        expect(screen.queryByRole("dialog", { name: "Rename Benchy" })).not.toBeInTheDocument(),
      );
      await act(async () => {
        release(json(aModel({ name: "Retired result", edit_version: 2 })));
        await pending;
      });
      expect(screen.getByRole("heading", { level: 1, name: "Benchy" })).toBeVisible();
      act(() =>
        app.client.setQueryData(
          queryKeys.model(1),
          aModel({ effective_role: "admin", edit_version: 4 }),
        ),
      );
      await user.click(await screen.findByRole("button", { name: "Rename Benchy" }));
      expect(screen.getByRole("textbox", { name: "Model name" })).toBeEnabled();
      expect(screen.getByRole("textbox", { name: "Model name" })).toHaveValue("Benchy");
    });

    it("ignores a late review after a new title draft begins", async () => {
      const user = userEvent.setup();
      let release!: (response: Response) => void;
      const pending = new Promise<Response>((resolve) => {
        release = resolve;
      });
      const app = renderDetail({
        routes: {
          "PATCH /api/v1/models/1": json({ detail: "edit_conflict" }, 412),
          "GET /api/v1/models/1": () => pending,
        },
      });
      await user.click(await screen.findByRole("button", { name: "Rename Benchy" }));
      await user.click(screen.getByRole("button", { name: "Save" }));
      await user.click(await screen.findByRole("button", { name: "Review latest version" }));
      await user.click(screen.getByRole("button", { name: "Cancel" }));
      await waitFor(() =>
        expect(screen.queryByRole("dialog", { name: "Rename Benchy" })).not.toBeInTheDocument(),
      );
      await user.click(screen.getByRole("button", { name: "Rename Benchy" }));
      await user.clear(screen.getByRole("textbox", { name: "Model name" }));
      await user.type(screen.getByRole("textbox", { name: "Model name" }), "New draft");
      await act(async () => {
        release(json(aModel({ name: "Late review", edit_version: 7 })));
        await pending;
      });
      expect(screen.queryByText("Late review")).not.toBeInTheDocument();
      expect(screen.getByRole("textbox", { name: "Model name" })).toHaveValue("New draft");
      expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
      expect(app.requestsWithMethod("PATCH")).toHaveLength(1);
    });

    it("keeps a refused title draft available for correction", async () => {
      const user = userEvent.setup();
      const app = renderDetail({
        routes: { "PATCH /api/v1/models/1": json({ detail: "duplicate_slug" }, 409) },
      });
      await user.click(await screen.findByRole("button", { name: "Rename Benchy" }));
      await user.clear(screen.getByRole("textbox", { name: "Model name" }));
      await user.type(screen.getByRole("textbox", { name: "Model name" }), "Taken name");
      await user.click(screen.getByRole("button", { name: "Save" }));

      expect(await screen.findByText("An item with that name already exists.")).toBeVisible();
      expect(screen.getByRole("textbox", { name: "Model name" })).toHaveValue("Taken name");
      expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
      expect(app.requestsWithMethod("PATCH")).toHaveLength(1);
    });
  });

  it("keeps mesh overlays hidden while the preview is unavailable", async () => {
    const user = userEvent.setup();
    renderDetail({
      model: aModel({
        files: [
          aFile(),
          aFile({
            id: 11,
            file_type: "gcode",
            original_filename: "cube.gcode",
            metadata: aMetadata({ bbox_x_mm: 10, bbox_y_mm: 20, bbox_z_mm: 30 }),
          }),
        ],
      }),
      routes: {
        "GET /api/v1/files/10/stl": json({ detail: "storage_authentication_failed" }, 422),
        "GET /api/v1/files/11/toolpath": json({ state: "queued" }, 202),
      },
    });
    expect(await screen.findByText(/provider rejected authentication/)).toBeVisible();
    expect(screen.queryByRole("button", { name: "Reset view" })).not.toBeInTheDocument();
    expect(screen.queryByText("10×20×30 mm")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "GCode" }));
    expect(screen.queryByRole("button", { name: "Reset view" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Zoom in" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Zoom out" })).not.toBeInTheDocument();
    expect(screen.queryByText("10×20×30 mm")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "3D" }));
    expect(await screen.findByText(/provider rejected authentication/)).toBeVisible();
    expect(screen.queryByRole("button", { name: "Reset view" })).not.toBeInTheDocument();
    expect(screen.queryByText("10×20×30 mm")).not.toBeInTheDocument();
  });

  it("omits mesh camera controls without a mesh", async () => {
    renderDetail({ model: aModel({ files: [] }) });
    await screen.findByRole("heading", { name: "Benchy" });
    expect(screen.queryByRole("button", { name: "Reset view" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Zoom in" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Zoom out" })).not.toBeInTheDocument();
  });

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
      void client.invalidateQueries({ queryKey: queryKeys.model(1) });
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

    it("opens a large mesh's 3D view without asking first", async () => {
      renderDetail({ model: aModel({ files: [aFile({ size_bytes: 48 * 1024 ** 2 })] }) });
      await screen.findByText("Benchy");

      expect(screen.queryByRole("button", { name: "Load 3D preview" })).not.toBeInTheDocument();
      expect(screen.queryByText(/Large 3D preview/)).not.toBeInTheDocument();
    });

    it("shows resin print facts on Overview", async () => {
      renderDetail({
        model: aModel({
          files: [
            aFile({
              file_type: "resin",
              original_filename: "slice.ctb",
              metadata: aMetadata({ layer_count: 120, exposure_time_s: 2.5, resin_volume_ml: 2.5 }),
            }),
          ],
        }),
      });
      expect(await screen.findByText("2.5 ml", { exact: true })).toBeVisible();
      expect(screen.getByText("120", { exact: true })).toBeVisible();
      expect(screen.getByText("2.5 s", { exact: true })).toBeVisible();
      expect(screen.queryByText(/No sliced G-code yet/)).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Send to printer" })).not.toBeInTheDocument();
    });

    it("offers each plate of a multi-plate 3MF", async () => {
      renderDetail({
        model: aModel({ files: [aFile({ file_type: "3mf", original_filename: "rack.3mf" })] }),
        routes: {
          "GET /api/v1/files/10/stl": json({ state: "queued" }, 202),
          "GET /api/v1/files/10/plates": json({
            state: "ready",
            plates: [aFilePlate(), aFilePlate({ number: 2, name: "Wall mount" })],
          }),
        },
      });

      const plates = await screen.findByRole("group", { name: "Plates" });

      expect(within(plates).getByRole("button", { name: /Plate 02.*Wall mount/ })).toBeVisible();
    });

    it("names the plate it shows beside the file", async () => {
      const user = userEvent.setup();
      renderDetail({
        model: aModel({ files: [aFile({ file_type: "3mf", original_filename: "rack.3mf" })] }),
        routes: {
          "GET /api/v1/files/10/stl": json({ state: "queued" }, 202),
          "GET /api/v1/files/10/plates": json({
            state: "ready",
            plates: [aFilePlate(), aFilePlate({ number: 2, name: "Wall mount" })],
          }),
        },
      });

      await user.click(await screen.findByRole("button", { name: /Plate 02/ }));

      expect(screen.getByText(/Viewing: rack\.3mf · Plate 02/)).toBeVisible();
    });

    it("offers no plate choice for a single-plate 3MF", async () => {
      const view = renderDetail({
        model: aModel({ files: [aFile({ file_type: "3mf", original_filename: "part.3mf" })] }),
        routes: {
          "GET /api/v1/files/10/stl": json({ state: "queued" }, 202),
          "GET /api/v1/files/10/plates": json({ state: "ready", plates: [aFilePlate()] }),
        },
      });
      await waitFor(() =>
        expect(view.requests().some(({ url }) => url.endsWith("/files/10/plates"))).toBe(true),
      );

      expect(screen.queryByRole("group", { name: "Plates" })).not.toBeInTheDocument();
    });

    it("asks for no plates of a model without a 3MF", async () => {
      const view = renderDetail();
      await screen.findByText("Benchy");

      expect(view.requests().some(({ url }) => url.includes("/plates"))).toBe(false);
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
    it("preserves the registered Library origin through tab changes", async () => {
      renderDetail({ at: "/?c=parts", libraryOrigin: true });
      await userEvent.click(screen.getByRole("link", { name: "Open model from library" }));
      expect(screen.getByLabelText("Registered library origin")).toHaveTextContent("/?c=parts");

      await userEvent.click(await screen.findByRole("tab", { name: /Files/ }));
      expect(screen.getByLabelText("Registered library origin")).toHaveTextContent("/?c=parts");
      expect(screen.getByLabelText("Current detail location")).toHaveTextContent(
        "return=%2F%3Fc%3Dparts&tab=files",
      );
      await userEvent.click(screen.getByRole("tab", { name: /Similar/ }));
      expect(screen.getByLabelText("Registered library origin")).toHaveTextContent("/?c=parts");
    });
    it("restores the Similar tab from a comparison return URL", async () => {
      renderDetail({ at: "/models/1?tab=similar&return_entry=library-position" });

      expect(await screen.findByRole("tab", { name: /Similar/ })).toHaveAttribute(
        "aria-selected",
        "true",
      );
      expect(await screen.findByRole("button", { name: "Find similar" })).toBeVisible();
    });

    it("preserves the library return context while selecting a tab", async () => {
      renderDetail({ at: "/models/1?return_entry=library-position&collection=parts" });
      await userEvent.click(await screen.findByRole("tab", { name: /Similar/ }));

      expect(screen.getByLabelText("Current detail location")).toHaveTextContent(
        "/models/1?return_entry=library-position&collection=parts&tab=similar",
      );
      expect(screen.getByRole("tab", { name: /Similar/ })).toHaveAttribute("aria-selected", "true");
    });

    it("restores the tab when browser history changes the current model URL", async () => {
      renderDetail({ at: "/models/1?tab=similar" });
      await screen.findByRole("button", { name: "Find similar" });
      await userEvent.click(screen.getByRole("button", { name: "Open Files URL" }));
      expect(screen.getByRole("tab", { name: /Files/ })).toHaveAttribute("aria-selected", "true");

      await userEvent.click(screen.getByRole("button", { name: "Previous detail" }));
      expect(screen.getByRole("tab", { name: /Similar/ })).toHaveAttribute("aria-selected", "true");
    });

    it("uses Overview for an unsupported tab URL", async () => {
      renderDetail({ at: "/models/1?tab=unknown" });

      expect(await screen.findByRole("tab", { name: /Overview/ })).toHaveAttribute(
        "aria-selected",
        "true",
      );
    });

    it("does not fetch Similar candidates for a view-only model tab URL", async () => {
      const app = renderDetail({
        at: "/models/1?tab=similar",
        model: aModel({ effective_role: "view" }),
        auth: memberSession(),
      });

      expect(await screen.findByRole("tab", { name: /Overview/ })).toHaveAttribute(
        "aria-selected",
        "true",
      );
      expect(screen.queryByRole("tab", { name: /Similar/ })).not.toBeInTheDocument();
      expect(app.requests().some((request) => request.url.includes("similarity/candidates"))).toBe(
        false,
      );
    });

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

    it.each([
      { label: "collection", collection: "parts/tools", expected: "/?c=parts%2Ftools" },
      { label: "root", collection: null, expected: "/" },
    ])("returns to the deleted Model $label destination", async ({ collection, expected }) => {
      const user = userEvent.setup();
      renderDetail({
        at: "/models/1",
        model: aModel({
          collection,
          collection_id: collection === null ? null : 1,
          collection_label: collection === null ? null : "Tools",
        }),
        routes: { "DELETE /api/v1/models/1": json(null, 204) },
      });
      await openActions(user);
      await user.click(screen.getByRole("menuitem", { name: /Delete model/ }));

      await user.click(
        within(await screen.findByRole("dialog")).getByRole("button", { name: "Delete" }),
      );

      await waitFor(() =>
        expect(screen.getByLabelText("Current detail location").textContent).toBe(expected),
      );
    });

    it("stays on the page when the delete is refused", async () => {
      // Navigating away from a model that still exists loses the user's place
      // for nothing.
      const user = userEvent.setup();
      renderDetail({
        at: "/models/1",
        routes: { "DELETE /api/v1/models/1": json({ detail: "model_in_use" }, 409) },
      });
      await openActions(user);
      await user.click(screen.getByRole("menuitem", { name: /Delete model/ }));

      await user.click(
        within(await screen.findByRole("dialog")).getByRole("button", { name: "Delete" }),
      );

      expect(
        await screen.findByText("This request could not be completed. Try again."),
      ).toBeInTheDocument();
      expect(screen.getByLabelText("Current detail location").textContent).toBe("/models/1");
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

describe("ModelDetail file hashes", () => {
  it("replaces the AI caption panel in Overview", async () => {
    renderDetail();
    const hashes = await screen.findByRole("region", { name: "File hashes" });
    expect(within(hashes).getByTitle("a".repeat(64), { exact: true })).toBeVisible();
    expect(screen.queryByRole("region", { name: "AI caption" })).not.toBeInTheDocument();
  });
});
