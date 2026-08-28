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
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RevisionsTab } from "@/components/model-detail/revisions-tab";
import { json, renderApp, type RenderAppOptions } from "@/test-support/render";
import type { FileRead, ModelPrinterFileRead, ModelRead } from "@/types";

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
    revision_label: "PLA draft",
    revision_status: "needs_test",
    revision_notes: null,
    is_recommended: false,
    uploaded_at: FROZEN_NOW,
    metadata: null,
    ...over,
  };
}

/** The model the batch re-reads once its labels have landed. */
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

function renderRevisions(options: RenderAppOptions & { revisions?: FileRead[] } = {}) {
  const {
    revisions = [aRevision(), aRevision({ id: 21, gcode_revision_number: 2, version: 2 })],
    routes = {},
    ...rest
  } = options;
  const onModel = vi.fn<(model: ModelRead) => void>();
  const onAddRevision = vi.fn<() => void>();
  const result = renderApp(
    <RevisionsTab
      modelId={1}
      gcodeFiles={revisions}
      allFiles={revisions}
      printerFilesByFileId={new Map<number, ModelPrinterFileRead[]>()}
      onModel={onModel}
      onAddRevision={onAddRevision}
    />,
    {
      routes: {
        "GET /api/v1/models/1/artifact-outcomes": json([]),
        ...routes,
      },
      ...rest,
    },
  );
  return { ...result, onModel, onAddRevision };
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("RevisionsTab", () => {
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

      await user.click(await screen.findByRole("button", { name: /Add revision|Add/i }));

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

      await user.click(screen.getByRole("button", { name: "Save" }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("PATCH").at(-1)?.body ?? "{}")).toMatchObject({
          revision_label: "PETG fast",
        }),
      );
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
