"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronDown,
  File as FileIcon,
  Layers,
  Link2,
  Loader2,
  Package,
  Plus,
  Upload,
  X,
} from "lucide-react";
import {
  createTag,
  capturePendingImport,
  getModel,
  getVaultConfig,
  inspectArchive,
  ingestModel,
  ingestOrca,
  listExternalLibraries,
  selectArchiveEntries,
} from "@/lib/api";
import { useCollections, useTags } from "@/lib/queries";
import { toast } from "@/lib/toast";
import {
  createTask,
  linkTaskToJob,
  trackImportJob,
  updateTask,
  waitForImportJob,
} from "@/lib/task-center";
import { useRequireAuth } from "@/lib/use-require-auth";
import { useAuth } from "@/lib/auth-context";
import { formatBytes } from "@/lib/format";
import { ModalShell } from "@/components/ui/modal";
import { DropdownMenu } from "@/components/ui/dropdown-menu";
import { useComboboxNav } from "@/lib/use-combobox-nav";
import {
  bulkTargetCollection,
  entriesFromDataTransfer,
  extensionOf,
  fileListToItems,
  isGcodeFile,
  isMeshFile,
  mergeBulkItems,
  walkEntries,
  MESH_ACCEPT,
  type BulkItem,
} from "@/lib/bulk-upload";
import {
  ArchiveManifest,
  CollectionRead,
  ExternalLibrary,
  IngestJobResult,
  IngestJobStatus,
} from "@/types";
import { ApiError } from "@/lib/errors";
import { useRouter } from "@/lib/navigation";

// `webkitdirectory` enables folder selection on a file input but isn't in the
// standard DOM typings — augment so the JSX attribute typechecks.
declare module "react" {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  interface InputHTMLAttributes<T> {
    webkitdirectory?: string;
  }
}

export type UploadMode = "files" | "bulk" | "url" | "zip";

/**
 * The open-time props the modal seeds its form from: files or bulk items handed
 * over by a drag-and-drop on the vault grid, plus the tab they belong on.
 */
interface UploadSeed {
  preloadFiles: File[] | null;
  preloadItems: BulkItem[] | null;
  initialMode: UploadMode | null;
}

/** Two preloads seed the same thing when they are the same list, or both empty. */
function samePreload<T>(applied: T[] | null, next: T[] | null): boolean {
  return applied === next || ((applied?.length ?? 0) === 0 && (next?.length ?? 0) === 0);
}

function sameSeed(applied: UploadSeed | null, next: UploadSeed): boolean {
  return (
    applied !== null &&
    applied.initialMode === next.initialMode &&
    samePreload(applied.preloadFiles, next.preloadFiles) &&
    samePreload(applied.preloadItems, next.preloadItems)
  );
}

const GCODE_ACCEPT = ".gcode,.g,.gco";

// Whether a filename matches a comma-separated `accept` extension list
// (e.g. ".stl,.3mf,.obj"). Used to validate drag-and-drop drops, which —
// unlike a native file input — don't enforce the `accept` attribute.
function acceptsFile(accept: string, name: string): boolean {
  const exts = accept.split(",").map((e) => e.trim().toLowerCase());
  return exts.includes(extensionOf(name));
}

function stemName(filename: string): string {
  return filename.replace(/\.[^/.]+$/, "");
}

function canWriteCollection(collection: CollectionRead): boolean {
  return collection.effective_role === "edit" || collection.effective_role === "admin";
}

