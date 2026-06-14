"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronDown,
  File as FileIcon,
  Loader2,
  Plus,
  Upload,
  X,
} from "lucide-react";
import {
  createTag,
  getJobStatus,
  getModel,
  ingestModel,
  ingestOrca,
} from "@/lib/api";
import { useCollections, useTags } from "@/lib/queries";
import { toast } from "@/lib/toast";
import { createTask, updateTask } from "@/lib/task-center";
import { useRequireAuth } from "@/lib/use-require-auth";
import { useAuth } from "@/lib/auth-context";
import { formatBytes } from "@/lib/format";
import { CollectionRead, IngestJobStatus } from "@/types";

const MESH_EXT = new Set([".stl", ".3mf", ".obj"]);
const GCODE_EXT = new Set([".gcode", ".g", ".gco"]);
const MESH_ACCEPT = ".stl,.3mf,.obj";
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

  function doSubmit(e: React.FormEvent) {
    e.preventDefault();
    if ((!meshFile && !gcodeFile) || submitting) return;
    if (!auth.isAuthenticated) {
      auth.showAuthRequiredToast();
      return;
    }
    if (!user?.is_superuser && !collectionPath) {
      toast.warning("Collection required", "Choose a collection you can edit.");
      return;
    }
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
              {/* File slots */}
              <div className="space-y-3">
                <FileSlot
                  label="3D Model"
                  accept={MESH_ACCEPT}
                  file={meshFile}
                  setFile={(f) => {
                    setMeshFile(f);
                    if (f) autoName(f);
                  }}
                  placeholder={".stl .3mf .obj"}
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

              {/* Model name */}
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
                  disabled={submitting || (!meshFile && !gcodeFile) || (!user?.is_superuser && !collectionPath)}
                  className="px-4 py-2 rounded bg-[var(--primary)] text-[var(--primary-foreground)] font-mono text-xs uppercase tracking-wider hover:opacity-90 transition-opacity disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
                >
                  {submitting ? (
                    <>
                      <Loader2 className="h-4 w-4 animate-spin" />
                      Uploading…
                    </>
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
