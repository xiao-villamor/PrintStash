/*
 * What actually happens after the user presses Upload.
 *
 * Ingestion is asynchronous: the POST only queues a job, and the dialog closes
 * on the queueing rather than on the result. So the interesting behaviour is not
 * "did a request go out" but what the *shape* of the work is — a mesh and a
 * G-code uploaded together are two jobs, and the second one has to carry the
 * first's content hash or the revision lands as a separate model with no mesh
 * beside it. That link is the whole reason for uploading them together.
 *
 * A failed job must fail its task rather than quietly completing. The task
 * centre is the only place the user learns an upload did not land; a job that
 * reports success on failure produces a library with a model that is not there.
 *
 * A bulk drop mirrors folders into nested collections, one job per file, and one
 * bad file must not abort the queue behind it — that is the difference between
 * losing one model and losing a hundred.
 *
 * An archive is inspected before anything is imported, because the user chooses
 * which entries come in. Importing on inspection would pull in every stray file
 * a downloaded ZIP happens to carry.
 */

import "@testing-library/jest-dom/vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { UploadModal } from "@/components/upload-modal";
import { queryKeys } from "@/lib/query-client";
import { setIngestJobSource } from "@/lib/task-center";
import { aCollection, aTag } from "@/test-support/factories";
import { json, renderApp, type RenderAppOptions } from "@/test-support/render";
import type { IngestJobStatus, ModelRead } from "@/types";

const FROZEN_NOW = "2026-01-01T00:00:00Z";

/**
 * The task centre caches terminal jobs by id for the life of the module, so two
 * tests sharing a job id share its outcome — the second reads the first's result
 * instead of the one it set up. Every test gets its own id.
 */
let jobSeq = 0;
const jobId = () => `job-${jobSeq}`;
const queued = () => ({ job_id: jobId(), state: "pending", message: "queued" });

function aJob(over: Partial<IngestJobStatus> = {}): IngestJobStatus {
  return {
    job_id: jobId(),
    state: "completed",
    model_id: 1,
    file_id: 20,
    error: null,
    started_at: FROZEN_NOW,
    finished_at: FROZEN_NOW,
    ...over,
  };
}

function aModel(over: Partial<ModelRead> = {}): ModelRead {
  return {
    id: 1,
    name: "Cube",
    slug: "cube",
    hash: "a".repeat(64),
    collection: null,
    collection_id: null,
    description: null,
    source_url: null,
    effective_role: null,
    tags: [],
    thumbnail_url: null,
    created_at: FROZEN_NOW,
    updated_at: FROZEN_NOW,
    files: [],
    starred: false,
    ...over,
  };
}

function renderUpload(options: RenderAppOptions & { onUploaded?: () => Promise<void> } = {}) {
  const {
    seed = [],
    routes = {},
    onUploaded = vi.fn<() => Promise<void>>().mockResolvedValue(undefined),
    ...rest
  } = options;
  const onClose = vi.fn<() => void>();
  // The multipart bodies never survive `String(init.body)`, so the fields are
  // read off the FormData here, at the route, in the order they were sent.
  const forms: FormData[] = [];
  const capture = (answer: Response) => (_url: string, init?: RequestInit) => {
    const body = init?.body;
    if (body instanceof FormData) forms.push(body);
    return answer;
  };
  const result = renderApp(
    <UploadModal open onClose={onClose} onUploaded={onUploaded} defaultCollection={null} />,
    {
      seed: [[queryKeys.collections, [aCollection()]], [queryKeys.tags, [aTag()]], ...seed],
      routes: {
        "GET /api/v1/libraries": json([]),
        "GET /api/v1/config": json({ external_libraries_enabled: false }),
        "GET /api/v1/collections": json([aCollection()]),
        "GET /api/v1/tags": json([aTag()]),
        "GET /api/v1/models/1": json(aModel()),
        "POST /api/v1/ingest/model": capture(json(queued())),
        "POST /api/v1/ingest/orca": capture(json(queued())),
        "POST /api/v1/ingest/archive/inspect": capture(json(queued())),
        ...routes,
      },
      ...rest,
    },
  );
  return { ...result, onClose, onUploaded, forms: () => [...forms] };
}

/** The mesh and G-code slots, in the order the dialog renders them. */
function fileInputs(container: HTMLElement) {
  return container.ownerDocument.querySelectorAll<HTMLInputElement>('input[type="file"]');
}

beforeEach(() => {
  window.localStorage.clear();
  jobSeq += 1;
  // Every ingestion waits on the task centre's job poll rather than starting a
  // second loop, so a test drives the whole pipeline by answering it.
  setIngestJobSource(async () => [aJob()]);
});

