"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronDown,
  File as FileIcon,
  Link2,
  Loader2,
  Package,
  Plus,
  Upload,
  X,
} from "lucide-react";
import {
  createTag,
  getJobStatus,
  getModel,
  ingestArchive,
  ingestModel,
  ingestOrca,
  ingestUrl,
  selectArchiveEntries,
} from "@/lib/api";
import { useCollections, useTags } from "@/lib/queries";
import { toast } from "@/lib/toast";
import { createTask, updateTask } from "@/lib/task-center";
import { useRequireAuth } from "@/lib/use-require-auth";
import { useAuth } from "@/lib/auth-context";
import { formatBytes } from "@/lib/format";
import { ArchiveManifest, CollectionRead, IngestJobStatus } from "@/types";

type UploadMode = "files" | "url" | "zip";

const MESH_EXT = new Set([".stl", ".3mf", ".obj", ".step", ".stp"]);
const GCODE_EXT = new Set([".gcode", ".g", ".gco"]);
const MESH_ACCEPT = ".stl,.3mf,.obj,.step,.stp";
const GCODE_ACCEPT = ".gcode,.g,.gco";

function ext(filename: string): string {
  return "." + (filename.split(".").pop()?.toLowerCase() ?? "");
}

function isMesh(name: string): boolean {
  return MESH_EXT.has(ext(name));
}

function isGcode(name: string): boolean {
  return GCODE_EXT.has(ext(name));
}


function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

const POLL_INTERVAL_MS = 1_000;
const POLL_TIMEOUT_MS = 15 * 60_000;

function canWriteCollection(collection: CollectionRead): boolean {
  return collection.effective_role === "edit" || collection.effective_role === "admin";
}

