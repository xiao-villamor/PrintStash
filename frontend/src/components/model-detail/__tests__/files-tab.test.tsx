/*
 * Source-file tags are direct Artifact metadata. Saving them must call the
 * Artifact endpoint and replace the surrounding Model with the server response.
 */
import "@testing-library/jest-dom/vitest";

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FilesTab } from "@/components/model-detail/files-tab";
import { json, renderApp } from "@/test-support/render";
import type { FileRead, ModelRead } from "@/types";

const createObjectURLDescriptor = Object.getOwnPropertyDescriptor(URL, "createObjectURL");
const revokeObjectURLDescriptor = Object.getOwnPropertyDescriptor(URL, "revokeObjectURL");

const artifact: FileRead = {
  id: 7,
  model_id: 1,
  original_filename: "bracket.stl",
  file_type: "stl",
  version: 1,
  gcode_revision_number: null,
  size_bytes: 1024,
  sha256: "b".repeat(64),
  revision_label: null,
  revision_status: null,
  revision_notes: null,
  is_recommended: false,
  uploaded_at: "2026-01-01T00:00:00Z",
  metadata: null,
  tags: ["Existing"],
};

const updatedModel: ModelRead = {
  id: 1,
  name: "Bracket",
  slug: "bracket",
  hash: "a".repeat(64),
  collection: null,
  collection_id: null,
  description: null,
  source_url: null,
  effective_role: "admin",
  tags: [],
  thumbnail_url: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  files: [{ ...artifact, tags: ["Existing", "Workshop"] }],
  starred: false,
};

describe("FilesTab", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    if (createObjectURLDescriptor) {
      Object.defineProperty(URL, "createObjectURL", createObjectURLDescriptor);
    } else {
      Reflect.deleteProperty(URL, "createObjectURL");
    }
    if (revokeObjectURLDescriptor) {
      Object.defineProperty(URL, "revokeObjectURL", revokeObjectURLDescriptor);
    } else {
      Reflect.deleteProperty(URL, "revokeObjectURL");
    }
  });

  it("replaces an Artifact tag set", async () => {
    const onModel = vi.fn<(model: ModelRead) => void>();
    const view = renderApp(
      <FilesTab modelId={1} sourceFiles={[artifact]} canEdit onModel={onModel} />,
      {
        routes: {
          "GET /api/v1/tags": json([
            { id: 1, name: "Existing", slug: "existing", model_count: 1 },
            { id: 2, name: "Workshop", slug: "workshop", model_count: 2 },
          ]),
          "PUT /api/v1/models/1/files/7/tags": json(updatedModel),
        },
      },
    );

    await userEvent.click(await screen.findByRole("button", { name: "Edit tags" }));
    await userEvent.click(await screen.findByRole("button", { name: "Workshop" }));
    await userEvent.click(screen.getByRole("button", { name: "Save tags" }));

    await waitFor(() => expect(onModel).toHaveBeenCalledWith(updatedModel));
    expect(JSON.parse(view.requestsWithMethod("PUT")[0]?.body ?? "{}")).toEqual({
      tags: ["Existing", "Workshop"],
    });
  });

  it("explains an empty source-file list", () => {
    renderApp(
      <FilesTab
        modelId={1}
        sourceFiles={[]}
        canEdit={false}
        onModel={vi.fn<(model: ModelRead) => void>()}
      />,
      {
        routes: { "GET /api/v1/tags": json([]) },
      },
    );

    expect(screen.getByText(/No source files/)).toBeVisible();
  });

  it("keeps the editor open when saving fails", async () => {
    const view = renderApp(
      <FilesTab
        modelId={1}
        sourceFiles={[artifact]}
        canEdit
        onModel={vi.fn<(model: ModelRead) => void>()}
      />,
      {
        routes: {
          "GET /api/v1/tags": json([]),
          "PUT /api/v1/models/1/files/7/tags": json({ detail: "denied" }, 500),
        },
      },
    );

    await userEvent.click(await screen.findByRole("button", { name: "Edit tags" }));
    await userEvent.click(screen.getByRole("button", { name: "Save tags" }));

    await waitFor(() => expect(view.requestsWithMethod("PUT")).toHaveLength(1));
    expect(screen.getByRole("dialog")).toBeVisible();
  });

  it("downloads the original Artifact", async () => {
    const createObjectURL = vi.fn<(_blob: Blob) => string>(() => "blob:artifact");
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: createObjectURL,
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: vi.fn<() => void>(),
    });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    const view = renderApp(
      <FilesTab
        modelId={1}
        sourceFiles={[{ ...artifact, is_external: true }]}
        canEdit={false}
        onModel={vi.fn<(model: ModelRead) => void>()}
      />,
      {
        routes: {
          "GET /api/v1/tags": json([]),
          "GET /api/v1/files/7/download": new Response("mesh"),
        },
      },
    );

    await userEvent.click(screen.getByTitle("Download"));

    await waitFor(() =>
      expect(
        view
          .requestsWithMethod("GET")
          .some((request) => request.url.includes("/api/v1/files/7/download")),
      ).toBe(true),
    );
    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Linked")).toBeVisible();
  });
});