afterEach(() => {
  setIngestJobSource(async () => []);
  vi.unstubAllGlobals();
});

describe("UploadModal ingestion", () => {
  describe("a mesh on its own", () => {
    it("uploads the file the user chose", async () => {
      const user = userEvent.setup();
      const { container, forms } = renderUpload();
      await screen.findByText(".stl .3mf .obj .step");
      await user.upload(fileInputs(container)[0], new File(["x"], "cube.stl"));

      await user.click(screen.getByRole("button", { name: "Upload to vault" }));

      await waitFor(() => expect(forms()).not.toHaveLength(0), { timeout: 5000 });
      expect(forms()[0].get("model_name")).toBe("cube");
    });

    it("files it in the collection the user chose", async () => {
      const user = userEvent.setup();
      const { container, forms } = renderUpload();
      await screen.findByText(".stl .3mf .obj .step");
      await user.upload(fileInputs(container)[0], new File(["x"], "cube.stl"));
      await user.click(await screen.findByRole("button", { name: "None" }));
      await user.click(await screen.findByRole("option", { name: /parts/ }));

      await user.click(screen.getByRole("button", { name: "Upload to vault" }));

      await waitFor(() => expect(forms()[0]?.get("collection")).toBe("parts"), { timeout: 5000 });
    });

    it("refreshes the vault once the job lands", async () => {
      // The grid is a cache; an upload that never invalidates it leaves the
      // model invisible until a reload.
      const user = userEvent.setup();
      const { container, onUploaded } = renderUpload();
      await screen.findByText(".stl .3mf .obj .step");
      await user.upload(fileInputs(container)[0], new File(["x"], "cube.stl"));

      await user.click(screen.getByRole("button", { name: "Upload to vault" }));

      await waitFor(() => expect(onUploaded).toHaveBeenCalled(), { timeout: 5000 });
    });

    it("does not refresh the vault when the job failed", async () => {
      // Reporting success for a job that failed produces a library with a model
      // that is not in it.
      setIngestJobSource(async () => [aJob({ state: "failed", error: "unsupported_file_type" })]);
      const user = userEvent.setup();
      const { container, onUploaded } = renderUpload();
      await screen.findByText(".stl .3mf .obj .step");
      await user.upload(fileInputs(container)[0], new File(["x"], "cube.stl"));

      await user.click(screen.getByRole("button", { name: "Upload to vault" }));

      await waitFor(() => expect(onUploaded).not.toHaveBeenCalled());
    });
  });

  describe("a G-code on its own", () => {
    it("goes in as a slicer artifact rather than a mesh", async () => {
      // The two endpoints parse different things; sending G-code to the mesh
      // ingester loses every slicer setting the file carries.
      const user = userEvent.setup();
      const { container, requestsWithMethod } = renderUpload();
      await screen.findByText(".stl .3mf .obj .step");
      await user.upload(fileInputs(container)[1], new File(["x"], "part.gcode"));

      await user.click(screen.getByRole("button", { name: "Upload to vault" }));

      await waitFor(() =>
        expect(requestsWithMethod("POST").some((call) => call.url.endsWith("/ingest/orca"))).toBe(
          true,
        ),
      );
    });
  });

  describe("a mesh and its slice together", () => {
    it("uploads the mesh first", async () => {
      const user = userEvent.setup();
      const { container, requestsWithMethod } = renderUpload();
      await screen.findByText(".stl .3mf .obj .step");
      await user.upload(fileInputs(container)[0], new File(["x"], "cube.stl"));
      await user.upload(fileInputs(container)[1], new File(["x"], "cube.gcode"));

      await user.click(screen.getByRole("button", { name: "Upload to vault" }));

      await waitFor(() => expect(requestsWithMethod("POST").at(0)?.url).toContain("/ingest/model"));
    });

    it("links the slice to the mesh it came from", async () => {
      // Without the source hash the revision lands as a separate model with no
      // mesh beside it — which is exactly what uploading them together avoids.
      const user = userEvent.setup();
      const { container, forms } = renderUpload();
      await screen.findByText(".stl .3mf .obj .step");
      await user.upload(fileInputs(container)[0], new File(["x"], "cube.stl"));
      await user.upload(fileInputs(container)[1], new File(["x"], "cube.gcode"));

      await user.click(screen.getByRole("button", { name: "Upload to vault" }));

      await waitFor(() => expect(forms()).toHaveLength(2), { timeout: 5000 });
      expect(forms()[1].get("source_hash")).toBe("a".repeat(64));
    });
  });

  describe("a bulk drop", () => {
    it("queues one job per file", async () => {
      const user = userEvent.setup();
      const { container, forms } = renderUpload();
      await user.click(screen.getByRole("button", { name: /\s*Bulk\s*/ }));
      await user.upload(fileInputs(container)[0], [
        new File(["x"], "a.stl"),
        new File(["x"], "b.stl"),
      ]);

      await user.click(await screen.findByRole("button", { name: /Upload 2 models/ }));

      await waitFor(() => expect(forms()).toHaveLength(2), { timeout: 5000 });
    });

    it("keeps going after a file the vault refused", async () => {
      // One bad file aborting the queue is the difference between losing one
      // model and losing a hundred.
      setIngestJobSource(async () => [aJob({ state: "failed", error: "unsupported_file_type" })]);
      const user = userEvent.setup();
      const { container, forms } = renderUpload();
      await user.click(screen.getByRole("button", { name: /\s*Bulk\s*/ }));
      await user.upload(fileInputs(container)[0], [
        new File(["x"], "a.stl"),
        new File(["x"], "b.stl"),
      ]);

      await user.click(await screen.findByRole("button", { name: /Upload 2 models/ }));

      await waitFor(() => expect(forms()).toHaveLength(2), { timeout: 5000 });
    });

    it("refreshes the vault once, after the whole queue", async () => {
      const user = userEvent.setup();
      const { container, onUploaded } = renderUpload();
      await user.click(screen.getByRole("button", { name: /\s*Bulk\s*/ }));
      await user.upload(fileInputs(container)[0], [
        new File(["x"], "a.stl"),
        new File(["x"], "b.stl"),
      ]);

      await user.click(await screen.findByRole("button", { name: /Upload 2 models/ }));

      await waitFor(() => expect(onUploaded).toHaveBeenCalledTimes(1), { timeout: 5000 });
    });
  });

  describe("an archive", () => {
    const inspected = () =>
      aJob({
        result: {
          archive_id: "arch-1",
          archive_name: "parts.zip",
          entries: [
            { name: "cube.stl", size_bytes: 10, file_type: "stl", is_image: false },
            { name: "readme.txt", size_bytes: 3, file_type: null, is_image: false },
          ],
        },
      });

    /** Pick a ZIP and inspect it, which is the only route to the entry list. */
    async function inspect(user: ReturnType<typeof userEvent.setup>, container: HTMLElement) {
      setIngestJobSource(async () => [inspected()]);
      await user.click(screen.getByRole("button", { name: /\s*From ZIP\s*/ }));
      await user.upload(fileInputs(container)[0], new File(["x"], "parts.zip"));
      await user.click(screen.getByRole("button", { name: "Inspect archive" }));
      await screen.findByText("cube.stl");
    }

    it("lists what the archive holds before importing anything", async () => {
      // Importing on inspection would pull in every stray file a downloaded ZIP
      // happens to carry.
      const user = userEvent.setup();
      const { container, requestsWithMethod } = renderUpload();

      await inspect(user, container);

      expect(requestsWithMethod("POST").some((call) => call.url.includes("/select"))).toBe(false);
    });

    it("leaves out entries the vault cannot import", async () => {
      const user = userEvent.setup();
      const { container } = renderUpload();

      await inspect(user, container);

      expect(screen.queryByText("readme.txt")).toBeNull();
    });

    it("imports the entries the user kept ticked", async () => {
      const user = userEvent.setup();
      const { container, requestsWithMethod } = renderUpload({
        routes: { "POST /api/v1/ingest/archive/arch-1/select": json(queued()) },
      });
      await inspect(user, container);

      await user.click(screen.getByRole("button", { name: /Import 1 selected/ }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("POST").at(-1)?.body ?? "{}")).toMatchObject({
          names: ["cube.stl"],
        }),
      );
    });

    it("will not import with nothing selected", async () => {
      const user = userEvent.setup();
      const { container } = renderUpload();
      await inspect(user, container);

      await user.click(screen.getAllByRole("checkbox")[0]);

      expect(screen.getByRole("button", { name: /Import 0 selected/ })).toBeDisabled();
    });

    it("lets the user back out to the file picker", async () => {
      const user = userEvent.setup();
      const { container } = renderUpload();
      await inspect(user, container);

      await user.click(screen.getByRole("button", { name: "Back" }));

      expect(screen.getByRole("button", { name: "Inspect archive" })).toBeInTheDocument();
    });
  });
});
