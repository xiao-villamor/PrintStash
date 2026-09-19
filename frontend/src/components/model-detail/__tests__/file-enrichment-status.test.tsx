/*
 * Saved files stay usable while previews are pending. Optional work can be
 * requested by an editor, and acceptance refreshes the displayed Model.
 */
import "@testing-library/jest-dom/vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { FileEnrichmentStatus } from "../file-enrichment-status";
import { json, renderApp } from "@/test-support/render";
import type { FileRead } from "@/types";

const file: FileRead = {
  id: 7,
  model_id: 1,
  original_filename: "cube.stl",
  file_type: "stl",
  version: 1,
  gcode_revision_number: null,
  size_bytes: 1024,
  sha256: "a".repeat(64),
  revision_label: null,
  revision_status: null,
  revision_notes: null,
  is_recommended: false,
  uploaded_at: "2026-01-01T00:00:00Z",
  metadata: null,
  tags: [],
  enrichment: {
    metadata: "ready",
    thumbnail: "on_demand",
    metadata_error: null,
    thumbnail_error: null,
  },
};

describe("FileEnrichmentStatus", () => {
  it("explains pending enrichment", () => {
    renderApp(
      <FileEnrichmentStatus
        file={{ ...file, enrichment: { ...file.enrichment!, thumbnail: "pending" } }}
        modelId={1}
        canEdit
        onModel={() => {}}
      />,
    );
    expect(screen.getByText("Preparing previews and details")).toBeVisible();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("does not offer writes to a reader", () => {
    renderApp(<FileEnrichmentStatus file={file} modelId={1} canEdit={false} onModel={() => {}} />);
    expect(screen.getByText("Preview available on request")).toBeVisible();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("queues a requested preview", async () => {
    const view = renderApp(
      <FileEnrichmentStatus file={file} modelId={1} canEdit onModel={() => {}} />,
      {
        routes: {
          "POST /api/v1/files/7/enrichment": json({ metadata: "ready", thumbnail: "pending" }),
          "GET /api/v1/models/1": json({ id: 1, name: "Cube", enrichment_pending: true }),
        },
      },
    );
    await userEvent.click(screen.getByRole("button", { name: "Generate preview" }));
    expect(view.requestsWithMethod("POST")).toHaveLength(1);
    expect(JSON.parse(view.requestsWithMethod("POST")[0]?.body ?? "{}")).toEqual({
      metadata: true,
      thumbnail: true,
    });
  });

  it("offers recovery for a failed preview", () => {
    renderApp(
      <FileEnrichmentStatus
        file={{ ...file, enrichment: { ...file.enrichment!, thumbnail: "failed" } }}
        modelId={1}
        canEdit
        onModel={() => {}}
      />,
    );
    expect(screen.getByRole("button", { name: "Retry enrichment" })).toBeEnabled();
  });

  it("keeps a preview request recoverable after server failure", async () => {
    renderApp(<FileEnrichmentStatus file={file} modelId={1} canEdit onModel={() => {}} />, {
      routes: { "POST /api/v1/files/7/enrichment": json({ detail: "unavailable" }, 503) },
    });

    await userEvent.click(screen.getByRole("button", { name: "Generate preview" }));

    expect(await screen.findByText("unavailable")).toBeVisible();
    expect(screen.getByRole("button", { name: "Generate preview" })).toBeEnabled();
    expect(screen.getByText("Preview available on request")).toBeVisible();
  });
});
