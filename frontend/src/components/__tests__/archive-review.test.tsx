/**
 * A prepared ZIP stays available for review after its upload dialog closes.
 * This picker preserves choices across folders, submits only supported files,
 * and keeps the chosen destination while allowing the user to change it.
 */
import "@testing-library/jest-dom/vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ArchiveReviewDialog } from "@/components/archive-review";
import { queryKeys } from "@/lib/query-client";
import { clearCompletedTasks, createTask, listTasks, updateTask } from "@/lib/task-center";
import { aCollection, aJob } from "@/test-support/factories";
import { json, renderApp } from "@/test-support/render";
import type { ArchiveEntry } from "@/types";

const entries: ArchiveEntry[] = [
  { name: "Animals/cat.stl", size_bytes: 12, file_type: "stl", is_image: false },
  { name: "Animals/dog.stl", size_bytes: 12, file_type: "stl", is_image: false },
  { name: "Vehicles/car.stl", size_bytes: 12, file_type: "stl", is_image: false },
  { name: "notes.txt", size_bytes: 8, file_type: null, is_image: false },
];

let sequence = 0;
function openReview(
  options: { broken?: boolean; entries?: ArchiveEntry[]; collection?: string | null } = {},
) {
  const jobId = `review-${++sequence}`;
  const taskId = createTask({
    title: "Prepare parts.zip",
    status: "completed",
    jobId,
    jobKind: "ingestion.archive_inspect",
    archiveCollection: options.collection ?? null,
    archiveTags: ["favorite"],
  });
  const onClose = vi.fn<() => void>();
  const result = renderApp(<ArchiveReviewDialog jobId={jobId} onClose={onClose} />, {
    seed: [[queryKeys.collections, [aCollection({ path: "My Parts", effective_role: "admin" })]]],
    routes: {
      [`GET /api/v1/jobs/${jobId}`]: json(
        aJob({
          job_id: jobId,
          kind: "ingestion.archive_inspect",
          state: "completed",
          result: options.broken
            ? null
            : {
                kind: "archive_manifest",
                archive_id: "archive-1",
                archive_name: "parts.zip",
                entries: options.entries ?? entries,
              },
        }),
      ),
      "POST /api/v1/ingest/archive/archive-1/select": json(
        { job_id: `import-${jobId}`, state: "queued" },
        202,
      ),
      "GET /api/v1/collections": json([aCollection({ path: "My Parts", effective_role: "admin" })]),
    },
  });
  return { ...result, onClose, taskId, jobId };
}

afterEach(() => {
  for (const task of listTasks())
    updateTask(task.id, { archiveReviewDone: true, status: "completed" });
  clearCompletedTasks();
  vi.unstubAllGlobals();
});

