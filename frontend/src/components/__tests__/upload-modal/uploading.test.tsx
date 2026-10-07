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
 * ZIP preparation queues a Job and closes the modal. Review resumes from Tasks
 * after that Job finishes, so a large archive never holds this form open.
 */

import "@testing-library/jest-dom/vitest";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { UploadModal } from "@/components/upload-modal";
import { TaskList } from "@/components/task-list";
import type { ArtifactUploadCreate, ArtifactUploadStatus } from "@/lib/api/artifact-uploads";
import { librarySourceKeys } from "@/lib/queries/settings-library-sources";
import { queryKeys } from "@/lib/query-client";
import { clearLogin, storeLogin } from "@/lib/auth-store";
import {
  listTasks,
  setJobSource,
  syncImportJobs,
  startTaskCenterSessionScope,
} from "@/lib/task-center";
import { collectionTreeRoutes } from "@/test-support/collection-tree";
import { aCollection, aJob as aSharedJob, aTag, aVaultConfig } from "@/test-support/factories";
import { FetchBackedXhr } from "@/test-support/fetch-backed-xhr";
import {
  adminSession,
  memberSession,
  json,
  renderApp,
  type RenderAppOptions,
} from "@/test-support/render";
import type { ExternalLibrary, JobStatus, ModelRead } from "@/types";

const FROZEN_NOW = "2026-01-01T00:00:00Z";

/**
 * The task centre caches terminal jobs by id for the life of the module, so two
 * tests sharing a job id share its outcome — the second reads the first's result
 * instead of the one it set up. Every test gets its own id.
 */
let jobSeq = 0;
const jobId = () => `job-${jobSeq}`;
const queued = () => ({ job_id: jobId(), state: "queued", message: "queued" });

function aJob(over: Partial<JobStatus> = {}): JobStatus {
  return aSharedJob({ job_id: jobId(), file_id: 20, ...over });
}

