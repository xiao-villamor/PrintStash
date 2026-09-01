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

const secondArtifact: FileRead = {
  ...artifact,
  id: 8,
  original_filename: "bracket-wide.stl",
  version: 2,
  sha256: "c".repeat(64),
  tags: [],
};

const thirdArtifact: FileRead = {
  ...artifact,
  id: 9,
  original_filename: "bracket-tall.stl",
  version: 3,
  sha256: "d".repeat(64),
  tags: [],
};

const partGroups = [
  {
    id: 3,
    name: "Bracket width",
    options: [
      { id: 4, file_id: 7, name: "Narrow", is_default: true },
      { id: 5, file_id: 8, name: "Wide", is_default: false },
    ],
  },
];

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
  part_groups: [],
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
      <FilesTab modelId={1} sourceFiles={[artifact]} partGroups={[]} canEdit onModel={onModel} />,
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
        partGroups={[]}
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
        partGroups={[]}
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
        partGroups={[]}
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

  it("creates a named Part Group with one default option", async () => {
    const onModel = vi.fn<(model: ModelRead) => void>();
    const response = {
      ...updatedModel,
      files: [artifact, secondArtifact],
      part_groups: [
        {
          id: 3,
          name: "Bracket width",
          options: [
            { id: 4, file_id: 7, name: "bracket", is_default: true },
            { id: 5, file_id: 8, name: "bracket-wide", is_default: false },
          ],
        },
      ],
    } satisfies ModelRead;
    const view = renderApp(
      <FilesTab
        modelId={1}
        sourceFiles={[artifact, secondArtifact]}
        partGroups={[]}
        canEdit
        onModel={onModel}
      />,
      {
        routes: {
          "GET /api/v1/tags": json([]),
          "PUT /api/v1/models/1/part-options": json(response),
        },
      },
    );

    await userEvent.click(screen.getByRole("button", { name: "Manage options" }));
    await userEvent.click(screen.getByRole("button", { name: "Add part" }));
    await userEvent.type(screen.getByPlaceholderText("e.g. Handle"), "Bracket width");
    await userEvent.click(screen.getByRole("button", { name: "Save options" }));

    await waitFor(() => expect(onModel).toHaveBeenCalledWith(response));
    expect(JSON.parse(view.requestsWithMethod("PUT")[0]?.body ?? "{}")).toEqual({
      groups: [
        {
          name: "Bracket width",
          options: [
            { file_id: 7, name: "bracket", is_default: true },
            { file_id: 8, name: "bracket-wide", is_default: false },
          ],
        },
      ],
    });
  });

  it("persists a revised three-choice Part Group", async () => {
    const onModel = vi.fn<(model: ModelRead) => void>();
    const response = {
      ...updatedModel,
      files: [artifact, secondArtifact, thirdArtifact],
      part_groups: [],
    } satisfies ModelRead;
    const view = renderApp(
      <FilesTab
        modelId={1}
        sourceFiles={[artifact, secondArtifact, thirdArtifact]}
        partGroups={partGroups}
        canEdit
        onModel={onModel}
      />,
      {
        routes: {
          "GET /api/v1/tags": json([]),
          "PUT /api/v1/models/1/part-options": json(response),
        },
      },
    );

    expect(screen.getByText("Bracket width", { exact: true })).toBeVisible();
    expect(screen.getByText("Default", { exact: true })).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Manage options" }));
    const groupName = screen.getByLabelText("Part name");
    await userEvent.clear(groupName);
    await userEvent.type(groupName, "Bracket height");
    await userEvent.click(screen.getByRole("button", { name: "Add option" }));
    await userEvent.click(screen.getAllByRole("radio", { name: "Use as default" })[2]);
    await userEvent.click(screen.getAllByRole("button", { name: "Remove option" })[0]);
    await userEvent.click(screen.getByRole("button", { name: "Save options" }));

    await waitFor(() => expect(onModel).toHaveBeenCalledWith(response));
    expect(JSON.parse(view.requestsWithMethod("PUT")[0]?.body ?? "{}")).toEqual({
      groups: [
        {
          name: "Bracket height",
          options: [
            { file_id: 8, name: "Wide", is_default: false },
            { file_id: 9, name: "bracket-tall", is_default: true },
          ],
        },
      ],
    });
  });

  it("keeps Part Option edits open when the server rejects them", async () => {
    const view = renderApp(
      <FilesTab
        modelId={1}
        sourceFiles={[artifact, secondArtifact]}
        partGroups={partGroups}
        canEdit
        onModel={vi.fn<(model: ModelRead) => void>()}
      />,
      {
        routes: {
          "GET /api/v1/tags": json([]),
          "PUT /api/v1/models/1/part-options": json({ detail: "conflict" }, 409),
        },
      },
    );

    await userEvent.click(screen.getByRole("button", { name: "Manage options" }));
    await userEvent.click(screen.getByRole("button", { name: "Save options" }));

    await waitFor(() => expect(view.requestsWithMethod("PUT")).toHaveLength(1));
    expect(screen.getByRole("dialog", { name: "Manage part options" })).toBeVisible();
  });

  it("refuses an unnamed Part Group before sending", async () => {
    const view = renderApp(
      <FilesTab
        modelId={1}
        sourceFiles={[artifact, secondArtifact]}
        partGroups={[]}
        canEdit
        onModel={vi.fn<(model: ModelRead) => void>()}
      />,
      { routes: { "GET /api/v1/tags": json([]) } },
    );

    await userEvent.click(screen.getByRole("button", { name: "Manage options" }));
    await userEvent.click(screen.getByRole("button", { name: "Add part" }));
    await userEvent.click(screen.getByRole("button", { name: "Save options" }));

    expect(view.requestsWithMethod("PUT")).toHaveLength(0);
    expect(screen.getByRole("dialog", { name: "Manage part options" })).toBeVisible();
  });
});