export function UploadModal({
  open,
  onClose,
  onUploaded,
  defaultCollection,
}: {
  open: boolean;
  onClose: () => void;
  onUploaded: () => void;
  defaultCollection?: string | null;
}) {
  const auth = useRequireAuth();
  const { user } = useAuth();
  const meshRef = useRef<HTMLInputElement>(null);
  const gcodeRef = useRef<HTMLInputElement>(null);
  const [meshFile, setMeshFile] = useState<File | null>(null);
  const [gcodeFile, setGcodeFile] = useState<File | null>(null);
  const [mode, setMode] = useState<UploadMode>("files");
  const [urlValue, setUrlValue] = useState("");
  const [zipFile, setZipFile] = useState<File | null>(null);
  const zipRef = useRef<HTMLInputElement>(null);
  const [manifest, setManifest] = useState<ArchiveManifest | null>(null);
  const [selectedEntries, setSelectedEntries] = useState<Set<string>>(new Set());
  const [modelName, setModelName] = useState("");
  const [collectionPath, setCollectionPath] = useState(defaultCollection ?? "");
  const [tagInput, setTagInput] = useState("");
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);
  // Shared taxonomy lists from the TanStack Query cache (deduped with the grid
  // and detail views; refetched after any create/delete).
  const { data: collections = [] } = useCollections();
  const { data: tags = [] } = useTags();
  const [catOpen, setCatOpen] = useState(false);
  const writableCollections = useMemo(
    () => collections.filter(canWriteCollection),
    [collections],
  );

  useEffect(() => {
    if (!open) return;
    if (defaultCollection) {
      setCollectionPath(defaultCollection);
      return;
    }
    if (!user?.is_superuser && writableCollections.length > 0) {
      setCollectionPath(writableCollections[0].path);
      return;
    }
    setCollectionPath("");
  }, [open, defaultCollection, user?.is_superuser, writableCollections]);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        reset();
        onClose();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const filteredTags = useMemo(() => {
    const q = tagInput.toLowerCase().trim();
    return tags.filter(
      (t) =>
        !selectedTags.includes(t.slug) &&
        (q === "" || t.name.toLowerCase().includes(q)),
    );
  }, [tags, tagInput, selectedTags]);

  const canCreateNewTag =
    tagInput.trim().length > 0 &&
    !tags.find(
      (t) => t.name.toLowerCase() === tagInput.trim().toLowerCase(),
    );

  if (!open) return null;

  function autoName(f: File) {
    if (!modelName)
      setModelName(f.name.replace(/\.[^/.]+$/, ""));
  }

  function sortIntoSlots(files: FileList | File[]) {
    for (const f of Array.from(files)) {
      if (isMesh(f.name)) {
        setMeshFile(f);
        autoName(f);
      } else if (isGcode(f.name)) {
        setGcodeFile(f);
        autoName(f);
      }
    }
  }

  function reset() {
    setMeshFile(null);
    setGcodeFile(null);
    setMode("files");
    setUrlValue("");
    setZipFile(null);
    setManifest(null);
    setSelectedEntries(new Set());
    setModelName("");
    setCollectionPath(defaultCollection ?? "");
    setSelectedTags([]);
    setTagInput("");
    setSubmitting(false);
  }

  function close() {
    reset();
    onClose();
  }

  async function pollJob(
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
    const startedAt = Date.now();
    let attempts = 0;

    while (Date.now() - startedAt < POLL_TIMEOUT_MS) {
      await sleep(POLL_INTERVAL_MS);
      attempts += 1;
      const status = await getJobStatus(jid);

      if (status.state === "completed") {
        updateTask(taskId, {
          status: completeTask ? "completed" : "running",
          progress: completeTask ? 100 : progressEnd,
          detail: completedDetail,
        });
        return status;
      }

      if (status.state === "failed") {
        updateTask(taskId, {
          status: "failed",
          progress: 100,
          detail: status.error || "Ingestion job failed",
        });
        throw new Error(status.error || "Ingestion job failed");
      }

      if (status.state === "pending" || status.state === "running") {
        // Prefer the backend's real per-step progress when available; fall
        // back to the time-based estimate for older backends.
        const fraction =
          typeof status.progress === "number"
            ? status.progress / 100
            : Math.min(attempts / 60, 1);
        const progress =
          progressStart + fraction * (progressEnd - progressStart);
        const stepDetail = status.label
          ? `${status.label.replaceAll("_", " ")}${
              status.step && status.total_steps
                ? ` (${status.step}/${status.total_steps})`
                : ""
            }`
          : null;
        updateTask(taskId, {
          status: status.state,
          progress,
          detail:
            stepDetail ??
            (status.state === "pending" ? pendingDetail : runningDetail),
        });
      }
    }

    throw new Error("Timed out waiting for ingestion to complete");
  }

  async function runUploadTask({
    taskId,
    mesh,
    gcode,
    name,
    collection,
    tagsForUpload,
  }: {
    taskId: string;
    mesh: File | null;
    gcode: File | null;
    name: string;
    collection: string;
    tagsForUpload: string[];
  }) {
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
        const meshRes = await ingestModel(meshFd);

        updateTask(taskId, {
          detail: "Processing mesh and thumbnail",
          status: "running",
          progress: 35,
        });
        const meshStatus = await pollJob(meshRes.job_id, taskId, {
          progressStart: 35,
          progressEnd: gcode ? 55 : 100,
          pendingDetail: "Waiting for the vault to start processing",
          runningDetail: "Extracting mesh metadata and thumbnail",
          completedDetail: gcode ? "Mesh processed; linking G-code" : "Upload processed",
          completeTask: !gcode,
        });

        if (!gcode) {
          onUploaded();
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
        const gcodeRes = await ingestOrca(gcodeFd);
        await pollJob(gcodeRes.job_id, taskId, {
          progressStart: 70,
          progressEnd: 100,
          pendingDetail: "Waiting for the vault to start processing G-code",
          runningDetail: "Parsing slicer metadata and thumbnail",
          completedDetail: "Upload processed",
          completeTask: true,
        });
        onUploaded();
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
        const res = await ingestOrca(fd);
        await pollJob(res.job_id, taskId, {
          progressStart: 45,
          progressEnd: 100,
          pendingDetail: "Waiting for the vault to start processing",
          runningDetail: "Parsing slicer metadata and thumbnail",
          completedDetail: "Upload processed",
          completeTask: true,
        });
        onUploaded();
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

  // Inline poll (modal stays open) — used for URL import where the result may
  // be an archive manifest the user must act on before anything is imported.
  async function pollJobInline(jid: string): Promise<IngestJobStatus> {
    const startedAt = Date.now();
    while (Date.now() - startedAt < POLL_TIMEOUT_MS) {
      await sleep(POLL_INTERVAL_MS);
      const status = await getJobStatus(jid);
      if (status.state === "completed" || status.state === "failed") return status;
    }
    throw new Error("Timed out waiting for the import to complete");
  }

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
    const taskId = createTask({
      title,
      detail: "Importing",
      status: "running",
      progress: 10,
    });
    void (async () => {
      try {
        await pollJob(jobId, taskId, {
          progressStart: 10,
          progressEnd: 100,
          pendingDetail: "Waiting for the vault to start importing",
          runningDetail: "Importing files",
          completedDetail: "Import processed",
          completeTask: true,
        });
        onUploaded();
      } catch (err) {
        toast.error(err);
      }
    })();
  }

  async function runUrlImport() {
    if (!collectionGate() || submitting) return;
    setSubmitting(true);
    try {
      const res = await ingestUrl({
        url: urlValue.trim(),
        collection: collectionPath || undefined,
        model_name: modelName || undefined,
        tags: selectedTags.length ? selectedTags.join(",") : undefined,
      });
      const status = await pollJobInline(res.job_id);
      if (status.state === "failed") {
        throw new Error(status.error || "Import failed");
      }
      const result = (status.result ?? {}) as Record<string, unknown>;
      if (result.kind === "archive_manifest") {
        const m: ArchiveManifest = {
          archive_id: String(result.archive_id),
          archive_name: String(result.archive_name),
          entries: (result.entries as ArchiveManifest["entries"]) ?? [],
        };
        showManifest(m);
        setSubmitting(false);
        return;
      }
      toast.success("Model imported from URL");
      onUploaded();
      close();
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
      const m = await ingestArchive(fd);
      showManifest(m);
    } catch (err) {
      toast.error(err);
    } finally {
      setSubmitting(false);
    }
  }

  function showManifest(m: ArchiveManifest) {
    setManifest(m);
    setSelectedEntries(
      new Set(m.entries.filter((e) => e.file_type).map((e) => e.name)),
    );
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
    if (!meshFile && !gcodeFile) return;
    if (!collectionGate()) return;
    const taskId = createTask({
      title: `Upload ${modelName || meshFile?.name || gcodeFile?.name || "model"}`,
      detail: "Preparing upload",
      status: "running",
      progress: 5,
    });
    setSubmitting(true);
    void runUploadTask({
      taskId,
      mesh: meshFile,
      gcode: gcodeFile,
      name: modelName,
      collection: collectionPath,
      tagsForUpload: [...selectedTags],
    });
    reset();
    onClose();
  }

  async function doCreateTag(name: string) {
    const trimmed = name.trim();
    if (!trimmed) return;
    const existing = tags.find(
      (t) => t.name.toLowerCase() === trimmed.toLowerCase(),
    );
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
      if (err && typeof err === "object" && "status" in err && (err as { status: number }).status === 401) return;
      toast.error(err);
    }
  }

  function toggleTag(slug: string) {
    setSelectedTags((p) =>
      p.includes(slug) ? p.filter((s) => s !== slug) : [...p, slug],
    );
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
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div
        className="absolute inset-0 bg-black/40 backdrop-blur-sm"
        onClick={close}
        aria-hidden
      />
      <div
        className="relative bg-[var(--surface-container-lowest)] border border-[var(--outline-variant)] rounded-md w-full max-w-lg max-h-[90vh] overflow-y-auto shadow-2xl"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="upload-modal-title"
      >
        <>
            <div className="flex items-start justify-between gap-4 px-6 py-4 border-b border-[var(--outline-variant)]">
              <div>
                <h3
                  id="upload-modal-title"
                  className="text-sm font-semibold text-[var(--on-surface)]"
                >
                  Upload model
                </h3>
                <p className="text-xs text-[var(--on-surface-variant)] mt-0.5">
                  Drop a 3D model, a G-code, or both together
                </p>
              </div>
              <button
                onClick={close}
                aria-label="Close"
                className="h-7 w-7 -mt-1 rounded hover:bg-[var(--surface-container)] flex items-center justify-center text-[var(--on-surface-variant)]"
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
              {!manifest && (
                <div className="flex gap-1 rounded border border-[var(--outline-variant)] p-1">
                  {(
                    [
                      ["files", "Files", Upload],
                      ["url", "From URL", Link2],
                      ["zip", "From ZIP", Package],
                    ] as const
                  ).map(([m, label, Icon]) => (
                    <button
                      key={m}
                      type="button"
                      onClick={() => setMode(m)}
                      className={`flex-1 flex items-center justify-center gap-1.5 rounded px-2 py-1.5 font-mono text-[11px] uppercase tracking-wider transition-colors ${
                        mode === m
                          ? "bg-[var(--secondary-container)] text-[var(--on-secondary-container)]"
                          : "text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-low)]"
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
              ) : mode === "url" ? (
                <div>
                  <label className="block font-mono text-xs text-[var(--on-surface-variant)] tracking-wider uppercase mb-2">
                    Source URL
                  </label>
                  <input
                    value={urlValue}
                    onChange={(e) => setUrlValue(e.target.value)}
                    className="w-full h-10 bg-[var(--surface-container-lowest)] text-[var(--on-surface)] font-mono text-sm border border-[var(--outline-variant)] rounded px-3 focus:outline-none focus:ring-2 focus:ring-[var(--primary)] focus:border-transparent"
                    placeholder="https://…/model.stl or …/pack.zip"
                  />
                  <p className="mt-1.5 font-mono text-[10px] text-[var(--on-surface-variant)]/70">
                    Direct file or .zip link — downloaded on the server.
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

              {/* Model name (single-file modes only) */}
              {!manifest && mode !== "zip" && (
                <div>
                  <label className="block font-mono text-xs text-[var(--on-surface-variant)] tracking-wider uppercase mb-2">
                    Model name
                  </label>
                  <input
                    value={modelName}
                    onChange={(e) => setModelName(e.target.value)}
                    className="w-full h-10 bg-[var(--surface-container-lowest)] text-[var(--on-surface)] font-mono text-sm border border-[var(--outline-variant)] rounded px-3 focus:outline-none focus:ring-2 focus:ring-[var(--primary)] focus:border-transparent"
                    placeholder="e.g. Bracket v2"
                  />
                </div>
              )}

              {/* Collection */}
              <div>
                <label className="block font-mono text-xs text-[var(--on-surface-variant)] tracking-wider uppercase mb-2">
                  Collection
                </label>
                <div className="relative">
                  <button
                    type="button"
                    onClick={() => setCatOpen((v) => !v)}
                    className="w-full h-10 flex items-center justify-between bg-[var(--surface-container-lowest)] text-[var(--on-surface)] font-mono text-sm border border-[var(--outline-variant)] rounded px-3 focus:outline-none focus:ring-2 focus:ring-[var(--primary)] focus:border-transparent"
                  >
                    <span
                      className={
                        collectionPath
                          ? ""
                          : "text-[var(--on-surface-variant)]/60"
                      }
                    >
                  {collectionPath || (user?.is_superuser ? "None" : "Choose collection")}
                    </span>
                    <ChevronDown className="h-4 w-4 text-[var(--on-surface-variant)]" />
                  </button>
                  {catOpen && (
                    <>
                      <div
                        className="fixed inset-0 z-20"
                        onClick={() => setCatOpen(false)}
                      />
                      <div className="absolute left-0 right-0 top-full mt-1 z-30 bg-[var(--surface-container-lowest)] border border-[var(--outline-variant)] rounded shadow-lg py-1 max-h-56 overflow-y-auto">
                        {user?.is_superuser && (
                          <button
                            type="button"
                            onClick={() => {
                              setCollectionPath("");
                              setCatOpen(false);
                            }}
                            className="w-full text-left px-3 py-1.5 font-mono text-xs text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-low)]"
                          >
                            None
                          </button>
                        )}
                        {writableCollections.length === 0 ? (
                          <div className="px-3 py-2 font-mono text-[11px] text-[var(--on-surface-variant)]/70">
                            No editable collections.
                          </div>
                        ) : (
                          writableCollections.map((c) => (
                            <button
                              key={c.id}
                              type="button"
                              onClick={() => {
                                setCollectionPath(c.path);
                                setCatOpen(false);
                              }}
                              className={`w-full text-left px-3 py-1.5 font-mono text-xs transition-colors ${
                                collectionPath === c.path
                                  ? "text-[var(--primary)] bg-[var(--secondary-container)]"
                                  : "text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-low)]"
                              }`}
                            >
                              {c.path}{" "}
                              <span className="opacity-50">
                                ({c.model_count})
                              </span>
                            </button>
                          ))
                        )}
                      </div>
                    </>
                  )}
                </div>
              </div>

              {/* Tags */}
              <div>
                <label className="block font-mono text-xs text-[var(--on-surface-variant)] tracking-wider uppercase mb-2">
                  Tags
                </label>
                <div className="relative">
                  <input
                    value={tagInput}
                    onChange={(e) => setTagInput(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && tagInput.trim()) {
                        e.preventDefault();
                        doCreateTag(tagInput);
                        setTagInput("");
                      } else if (
                        e.key === "Backspace" &&
                        !tagInput &&
                        selectedTags.length
                      ) {
                        setSelectedTags((p) => p.slice(0, -1));
                      }
                    }}
                    placeholder="Search or create — press Enter"
                    className="w-full h-10 bg-[var(--surface-container-lowest)] text-[var(--on-surface)] font-mono text-sm border border-[var(--outline-variant)] rounded px-3 focus:outline-none focus:ring-2 focus:ring-[var(--primary)] focus:border-transparent"
                  />
                  {tagInput &&
                    (filteredTags.length > 0 || canCreateNewTag) && (
                      <div className="absolute left-0 right-0 top-full mt-1 z-30 bg-[var(--surface-container-lowest)] border border-[var(--outline-variant)] rounded shadow-lg py-1 max-h-40 overflow-y-auto">
                        {filteredTags.slice(0, 6).map((t) => (
                          <button
                            key={t.id}
                            type="button"
                            onClick={() => {
                              toggleTag(t.slug);
                              setTagInput("");
                            }}
                            className="w-full text-left px-3 py-1.5 font-mono text-xs text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-low)] flex justify-between"
                          >
                            <span>{t.name}</span>
                            <span className="opacity-50">
                              ({t.model_count})
                            </span>
                          </button>
                        ))}
                        {canCreateNewTag && (
                          <button
                            type="button"
                            onClick={() => {
                              doCreateTag(tagInput);
                              setTagInput("");
                            }}
                            className="w-full text-left px-3 py-1.5 font-mono text-xs text-[var(--primary)] hover:bg-[var(--surface-container-low)] flex items-center gap-2"
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
                          className="inline-flex items-center gap-1 bg-[var(--secondary-container)] text-[var(--on-secondary-container)] pl-2 pr-1 py-0.5 rounded font-mono text-[10px] uppercase tracking-wider"
                        >
                          {t?.name || slug}
                          <button
                            type="button"
                            onClick={() => toggleTag(slug)}
                            aria-label={`Remove ${t?.name || slug}`}
                            className="h-3.5 w-3.5 rounded-sm flex items-center justify-center hover:bg-[var(--on-secondary-container)]/10"
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
                  className="px-4 py-2 rounded border border-[var(--outline-variant)] text-[var(--on-surface-variant)] font-mono text-xs uppercase tracking-wider hover:bg-[var(--surface-container-low)] transition-colors"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={
                    submitting ||
                    (!user?.is_superuser && !collectionPath) ||
                    (manifest
                      ? selectedEntries.size === 0
                      : mode === "files"
                        ? !meshFile && !gcodeFile
                        : mode === "url"
                          ? !urlValue.trim()
                          : !zipFile)
                  }
                  className="px-4 py-2 rounded bg-[var(--primary)] text-[var(--primary-foreground)] font-mono text-xs uppercase tracking-wider hover:opacity-90 transition-opacity disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
                >
                  {submitting ? (
                    <>
                      <Loader2 className="h-4 w-4 animate-spin" />
                      {manifest
                        ? "Importing…"
                        : mode === "zip"
                          ? "Inspecting…"
                          : "Working…"}
                    </>
                  ) : manifest ? (
                    `Import ${selectedEntries.size} selected`
                  ) : mode === "url" ? (
                    "Import from URL"
                  ) : mode === "zip" ? (
                    "Inspect archive"
                  ) : (
                    "Upload to vault"
                  )}
                </button>
              </div>
            </form>
        </>
      </div>
    </div>
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
  return (
    <div>
      <span className="block font-mono text-[10px] text-[var(--on-surface-variant)] tracking-wider uppercase mb-1.5">
        {label}
      </span>
      <div
        onClick={() => {
          if (!file) inputRef.current?.click();
        }}
        className={`flex items-center justify-between rounded border border-dashed px-3 py-2.5 transition-colors cursor-pointer ${
          file
            ? "border-[var(--primary)] bg-[var(--primary)]/5"
            : "border-[var(--outline-variant)] hover:border-[var(--outline)]"
        }`}
      >
        {file ? (
          <div className="flex items-center gap-2 min-w-0 flex-1">
            <FileIcon className="h-4 w-4 text-[var(--primary)] flex-shrink-0" />
            <span className="text-xs font-medium text-[var(--on-surface)] truncate">
              {file.name}
            </span>
            <span className="font-mono text-[10px] text-[var(--on-surface-variant)] flex-shrink-0">
              {formatBytes(file.size)}
            </span>
            <button
              type="button"
              onClick={(ev) => {
                ev.stopPropagation();
                setFile(null);
              }}
              aria-label={`Remove ${label}`}
              className="h-5 w-5 rounded hover:bg-[var(--surface-container)] flex items-center justify-center text-[var(--on-surface-variant)] flex-shrink-0"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        ) : (
          <span className="font-mono text-xs text-[var(--on-surface-variant)]/60">
            {placeholder}
          </span>
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
        <span className="font-mono text-[10px] text-[var(--on-surface-variant)] tracking-wider uppercase truncate">
          {manifest.archive_name} · {importable.length} importable
        </span>
        <button
          type="button"
          onClick={onBack}
          className="font-mono text-[10px] text-[var(--on-surface-variant)] uppercase tracking-wider hover:text-[var(--on-surface)]"
        >
          Back
        </button>
      </div>
      <div className="rounded border border-[var(--outline-variant)] divide-y divide-[var(--outline-variant)] max-h-56 overflow-y-auto">
        {importable.length === 0 ? (
          <div className="px-3 py-3 font-mono text-[11px] text-[var(--on-surface-variant)]/70">
            No importable 3D files in this archive.
          </div>
        ) : (
          importable.map((e) => (
            <label
              key={e.name}
              className="flex items-center gap-2 px-3 py-2 cursor-pointer hover:bg-[var(--surface-container-low)]"
            >
              <input
                type="checkbox"
                checked={selected.has(e.name)}
                onChange={() => onToggle(e.name)}
                className="accent-[var(--primary)]"
              />
              <span className="text-xs text-[var(--on-surface)] truncate flex-1">
                {e.name}
              </span>
              <span className="font-mono text-[10px] uppercase text-[var(--on-surface-variant)] flex-shrink-0">
                {e.file_type}
              </span>
              <span className="font-mono text-[10px] text-[var(--on-surface-variant)] flex-shrink-0">
                {formatBytes(e.size_bytes)}
              </span>
            </label>
          ))
        )}
      </div>
    </div>
  );
}
