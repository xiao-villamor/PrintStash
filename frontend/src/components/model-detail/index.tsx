"use client";

import { Suspense, lazy, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "@/lib/navigation";
import { useRouter } from "@/lib/navigation";
import {
  ArrowLeft,
  Check,
  FileText,
  Link2,
  Loader2,
  Minus,
  Pencil,
  Plus,
  Printer as PrinterIcon,
  RotateCcw,
  Star,
  Trash2,
  Wifi,
} from "lucide-react";

import type { STLViewerControls, ViewerDisplayMode } from "@/components/stl-viewer";
import type { ViewerMode } from "@/components/model-detail/viewer-toolbar";
import {
  createTag,
  deleteModel,
  deleteTag,
  getAssetUrl,
  getModelPrinterFiles,
  getModelPrintJobs,
  updateModel,
} from "@/lib/api";
import { useCollections, useTags } from "@/lib/queries";
import { timeAgo } from "@/lib/format";
import {
  DEFAULT_METADATA_PREFERENCES,
  MetadataPreferences,
  readMetadataPreferences,
} from "@/lib/metadata-preferences";
import { toast } from "@/lib/toast";
import { useAuth } from "@/lib/auth-context";
import { useAuthenticatedAssetUrl } from "@/lib/use-authenticated-asset-url";
import { useRequireAuth } from "@/lib/use-require-auth";
import {
  ModelPrinterFileRead,
  ModelPrintJobRead,
  ModelRead,
  TagRead,
} from "@/types";

import { ConfirmModal } from "@/components/ui/confirm-modal";
import { AddGcodeRevisionModal } from "./add-revision-modal";
import { FilesTab } from "./files-tab";
import { OverviewTab } from "./overview-tab";
import {
  TABS,
  TabKey,
  buildPrintSettingRows,
  headerStatusLabel,
  revisionStatusClass,
} from "./presentation";
import { PrintHistorySection } from "./print-history-section";
import { RevisionsTab } from "./revisions-tab";
import { SendToButtons } from "./send-to-buttons";
import { ShareDialog } from "./share-dialog";
import { SettingsTab } from "./settings-tab";
import { useRevisionUpdater } from "./use-revision-updater";
import { ViewerToolbar } from "./viewer-toolbar";

const STLViewer = lazy(() =>
  import("@/components/stl-viewer").then((m) => ({ default: m.STLViewer })),
);

const GcodeViewer = lazy(() =>
  import("@/components/gcode-viewer").then((m) => ({ default: m.GcodeViewer })),
);

const ViewerFallback = (
  <Loader2 className="h-8 w-8 animate-spin text-[var(--on-surface-variant)]" />
);

const PRINTER_BED_SIZES: Record<string, { x: number; y: number }> = {
  default: { x: 235, y: 235 },
};

function getBedSize(printerModel: string | null | undefined): { x: number; y: number } {
  if (!printerModel) return PRINTER_BED_SIZES.default;
  const m = printerModel.toLowerCase();
  if (m.includes("a1 mini") || (m.includes("bambu") && m.includes("mini"))) return { x: 180, y: 180 };
  if (m.includes("bambu") || m.includes("x1") || m.includes("p1s") || m.includes("a1")) return { x: 256, y: 256 };
  if (m.includes("prusa") && m.includes("mini")) return { x: 180, y: 180 };
  if (m.includes("prusa") || m.includes("mk3") || m.includes("mk4")) return { x: 250, y: 210 };
  if (m.includes("voron") || m.includes("v2.4")) {
    if (m.includes("350")) return { x: 350, y: 350 };
    if (m.includes("300")) return { x: 300, y: 300 };
    return { x: 250, y: 250 };
  }
  if (m.includes("cr-10") || m.includes("cr10")) return { x: 300, y: 300 };
  if (m.includes("ender") || m.includes("k1")) return { x: 220, y: 220 };
  return PRINTER_BED_SIZES.default;
}

export function ModelDetail({ model: initialModel }: { model: ModelRead }) {
  const router = useRouter();
  const auth = useRequireAuth();
  const { user } = useAuth();
  const [model, setModel] = useState(initialModel);
  const [deleting, setDeleting] = useState(false);
  const [editing, setEditing] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [editName, setEditName] = useState(model.name);
  const [editDescription, setEditDescription] = useState(model.description || "");
  const [editSourceUrl, setEditSourceUrl] = useState(model.source_url || "");
  const [editCollection, setEditCollection] = useState(model.collection || "");
  const [editTags, setEditTags] = useState<string[]>([...model.tags]);
  const [catOpen, setCatOpen] = useState(false);
  const [tagInput, setTagInput] = useState("");
  // Shared taxonomy lists come from the TanStack Query cache (deduped across
  // the app, refetched on focus + after any mutation).
  const { data: collections = [] } = useCollections();
  const { data: tags = [] } = useTags();
  const [metadataPreferences, setMetadataPreferences] = useState<MetadataPreferences>(
    DEFAULT_METADATA_PREFERENCES,
  );
  const [addRevisionOpen, setAddRevisionOpen] = useState(false);
  const [printerFiles, setPrinterFiles] = useState<ModelPrinterFileRead[]>([]);
  const [printJobs, setPrintJobs] = useState<ModelPrintJobRead[]>([]);
  const [activeTab, setActiveTab] = useState<TabKey>("overview");
  const [displayMode, setDisplayMode] = useState<ViewerDisplayMode>("solid");

  const [showGrid, setShowGrid] = useState(true);
  const [sendOpen, setSendOpen] = useState(false);
  const [sendFileId, setSendFileId] = useState<number | undefined>(undefined);
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);
  const [viewerMode, setViewerMode] = useState<ViewerMode>("model");
  const viewerControls = useRef<STLViewerControls | null>(null);
  const canEditModel = model.effective_role === "edit" || model.effective_role === "admin";
  const canViewPrinters = !!user?.is_superuser;
  const visibleTabs = useMemo(
    () => TABS.filter((tab) => tab.key !== "history" || canViewPrinters),
    [canViewPrinters],
  );

  // Quick actions on the Overview card (mark failed / recommend).
  const revisionUpdater = useRevisionUpdater(model.id, setModel);

  useEffect(() => {
    if (!canViewPrinters) {
      setPrinterFiles([]);
      setPrintJobs([]);
      return;
    }
    getModelPrinterFiles(model.id).then(setPrinterFiles).catch(() => {});
    getModelPrintJobs(model.id).then(setPrintJobs).catch(() => {});
  }, [model.id, canViewPrinters]);

  useEffect(() => {
    setMetadataPreferences(readMetadataPreferences());
  }, []);

  useEffect(() => {
    if (!canViewPrinters && activeTab === "history") setActiveTab("overview");
  }, [activeTab, canViewPrinters]);

  async function doDelete() {
    setDeleting(true);
    try {
      await deleteModel(model.id);
      toast.success("Model deleted");
      router.push("/");
      router.refresh();
    } catch (e) {
      toast.error(e);
      setDeleting(false);
    }
  }

  function enterEdit() {
    setEditName(model.name);
    setEditDescription(model.description || "");
    setEditSourceUrl(model.source_url || "");
    setEditCollection(model.collection || "");
    setEditTags([...model.tags]);
    setTagInput("");
    setCatOpen(false);
    setActiveTab("overview");
    setEditing(true);
  }

  function cancelEdit() {
    setEditing(false);
  }

  async function saveEdit() {
    setSaving(true);
    try {
      const updated = await updateModel(model.id, {
        name: editName.trim() || undefined,
        description: editDescription.trim() || undefined,
        source_url: editSourceUrl.trim() || null,
        collection: editCollection || undefined,
        tags: editTags.length ? editTags : undefined,
      });
      setModel(updated);
      setEditing(false);
      toast.success("Model updated");
    } catch (e) {
      toast.error(e);
    } finally {
      setSaving(false);
    }
  }

  function editToggleTag(name: string) {
    const lower = name.toLowerCase();
    setEditTags((p) =>
      p.map((n) => n.toLowerCase()).includes(lower)
        ? p.filter((s) => s.toLowerCase() !== lower)
        : [...p, name],
    );
  }

  async function editCreateTag(name: string) {
    const trimmed = name.trim();
    if (!trimmed) return;
    const existing = tags.find(
      (t) => t.name.toLowerCase() === trimmed.toLowerCase(),
    );
    if (existing) {
      if (!editTags.map((n) => n.toLowerCase()).includes(existing.name.toLowerCase())) {
        editToggleTag(existing.name);
      }
      return;
    }
    try {
      const t = await createTag({ name: trimmed });
      // createTag invalidates the query cache, so useTags() refetches the new
      // tag automatically; we just select it here.
      setEditTags((p) => [...p, t.name]);
    } catch {
      /* ignored */
    }
  }

  // Delete a tag globally (not just unassign it from this model). Lives here
  // now that the Catalog page is gone; deleteTag invalidates the query cache,
  // so useTags() refetches the list automatically.
  async function editDeleteTag(tag: TagRead) {
    if (!auth.isAuthenticated) {
      auth.showAuthRequiredToast();
      return;
    }
    if (
      tag.model_count > 0 &&
      !window.confirm(
        `Delete tag "${tag.name}"? It will be removed from ${tag.model_count} model${tag.model_count === 1 ? "" : "s"}.`,
      )
    ) {
      return;
    }
    try {
      await deleteTag(tag.id);
      setEditTags((p) =>
        p.filter((n) => n.toLowerCase() !== tag.name.toLowerCase()),
      );
      toast.success(`Tag "${tag.name}" deleted`);
    } catch (e) {
      toast.error(e);
    }
  }

  const editFilteredTags = useMemo(() => {
    const q = tagInput.toLowerCase().trim();
    const selectedNames = editTags.map((n) => n.toLowerCase());
    return tags.filter(
      (t) =>
        !selectedNames.includes(t.name.toLowerCase()) &&
        (q === "" || t.name.toLowerCase().includes(q)),
    );
  }, [tags, tagInput, editTags]);

  const editCanCreate =
    tagInput.trim().length > 0 &&
    !tags.find(
      (t) => t.name.toLowerCase() === tagInput.trim().toLowerCase(),
    );

  const sortedFiles = useMemo(
    () => [...model.files].sort((a, b) => a.version - b.version),
    [model.files],
  );
  const gcodeFiles = useMemo(
    () => sortedFiles.filter((f) => f.file_type === "gcode"),
    [sortedFiles],
  );
  const sourceFiles = useMemo(
    () => sortedFiles.filter((f) => f.file_type !== "gcode"),
    [sortedFiles],
  );
  const recommendedGcode = gcodeFiles.find((f) => f.is_recommended) ?? null;
  const latestFile = recommendedGcode ?? gcodeFiles[gcodeFiles.length - 1] ?? sortedFiles[sortedFiles.length - 1];
  const meta = latestFile?.metadata;
  const printSettingRows = buildPrintSettingRows(meta, metadataPreferences);
  const meshFile = model.files.find(
    (f) =>
      f.file_type === "stl" ||
      f.file_type === "3mf" ||
      f.file_type === "obj" ||
      f.file_type === "step",
  );
  const hasGcode = gcodeFiles.length > 0;
  const thumbUrl = useAuthenticatedAssetUrl(model.thumbnail_url);
  const printerFilesByFileId = useMemo(() => {
    const grouped = new Map<number, ModelPrinterFileRead[]>();
    for (const row of printerFiles) {
      if (row.missing_since) continue;
      grouped.set(row.file_id, [...(grouped.get(row.file_id) ?? []), row]);
    }
    return grouped;
  }, [printerFiles]);

  function requestSend(fileId: number) {
    if (!auth.isAuthenticated) {
      auth.showAuthRequiredToast();
      return;
    }
    if (!canViewPrinters) return;
    setSendFileId(fileId);
    setSendOpen(true);
  }

  function requestAddRevision() {
    if (!auth.isAuthenticated || !canEditModel) {
      auth.showAuthRequiredToast();
      return;
    }
    setAddRevisionOpen(true);
  }

  return (
    <div className="flex flex-col h-full">
      <ConfirmModal
        open={confirmDeleteOpen}
        onClose={() => setConfirmDeleteOpen(false)}
        onConfirm={doDelete}
        busy={deleting}
        title="Delete model?"
        description="This will move the model to trash. Files will be permanently removed after the retention period."
        confirmLabel="Delete"
      />
      {addRevisionOpen && (
        <AddGcodeRevisionModal
          modelId={model.id}
          onClose={() => setAddRevisionOpen(false)}
          onUploaded={(updated) => {
            setModel(updated);
            setAddRevisionOpen(false);
            toast.success("G-code revision added");
          }}
        />
      )}
      <ShareDialog
        modelId={model.id}
        open={shareOpen}
        onClose={() => setShareOpen(false)}
      />
      {/* Detail Header */}
      <header className="flex flex-wrap items-center justify-between px-4 md:px-6 py-3 gap-2 border-b border-[var(--outline-variant)] bg-[var(--surface-container-lowest)] shrink-0">
        <div className="flex items-center gap-4">
          <Link
            href="/"
            className="w-10 h-10 flex items-center justify-center rounded hover:bg-[var(--surface-container-high)] text-[var(--on-surface-variant)] transition-colors"
          >
            <ArrowLeft className="h-5 w-5" />
          </Link>
          <div className="min-w-0">
            {editing ? (
              <input
                value={editName}
                onChange={(e) => setEditName(e.target.value)}
                className="w-full bg-[var(--surface)] text-[var(--on-surface)] font-mono text-lg border border-[var(--outline-variant)] rounded px-2 py-0.5 focus:outline-none focus:ring-2 focus:ring-[var(--primary)] focus:border-transparent"
                placeholder="Model name"
              />
            ) : (
              <h1 className="text-xl font-semibold text-[var(--on-surface)] leading-tight truncate">
                {model.name}
              </h1>
            )}
            <span className="font-mono text-[13px] text-[var(--on-surface-variant)]">
              {(meshFile ?? sourceFiles[0]) ? `${(meshFile ?? sourceFiles[0])!.file_type.toUpperCase()} source · ` : ""}
              {gcodeFiles.length} G-code revision{gcodeFiles.length === 1 ? "" : "s"} · Last updated {timeAgo(model.updated_at)}
            </span>
            {!editing && (recommendedGcode || meta?.material_type || meta?.printer_model) && (
              <div className="flex flex-wrap items-center gap-1.5 mt-1.5">
                {recommendedGcode && (
                  <span className="inline-flex items-center gap-1 border border-[var(--primary)]/30 bg-[var(--secondary-container)] text-[var(--on-secondary-container)] rounded px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider">
                    <Star className="h-3 w-3 fill-current" /> Recommended Rev {recommendedGcode.gcode_revision_number ?? recommendedGcode.version}
                  </span>
                )}
                {recommendedGcode?.revision_status && (
                  <span className={`border rounded px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider ${revisionStatusClass(recommendedGcode.revision_status)}`}>
                    {headerStatusLabel(recommendedGcode.revision_status)}
                  </span>
                )}
                {meta?.material_type && (
                  <span className="inline-flex items-center gap-1 bg-amber-50 dark:bg-amber-950/40 border border-amber-200 dark:border-amber-800 text-amber-700 dark:text-amber-400 rounded px-2 py-0.5 font-mono text-[11px] font-semibold uppercase tracking-wider">
                    {meta.material_type}
                  </span>
                )}
                {meta?.printer_model && (
                  <span className="inline-flex items-center gap-1 bg-blue-50 dark:bg-blue-950/40 border border-blue-200 dark:border-blue-800 text-blue-700 dark:text-blue-400 rounded px-2 py-0.5 font-mono text-[11px] font-semibold uppercase tracking-wider">
                    <PrinterIcon className="h-3 w-3" /> {meta.printer_model}
                  </span>
                )}
              </div>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2">
          {editing ? (
            <>
              <button
                onClick={cancelEdit}
                className="px-4 py-2 rounded border border-[var(--outline-variant)] text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-low)] transition-colors font-mono text-xs uppercase tracking-wider"
              >
                Cancel
              </button>
              <button
                onClick={saveEdit}
                disabled={saving}
                className="px-4 py-2 rounded bg-[var(--primary)] text-[var(--primary-foreground)] hover:opacity-90 transition-opacity font-mono text-xs uppercase tracking-wider flex items-center gap-1.5 disabled:opacity-50"
              >
                {saving ? (
                  <><Loader2 className="h-4 w-4 animate-spin" /> Saving…</>
                ) : (
                  <><Check className="h-4 w-4" /> Save</>
                )}
              </button>
            </>
          ) : (
            <>
              <button
                onClick={auth.isAuthenticated && canEditModel ? () => setShareOpen(true) : auth.showAuthRequiredToast}
                disabled={!auth.isAuthenticated || !canEditModel}
                title={auth.blockReason ?? (canEditModel ? "Share model" : "Edit access required")}
                className="px-4 py-2 rounded border border-[var(--outline-variant)] text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-low)] transition-colors font-mono text-xs uppercase tracking-wider flex items-center gap-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <Link2 className="h-4 w-4" /> Share
              </button>
              <button
                onClick={auth.isAuthenticated && canEditModel ? enterEdit : auth.showAuthRequiredToast}
                disabled={!auth.isAuthenticated || !canEditModel}
                title={auth.blockReason ?? (canEditModel ? "Edit model" : "Edit access required")}
                className="px-4 py-2 rounded border border-[var(--outline-variant)] text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-low)] transition-colors font-mono text-xs uppercase tracking-wider flex items-center gap-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <Pencil className="h-4 w-4" /> {auth.isAuthenticated ? "Edit" : "Sign in to edit"}
              </button>
              <button
                onClick={auth.isAuthenticated && canEditModel ? () => setConfirmDeleteOpen(true) : auth.showAuthRequiredToast}
                disabled={deleting || !auth.isAuthenticated || !canEditModel}
                title={auth.blockReason ?? (canEditModel ? "Delete model" : "Edit access required")}
                className="px-4 py-2 rounded border border-[var(--outline-variant)] text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-low)] transition-colors font-mono text-xs uppercase tracking-wider flex items-center gap-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {deleting ? (
                  <><Loader2 className="h-4 w-4 animate-spin" /> Deleting...</>
                ) : auth.isAuthenticated ? (
                  <><Trash2 className="h-4 w-4" /> Delete</>
                ) : (
                  <><Trash2 className="h-4 w-4" /> Sign in to delete</>
                )}
              </button>
            </>
          )}
        </div>
      </header>

      {/* Two-Column Layout */}
      <div className="flex-1 flex flex-col md:flex-row min-h-0 overflow-hidden md:overflow-hidden">
        {/* Left: 3D Model Preview */}
        <div className="flex-1 min-h-[250px] md:min-h-0 bg-[var(--surface-container-low)] relative border-b md:border-b-0 md:border-r border-[var(--outline-variant)] flex items-center justify-center m-2 md:m-4 rounded overflow-hidden"
          style={{ boxShadow: "inset 0 0 0 1px var(--surface-variant)" }}>
          {viewerMode === "gcode" && hasGcode ? (
            <Suspense fallback={ViewerFallback}>
              <GcodeViewer
                url={`/api/v1/files/${(recommendedGcode ?? gcodeFiles[gcodeFiles.length - 1])!.id}/download`}
                printerBedMm={getBedSize(meta?.printer_model)}
                screenshotName={model.slug || model.name}
              />
            </Suspense>
          ) : meshFile ? (
            <Suspense fallback={ViewerFallback}>
              <STLViewer
                url={getAssetUrl(`/api/v1/files/${meshFile.id}/stl`)}
                onControlsReady={(api) => { viewerControls.current = api; }}
                displayMode={displayMode}
                showGrid={showGrid}
                screenshotName={model.slug || model.name}
              />
            </Suspense>
          ) : thumbUrl ? (
            <img
              src={thumbUrl}
              alt={model.name}
              className="max-w-full max-h-full object-contain"
            />
          ) : (
            <div className="flex items-center justify-center text-[var(--on-surface-variant)]">
              <FileText className="h-20 w-20 opacity-20" />
            </div>
          )}

          {/* Viewer toolbar (top-left) */}
          {(meshFile || hasGcode) && (
            <ViewerToolbar
              displayMode={displayMode}
              setDisplayMode={setDisplayMode}
              showGrid={showGrid}
              setShowGrid={setShowGrid}
              controls={viewerControls}
              viewerMode={viewerMode}
              setViewerMode={setViewerMode}
              hasGcode={hasGcode}
            />
          )}

          {/* Viewing label (top-right) */}
          {(meshFile || (viewerMode === "gcode" && hasGcode)) && (
            <div className="absolute top-4 right-4 z-10 max-w-[60%]">
              <div className="bg-[var(--surface-container-lowest)]/90 backdrop-blur border border-[var(--outline-variant)] rounded px-2.5 py-1.5 text-right">
                {viewerMode === "gcode" ? (
                  <>
                    <p className="font-mono text-[11px] text-[var(--on-surface)] truncate">
                      {(recommendedGcode ?? gcodeFiles[gcodeFiles.length - 1])?.original_filename}
                    </p>
                    <p className="font-mono text-[10px] uppercase tracking-wider text-[var(--on-surface-variant)]">
                      G-code toolpath
                    </p>
                  </>
                ) : (
                  <>
                    <p className="font-mono text-[11px] text-[var(--on-surface)] truncate">
                      Viewing: {meshFile?.original_filename}
                    </p>
                    <p className="font-mono text-[10px] uppercase tracking-wider text-[var(--on-surface-variant)]">
                      Source model
                      {recommendedGcode
                        ? ` · Recommended G-code: Rev ${recommendedGcode.gcode_revision_number ?? recommendedGcode.version}`
                        : ""}
                    </p>
                  </>
                )}
              </div>
            </div>
          )}

          {/* 3D Viewer Controls */}
          <div className="absolute bottom-4 right-4 flex flex-col gap-1 z-10">
            <div className="flex bg-[var(--surface-container-lowest)]/90 backdrop-blur border border-[var(--outline-variant)] rounded overflow-hidden shadow-sm">
              <button
                onClick={() => viewerControls.current?.zoomIn()}
                className="w-9 h-9 flex items-center justify-center text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-high)] hover:text-[var(--primary)] transition-colors border-r border-[var(--outline-variant)]"
                title="Zoom in"
              >
                <Plus className="h-4 w-4" />
              </button>
              <button
                onClick={() => viewerControls.current?.zoomOut()}
                className="w-9 h-9 flex items-center justify-center text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-high)] hover:text-[var(--primary)] transition-colors"
                title="Zoom out"
              >
                <Minus className="h-4 w-4" />
              </button>
            </div>
            <button
              onClick={() => viewerControls.current?.resetView()}
              className="h-9 px-3 bg-[var(--surface-container-lowest)]/90 backdrop-blur border border-[var(--outline-variant)] rounded shadow-sm flex items-center justify-center text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-high)] hover:text-[var(--primary)] transition-colors"
              title="Reset view"
            >
              <RotateCcw className="h-4 w-4" />
            </button>
          </div>

          {/* Dimensions overlay (bottom-left) */}
          {meta?.bbox_x_mm && meta?.bbox_y_mm && meta?.bbox_z_mm && (
            <div className="absolute bottom-4 left-4 z-10">
              <div className="bg-[var(--surface-container-lowest)]/90 backdrop-blur border border-[var(--outline-variant)] rounded px-2 py-1 flex items-center gap-2">
                <span className="w-2 h-2 rounded-full bg-emerald-500" />
                <span className="font-mono text-[13px] text-[var(--on-surface)]">
                  {meta.bbox_x_mm}×{meta.bbox_y_mm}×{meta.bbox_z_mm} mm
                </span>
              </div>
            </div>
          )}
        </div>

        {/* Right: Settings & Files Panel */}
        <div className="md:w-[480px] bg-[var(--surface-container-lowest)] border-l-0 md:border-l border-t md:border-t-0 border-[var(--outline-variant)] flex flex-col h-auto md:h-full shrink-0 min-h-0">
          {/* Segmented tab navigation */}
          <nav className="flex shrink-0 border-b border-[var(--outline-variant)] bg-[var(--surface-container-lowest)] px-2 overflow-x-auto scrollbar-none [&::-webkit-scrollbar]:hidden [-ms-overflow-style:none] [scrollbar-width:none]">
            {visibleTabs.map((tab) => (
              <button
                key={tab.key}
                onClick={() => setActiveTab(tab.key)}
                className={`relative px-3 py-3 font-mono text-[11px] uppercase tracking-wider whitespace-nowrap transition-colors ${
                  activeTab === tab.key
                    ? "text-[var(--primary)]"
                    : "text-[var(--on-surface-variant)] hover:text-[var(--on-surface)]"
                }`}
              >
                {tab.label}
                {tab.key === "revisions" && gcodeFiles.length > 0 && (
                  <span className="ml-1 opacity-60">{gcodeFiles.length}</span>
                )}
                {activeTab === tab.key && (
                  <span className="absolute inset-x-2 -bottom-px h-0.5 bg-[var(--primary)] rounded-full" />
                )}
              </button>
            ))}
          </nav>
          <div className="flex-1 overflow-y-auto p-4 md:p-6 space-y-6 md:space-y-8 [scrollbar-width:thin] [scrollbar-color:var(--outline-variant)_transparent] [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-track]:bg-transparent [&::-webkit-scrollbar-thumb]:bg-[var(--outline-variant)] [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb:hover]:bg-[var(--primary)]/50">
            {activeTab === "overview" && (
              <OverviewTab
                model={model}
                editing={editing}
                editor={{
                  collection: editCollection,
                  setCollection: setEditCollection,
                  catOpen,
                  setCatOpen,
                  collections,
                  description: editDescription,
                  setDescription: setEditDescription,
                  sourceUrl: editSourceUrl,
                  setSourceUrl: setEditSourceUrl,
                  tagInput,
                  setTagInput,
                  tags: editTags,
                  setTags: setEditTags,
                  toggleTag: editToggleTag,
                  createTag: editCreateTag,
                  deleteTag: editDeleteTag,
                  filteredTags: editFilteredTags,
                  canCreate: editCanCreate,
                }}
                recommendedFile={recommendedGcode}
                hasGcode={hasGcode}
                revisionSaving={revisionUpdater.saving}
                onSend={requestSend}
                canSend={canViewPrinters}
                onCompare={() => setActiveTab("revisions")}
                onMark={(file, patch) => void revisionUpdater.update(file, patch)}
                onAddRevision={requestAddRevision}
              />
            )}

            {activeTab === "settings" && (
              <SettingsTab
                meta={meta}
                printSettingRows={printSettingRows}
                preferences={metadataPreferences}
              />
            )}

            {activeTab === "revisions" && (
              <RevisionsTab
                modelId={model.id}
                gcodeFiles={gcodeFiles}
                printerFilesByFileId={printerFilesByFileId}
                onModel={setModel}
                onAddRevision={requestAddRevision}
              />
            )}

            {activeTab === "files" && <FilesTab sourceFiles={sourceFiles} />}

            {activeTab === "history" && canViewPrinters && (
              <PrintHistorySection
                jobs={printJobs}
                modelId={model.id}
                gcodeFiles={gcodeFiles}
                onJobCreated={(job) => setPrintJobs((p) => [job, ...p])}
              />
            )}
          </div>

          {/* Klipper Sync Panel */}
          <div className="p-4 md:p-6 border-t border-[var(--outline-variant)] bg-[var(--surface-container-low)] shrink-0 space-y-3">
            {hasGcode && canViewPrinters && (
              <SendToButtons
                modelId={model.id}
                gcodeFiles={gcodeFiles}
                printerFiles={printerFiles}
                open={sendOpen}
                onOpenChange={setSendOpen}
                preselectFileId={sendFileId}
              />
            )}
            {!hasGcode && (
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <span className="font-mono text-xs text-[var(--on-surface-variant)] uppercase tracking-wider">
                    Sync status
                  </span>
                  <div className="flex items-center gap-1.5 px-2 py-1 bg-[var(--surface-container-lowest)] border border-[var(--outline-variant)] rounded">
                    <Wifi className="h-3 w-3 text-[var(--on-surface-variant)]" />
                    <span className="font-mono text-xs text-[var(--on-surface-variant)]">
                      No G-code file
                    </span>
                  </div>
                </div>
              </div>
            )}
            <div className="flex items-center justify-between border-t border-[var(--surface-container-highest)] pt-3">
              <span className="font-mono text-xs text-[var(--on-surface-variant)] uppercase tracking-wider">Files</span>
              <span className="font-mono text-sm text-[var(--on-surface)] font-semibold">{model.files.length}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="font-mono text-xs text-[var(--on-surface-variant)] uppercase tracking-wider">Created</span>
              <span className="font-mono text-xs text-[var(--on-surface)]">{new Date(model.created_at).toLocaleDateString()}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