export function UploadModal({
  open,
  onClose,
  onUploaded,
  defaultCollection,
  preloadFiles,
  preloadItems,
  initialMode,
}: {
  open: boolean;
  onClose: () => void;
  onUploaded: () => Promise<void>;
  defaultCollection?: string | null;
  preloadFiles?: File[] | null;
  preloadItems?: BulkItem[] | null;
  initialMode?: UploadMode;
}) {
  const router = useRouter();
  const auth = useRequireAuth();
  const { user } = useAuth();
  const meshRef = useRef<HTMLInputElement>(null);
  const gcodeRef = useRef<HTMLInputElement>(null);
  const [meshFile, setMeshFile] = useState<File | null>(null);
  const [gcodeFile, setGcodeFile] = useState<File | null>(null);
  const [mode, setMode] = useState<UploadMode>("files");
  // Bulk mode: each mesh becomes its own model, queued as an independent
  // ingest task. No mesh+G-code linking (that stays on the "Files" tab).
  // Picking/dropping a folder mirrors its subfolders into nested collections.
  const [bulkFiles, setBulkFiles] = useState<BulkItem[]>([]);
  const bulkRef = useRef<HTMLInputElement>(null);
  const bulkFolderRef = useRef<HTMLInputElement>(null);
  const [urlValue, setUrlValue] = useState("");
  const [zipFile, setZipFile] = useState<File | null>(null);
  const zipRef = useRef<HTMLInputElement>(null);
  const [manifest, setManifest] = useState<ArchiveManifest | null>(null);
  // Selected ids: archive entry names, model file ids, or collection member ids
  // — only one manifest is ever active at a time, so a single set is enough.
  const [selectedEntries, setSelectedEntries] = useState<Set<string>>(new Set());
  const reviewing = manifest !== null;
  const [modelName, setModelName] = useState("");
  const [tagInput, setTagInput] = useState("");
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);
  // NAS write-back: when mirroring is enabled, new uploads can target a library
  // instead of vault storage. Empty string = vault.
  const [libraries, setLibraries] = useState<ExternalLibrary[]>([]);
  const [targetLibraryId, setTargetLibraryId] = useState<number | "">("");
  // Shared taxonomy lists from the TanStack Query cache (deduped with the grid
  // and detail views; refetched after any create/delete).
  const { data: collections = [] } = useCollections();
  const { data: tags = [] } = useTags();
  const [catOpen, setCatOpen] = useState(false);
  const writableCollections = useMemo(() => collections.filter(canWriteCollection), [collections]);

  function sortIntoSlots(files: File[]) {
    for (const f of files) {
      if (isMeshFile(f.name)) setMeshFile(f);
      else if (isGcodeFile(f.name)) setGcodeFile(f);
    }
  }

  function applySeed(seed: UploadSeed) {
    if (seed.initialMode) setMode(seed.initialMode);
    if (!seed.preloadFiles?.length && !seed.preloadItems?.length) return;
    if (seed.initialMode === "bulk") {
      setBulkFiles(
        seed.preloadItems?.length ? seed.preloadItems : fileListToItems(seed.preloadFiles ?? []),
      );
    } else if (seed.initialMode === "zip") {
      setZipFile(seed.preloadFiles?.[0] ?? null);
    } else {
      setMeshFile(null);
      setGcodeFile(null);
      setModelName("");
      sortIntoSlots(seed.preloadFiles ?? []);
    }
  }

  // Where an upload lands unless the user picks another collection. Derived
  // during render instead of pushed into state by an effect, so it is right on
  // the first frame and still right when the writable-collection list arrives
  // after the modal is already open — without overwriting a pick made since.
  const suggestedCollection =
    defaultCollection ||
    (!user?.is_superuser && writableCollections.length > 0 ? writableCollections[0].path : "");
  const [pickedCollection, setPickedCollection] = useState<string | null>(null);
  const collectionPath = pickedCollection ?? suggestedCollection;

  // Seeding the form from the props above is an adjustment to changed props,
  // not synchronization with an external system, so it happens during render:
  // the modal's first painted frame already shows the dropped files. Closing
  // forgets the seed, so the next open re-applies it.
  const [seeded, setSeeded] = useState<UploadSeed | null>(null);
  const seed: UploadSeed = {
    preloadFiles: preloadFiles ?? null,
    preloadItems: preloadItems ?? null,
    initialMode: initialMode ?? null,
  };
  if (!open && seeded !== null) setSeeded(null);
  if (open && !sameSeed(seeded, seed)) {
    setSeeded(seed);
    setPickedCollection(null);
    applySeed(seed);
  }

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    getVaultConfig()
      .then((cfg) => {
        if (cancelled || !cfg.external_libraries_enabled) {
          setLibraries([]);
          return;
        }
        return listExternalLibraries().then((libs) => {
          if (!cancelled) setLibraries(libs.filter((l) => l.enabled));
        });
      })
      .catch(() => {
        if (!cancelled) setLibraries([]);
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  const filteredTags = useMemo(() => {
    const q = tagInput.toLowerCase().trim();
    return tags.filter(
      (t) => !selectedTags.includes(t.slug) && (q === "" || t.name.toLowerCase().includes(q)),
    );
  }, [tags, tagInput, selectedTags]);

  const canCreateNewTag =
    tagInput.trim().length > 0 &&
    !tags.find((t) => t.name.toLowerCase() === tagInput.trim().toLowerCase());

  const shownTags = filteredTags.slice(0, 6);
  const tagItems = [...shownTags, ...(canCreateNewTag ? [tagInput.trim()] : [])];
  const tagNav = useComboboxNav(tagInput ? tagItems.length : 0, {
    onSelect: (i) => {
      if (i < shownTags.length) {
        toggleTag(shownTags[i].slug);
      } else {
        doCreateTag(tagInput);
      }
      setTagInput("");
    },
    onCommitInput: () => {
      if (tagInput.trim()) {
        doCreateTag(tagInput);
        setTagInput("");
      }
    },
  });

  function autoName(f: File) {
    if (!modelName) setModelName(stemName(f.name));
  }

  // Merge newly picked/dropped meshes into the bulk queue (drops non-mesh
  // files and duplicates), then surface a notice for anything skipped.
  function addBulkItems(items: BulkItem[]) {
    setBulkFiles((prev) => mergeBulkItems(prev, items).items);
    const skipped = items.length - items.filter((it) => isMeshFile(it.file.name)).length;
    if (skipped > 0) {
      toast.warning(
        "Some files skipped",
        `${skipped} file${skipped === 1 ? "" : "s"} ignored — only 3D models (${MESH_ACCEPT}) are accepted here.`,
      );
    }
  }

  function reset() {
    setMeshFile(null);
    setGcodeFile(null);
    setMode("files");
    setBulkFiles([]);
    setUrlValue("");
    setZipFile(null);
    setManifest(null);
    setSelectedEntries(new Set());
    setModelName("");
    setPickedCollection(null);
    setSelectedTags([]);
    setTagInput("");
    setTargetLibraryId("");
    setSubmitting(false);
  }

  function close() {
    reset();
    onClose();
  }

  async function waitForJob(
    jid: string,
    taskId: string,
    {
      progressStart,
      progressEnd,
      pendingDetail,
      runningDetail,
      completedDetail,
      completeTask,
    }: {
      progressStart: number;
      progressEnd: number;
      pendingDetail: string;
      runningDetail: string;
      completedDetail: string;
      completeTask: boolean;
    },
  ): Promise<IngestJobStatus> {
    void progressStart;
    void pendingDetail;
    void runningDetail;
    const status = await waitForImportJob(jid);
    if (status.state === "failed") {
      throw new Error(status.error || "Ingestion job failed");
    }
    updateTask(taskId, {
      status: completeTask ? "completed" : "running",
      progress: completeTask ? 100 : progressEnd,
      detail: completedDetail,
    });
    return status;
  }

  async function runUploadTask({
    taskId,
    mesh,
    gcode,
    name,
    collection,
    tagsForUpload,
    libraryId,
    refreshAfter = true,
  }: {
    taskId: string;
    mesh: File | null;
    gcode: File | null;
    name: string;
    collection: string;
    tagsForUpload: string[];
    libraryId: number | "";
    refreshAfter?: boolean;
  }) {
    const appendLibrary = (fd: FormData) => {
      if (libraryId !== "") fd.append("target_library_id", String(libraryId));
    };
    try {
      if (mesh) {
        updateTask(taskId, {
          detail: `Uploading ${mesh.name}`,
          status: "running",
          progress: 15,
        });
        const meshFd = new FormData();
        meshFd.append("file", mesh);
        meshFd.append("model_name", name || mesh.name);
        if (collection) meshFd.append("collection", collection);
        if (tagsForUpload.length) meshFd.append("tags", tagsForUpload.join(","));
        appendLibrary(meshFd);
        const meshRes = await ingestModel(meshFd);
        linkTaskToJob(taskId, meshRes.job_id);

        updateTask(taskId, {
          detail: "Processing mesh and thumbnail",
          status: "running",
          progress: 35,
        });
        const meshStatus = await waitForJob(meshRes.job_id, taskId, {
          progressStart: 35,
          progressEnd: gcode ? 55 : 100,
          pendingDetail: "Waiting for the vault to start processing",
          runningDetail: "Extracting mesh metadata and thumbnail",
          completedDetail: gcode ? "Mesh processed; linking G-code" : "Upload processed",
          completeTask: !gcode,
        });

        if (!gcode) {
          if (refreshAfter) await onUploaded();
          return;
        }

        if (meshStatus.model_id == null) {
          throw new Error("Mesh job completed but no model_id returned");
        }

        const full = await getModel(meshStatus.model_id);
        updateTask(taskId, {
          detail: `Uploading ${gcode.name}`,
          status: "running",
          progress: 60,
        });
        const gcodeFd = new FormData();
        gcodeFd.append("file", gcode);
        gcodeFd.append("model_name", name || gcode.name);
        if (collection) gcodeFd.append("collection", collection);
        if (tagsForUpload.length) gcodeFd.append("tags", tagsForUpload.join(","));
        gcodeFd.append("source_hash", full.hash);
        appendLibrary(gcodeFd);
        const gcodeRes = await ingestOrca(gcodeFd);
        linkTaskToJob(taskId, gcodeRes.job_id);
        await waitForJob(gcodeRes.job_id, taskId, {
          progressStart: 70,
          progressEnd: 100,
          pendingDetail: "Waiting for the vault to start processing G-code",
          runningDetail: "Parsing slicer metadata and thumbnail",
          completedDetail: "Upload processed",
          completeTask: true,
        });
        if (refreshAfter) await onUploaded();
        return;
      }

      if (gcode) {
        updateTask(taskId, {
          detail: `Uploading ${gcode.name}`,
          status: "running",
          progress: 25,
        });
        const fd = new FormData();
        fd.append("file", gcode);
        fd.append("model_name", name || gcode.name);
        if (collection) fd.append("collection", collection);
        if (tagsForUpload.length) fd.append("tags", tagsForUpload.join(","));
        appendLibrary(fd);
        const res = await ingestOrca(fd);
        linkTaskToJob(taskId, res.job_id);
        await waitForJob(res.job_id, taskId, {
          progressStart: 45,
          progressEnd: 100,
          pendingDetail: "Waiting for the vault to start processing",
          runningDetail: "Parsing slicer metadata and thumbnail",
          completedDetail: "Upload processed",
          completeTask: true,
        });
        if (refreshAfter) await onUploaded();
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      updateTask(taskId, {
        status: "failed",
        progress: 100,
        detail: msg,
      });
      toast.error(err);
    }
  }

  // Bulk: create one task per mesh upfront (so the whole queue is visible in
  // the task center), then process sequentially to avoid hammering the vault
  // with concurrent uploads. Each file becomes its own model — no G-code
  // linking, which is what keeps this distinct from the "Files" tab.
  async function runBulkUpload({
    files,
    collection,
    tagsForUpload,
    libraryId,
  }: {
    files: BulkItem[];
    collection: string;
    tagsForUpload: string[];
    libraryId: number | "";
  }) {
    const queue = files.map((item) => ({
      item,
      taskId: createTask({
        title: `Upload ${item.relPath ? `${item.relPath}/` : ""}${item.file.name}`,
        detail: "Queued",
        status: "pending" as const,
        progress: 0,
        expectedJobCount: 1,
      }),
    }));
    try {
      for (const { item, taskId } of queue) {
        // Mirror the file's source folder into a nested collection under the
        // chosen base — the backend auto-creates intermediate collections.
        const targetCollection = bulkTargetCollection(collection, item.relPath);
        // runUploadTask owns its own error handling and marks the task failed,
        // so one bad file doesn't abort the rest of the queue.
        await runUploadTask({
          taskId,
          mesh: item.file,
          gcode: null,
          name: stemName(item.file.name),
          collection: targetCollection,
          tagsForUpload,
          libraryId,
          refreshAfter: false,
        });
      }
    } finally {
      // One invalidation after the entire queue, including partial failures.
      await onUploaded();
    }
  }

  // Modal workflows await Task Center's common terminal event; they never
  // start a second polling loop of their own.
  const waitForJobInline = (jid: string) => waitForImportJob(jid);

  function collectionGate(): boolean {
    if (!auth.isAuthenticated) {
      auth.showAuthRequiredToast();
      return false;
    }
    if (!user?.is_superuser && !collectionPath) {
      toast.warning("Collection required", "Choose a collection you can edit.");
      return false;
    }
    return true;
  }

  function startImportTask(jobId: string, title: string) {
    const taskId = trackImportJob(jobId, title);
    void (async () => {
      try {
        await waitForJob(jobId, taskId, {
          progressStart: 10,
          progressEnd: 100,
          pendingDetail: "Waiting for the vault to start importing",
          runningDetail: "Importing files",
          completedDetail: "Import processed",
          completeTask: true,
        });
        await onUploaded();
      } catch (err) {
        toast.error(err);
      }
    })();
  }

  async function runUrlImport() {
    if (!auth.isAuthenticated || submitting) {
      if (!auth.isAuthenticated) auth.showAuthRequiredToast();
      return;
    }
    setSubmitting(true);
    try {
      const item = await capturePendingImport({
        url: urlValue.trim(),
        collection_id: collectionPath
          ? (collections.find((collection) => collection.path === collectionPath)?.id ?? null)
          : null,
        tags: selectedTags,
      });
      toast.success("Captured URL. Review it before importing.");
      close();
      router.push(`/inbox/${item.id}`);
    } catch (err) {
      toast.error(err);
      setSubmitting(false);
    }
  }

  async function doInspectZip() {
    if (!collectionGate() || submitting || !zipFile) return;
    setSubmitting(true);
    try {
      const fd = new FormData();
      fd.append("file", zipFile);
      const response = await inspectArchive(fd);
      trackImportJob(response.job_id, `Inspect ${zipFile.name}`);
      const status = await waitForJobInline(response.job_id);
      if (status.state === "failed") throw new Error(status.error || "Archive inspection failed");
      const result: IngestJobResult = status.result ?? {};
      const m: ArchiveManifest = {
        archive_id: String(result.archive_id),
        archive_name: String(result.archive_name),
        entries: result.entries ?? [],
      };
      showManifest(m);
    } catch (err) {
      toast.error(err);
    } finally {
      setSubmitting(false);
    }
  }

  function showManifest(m: ArchiveManifest) {
    setManifest(m);
    setSelectedEntries(new Set(m.entries.filter((e) => e.file_type).map((e) => e.name)));
  }

  async function doImportSelected() {
    if (!manifest || submitting) return;
    const names = [...selectedEntries];
    if (names.length === 0) {
      toast.warning("Nothing selected", "Pick at least one file to import.");
      return;
    }
    setSubmitting(true);
    try {
      const res = await selectArchiveEntries(manifest.archive_id, {
        names,
        collection: collectionPath || undefined,
        tags: selectedTags.length ? selectedTags.join(",") : undefined,
      });
      startImportTask(res.job_id, `Import ${manifest.archive_name}`);
      close();
    } catch (err) {
      toast.error(err);
      setSubmitting(false);
    }
  }

  function doSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (submitting) return;
    if (manifest) {
      void doImportSelected();
      return;
    }
    if (mode === "url") {
      if (!urlValue.trim()) return;
      void runUrlImport();
      return;
    }
    if (mode === "zip") {
      if (!zipFile) return;
      void doInspectZip();
      return;
    }
    if (mode === "bulk") {
      if (bulkFiles.length === 0) return;
      if (!collectionGate()) return;
      void runBulkUpload({
        files: bulkFiles,
        collection: collectionPath,
        tagsForUpload: [...selectedTags],
        libraryId: targetLibraryId,
      });
      toast.success(
        `Queued ${bulkFiles.length} upload${bulkFiles.length === 1 ? "" : "s"} — track progress in the task center`,
      );
      reset();
      onClose();
      return;
    }
    if (!meshFile && !gcodeFile) return;
    if (!collectionGate()) return;
    const taskId = createTask({
      title: `Upload ${modelName || meshFile?.name || gcodeFile?.name || "model"}`,
      detail: "Preparing upload",
      status: "running",
      progress: 5,
      expectedJobCount: meshFile && gcodeFile ? 2 : 1,
    });
    setSubmitting(true);
    void runUploadTask({
      taskId,
      mesh: meshFile,
      gcode: gcodeFile,
      name: modelName,
      collection: collectionPath,
      tagsForUpload: [...selectedTags],
      libraryId: targetLibraryId,
    });
    reset();
    onClose();
  }

  async function doCreateTag(name: string) {
    const trimmed = name.trim();
    if (!trimmed) return;
    const existing = tags.find((t) => t.name.toLowerCase() === trimmed.toLowerCase());
    if (existing) {
      if (!selectedTags.includes(existing.slug)) toggleTag(existing.slug);
      return;
    }
    try {
      const t = await createTag({ name: trimmed });
      // createTag invalidates the query cache → useTags() refetches the new
      // tag; we just select it here.
      setSelectedTags((p) => [...p, t.slug]);
    } catch (err) {
      // 401 is surfaced by AuthBanner; duplicate slug is harmless.
      if (err instanceof ApiError && err.isAuthError) return;
      toast.error(err);
    }
  }

  function toggleTag(slug: string) {
    setSelectedTags((p) => (p.includes(slug) ? p.filter((s) => s !== slug) : [...p, slug]));
  }

  function toggleEntry(name: string) {
    setSelectedEntries((p) => {
      const next = new Set(p);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }

  return (
    <ModalShell
      open={open}
      onClose={close}
      labelledBy="upload-modal-title"
      className="bg-surface-container-lowest border border-outline-variant rounded-md w-full max-w-lg max-h-[90vh] overflow-y-auto shadow-2xl"
    >
      <>
        <div className="flex items-start justify-between gap-4 px-6 py-4 border-b border-outline-variant">
          <div>
            <h3 id="upload-modal-title" className="text-sm font-semibold text-on-surface">
              Upload model
            </h3>
            <p className="text-xs text-on-surface-variant mt-0.5">
              Drop a 3D model, a G-code, or both together
            </p>
          </div>
          <button
            onClick={close}
            aria-label="Close"
            className="h-7 w-7 -mt-1 rounded hover:bg-surface-container flex items-center justify-center text-on-surface-variant"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {!auth.isAuthenticated && (
          <div className="mx-6 mt-4 rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-200 font-mono">
            Sign in before uploading.{" "}
            <a href="/login" className="underline">
              Open login
            </a>
            .
          </div>
        )}

        <form onSubmit={doSubmit} className="p-6 space-y-5">
          {/* Mode tabs */}
          {!reviewing && (
            <div className="flex gap-1 rounded border border-outline-variant p-1">
              {(
                [
                  ["files", "Files", Upload],
                  ["bulk", "Bulk", Layers],
                  ["url", "From URL", Link2],
                  ["zip", "From ZIP", Package],
                ] as const
              ).map(([m, label, Icon]) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => setMode(m)}
                  className={`flex-1 flex items-center justify-center gap-1.5 rounded px-2 py-1.5 font-mono text-2xs uppercase tracking-wider transition-colors ${
                    mode === m
                      ? "bg-secondary-container text-on-secondary-container"
                      : "text-on-surface-variant hover:bg-surface-container-low"
                  }`}
                >
                  <Icon className="h-3.5 w-3.5" /> {label}
                </button>
              ))}
            </div>
          )}

          {/* Input area */}
          {manifest ? (
            <ManifestList
              manifest={manifest}
              selected={selectedEntries}
              onToggle={toggleEntry}
              onBack={() => {
                setManifest(null);
                setSelectedEntries(new Set());
              }}
            />
          ) : mode === "files" ? (
            <div className="space-y-3">
              <FileSlot
                label="3D Model"
                accept={MESH_ACCEPT}
                file={meshFile}
                setFile={(f) => {
                  setMeshFile(f);
                  if (f) autoName(f);
                }}
                placeholder={".stl .3mf .obj .step"}
                inputRef={meshRef}
              />
              <FileSlot
                label="G-code"
                accept={GCODE_ACCEPT}
                file={gcodeFile}
                setFile={(f) => {
                  setGcodeFile(f);
                  if (f) autoName(f);
                }}
                placeholder={".gcode .g .gco"}
                inputRef={gcodeRef}
              />
            </div>
          ) : mode === "bulk" ? (
            <BulkFiles
              items={bulkFiles}
              fileInputRef={bulkRef}
              folderInputRef={bulkFolderRef}
              onAddItems={addBulkItems}
              onRemove={(idx) => setBulkFiles((prev) => prev.filter((_, i) => i !== idx))}
              onClear={() => setBulkFiles([])}
            />
          ) : mode === "url" ? (
            <div>
              <label className="block font-mono text-xs text-on-surface-variant tracking-wider uppercase mb-2">
                Source URL
              </label>
              <input
                value={urlValue}
                onChange={(e) => setUrlValue(e.target.value)}
                className="w-full h-10 bg-surface-container-lowest text-on-surface font-mono text-sm border border-outline-variant rounded px-3 focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent"
                placeholder="Model page, collection, or direct .stl/.zip link"
              />
              <p className="mt-1.5 font-mono text-3xs text-on-surface-variant/70">
                Create a durable capture, then choose files and destination on the review page.
              </p>
            </div>
          ) : (
            <FileSlot
              label="ZIP archive"
              accept=".zip"
              file={zipFile}
              setFile={setZipFile}
              placeholder={".zip"}
              inputRef={zipRef}
            />
          )}

          {/* Model name (single-file uploads only; URL imports take their
                  name from the downloaded file/page) */}
          {!reviewing && mode === "files" && (
            <div>
              <label className="block font-mono text-xs text-on-surface-variant tracking-wider uppercase mb-2">
                Model name
              </label>
              <input
                value={modelName}
                onChange={(e) => setModelName(e.target.value)}
                className="w-full h-10 bg-surface-container-lowest text-on-surface font-mono text-sm border border-outline-variant rounded px-3 focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent"
                placeholder="e.g. Bracket v2"
              />
            </div>
          )}

          {/* Collection */}
          <div>
            <label className="block font-mono text-xs text-on-surface-variant tracking-wider uppercase mb-2">
              Collection
            </label>
            <DropdownMenu
              open={catOpen}
              onOpenChange={setCatOpen}
              align="start"
              role="listbox"
              contentClassName="w-full bg-surface-container-lowest border border-outline-variant rounded shadow-lg py-1 max-h-56 overflow-y-auto"
              trigger={
                <button
                  type="button"
                  data-menu-trigger
                  onClick={() => setCatOpen((v) => !v)}
                  aria-haspopup="listbox"
                  aria-expanded={catOpen}
                  className="w-full h-10 flex items-center justify-between bg-surface-container-lowest text-on-surface font-mono text-sm border border-outline-variant rounded px-3 focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent"
                >
                  <span className={collectionPath ? "" : "text-on-surface-variant/60"}>
                    {collectionPath || (user?.is_superuser ? "None" : "Choose collection")}
                  </span>
                  <ChevronDown className="h-4 w-4 text-on-surface-variant" />
                </button>
              }
            >
              {user?.is_superuser && (
                <button
                  type="button"
                  role="option"
                  aria-selected={collectionPath === ""}
                  onClick={() => {
                    setPickedCollection("");
                    setCatOpen(false);
                  }}
                  className="w-full text-left px-3 py-1.5 font-mono text-xs text-on-surface-variant hover:bg-surface-container-low"
                >
                  None
                </button>
              )}
              {writableCollections.length === 0 ? (
                <div className="px-3 py-2 font-mono text-2xs text-on-surface-variant/70">
                  No editable collections.
                </div>
              ) : (
                writableCollections.map((c) => (
                  <button
                    key={c.id}
                    type="button"
                    role="option"
                    aria-selected={collectionPath === c.path}
                    onClick={() => {
                      setPickedCollection(c.path);
                      setCatOpen(false);
                    }}
                    className={`w-full text-left px-3 py-1.5 font-mono text-xs transition-colors ${
                      collectionPath === c.path
                        ? "text-primary bg-secondary-container"
                        : "text-on-surface-variant hover:bg-surface-container-low"
                    }`}
                  >
                    {c.path} <span className="opacity-50">({c.model_count})</span>
                  </button>
                ))
              )}
            </DropdownMenu>
          </div>

          {/* Destination (NAS write-back) — only when mirroring is enabled */}
          {(mode === "files" || mode === "bulk") && libraries.length > 0 && (
            <div>
              <label className="block font-mono text-xs text-on-surface-variant tracking-wider uppercase mb-2">
                Store in
              </label>
              <select
                value={targetLibraryId}
                onChange={(e) =>
                  setTargetLibraryId(e.target.value === "" ? "" : Number(e.target.value))
                }
                className="w-full h-10 bg-surface-container-lowest text-on-surface font-mono text-sm border border-outline-variant rounded px-3 focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent"
              >
                <option value="">Vault storage</option>
                {libraries.map((lib) => (
                  <option key={lib.id} value={lib.id}>
                    {lib.name} (shared volume)
                  </option>
                ))}
              </select>
              <p className="mt-1 font-mono text-3xs text-on-surface-variant/70">
                NAS libraries write the file into the folder; revisions to a linked model always
                follow that model automatically.
              </p>
            </div>
          )}

          {/* Tags */}
          <div>
            <label className="block font-mono text-xs text-on-surface-variant tracking-wider uppercase mb-2">
              Tags
            </label>
            <div className="relative">
              <input
                value={tagInput}
                onChange={(e) => {
                  setTagInput(e.target.value);
                  tagNav.setActiveIndex(-1);
                }}
                {...tagNav.inputProps}
                onKeyDown={(e) => {
                  tagNav.inputProps.onKeyDown(e);
                  if (e.defaultPrevented) return;
                  if (e.key === "Backspace" && !tagInput && selectedTags.length) {
                    setSelectedTags((p) => p.slice(0, -1));
                  }
                }}
                placeholder="Search or create — press Enter"
                className="w-full h-10 bg-surface-container-lowest text-on-surface font-mono text-sm border border-outline-variant rounded px-3 focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent"
              />
              {tagInput && (filteredTags.length > 0 || canCreateNewTag) && (
                <div
                  id={tagNav.listboxId}
                  role="listbox"
                  className="pop-in absolute left-0 right-0 top-full mt-1 z-dropdown bg-surface-container-lowest border border-outline-variant rounded shadow-lg py-1 max-h-40 overflow-y-auto"
                >
                  {shownTags.map((t, i) => (
                    <button
                      key={t.id}
                      id={tagNav.optionId(i)}
                      role="option"
                      aria-selected={i === tagNav.activeIndex}
                      type="button"
                      onClick={() => {
                        toggleTag(t.slug);
                        setTagInput("");
                      }}
                      className={`w-full text-left px-3 py-1.5 font-mono text-xs text-on-surface-variant hover:bg-surface-container-low flex justify-between ${i === tagNav.activeIndex ? "bg-surface-container-low" : ""}`}
                    >
                      <span>{t.name}</span>
                      <span className="opacity-50">({t.model_count})</span>
                    </button>
                  ))}
                  {canCreateNewTag && (
                    <button
                      type="button"
                      id={tagNav.optionId(shownTags.length)}
                      role="option"
                      aria-selected={shownTags.length === tagNav.activeIndex}
                      onClick={() => {
                        doCreateTag(tagInput);
                        setTagInput("");
                      }}
                      className={`w-full text-left px-3 py-1.5 font-mono text-xs text-primary hover:bg-surface-container-low flex items-center gap-2 ${shownTags.length === tagNav.activeIndex ? "bg-surface-container-low" : ""}`}
                    >
                      <Plus className="h-3 w-3" /> Create &quot;
                      {tagInput.trim()}&quot;
                    </button>
                  )}
                </div>
              )}
            </div>
            {selectedTags.length > 0 && (
              <div className="flex flex-wrap gap-1.5 mt-2">
                {selectedTags.map((slug) => {
                  const t = tags.find((x) => x.slug === slug);
                  return (
                    <span
                      key={slug}
                      className="inline-flex items-center gap-1 bg-secondary-container text-on-secondary-container pl-2 pr-1 py-0.5 rounded font-mono text-3xs uppercase tracking-wider"
                    >
                      {t?.name || slug}
                      <button
                        type="button"
                        onClick={() => toggleTag(slug)}
                        aria-label={`Remove ${t?.name || slug}`}
                        className="h-3.5 w-3.5 rounded-sm flex items-center justify-center hover:bg-on-secondary-container/10"
                      >
                        <X className="h-3 w-3" />
                      </button>
                    </span>
                  );
                })}
              </div>
            )}
          </div>

          <div className="flex justify-end gap-3 pt-2">
            <button
              type="button"
              onClick={close}
              className="px-4 py-2 rounded border border-outline-variant text-on-surface-variant font-mono text-xs uppercase tracking-wider hover:bg-surface-container-low transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={
                submitting ||
                (!user?.is_superuser && !collectionPath) ||
                (reviewing
                  ? selectedEntries.size === 0
                  : mode === "files"
                    ? !meshFile && !gcodeFile
                    : mode === "bulk"
                      ? bulkFiles.length === 0
                      : mode === "url"
                        ? !urlValue.trim()
                        : !zipFile)
              }
              className="px-4 py-2 rounded bg-primary text-primary-foreground font-mono text-xs uppercase tracking-wider hover:opacity-90 transition-opacity disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
            >
              {submitting ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  {reviewing ? "Importing…" : mode === "zip" ? "Inspecting…" : "Working…"}
                </>
              ) : reviewing ? (
                `Import ${selectedEntries.size} selected`
              ) : mode === "bulk" ? (
                bulkFiles.length > 0 ? (
                  `Upload ${bulkFiles.length} model${bulkFiles.length === 1 ? "" : "s"}`
                ) : (
                  "Upload to vault"
                )
              ) : mode === "url" ? (
                "Review URL"
              ) : mode === "zip" ? (
                "Inspect archive"
              ) : (
                "Upload to vault"
              )}
            </button>
          </div>
        </form>
      </>
    </ModalShell>
  );
}