describe("ArchiveReviewDialog", () => {
  it("lists files by name within a folder", async () => {
    const user = userEvent.setup();
    openReview({ entries: [entries[1], entries[0]] });

    await user.click(await screen.findByRole("button", { name: "Open folder Animals" }));
    expect(
      within(screen.getByRole("list", { name: "ZIP contents" }))
        .getAllByRole("button")
        .map((button) => button.getAttribute("aria-label")),
    ).toEqual(["Animals/cat.stl", "Animals/dog.stl"]);
  });

  it("browses folders without losing a selected file", async () => {
    const user = userEvent.setup();
    openReview();

    await user.click(await screen.findByRole("button", { name: "Open folder Animals" }));
    await user.click(screen.getByRole("button", { name: "Animals/cat.stl" }));
    await user.click(screen.getByRole("button", { name: "ZIP root" }));
    await user.click(screen.getByRole("button", { name: "Open folder Vehicles" }));
    await user.click(screen.getByRole("button", { name: "Vehicles/car.stl" }));

    expect(screen.getByRole("status")).toHaveTextContent("2 of 3 files selected");
    expect(screen.queryByRole("button", { name: "Animals/cat.stl" })).not.toBeInTheDocument();
  });

  it("submits only chosen archive names with the saved destination", async () => {
    const user = userEvent.setup();
    const { requestsWithMethod, onClose, taskId } = openReview({ collection: "My Parts" });

    await user.click(await screen.findByRole("button", { name: "Open folder Animals" }));
    await user.click(screen.getByRole("button", { name: "Animals/dog.stl" }));
    await user.click(screen.getByRole("button", { name: "Import 1 selected" }));

    await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
    const selection = requestsWithMethod("POST").find((request) => request.url.endsWith("/select"));
    expect(JSON.parse(selection?.body ?? "{}")).toEqual({
      names: ["Animals/dog.stl"],
      collection: "My Parts",
      tags: "favorite",
    });
    expect(listTasks().find((task) => task.id === taskId)?.archiveReviewDone).toBe(true);
  });

  it("allows the vault root after a saved collection was chosen", async () => {
    const user = userEvent.setup();
    const { requestsWithMethod } = openReview({ collection: "My Parts" });

    await user.click(await screen.findByRole("button", { name: "Open folder Animals" }));
    await user.click(screen.getByRole("button", { name: "Animals/cat.stl" }));
    await user.selectOptions(screen.getByLabelText("Destination collection"), "");
    await user.click(screen.getByRole("button", { name: "Import 1 selected" }));

    await waitFor(() => expect(requestsWithMethod("POST")).toHaveLength(1));
    expect(JSON.parse(requestsWithMethod("POST")[0].body)).not.toHaveProperty("collection");
  });

  it("does not offer unsupported entries for import", async () => {
    openReview();

    await screen.findByRole("button", { name: "Open folder Animals" });
    expect(screen.queryByRole("button", { name: "notes.txt" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Import 0 selected" })).toBeDisabled();
  });

  it("disables bulk selection when no files are importable", async () => {
    openReview({ entries: [entries[3]] });

    expect(await screen.findByRole("button", { name: "Select all 0 ZIP files" })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("0 of 0 files selected");
  });

  it("shows a recoverable error when the completed Job has no manifest", async () => {
    openReview({ broken: true });

    expect(await screen.findByRole("alert")).toHaveTextContent("not ready for review");
    expect(screen.queryByRole("button", { name: /Import \d+ selected/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
  });

  it("selects a folder's nested files without opening it", async () => {
    const user = userEvent.setup();
    openReview({
      entries: [
        ...entries,
        { name: "Animals/Small/fox.stl", size_bytes: 12, file_type: "stl", is_image: false },
      ],
    });

    await user.click(await screen.findByRole("button", { name: "Select folder Animals" }));

    expect(screen.getByRole("status")).toHaveTextContent("3 of 4 files selected");
    expect(screen.getByRole("button", { name: "Deselect folder Animals" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: "Open folder Animals" })).toBeVisible();
  });

  it("completes a partially selected folder", async () => {
    const user = userEvent.setup();
    openReview();

    await user.click(await screen.findByRole("button", { name: "Open folder Animals" }));
    await user.click(screen.getByRole("button", { name: "Animals/cat.stl" }));
    await user.click(screen.getByRole("button", { name: "ZIP root" }));
    expect(screen.getByRole("button", { name: "Select folder Animals" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    await user.click(screen.getByRole("button", { name: "Select folder Animals" }));

    expect(screen.getByRole("status")).toHaveTextContent("2 of 3 files selected");
  });

  it("deselects a folder without changing another folder", async () => {
    const user = userEvent.setup();
    openReview();

    await user.click(await screen.findByRole("button", { name: "Select all 3 ZIP files" }));
    await user.click(screen.getByRole("button", { name: "Deselect folder Animals" }));

    expect(screen.getByRole("status")).toHaveTextContent("1 of 3 files selected");
    expect(screen.getByRole("button", { name: "Deselect folder Vehicles" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("selects all importable files across folders while search is active", async () => {
    const user = userEvent.setup();
    openReview();

    await user.type(await screen.findByRole("textbox", { name: "Search ZIP files" }), "cat");
    await user.click(screen.getByRole("button", { name: "Select all 3 ZIP files" }));

    expect(screen.getByRole("status")).toHaveTextContent("3 of 3 files selected");
    expect(screen.queryByRole("button", { name: "notes.txt" })).not.toBeInTheDocument();
  });

  it("clears a bulk selection", async () => {
    const user = userEvent.setup();
    openReview();

    await user.click(await screen.findByRole("button", { name: "Select all 3 ZIP files" }));
    await user.click(screen.getByRole("button", { name: "Clear selection" }));

    expect(screen.getByRole("status")).toHaveTextContent("0 of 3 files selected");
    expect(screen.getByRole("button", { name: "Import 0 selected" })).toBeDisabled();
  });

  it("submits a selection of 500 files", async () => {
    const user = userEvent.setup();
    const manyFiles = Array.from({ length: 500 }, (_, index) => ({
      name: `Batch/item-${String(index).padStart(3, "0")}.stl`,
      size_bytes: 12,
      file_type: "stl" as const,
      is_image: false,
    }));
    const { requestsWithMethod } = openReview({ entries: manyFiles });

    await user.click(await screen.findByRole("button", { name: "Select all 500 ZIP files" }));
    expect(screen.getByRole("status")).toHaveTextContent("500 of 500 files selected");
    await user.click(screen.getByRole("button", { name: "Import 500 selected" }));

    await waitFor(() => expect(requestsWithMethod("POST")).toHaveLength(1));
    expect(JSON.parse(requestsWithMethod("POST")[0].body)).toEqual({
      names: manyFiles.map((entry) => entry.name),
      tags: "favorite",
    });
  });
});