function aModel(over: Partial<ModelRead> = {}): ModelRead {
  return {
    edit_epoch: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    edit_version: 1,
    id: 1,
    name: "Cube",
    slug: "cube",
    hash: "a".repeat(64),
    collection: null,
    collection_id: null,
    collection_label: null,
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

function aLibrary(over: Partial<ExternalLibrary> = {}): ExternalLibrary {
  return {
    edit_epoch: "a".repeat(32),
    edit_version: 1,
    id: 4,
    name: "NAS models",
    root_path: "/mnt/nas/models",
    enabled: true,
    scan_interval_minutes: 60,
    scan_schedule: "0 * * * *",
    watch_mode: "auto",
    fs_kind: "network",
    watch_active: false,
    binding_state: "bound",
    binding_reason: null,
    root_enrollable: false,
    collection_mode: "mirror",
    target_collection_id: null,
    last_scanned_at: null,
    last_scan_status: null,
    last_scan_summary: null,
    ...over,
  };
}

function renderUpload(
  options: RenderAppOptions & {
    onUploaded?: () => Promise<void>;
    beforeChunk?: () => Promise<void>;
  } = {},
) {
  const {
    seed = [],
    routes = {},
    beforeChunk,
    onUploaded = vi.fn<() => Promise<void>>().mockResolvedValue(undefined),
    ...rest
  } = options;
  const onClose = vi.fn<() => void>();
  // The multipart bodies never survive `String(init.body)`, so the fields are
  // read off the FormData here, at the route, in the order they were sent.
  const forms: FormData[] = [];
  const uploadRequests: ArtifactUploadCreate[] = [];
  let uploadSequence = 0;
  const capture = (answer: Response) => (_url: string, init?: RequestInit) => {
    const body = init?.body;
    if (body instanceof FormData) forms.push(body);
    return answer;
  };
  const status = (
    id: string,
    request: ArtifactUploadCreate,
    overrides: Partial<ArtifactUploadStatus> = {},
  ): ArtifactUploadStatus => ({
    id,
    purpose: request.purpose,
    target_role: request.target_role,
    target_id: request.target_id ?? null,
    filename: request.filename,
    media_type: request.media_type,
    size_bytes: request.size_bytes,
    state: "uploading",
    mode: "api_chunks",
    received_bytes: 0,
    verified_size: null,
    verified_sha256: null,
    job_id: null,
    retryable: false,
    error_code: null,
    created_at: FROZEN_NOW,
    updated_at: FROZEN_NOW,
    expires_at: FROZEN_NOW,
    parts: [],
    ...overrides,
  });
  const requestForUrl = (url: string) => {
    const id = url.match(/artifact-uploads\/(session-\d+)/)?.[1];
    const index = id ? Number(id.slice("session-".length)) - 1 : -1;
    const request = uploadRequests[index];
    if (!id || !request) throw new Error(`Unknown upload session in ${url}`);
    return { id, request };
  };
  const result = renderApp(
    <UploadModal open onClose={onClose} onUploaded={onUploaded} defaultCollection={null} />,
    {
      seed: [[queryKeys.tags, [aTag()]], ...seed],
      routes: {
        "GET /api/v1/libraries": json([]),
        "GET /api/v1/config": json(aVaultConfig({ external_libraries_enabled: false })),
        ...collectionTreeRoutes([aCollection()]),
        "GET /api/v1/tags": json([aTag()]),
        "GET /api/v1/models/1": json(aModel()),
        "POST /api/v1/ingest/model": capture(json(queued())),
        "POST /api/v1/ingest/orca": capture(json(queued())),
        "POST /api/v1/ingest/archive/inspect": capture(json(queued())),
        "GET /api/v1/artifact-uploads/": (url) => {
          const { id } = requestForUrl(url);
          return json({
            session_id: id,
            mode: "api_chunks",
            chunk_size: 1024,
            max_parallel: 1,
            upload_path: `/api/v1/artifact-uploads/${id}/chunks/{index}`,
            uploaded_parts: [],
            expires_at: FROZEN_NOW,
          });
        },
        "PUT /api/v1/artifact-uploads/": async (url) => {
          await beforeChunk?.();
          const { id, request } = requestForUrl(url);
          return json({
            session: status(id, request, {
              received_bytes: request.size_bytes,
              state: "uploading",
            }),
            part: { index: 0, offset: 0, size_bytes: request.size_bytes, sha256: request.sha256 },
          });
        },
        "POST /api/v1/artifact-uploads": (url, init) => {
          if (!url.includes("/finalize")) {
            // SAFETY: this route receives JSON serialized by createArtifactUpload.
            const request = JSON.parse(String(init?.body)) as ArtifactUploadCreate;
            uploadRequests.push(request);
            uploadSequence += 1;
            return json(status(`session-${uploadSequence}`, request));
          }
          const { id, request } = requestForUrl(url);
          return json(status(id, request, { state: "ingesting", job_id: jobId() }));
        },
        ...routes,
      },
      ...rest,
    },
  );
  return {
    ...result,
    onClose,
    onUploaded,
    forms: () => [...forms],
    uploadRequests: () => [...uploadRequests],
  };
}

/** The mesh and G-code slots, in the order the dialog renders them. */
function fileInputs(container: HTMLElement) {
  return container.ownerDocument.querySelectorAll<HTMLInputElement>('input[type="file"]');
}

let stopSessionScope: (() => void) | undefined;

beforeEach(() => {
  window.localStorage.clear();
  stopSessionScope = startTaskCenterSessionScope();
  FetchBackedXhr.requests = [];
  vi.stubGlobal("XMLHttpRequest", FetchBackedXhr);
  jobSeq += 1;
  // Every ingestion waits on the task centre's job poll rather than starting a
  // second loop, so a test drives the whole pipeline by answering it.
  setJobSource(async () => [aJob()]);
});

afterEach(async () => {
  // Uploads intentionally outlive the modal. Finish this test's work before
  // replacing fetch: a queued slice must not enter the next test's recorder.
  await waitFor(
    () => {
      const unfinished = listTasks().filter((task) => ["pending", "running"].includes(task.status));
      if (unfinished.length) throw new Error("Upload test left background tasks running");
    },
    { timeout: 5000 },
  );
  setJobSource(async () => []);
  stopSessionScope?.();
  vi.unstubAllGlobals();
});

describe("UploadModal ingestion", () => {
  describe("library-source destinations", () => {
    it("offers only enabled roots with a bound identity", async () => {
      const safe = aLibrary({ id: 4, name: "Safe NAS" });
      const disabled = aLibrary({ id: 5, name: "Paused NAS", enabled: false });
      const unbound = aLibrary({ id: 6, name: "Legacy NAS", binding_state: "unbound" });
      renderUpload({
        routes: {
          "GET /api/v1/config": json(aVaultConfig({ external_libraries_enabled: true })),
          "GET /api/v1/libraries": json([safe, disabled, unbound]),
        },
      });

      expect(
        await screen.findByRole("option", { name: "Safe NAS (library source)" }),
      ).toBeInTheDocument();
      expect(screen.queryByRole("option", { name: "Paused NAS (library source)" })).toBeNull();
      expect(screen.queryByRole("option", { name: "Legacy NAS (library source)" })).toBeNull();
    });

    it("avoids administrative source reads for members", async () => {
      const result = renderUpload({ auth: memberSession() });
      await screen.findByText(".stl .3mf .obj .step .dxf");
      expect(
        result
          .requestsWithMethod("GET")
          .filter((row) => ["/api/v1/config", "/api/v1/libraries"].includes(row.url)),
      ).toHaveLength(0);
    });
    it("recovers a failed destination catalog", async () => {
      const result = renderUpload({
        routes: {
          "GET /api/v1/config": json(aVaultConfig({ external_libraries_enabled: true })),
          "GET /api/v1/libraries": json({ detail: "unavailable" }, 503),
        },
      });
      await userEvent.upload(fileInputs(result.container)[0], new File(["x"], "cube.stl"));
      expect(await screen.findByRole("alert")).toHaveTextContent(
        "Could not load upload destinations.",
      );
      result.route({ "GET /api/v1/libraries": json([aLibrary({ name: "Recovered NAS" })]) });
      await userEvent.click(screen.getByRole("button", { name: "Retry" }));
      expect(
        await screen.findByRole("option", { name: "Recovered NAS (library source)" }),
      ).toBeVisible();
      expect(screen.getByDisplayValue("cube")).toBeVisible();
      expect(screen.queryByRole("alert")).toBeNull();
    });
    it("follows canonical source updates while open", async () => {
      const result = renderUpload({
        routes: {
          "GET /api/v1/config": json(aVaultConfig({ external_libraries_enabled: true })),
          "GET /api/v1/libraries": json([aLibrary({ name: "Old name" })]),
        },
      });
      await screen.findByRole("option", { name: "Old name (library source)" });
      act(() =>
        result.client.setQueryData(librarySourceKeys.all, {
          kind: "enabled",
          items: [aLibrary({ name: "Confirmed name" })],
        }),
      );
      expect(
        await screen.findByRole("option", { name: "Confirmed name (library source)" }),
      ).toBeVisible();
      expect(
        result.requestsWithMethod("GET").filter((row) => row.url === "/api/v1/libraries"),
      ).toHaveLength(1);
    });
    it.each([403, 503])("blocks a selected destination after a %s refresh", async (status) => {
      const result = renderUpload({
        routes: {
          "GET /api/v1/config": json(aVaultConfig({ external_libraries_enabled: true })),
          "GET /api/v1/libraries": json([aLibrary({ name: "Private NAS" })]),
        },
      });
      const option = await screen.findByRole("option", { name: "Private NAS (library source)" });
      const destination = option.closest("select");
      if (!destination) throw new Error("destination select missing");
      await userEvent.selectOptions(destination, "4");
      await userEvent.upload(fileInputs(result.container)[0], new File(["x"], "cube.stl"));
      result.route({
        "GET /api/v1/libraries": json(
          { detail: status === 403 ? "forbidden" : "unavailable" },
          status,
        ),
      });
      await act(async () => {
        await result.client.invalidateQueries({ queryKey: librarySourceKeys.all });
      });
      await screen.findByRole("alert");
      expect(screen.queryByRole("option", { name: "Private NAS (library source)" })).toBeNull();
      expect(destination).toHaveValue("4");
      expect(screen.getByRole("button", { name: "Upload to vault" })).toBeDisabled();
      expect(result.uploadRequests()).toHaveLength(0);
      result.route({ "GET /api/v1/libraries": json([aLibrary({ name: "Private NAS" })]) });
      await userEvent.click(screen.getByRole("button", { name: "Retry" }));
      await waitFor(() =>
        expect(screen.getByRole("button", { name: "Upload to vault" })).toBeEnabled(),
      );
      expect(destination).toHaveValue("4");
      expect(screen.getByDisplayValue("cube")).toBeVisible();
      expect(result.uploadRequests()).toHaveLength(0);
    });

    it("dispatches to the selected eligible source", async () => {
      const result = renderUpload({
        routes: {
          "GET /api/v1/config": json(aVaultConfig({ external_libraries_enabled: true })),
          "GET /api/v1/libraries": json([aLibrary({ name: "Chosen NAS" })]),
        },
      });
      const option = await screen.findByRole("option", { name: "Chosen NAS (library source)" });
      const destination = option.closest("select");
      if (!destination) throw new Error("destination select missing");
      await userEvent.selectOptions(destination, "4");
      await userEvent.upload(fileInputs(result.container)[0], new File(["x"], "cube.stl"));
      await userEvent.click(screen.getByRole("button", { name: "Upload to vault" }));
      await waitFor(() => expect(result.uploadRequests()).toHaveLength(1));
      expect(result.uploadRequests()[0].target_library_id).toBe(4);
    });

    it.each([
      {
        label: "deletes it",
        refreshedLibraries: [],
      },
      {
        label: "unbinds it",
        refreshedLibraries: [
          aLibrary({
            id: 4,
            name: "Safe NAS",
            binding_state: "unbound",
            watch_active: false,
          }),
        ],
      },
    ])("requires destination review when a refresh $label", async ({ refreshedLibraries }) => {
      const safe = aLibrary({ id: 4, name: "Safe NAS" });
      const result = renderUpload({
        routes: {
          "GET /api/v1/config": json(aVaultConfig({ external_libraries_enabled: true })),
          "GET /api/v1/libraries": json([safe]),
        },
      });
      const vaultOption = await screen.findByRole("option", { name: "Vault storage" });
      const destination = vaultOption.closest("select");
      if (!destination) throw new Error("destination select missing");
      await userEvent.selectOptions(destination, "4");
      await userEvent.upload(fileInputs(result.container)[0], new File(["x"], "cube.stl"));
      result.route({ "GET /api/v1/libraries": json(refreshedLibraries) });
      await act(async () => {
        await result.client.invalidateQueries({ queryKey: librarySourceKeys.all });
      });
      expect(await screen.findByRole("alert")).toHaveTextContent(
        "Choose an available destination before uploading.",
      );
      expect(screen.getByRole("button", { name: "Upload to vault" })).toBeDisabled();
      expect(screen.getByDisplayValue("cube")).toBeVisible();
      expect(result.uploadRequests()).toHaveLength(0);
      await userEvent.selectOptions(destination, "");
      await userEvent.click(screen.getByRole("button", { name: "Upload to vault" }));
      await waitFor(() => expect(result.uploadRequests()).toHaveLength(1));
      expect(result.uploadRequests()[0].target_library_id).toBeUndefined();
    });
  });

  describe("a mesh on its own", () => {
    it("uploads the file the user chose", async () => {
      const user = userEvent.setup();
      const { container, uploadRequests } = renderUpload();
      await screen.findByText(".stl .3mf .obj .step .dxf");
      await user.upload(fileInputs(container)[0], new File(["x"], "cube.stl"));

      await user.click(screen.getByRole("button", { name: "Upload to vault" }));

      await waitFor(() => expect(uploadRequests()).not.toHaveLength(0), { timeout: 5000 });
      expect(uploadRequests()[0].model_name).toBe("cube");
    });

    it("files it in the collection the user chose", async () => {
      const user = userEvent.setup();
      const { container, uploadRequests } = renderUpload();
      await screen.findByText(".stl .3mf .obj .step .dxf");
      await user.upload(fileInputs(container)[0], new File(["x"], "cube.stl"));
      await user.click(await screen.findByRole("button", { name: "None" }));
      await user.click(await screen.findByRole("option", { name: /Parts/ }));

      await user.click(screen.getByRole("button", { name: "Upload to vault" }));

      await waitFor(() => expect(uploadRequests()[0]?.collection).toBe("parts"), { timeout: 5000 });
    });

    it("refreshes the vault once the job lands", async () => {
      // The grid is a cache; an upload that never invalidates it leaves the
      // model invisible until a reload.
      const user = userEvent.setup();
      const { container, onUploaded } = renderUpload();
      await screen.findByText(".stl .3mf .obj .step .dxf");
      await user.upload(fileInputs(container)[0], new File(["x"], "cube.stl"));

      await user.click(screen.getByRole("button", { name: "Upload to vault" }));

      await waitFor(() => expect(onUploaded).toHaveBeenCalled(), { timeout: 5000 });
    });

    it("does not refresh the vault when the job failed", async () => {
      // Reporting success for a job that failed produces a library with a model
      // that is not in it.
      setJobSource(async () => [aJob({ state: "failed", error: "unsupported_file_type" })]);
      const user = userEvent.setup();
      const { container, onUploaded } = renderUpload();
      await screen.findByText(".stl .3mf .obj .step .dxf");
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
      await screen.findByText(".stl .3mf .obj .step .dxf");
      await user.upload(fileInputs(container)[1], new File(["x"], "part.gcode"));

      await user.click(screen.getByRole("button", { name: "Upload to vault" }));

      await waitFor(() =>
        expect(
          requestsWithMethod("POST").some((call) => call.url.endsWith("/artifact-uploads")),
        ).toBe(true),
      );
    });
  });

  describe("a mesh and its slice together", () => {
    it("uploads the mesh first", async () => {
      const user = userEvent.setup();
      const { container, requestsWithMethod } = renderUpload();
      await screen.findByText(".stl .3mf .obj .step .dxf");
      await user.upload(fileInputs(container)[0], new File(["x"], "cube.stl"));
      await user.upload(fileInputs(container)[1], new File(["x"], "cube.gcode"));

      await user.click(screen.getByRole("button", { name: "Upload to vault" }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("POST").at(0)?.body ?? "{}").purpose).toBe("model"),
      );
    });

    it("links the slice to the mesh it came from", async () => {
      // Without the source hash the revision lands as a separate model with no
      // mesh beside it — which is exactly what uploading them together avoids.
      const user = userEvent.setup();
      const { container, uploadRequests } = renderUpload();
      await screen.findByText(".stl .3mf .obj .step .dxf");
      await user.upload(fileInputs(container)[0], new File(["x"], "cube.stl"));
      await user.upload(fileInputs(container)[1], new File(["x"], "cube.gcode"));

      await user.click(screen.getByRole("button", { name: "Upload to vault" }));

      await waitFor(() => expect(uploadRequests()).toHaveLength(2), { timeout: 5000 });
      expect(uploadRequests()[1].source_hash).toBe("a".repeat(64));
    }, 10_000);
  });

  describe("a bulk drop", () => {
    it("reports a failed final bulk publication", async () => {
      const user = userEvent.setup();
      const onUploaded = vi
        .fn<() => Promise<void>>()
        .mockRejectedValue(new Error("Refresh failed"));
      const { container } = renderUpload({ onUploaded });
      await user.click(screen.getByRole("button", { name: /\s*Bulk\s*/ }));
      await user.upload(fileInputs(container)[0], [
        new File(["x"], "a.stl"),
        new File(["x"], "b.stl"),
      ]);
      await user.click(await screen.findByRole("button", { name: /Upload 2 models/ }));
      expect(
        await screen.findByText(
          "Something went wrong reaching the server. Check that PrintStash is running and try again.",
        ),
      ).toBeInTheDocument();
      expect(onUploaded).toHaveBeenCalledTimes(1);
    });

    it.each(["transfer", "job"] as const)(
      "retires remaining bulk files during a held %s",
      async (phase) => {
        const held = Promise.withResolvers<void>();
        let entered = false;
        const beforeChunk =
          phase === "transfer"
            ? async () => {
                entered = true;
                await held.promise;
              }
            : undefined;
        if (phase === "job")
          setJobSource(async () => {
            entered = true;
            await held.promise;
            return [aJob()];
          });
        const user = userEvent.setup();
        const { container, uploadRequests, onUploaded } = renderUpload({ beforeChunk });
        await user.click(screen.getByRole("button", { name: /\s*Bulk\s*/ }));
        await user.upload(fileInputs(container)[0], [
          new File(["x"], "private-a.stl"),
          new File(["x"], "private-b.stl"),
        ]);
        await user.click(await screen.findByRole("button", { name: /Upload 2 models/ }));
        await waitFor(() => expect(entered).toBe(true));
        await act(async () => {
          clearLogin();
          const replacement = adminSession().user;
          if (!replacement) throw new Error("Missing replacement account");
          storeLogin("", { ...replacement, id: 90 });
          held.resolve();
          // Flush the released transfer and any incorrectly dispatched WebCrypto work.
          await new Promise((resolve) => setTimeout(resolve, 100));
        });
        try {
          expect(uploadRequests().map((request) => request.filename)).toEqual(["private-a.stl"]);
          expect(onUploaded).not.toHaveBeenCalled();
          expect(listTasks()).toEqual([]);
        } finally {
          // A red regression can have incorrectly registered new-session work.
          act(() => clearLogin());
        }
      },
    );

    it("continues accepted bulk work after the form unmounts", async () => {
      const held = Promise.withResolvers<void>();
      let entered = false;
      const user = userEvent.setup();
      const { container, uploadRequests, onUploaded, unmount } = renderUpload({
        beforeChunk: async () => {
          entered = true;
          await held.promise;
        },
      });
      await user.click(screen.getByRole("button", { name: /\s*Bulk\s*/ }));
      await user.upload(fileInputs(container)[0], [
        new File(["x"], "a.stl"),
        new File(["x"], "b.stl"),
      ]);
      await user.click(await screen.findByRole("button", { name: /Upload 2 models/ }));
      await waitFor(() => expect(entered).toBe(true));
      unmount();
      held.resolve();
      await waitFor(() => expect(onUploaded).toHaveBeenCalledTimes(1));
      expect(uploadRequests()).toHaveLength(2);
    });

    it("queues one job per file", async () => {
      const user = userEvent.setup();
      const { container, uploadRequests } = renderUpload();
      await user.click(screen.getByRole("button", { name: /\s*Bulk\s*/ }));
      await user.upload(fileInputs(container)[0], [
        new File(["x"], "a.stl"),
        new File(["x"], "b.stl"),
      ]);

      await user.click(await screen.findByRole("button", { name: /Upload 2 models/ }));

      await waitFor(() => expect(uploadRequests()).toHaveLength(2), { timeout: 5000 });
    });

    it("keeps going after a file the vault refused", async () => {
      // One bad file aborting the queue is the difference between losing one
      // model and losing a hundred.
      setJobSource(async () => [aJob({ state: "failed", error: "unsupported_file_type" })]);
      const user = userEvent.setup();
      const { container, uploadRequests } = renderUpload();
      await user.click(screen.getByRole("button", { name: /\s*Bulk\s*/ }));
      await user.upload(fileInputs(container)[0], [
        new File(["x"], "a.stl"),
        new File(["x"], "b.stl"),
      ]);

      await user.click(await screen.findByRole("button", { name: /Upload 2 models/ }));

      await waitFor(() => expect(uploadRequests()).toHaveLength(2), { timeout: 5000 });
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
    it("shows server acceptance as a separate phase after upload bytes finish", async () => {
      const user = userEvent.setup();
      let finishRequest: ((response: Response) => void) | undefined;
      const response = new Promise<Response>((resolve) => {
        finishRequest = resolve;
      });
      const { container } = renderUpload({
        routes: { "POST /api/v1/ingest/archive/inspect": () => response },
      });
      await user.click(screen.getByRole("button", { name: /\s*From ZIP\s*/ }));
      await user.upload(fileInputs(container)[0], new File([new Uint8Array(1024)], "wait.zip"));
      await user.click(screen.getByRole("button", { name: "Prepare ZIP" }));

      act(() => FetchBackedXhr.requests[0].emitProgress(1024, 1024));

      expect(listTasks().find((task) => task.title === "Upload wait.zip")).toMatchObject({
        status: "running",
        progress: 95,
        detail: "Finishing transfer to PrintStash. ZIP preparation starts next.",
        archiveTransferredBytes: 1024,
        archiveEtaSeconds: undefined,
      });
      finishRequest?.(json(queued()));
      await waitFor(() => expect(listTasks().some((task) => task.jobId === jobId())).toBe(true));
      expect(listTasks().find((task) => task.jobId === jobId())?.title).toBe("Prepare wait.zip");
      await syncImportJobs();
    });

    it("updates transfer progress before the server accepts the ZIP", async () => {
      const user = userEvent.setup();
      let finishRequest: ((response: Response) => void) | undefined;
      const response = new Promise<Response>((resolve) => {
        finishRequest = resolve;
      });
      const now = vi.spyOn(performance, "now").mockReturnValue(1000);
      const { container } = renderUpload({
        routes: { "POST /api/v1/ingest/archive/inspect": () => response },
      });
      await user.click(screen.getByRole("button", { name: /\s*From ZIP\s*/ }));
      await user.upload(fileInputs(container)[0], new File([new Uint8Array(1024)], "large.zip"));
      await user.click(screen.getByRole("button", { name: "Prepare ZIP" }));

      now.mockReturnValue(3000);
      act(() => FetchBackedXhr.requests[0].emitProgress(512, 1024));

      expect(listTasks().find((task) => task.title === "Upload large.zip")).toMatchObject({
        status: "running",
        progress: 45,
        archiveTransferredBytes: 512,
        archiveSpeedBytesPerSecond: 256,
        archiveEtaSeconds: 2,
      });
      now.mockRestore();
      finishRequest?.(json(queued()));
      await waitFor(() => expect(listTasks().some((task) => task.jobId === jobId())).toBe(true));
      await syncImportJobs();
    });

    it("shows a task immediately while the ZIP is still transferring", async () => {
      const user = userEvent.setup();
      let finishRequest: ((response: Response) => void) | undefined;
      const response = new Promise<Response>((resolve) => {
        finishRequest = resolve;
      });
      const { container, onClose } = renderUpload({
        routes: { "POST /api/v1/ingest/archive/inspect": () => response },
      });
      await user.click(screen.getByRole("button", { name: /\s*From ZIP\s*/ }));
      await user.upload(fileInputs(container)[0], new File(["x"], "large.zip"));

      await user.click(screen.getByRole("button", { name: "Prepare ZIP" }));

      expect(onClose).toHaveBeenCalledTimes(1);
      expect(listTasks().find((task) => task.title === "Upload large.zip")).toMatchObject({
        status: "running",
        detail: "Transferring file",
      });
      finishRequest?.(json(queued()));
      await waitFor(() => expect(listTasks().some((task) => task.jobId === jobId())).toBe(true));
      await syncImportJobs();
    });

    it("shows a rejected ZIP upload as a failed task", async () => {
      const user = userEvent.setup();
      const { container } = renderUpload({
        routes: {
          "POST /api/v1/ingest/archive/inspect": json({ detail: "upload_too_large" }, 413),
        },
      });
      await user.click(screen.getByRole("button", { name: /\s*From ZIP\s*/ }));
      await user.upload(fileInputs(container)[0], new File(["x"], "oversized.zip"));

      await user.click(screen.getByRole("button", { name: "Prepare ZIP" }));

      await waitFor(() =>
        expect(listTasks().find((task) => task.title === "Upload oversized.zip")?.status).toBe(
          "failed",
        ),
      );
      expect(listTasks().find((task) => task.title === "Upload oversized.zip")?.detail).toBe(
        "upload_too_large",
      );
    });

    it("retires ZIP transfer feedback with its session", async () => {
      const user = userEvent.setup();
      const pending = Promise.withResolvers<Response>();
      const { container } = renderUpload({
        routes: { "POST /api/v1/ingest/archive/inspect": () => pending.promise },
      });
      await user.click(screen.getByRole("button", { name: /\s*From ZIP\s*/ }));
      await user.upload(fileInputs(container)[0], new File(["x"], "private.zip"));
      await user.click(screen.getByRole("button", { name: "Prepare ZIP" }));

      await act(async () => {
        clearLogin();
        storeLogin("", { id: 90, username: "replacement", email: null, is_superuser: true });
        pending.resolve(json(queued()));
      });

      expect(listTasks()).toEqual([]);
      expect(screen.queryByText("Something went wrong. Please try again.")).not.toBeInTheDocument();
      expect(
        screen.queryByText(
          "ZIP preparation continues in the background. We'll notify you when it's ready.",
        ),
      ).not.toBeInTheDocument();
      expect(document.querySelector("[data-sonner-toast]")).toBeNull();
    });

    it("cancels an in-flight ZIP upload from Tasks", async () => {
      const user = userEvent.setup();
      const { container } = renderUpload({
        routes: { "POST /api/v1/ingest/archive/inspect": () => new Promise<Response>(() => {}) },
      });
      await user.click(screen.getByRole("button", { name: /\s*From ZIP\s*/ }));
      await user.upload(fileInputs(container)[0], new File(["x"], "cancel.zip"));
      await user.click(screen.getByRole("button", { name: "Prepare ZIP" }));
      const task = listTasks().find((item) => item.title === "Upload cancel.zip");
      if (!task) throw new Error("ZIP task was not created");
      renderApp(<TaskList tasks={[task]} onClear={() => {}} />);

      await user.click(screen.getByRole("button", { name: "Cancel upload" }));

      expect(listTasks().find((item) => item.id === task.id)).toMatchObject({
        status: "failed",
        detail: "Upload cancelled",
      });
    });

    async function prepare(user: ReturnType<typeof userEvent.setup>, container: HTMLElement) {
      setJobSource(async () => [aJob({ kind: "ingestion.archive_inspect", state: "completed" })]);
      await user.click(screen.getByRole("button", { name: /\s*From ZIP\s*/ }));
      await user.upload(fileInputs(container)[0], new File(["x"], "parts.zip"));
      await user.click(screen.getByRole("button", { name: "Prepare ZIP" }));
      await waitFor(() => expect(listTasks().some((task) => task.jobId === jobId())).toBe(true));
      await syncImportJobs();
    }

    it("queues preparation without importing archive entries", async () => {
      const user = userEvent.setup();
      const { container, requestsWithMethod } = renderUpload();

      await prepare(user, container);

      expect(requestsWithMethod("POST").some((call) => call.url.includes("/select"))).toBe(false);
    });

    it("closes the upload modal once ZIP preparation is queued", async () => {
      const user = userEvent.setup();
      const { container, onClose } = renderUpload();

      await prepare(user, container);

      await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    });

    it("tracks the ZIP Job with the selected destination", async () => {
      const user = userEvent.setup();
      const { container } = renderUpload();
      await user.click(screen.getByRole("button", { name: "None" }));
      await user.click(screen.getByRole("option", { name: /Parts/ }));
      await user.type(screen.getByPlaceholderText("Search or create — press Enter"), "fun");
      await user.click(screen.getByRole("option", { name: /functional/ }));
      await prepare(user, container);

      await waitFor(() =>
        expect(listTasks().some((task) => task.title === "Prepare parts.zip")).toBe(true),
      );
      expect(listTasks().find((task) => task.title === "Prepare parts.zip")).toMatchObject({
        archiveCollection: "parts",
        archiveTags: ["functional"],
      });
    });
  });
});