function FileSlot({
  label,
  accept,
  file,
  setFile,
  placeholder,
  inputRef,
}: {
  label: string;
  accept: string;
  file: File | null;
  setFile: (f: File | null) => void;
  placeholder: string;
  inputRef: React.RefObject<HTMLInputElement | null>;
}) {
  const [dragActive, setDragActive] = useState(false);
  // Count drag enter/leave so crossing a child element doesn't flicker the
  // highlight off — only a real leave (depth back to 0) clears it.
  const dragDepth = useRef(0);
  return (
    <div>
      <span className="block font-mono text-3xs text-on-surface-variant tracking-wider uppercase mb-1.5">
        {label}
      </span>
      <div
        onClick={() => {
          if (!file) inputRef.current?.click();
        }}
        onDragEnter={(e) => {
          e.preventDefault();
          dragDepth.current += 1;
          setDragActive(true);
        }}
        onDragOver={(e) => {
          e.preventDefault();
          e.dataTransfer.dropEffect = "copy";
        }}
        onDragLeave={() => {
          dragDepth.current -= 1;
          if (dragDepth.current <= 0) {
            dragDepth.current = 0;
            setDragActive(false);
          }
        }}
        onDrop={(e) => {
          e.preventDefault();
          dragDepth.current = 0;
          setDragActive(false);
          const dropped = e.dataTransfer.files?.[0];
          if (!dropped) return;
          if (!acceptsFile(accept, dropped.name)) {
            toast.warning(`Wrong file type for ${label}`, `Drop a ${accept} file here.`);
            return;
          }
          setFile(dropped);
        }}
        className={`flex items-center justify-between rounded border border-dashed px-3 py-2.5 transition-colors cursor-pointer ${
          file
            ? "border-primary bg-primary/5"
            : dragActive
              ? "border-primary bg-primary/10"
              : "border-outline-variant hover:border-outline"
        }`}
      >
        {file ? (
          <div className="flex items-center gap-2 min-w-0 flex-1">
            <FileIcon className="h-4 w-4 text-primary flex-shrink-0" />
            <span className="text-xs font-medium text-on-surface truncate">{file.name}</span>
            <span className="font-mono text-3xs text-on-surface-variant flex-shrink-0">
              {formatBytes(file.size)}
            </span>
            <button
              type="button"
              onClick={(ev) => {
                ev.stopPropagation();
                setFile(null);
              }}
              aria-label={`Remove ${label}`}
              className="h-5 w-5 rounded hover:bg-surface-container flex items-center justify-center text-on-surface-variant flex-shrink-0"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        ) : (
          <span className="font-mono text-xs text-on-surface-variant/60">{placeholder}</span>
        )}
        <input
          ref={inputRef}
          type="file"
          accept={accept}
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          className="hidden"
        />
      </div>
    </div>
  );
}

