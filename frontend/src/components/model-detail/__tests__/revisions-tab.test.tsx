/*
 * A model's G-code revisions: the versions somebody actually prints from.
 *
 * One revision per model is *recommended*, and that is what the send-to button
 * reaches for. Recommending a second without unrecommending the first would make
 * "print this model" ambiguous, so the flag is exclusive and the exclusivity is
 * the property worth pinning — not the checkbox.
 *
 * A revision's status is how a user records that a slice failed, which is only
 * useful if it survives: the row's edit form has to send what was typed rather
 * than what was loaded. Deleting one is the destructive case, and it asks first
 * — a revision is bytes nobody can re-slice from memory.
 *
 * Comparing two revisions is the read-only half, and it defaults to the two most
 * recent because that is the comparison a user almost always wants; a default of
 * "nothing selected" makes them do the work twice.
 */

import "@testing-library/jest-dom/vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RevisionsTab } from "@/components/model-detail/revisions-tab";
import { clearLogin } from "@/lib/auth-store";
import { json, renderApp, type RenderAppOptions } from "@/test-support/render";
import type { ArtifactOutcomeRead, FileRead, ModelPrinterFileRead, ModelRead } from "@/types";

const FROZEN_NOW = "2026-01-01T00:00:00Z";

function aRevision(over: Partial<FileRead> = {}): FileRead {
  return {
    id: 20,
    model_id: 1,
    original_filename: "part_0.2mm.gcode",
    file_type: "gcode",
    version: 1,
    gcode_revision_number: 1,
    size_bytes: 4096,
    sha256: "b".repeat(64),
    geometric_sha256: null,
    revision_label: "PLA draft",
    revision_status: "needs_test",
    revision_notes: null,
    is_recommended: false,
    uploaded_at: FROZEN_NOW,
    metadata: null,
    tags: [],
    ...over,
  };
}

/** The model the batch re-reads once its labels have landed. */
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
    derivatives_revision: "0",
    ...over,
  };
}