// Exported for unit tests; not used outside this module.
export function BulkFiles({
  items,
  fileInputRef,
  folderInputRef,
  onAddItems,
  onRemove,
  onClear,
}: {
  items: BulkItem[];
  fileInputRef: React.RefObject<HTMLInputElement | null>;
  folderInputRef: React.RefObject<HTMLInputElement | null>;
  onAddItems: (items: BulkItem[]) => void;
  onRemove: (index: number) => void;
  onClear: () => void;
}) {
  const [dragActive, setDragActive] = useState(false);
  // See FileSlot: count enter/leave depth so dragging over child elements
  // (icon, hint text, the "select a folder" button) doesn't flicker the
  // highlight off.
  const dragDepth = useRef(0);
  const totalBytes = items.reduce((sum, it) => sum + it.file.size, 0);
  const folderCount = new Set(items.map((it) => it.relPath).filter(Boolean)).size;

  function handleDrop(e: React.DragEvent) {
    e.preventDefault();
    dragDepth.current = 0;
    setDragActive(false);
    const dt = e.dataTransfer;
    // Entries must be pulled out synchronously — the DataTransfer is emptied
    // once this handler returns; the async folder walk happens afterwards.
    const entries = entriesFromDataTransfer(dt.items);
    if (entries.length > 0) {
      walkEntries(entries)
        .then(onAddItems)
        .catch((err) =>
          toast.error(err instanceof Error ? err : new Error("Couldn't read the dropped folder")),
        );
    } else if (dt.files?.length) {
      // Browsers without the entries API still give a flat FileList.
      onAddItems(fileListToItems(dt.files));
    }
  }

  return (
    <div>
      <div
        onClick={() => fileInputRef.current?.click()}
        onDragEnter={(e) => {
          e.preventDefault();
          dragDepth.current += 1;
          setDragActive(true);
        }}
        onDragOver={(e) => {
          e.preventDefault();
          e.dataTransfer.dropEffect = "copy";
        }}
        onDragLeave={() => {
          dragDepth.current -= 1;
          if (dragDepth.current <= 0) {
            dragDepth.current = 0;
            setDragActive(false);
          }
        }}
        onDrop={handleDrop}
        className={`flex flex-col items-center justify-center gap-1.5 rounded border border-dashed px-3 py-6 text-center transition-colors cursor-pointer ${
          dragActive
            ? "border-primary bg-primary/10"
            : "border-outline-variant hover:border-outline"
        }`}
      >
        <Layers className="h-5 w-5 text-on-surface-variant" />
        <span className="text-xs text-on-surface">Drop 3D models or a folder here</span>
        <span className="font-mono text-3xs text-on-surface-variant/60">
          {MESH_ACCEPT} · subfolders become nested collections
        </span>
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            folderInputRef.current?.click();
          }}
          className="mt-1 font-mono text-3xs text-primary uppercase tracking-wider hover:underline"
        >
          Or select a folder
        </button>
        <input
          ref={fileInputRef}
          type="file"
          accept={MESH_ACCEPT}
          multiple
          onChange={(e) => {
            if (e.target.files?.length) onAddItems(fileListToItems(e.target.files));
            // Allow re-picking the same files after a removal.
            e.target.value = "";
          }}
          className="hidden"
        />
        <input
          ref={folderInputRef}
          type="file"
          webkitdirectory=""
          multiple
          onChange={(e) => {
            if (e.target.files?.length) onAddItems(fileListToItems(e.target.files));
            e.target.value = "";
          }}
          className="hidden"
        />
      </div>
      {items.length > 0 && (
        <>
          <div className="flex items-center justify-between mt-2 mb-1.5">
            <span className="font-mono text-3xs text-on-surface-variant tracking-wider uppercase">
              {items.length} file{items.length === 1 ? "" : "s"}
              {folderCount > 0
                ? ` · ${folderCount} folder${folderCount === 1 ? "" : "s"}`
                : ""} ·{" "}
              {formatBytes(totalBytes)}
            </span>
            <button
              type="button"
              onClick={onClear}
              className="font-mono text-3xs text-on-surface-variant uppercase tracking-wider hover:text-on-surface"
            >
              Clear
            </button>
          </div>
          <div className="rounded border border-outline-variant divide-y divide-outline-variant max-h-56 overflow-y-auto">
            {items.map((it, idx) => (
              <div
                key={`${it.relPath}/${it.file.name}:${it.file.size}:${idx}`}
                className="flex items-center gap-2 px-3 py-2"
              >
                <FileIcon className="h-4 w-4 text-primary flex-shrink-0" />
                <span className="min-w-0 flex-1 truncate text-xs text-on-surface">
                  {it.relPath && <span className="text-on-surface-variant/60">{it.relPath}/</span>}
                  {it.file.name}
                </span>
                <span className="font-mono text-3xs text-on-surface-variant flex-shrink-0">
                  {formatBytes(it.file.size)}
                </span>
                <button
                  type="button"
                  onClick={() => onRemove(idx)}
                  aria-label={`Remove ${it.file.name}`}
                  className="h-5 w-5 rounded hover:bg-surface-container flex items-center justify-center text-on-surface-variant flex-shrink-0"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function ManifestList({
  manifest,
  selected,
  onToggle,
  onBack,
}: {
  manifest: ArchiveManifest;
  selected: Set<string>;
  onToggle: (name: string) => void;
  onBack: () => void;
}) {
  const importable = manifest.entries.filter((e) => e.file_type);
  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <span className="font-mono text-3xs text-on-surface-variant tracking-wider uppercase truncate">
          {manifest.archive_name} · {importable.length} importable
        </span>
        <button
          type="button"
          onClick={onBack}
          className="font-mono text-3xs text-on-surface-variant uppercase tracking-wider hover:text-on-surface"
        >
          Back
        </button>
      </div>
      <div className="rounded border border-outline-variant divide-y divide-outline-variant max-h-56 overflow-y-auto">
        {importable.length === 0 ? (
          <div className="px-3 py-3 font-mono text-2xs text-on-surface-variant/70">
            No importable 3D files in this archive.
          </div>
        ) : (
          importable.map((e) => (
            <label
              key={e.name}
              className="flex items-center gap-2 px-3 py-2 cursor-pointer hover:bg-surface-container-low"
            >
              <input
                type="checkbox"
                checked={selected.has(e.name)}
                onChange={() => onToggle(e.name)}
                className="accent-primary"
              />
              <span className="text-xs text-on-surface truncate flex-1">{e.name}</span>
              <span className="font-mono text-3xs uppercase text-on-surface-variant flex-shrink-0">
                {e.file_type}
              </span>
              <span className="font-mono text-3xs text-on-surface-variant flex-shrink-0">
                {formatBytes(e.size_bytes)}
              </span>
            </label>
          ))
        )}
      </div>
    </div>
  );
}