function renderRevisions(
  options: RenderAppOptions & { revisions?: FileRead[]; startComparing?: boolean } = {},
) {
  const {
    revisions = [aRevision(), aRevision({ id: 21, gcode_revision_number: 2, version: 2 })],
    routes = {},
    startComparing = false,
    ...rest
  } = options;
  const onModel = vi.fn<(model: ModelRead) => void>();
  const onAddRevision = vi.fn<() => void>();
  const onLogPrint = vi.fn<(fileId: number, state: "completed" | "failed") => void>();
  const content = (files: FileRead[]) => (
    <RevisionsTab
      modelId={1}
      gcodeFiles={files.filter((file) => file.file_type === "gcode")}
      allFiles={files}
      startComparing={startComparing}
      printerFilesByFileId={new Map<number, ModelPrinterFileRead[]>()}
      onModel={onModel}
      onAddRevision={onAddRevision}
      onLogPrint={onLogPrint}
      canEdit
    />
  );
  const result = renderApp(content(revisions), {
    routes: {
      "GET /api/v1/models/1/artifact-outcomes": json([]),
      ...routes,
    },
    ...rest,
  });
  return {
    ...result,
    onModel,
    onAddRevision,
    onLogPrint,
    rerenderRevisions: (files: FileRead[]) => result.rerender(content(files)),
  };
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("RevisionsTab", () => {
  describe("keyboard revision selection", () => {
    it("moves revision focus with the arrow selection", async () => {
      const user = userEvent.setup();
      renderRevisions();
      await user.click(await screen.findByRole("button", { name: /^Rev 1\s*PLA draft$/ }));

      await user.keyboard("{ArrowDown}");

      const next = screen.getByRole("button", { name: /^Rev 2\s*PLA draft$/ });
      expect(next).toHaveFocus();
      expect(next).toHaveAttribute("aria-pressed", "true");
    });

    it("keeps focus on the last revision at the boundary", async () => {
      const user = userEvent.setup();
      renderRevisions();
      await user.click(await screen.findByRole("button", { name: /^Rev 2\s*PLA draft$/ }));

      await user.keyboard("{ArrowDown}");

      expect(screen.getByRole("button", { name: /^Rev 2\s*PLA draft$/ })).toHaveFocus();
    });
  });

  describe("revision print shortcuts", () => {
    it.each([
      ["1", "completed"],
      ["2", "failed"],
    ] as const)("logs %s for the focused revision", async (key, state) => {
      const user = userEvent.setup();
      const view = renderRevisions();
      await user.click(await screen.findByRole("button", { name: /^Rev 2\s*PLA draft$/ }));
      await user.keyboard(key);
      expect(view.onLogPrint).toHaveBeenCalledExactlyOnceWith(21, state);
    });

    it("preserves native keyboard editing in the revision label draft", async () => {
      const user = userEvent.setup();
      const view = renderRevisions();
      await user.click((await screen.findAllByRole("button", { name: "Edit revision" }))[0]);
      const label = await screen.findByPlaceholderText("Revision label");
      await user.clear(label);
      await user.type(label, "12{ArrowLeft}");
      expect(label).toHaveValue("12");
      expect(label).toHaveFocus();
      expect(view.onLogPrint).not.toHaveBeenCalled();
    });
  });

  describe("comparison outcome ownership", () => {
    const recorded: ArtifactOutcomeRead = {
      file_id: 20,
      print_count: 7,
      completed_count: 7,
      failed_count: 0,
      cancelled_count: 0,
      success_rate: 1,
      average_duration_s: null,
      total_filament_g: null,
      total_cost: null,
    };

    it("reads the resolved pair when a second file arrives", async () => {
      const first = aRevision();
      const second = aRevision({ id: 21, version: 2, gcode_revision_number: 2 });
      const view = renderRevisions({
        revisions: [first],
        startComparing: true,
        routes: { "GET /api/v1/models/1/artifact-outcomes": json([recorded]) },
      });
      view.rerenderRevisions([first, second]);

      expect(
        await within(await screen.findByRole("row", { name: /^Print count/ })).findByText("7"),
      ).toBeVisible();
      expect(
        view
          .requests()
          .filter(({ url }) => url.includes("artifact-outcomes"))
          .map(({ url }) => new URL(url, "http://test").searchParams.getAll("file_id")),
      ).toEqual([["20", "21"]]);
    });

    it("ignores an outcome response for a removed selection", async () => {
      const first = aRevision();
      const second = aRevision({ id: 21, version: 2, gcode_revision_number: 2 });
      const third = aRevision({ id: 22, version: 3, gcode_revision_number: 3 });
      let release!: (response: Response) => void;
      const pending = new Promise<Response>((resolve) => {
        release = resolve;
      });
      const view = renderRevisions({
        revisions: [first, second],
        startComparing: true,
        routes: {
          "GET /api/v1/models/1/artifact-outcomes": (url) =>
            new URL(url, "http://test").searchParams.getAll("file_id").includes("21")
              ? pending
              : json([{ ...recorded, print_count: 3 }]),
        },
      });
      await waitFor(() =>
        expect(view.requests().some(({ url }) => url.includes("artifact-outcomes"))).toBe(true),
      );
      view.rerenderRevisions([first, third]);
      expect(
        await within(await screen.findByRole("row", { name: /^Print count/ })).findByText("3"),
      ).toBeVisible();

      await act(async () => {
        release(json([{ ...recorded, print_count: 99 }]));
        await pending;
      });

      const count = within(screen.getByRole("row", { name: /^Print count/ }));
      expect(count.getByText("3")).toBeVisible();
      expect(count.queryByText("99")).not.toBeInTheDocument();
    });

    it("ignores an outcome response after session retirement", async () => {
      let release!: (response: Response) => void;
      const pending = new Promise<Response>((resolve) => {
        release = resolve;
      });
      const view = renderRevisions({
        startComparing: true,
        routes: { "GET /api/v1/models/1/artifact-outcomes": () => pending },
      });
      await waitFor(() =>
        expect(view.requests().some(({ url }) => url.includes("artifact-outcomes"))).toBe(true),
      );

      await act(async () => {
        clearLogin();
        release(json([recorded]));
        await pending;
      });

      const count = within(screen.getByRole("row", { name: /^Print count/ }));
      expect(count.getAllByText("Not recorded")).toHaveLength(2);
      expect(count.queryByText("7")).not.toBeInTheDocument();
      expect(view.requests().filter(({ url }) => url.includes("artifact-outcomes"))).toHaveLength(
        1,
      );
    });

    it("keeps comparison modes available when the chosen scope is empty", async () => {
      const user = userEvent.setup();
      renderRevisions({ startComparing: true });
      await user.click(
        within(screen.getByRole("group", { name: "Comparison type" })).getByRole("button", {
          name: "Source files",
        }),
      );
      expect(screen.getByText("Add a second file to compare recorded information.")).toBeVisible();

      await user.click(
        within(screen.getByRole("group", { name: "Comparison type" })).getByRole("button", {
          name: "G-code revisions",
        }),
      );

      expect(screen.getByRole("heading", { name: "Compare revisions" })).toBeVisible();
      expect(screen.getAllByRole("table")).not.toHaveLength(0);
    });
  });

  describe("listing revisions", () => {
    it("lists every revision", async () => {
      renderRevisions();

      expect(await screen.findAllByText(/part_0\.2mm\.gcode/)).not.toHaveLength(0);
    });

    it("shows a revision's label", async () => {
      renderRevisions();

      expect(await screen.findAllByText("PLA draft")).not.toHaveLength(0);
    });

    it("offers to add another", async () => {
      const user = userEvent.setup();
      const { onAddRevision } = renderRevisions();

      await user.click(await screen.findByRole("button", { name: "Add revision" }));

      expect(onAddRevision).toHaveBeenCalledTimes(1);
    });
  });

  describe("editing a revision", () => {
    it("opens the edit form for one row", async () => {
      const user = userEvent.setup();
      renderRevisions();

      await user.click((await screen.findAllByRole("button", { name: "Edit revision" }))[0]);

      expect(await screen.findByPlaceholderText("Revision label")).toBeInTheDocument();
    });

    it("PATCHes what the user typed", async () => {
      // The status is how a failed slice is recorded, so sending back the value
      // that was loaded silently discards the correction.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderRevisions({
        routes: { "PATCH /api/v1/models/1/files/20/revision": json({ id: 1 }) },
      });
      await user.click((await screen.findAllByRole("button", { name: "Edit revision" }))[0]);
      const label = await screen.findByPlaceholderText("Revision label");
      await user.clear(label);
      await user.type(label, "PETG fast");

      await user.click(screen.getByRole("button", { name: "Save Revision" }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("PATCH").at(-1)?.body ?? "{}")).toMatchObject({
          revision_label: "PETG fast",
        }),
      );
    });

    it("localizes a successful revision update", async () => {
      const user = userEvent.setup();
      renderRevisions({
        locale: "es",
        routes: { "PATCH /api/v1/models/1/files/20/revision": json(aModel()) },
      });
      await user.click((await screen.findAllByRole("button", { name: "Editar revisión" }))[0]);

      await user.click(screen.getByRole("button", { name: "Guardar revisión" }));

      expect(await screen.findByText("Revisión actualizada")).toBeInTheDocument();
    });
  });

  describe("deleting a revision", () => {
    it("asks before deleting", async () => {
      // A revision is bytes nobody can re-slice from memory.
      const user = userEvent.setup();
      renderRevisions();

      await user.click((await screen.findAllByRole("button", { name: "Delete revision" }))[0]);

      expect(await screen.findByRole("dialog")).toBeInTheDocument();
    });

    it("deletes once the user confirms", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderRevisions({
        routes: { "DELETE /api/v1/models/1/files/20/revision": json({ id: 1 }) },
      });
      await user.click((await screen.findAllByRole("button", { name: "Delete revision" }))[0]);

      const dialog = await screen.findByRole("dialog");
      await user.click(within(dialog).getByRole("button", { name: /Delete/ }));

      await waitFor(() =>
        expect(requestsWithMethod("DELETE").some((call) => call.url.includes("/revision"))).toBe(
          true,
        ),
      );
    });

    it("deletes nothing when the user backs out", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderRevisions();
      await user.click((await screen.findAllByRole("button", { name: "Delete revision" }))[0]);

      const dialog = await screen.findByRole("dialog");
      await user.click(within(dialog).getByRole("button", { name: /Cancel|Keep/ }));

      expect(requestsWithMethod("DELETE")).toHaveLength(0);
    });
  });

  describe("labelling several at once", () => {
    it("offers a selection mode", async () => {
      const user = userEvent.setup();
      renderRevisions();

      await user.click(await screen.findByRole("button", { name: "Edit labels" }));

      expect(await screen.findByRole("button", { name: "Cancel selection" })).toBeInTheDocument();
    });

    it("leaves selection mode on request", async () => {
      const user = userEvent.setup();
      renderRevisions();
      await user.click(await screen.findByRole("button", { name: "Edit labels" }));

      await user.click(screen.getByRole("button", { name: "Cancel selection" }));

      expect(screen.getByRole("button", { name: "Edit labels" })).toBeInTheDocument();
    });
  });

  describe("with nothing to show", () => {
    it("says so rather than rendering an empty list", async () => {
      renderRevisions({ revisions: [] });

      await waitFor(() =>
        expect(screen.queryAllByRole("button", { name: "Edit revision" })).toHaveLength(0),
      );
    });
  });
  describe("applying one label to several revisions", () => {
    /** Enter selection mode and tick the first revision. */
    async function selectFirst(user: ReturnType<typeof userEvent.setup>) {
      await user.click(await screen.findByRole("button", { name: "Edit labels" }));
      await user.click(await screen.findByRole("checkbox", { name: "Select revision 1" }));
    }

    it("cannot apply a label with nothing selected", async () => {
      // A batch over an empty selection is a request that changes nothing and
      // reports success.
      const user = userEvent.setup();
      renderRevisions();

      await user.click(await screen.findByRole("button", { name: "Edit labels" }));

      expect(screen.getByRole("button", { name: /Apply label/ })).toBeDisabled();
    });

    it("counts what is selected", async () => {
      const user = userEvent.setup();
      renderRevisions();

      await selectFirst(user);

      expect(screen.getByText("1 selected")).toBeInTheDocument();
    });

    it("labels the revisions the user ticked", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderRevisions({
        routes: {
          "PATCH /api/v1/models/batch/revision-labels": json({ updated: 1 }),
          "GET /api/v1/models/1": json(aModel()),
        },
      });
      await selectFirst(user);
      await user.type(screen.getByPlaceholderText("Label (blank clears)"), "PETG tested");

      await user.click(screen.getByRole("button", { name: /Apply label/ }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("PATCH").at(-1)?.body ?? "{}")).toMatchObject({
          file_ids: [20],
          revision_label: "PETG tested",
        }),
      );
    });

    it("clears the label when the field is left blank", async () => {
      // Blank has to mean "remove", or there is no way back from a label
      // applied by mistake across a dozen revisions.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderRevisions({
        routes: {
          "PATCH /api/v1/models/batch/revision-labels": json({ updated: 1 }),
          "GET /api/v1/models/1": json(aModel()),
        },
      });
      await selectFirst(user);

      await user.click(screen.getByRole("button", { name: /Apply label/ }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("PATCH").at(-1)?.body ?? "{}")).toMatchObject({
          revision_label: "",
        }),
      );
    });

    it("re-reads the model so the new labels are on screen", async () => {
      const user = userEvent.setup();
      const { onModel } = renderRevisions({
        routes: {
          "PATCH /api/v1/models/batch/revision-labels": json({ updated: 1 }),
          "GET /api/v1/models/1": json(aModel()),
        },
      });
      await selectFirst(user);

      await user.click(screen.getByRole("button", { name: /Apply label/ }));

      await waitFor(() => expect(onModel).toHaveBeenCalled());
    });

    it("leaves selection mode once the batch lands", async () => {
      const user = userEvent.setup();
      renderRevisions({
        routes: {
          "PATCH /api/v1/models/batch/revision-labels": json({ updated: 1 }),
          "GET /api/v1/models/1": json(aModel()),
        },
      });
      await selectFirst(user);

      await user.click(screen.getByRole("button", { name: /Apply label/ }));

      expect(await screen.findByRole("button", { name: "Edit labels" })).toBeInTheDocument();
    });

    it("stays in selection mode when the batch is refused", async () => {
      // Dropping the selection on failure means ticking a dozen rows again.
      const user = userEvent.setup();
      renderRevisions({
        routes: {
          "PATCH /api/v1/models/batch/revision-labels": json({ detail: "forbidden" }, 403),
        },
      });
      await selectFirst(user);

      await user.click(screen.getByRole("button", { name: /Apply label/ }));

      expect(await screen.findByRole("button", { name: "Cancel selection" })).toBeInTheDocument();
    });

    it("unticks a revision the user changed their mind about", async () => {
      const user = userEvent.setup();
      renderRevisions();
      await selectFirst(user);

      await user.click(screen.getByRole("checkbox", { name: "Select revision 1" }));

      expect(screen.getByText("0 selected")).toBeInTheDocument();
    });
  });
});
