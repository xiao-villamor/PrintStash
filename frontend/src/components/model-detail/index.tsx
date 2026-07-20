"use client";

import { Suspense, lazy, useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, PointerEvent as ReactPointerEvent } from "react";
import { Link } from "@/lib/navigation";
import { useRouter } from "@/lib/navigation";
import {
  ArrowLeft,
  Check,
  FileText,
  Link2,
  Loader2,
  MoreHorizontal,
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
  starModel,
  unstarModel,
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
import { Button } from "@/components/ui/button";
import { DropdownMenu } from "@/components/ui/dropdown-menu";
import { TabBar } from "@/components/ui/tabs";
import { AddGcodeRevisionModal } from "./add-revision-modal";
import { FilesTab } from "./files-tab";
import { OverviewTab } from "./overview-tab";
import {
  TABS,
  TabKey,
  buildPrintSettingRows,
  headerStatusLabel,
  normalizeRecommendedGcodeFiles,
  revisionStatusClass,
} from "./presentation";
import { PrintHistorySection } from "./print-history-section";
import { RevisionsTab } from "./revisions-tab";
import { SendToButtons } from "./send-to-buttons";
import { ShareDialog } from "./share-dialog";
import { SettingsTab } from "./settings-tab";
import { useRevisionUpdater } from "./use-revision-updater";
import { ViewerToolbar } from "./viewer-toolbar";
import { Localized } from "@/components/ui/localized";

const STLViewer = lazy(() =>
  import("@/components/stl-viewer").then((m) => ({ default: m.STLViewer })),
);

const GcodeViewer = lazy(() =>
  import("@/components/gcode-viewer").then((m) => ({ default: m.GcodeViewer })),
);

const ViewerFallback = (
  <Loader2 className="h-8 w-8 animate-spin text-on-surface-variant" />
);

const PRINTER_BED_SIZES: Record<string, { x: number; y: number }> = {
  default: { x: 235, y: 235 },
};

const DETAIL_SIDEBAR_STORAGE_KEY = "ps-model-detail-sidebar-width";
const DETAIL_SIDEBAR_DEFAULT_WIDTH = 480;
const DETAIL_SIDEBAR_MIN_WIDTH = 400;
const DETAIL_SIDEBAR_MAX_WIDTH = 800;

function clampDetailSidebarWidth(width: number): number {
  const viewportMax = typeof window === "undefined"
    ? DETAIL_SIDEBAR_MAX_WIDTH
    : Math.max(DETAIL_SIDEBAR_MIN_WIDTH, window.innerWidth - 320);
  return Math.round(Math.min(DETAIL_SIDEBAR_MAX_WIDTH, viewportMax, Math.max(DETAIL_SIDEBAR_MIN_WIDTH, width)));
}

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
  const [starBusy, setStarBusy] = useState(false);
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
  const [detailSidebarWidth, setDetailSidebarWidth] = useState(() => {
    try {
      const stored = Number(localStorage.getItem(DETAIL_SIDEBAR_STORAGE_KEY));
      return Number.isFinite(stored) && stored > 0
        ? clampDetailSidebarWidth(stored)
        : DETAIL_SIDEBAR_DEFAULT_WIDTH;
    } catch {
      return DETAIL_SIDEBAR_DEFAULT_WIDTH;
    }
  });

  const [showGrid, setShowGrid] = useState(true);
  const [sendOpen, setSendOpen] = useState(false);
  const [sendFileId, setSendFileId] = useState<number | undefined>(undefined);
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);
  const [actionsOpen, setActionsOpen] = useState(false);
  const [deleteTagTarget, setDeleteTagTarget] = useState<TagRead | null>(null);
  const [deleteTagBusy, setDeleteTagBusy] = useState(false);
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

  async function toggleFavorite() {
    if (!auth.isAuthenticated || starBusy) { auth.showAuthRequiredToast(); return; }
    const starred = !model.starred;
    setModel((current) => ({ ...current, starred }));
    setStarBusy(true);
    try { await (starred ? starModel(model.id) : unstarModel(model.id)); }
    catch (error) { setModel((current) => ({ ...current, starred: !starred })); toast.error(error); }
    finally { setStarBusy(false); }
  }

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
    try { localStorage.setItem(DETAIL_SIDEBAR_STORAGE_KEY, String(detailSidebarWidth)); }
    catch { /* Storage can be unavailable in hardened browser contexts. */ }
  }, [detailSidebarWidth]);

  useEffect(() => {
    const onResize = () => setDetailSidebarWidth((width) => clampDetailSidebarWidth(width));
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  function startDetailSidebarResize(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return;
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = detailSidebarWidth;
    const previousCursor = document.body.style.cursor;
    const previousUserSelect = document.body.style.userSelect;

    const onMove = (moveEvent: PointerEvent) => {
      setDetailSidebarWidth(clampDetailSidebarWidth(startWidth + startX - moveEvent.clientX));
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
      document.body.style.cursor = previousCursor;
      document.body.style.userSelect = previousUserSelect;
    };

    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
  }

  useEffect(() => {
    if (!canViewPrinters && activeTab === "history") setActiveTab("overview");
  }, [activeTab, canViewPrinters]);

  async function doDelete() {
    setDeleting(true);
    try {
      await deleteModel(model.id);
      toast.success("Model deleted");
      // Return to the folder the model lived in, not the root — deleting one
      // model shouldn't kick the user out of the collection they were browsing.
      router.push(
        model.collection ? `/?c=${encodeURIComponent(model.collection)}` : "/",
      );
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
    setDeleteTagTarget(tag);
  }

  async function confirmDeleteTag() {
    if (!deleteTagTarget) return;
    const tag = deleteTagTarget;
    setDeleteTagBusy(true);
    try {
      await deleteTag(tag.id);
      setEditTags((p) =>
        p.filter((n) => n.toLowerCase() !== tag.name.toLowerCase()),
      );
      toast.success(`Tag "${tag.name}" deleted`);
    } catch (e) {
      toast.error(e);
    } finally {
      setDeleteTagBusy(false);
      setDeleteTagTarget(null);
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
    () => normalizeRecommendedGcodeFiles([...model.files].sort((a, b) => a.version - b.version)),
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
  // Binary G-code (.bgcode) is indexed for metadata but can't be printed by the
  // Moonraker/Bambu providers, so it's excluded from the "send to printer" list.
  const printableGcodeFiles = useMemo(
    () => gcodeFiles.filter((f) => !f.original_filename.toLowerCase().endsWith(".bgcode")),
    [gcodeFiles],
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
    <Localized><div className="flex flex-col h-full">
      <ConfirmModal
        open={confirmDeleteOpen}
        onClose={() => setConfirmDeleteOpen(false)}
        onConfirm={doDelete}
        busy={deleting}
        title="Delete model?"
        description="This will move the model to trash. Files will be permanently removed after the retention period."
        confirmLabel="Delete"
      />
      <ConfirmModal
        open={!!deleteTagTarget}
        onClose={() => setDeleteTagTarget(null)}
        onConfirm={confirmDeleteTag}
        busy={deleteTagBusy}
        title="Delete tag?"
        description={deleteTagTarget
          ? `"${deleteTagTarget.name}" will be removed from ${deleteTagTarget.model_count} model${deleteTagTarget.model_count === 1 ? "" : "s"}.`
          : "This tag will be removed from the model."}
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
        files={model.files}
        open={shareOpen}
        onClose={() => setShareOpen(false)}
      />
      {/* Detail Header */}
      <header className="flex flex-wrap items-center justify-between px-4 md:px-6 py-3 gap-2 border-b border-outline-variant bg-surface-container-lowest shrink-0">
        <div className="flex items-center gap-4">
          <Link
            href="/"
            className="w-10 h-10 flex items-center justify-center rounded hover:bg-surface-container-high text-on-surface-variant transition-colors"
          >
            <ArrowLeft className="h-5 w-5" />
          </Link>
          <div className="min-w-0">
            {editing ? (
              <input
                value={editName}
                onChange={(e) => setEditName(e.target.value)}
                className="w-full bg-surface text-on-surface font-mono text-lg border border-outline-variant rounded px-2 py-0.5 focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent"
                placeholder="Model name"
              />
            ) : (
              <h1 className="text-xl font-semibold text-on-surface leading-tight truncate">
                {model.name}
              </h1>
            )}
            <span className="font-mono text-xs text-on-surface-variant">
              {(meshFile ?? sourceFiles[0]) && <>{(meshFile ?? sourceFiles[0])!.file_type.toUpperCase()} source · </>}
              {gcodeFiles.length} G-code revision{gcodeFiles.length === 1 ? "" : "s"} · Last updated {timeAgo(model.updated_at)}
            </span>
            {!editing && (recommendedGcode || meta?.material_type || meta?.printer_model) && (
              <div className="flex flex-wrap items-center gap-1.5 mt-1.5">
                {recommendedGcode && (
                  <span className="inline-flex items-center gap-1 border border-primary/30 bg-secondary-container text-on-secondary-container rounded px-1.5 py-0.5 font-mono text-3xs uppercase tracking-wider">
                    <Star className="h-3 w-3 fill-current" /> Recommended Rev {recommendedGcode.gcode_revision_number ?? recommendedGcode.version}
                  </span>
                )}
                {recommendedGcode?.revision_status && (
                  <span className={`border rounded px-1.5 py-0.5 font-mono text-3xs uppercase tracking-wider ${revisionStatusClass(recommendedGcode.revision_status)}`}>
                    {headerStatusLabel(recommendedGcode.revision_status)}
                  </span>
                )}
                {meta?.material_type && (
                  <span className="inline-flex items-center gap-1 bg-amber-50 dark:bg-amber-950/40 border border-amber-200 dark:border-amber-800 text-amber-700 dark:text-amber-400 rounded px-2 py-0.5 font-mono text-2xs font-semibold uppercase tracking-wider">
                    {meta.material_type}
                  </span>
                )}
                {meta?.printer_model && (
                  <span className="inline-flex items-center gap-1 bg-blue-50 dark:bg-blue-950/40 border border-blue-200 dark:border-blue-800 text-blue-700 dark:text-blue-400 rounded px-2 py-0.5 font-mono text-2xs font-semibold uppercase tracking-wider">
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
              <Button variant="outline" size="sm" onClick={cancelEdit}>Cancel</Button>
              <Button
                size="sm"
                onClick={saveEdit}
                loading={saving}
              >
                {!saving && <Check className="h-4 w-4" />} {saving ? "Saving…" : "Save"}
              </Button>
            </>
          ) : (
            <>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => void toggleFavorite()}
                disabled={starBusy || !auth.isAuthenticated}
              >
                <Star className={`h-4 w-4 ${model.starred ? "fill-current text-primary" : ""}`} />
                {model.starred ? "Favorited" : "Favorite"}
              </Button>
              <DropdownMenu
                open={actionsOpen}
                onOpenChange={setActionsOpen}
                trigger={
                  <Button
                    type="button"
                    data-menu-trigger
                    variant="outline"
                    size="icon-sm"
                    onClick={() => setActionsOpen((open) => !open)}
                    aria-haspopup="menu"
                    aria-expanded={actionsOpen}
                    aria-label="Model actions"
                  >
                    <MoreHorizontal className="h-4 w-4" />
                  </Button>
                }
                contentClassName="w-48 rounded-lg border border-border bg-popover p-1 text-popover-foreground shadow-lg"
              >
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setActionsOpen(false);
                    if (auth.isAuthenticated && canEditModel) setShareOpen(true);
                    else auth.showAuthRequiredToast();
                  }}
                  disabled={!auth.isAuthenticated || !canEditModel}
                  className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm hover:bg-popover-hover focus-visible:bg-popover-hover focus-visible:outline-none disabled:pointer-events-none disabled:opacity-50"
                >
                  <Link2 className="h-4 w-4" /> Share
                </button>
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setActionsOpen(false);
                    if (auth.isAuthenticated && canEditModel) enterEdit();
                    else auth.showAuthRequiredToast();
                  }}
                  disabled={!auth.isAuthenticated || !canEditModel}
                  className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm hover:bg-popover-hover focus-visible:bg-popover-hover focus-visible:outline-none disabled:pointer-events-none disabled:opacity-50"
                >
                  <Pencil className="h-4 w-4" /> Edit details
                </button>
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setActionsOpen(false);
                    if (auth.isAuthenticated && canEditModel) setConfirmDeleteOpen(true);
                    else auth.showAuthRequiredToast();
                  }}
                  disabled={deleting || !auth.isAuthenticated || !canEditModel}
                  className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm text-destructive hover:bg-destructive/10 focus-visible:bg-destructive/10 focus-visible:outline-none disabled:pointer-events-none disabled:opacity-50"
                >
                  <Trash2 className="h-4 w-4" /> Delete model
                </button>
              </DropdownMenu>
            </>
          )}
        </div>
      </header>

      {/* Two-Column Layout. On mobile the panels stack and the whole area
          scrolls; on desktop each pane keeps its own fixed height. */}
      <div className="flex-1 flex flex-col md:flex-row min-h-0 overflow-y-auto md:overflow-hidden pb-24 md:pb-0">
        {/* Left: 3D Model Preview */}
        <div className="flex-1 min-h-[250px] md:min-h-0 bg-surface-container-low relative border-b md:border-b-0 md:border-r border-outline-variant flex items-center justify-center m-2 md:m-4 rounded overflow-hidden"
          style={{ boxShadow: "inset 0 0 0 1px var(--outline-variant)" }}>
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
            <div className="flex items-center justify-center text-on-surface-variant">
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
              <div className="bg-surface-container-lowest/90 backdrop-blur border border-outline-variant rounded px-2.5 py-1.5 text-right">
                {viewerMode === "gcode" ? (
                  <>
                    <p className="font-mono text-2xs text-on-surface truncate">
                      {(recommendedGcode ?? gcodeFiles[gcodeFiles.length - 1])?.original_filename}
                    </p>
                    <p className="font-mono text-3xs uppercase tracking-wider text-on-surface-variant">
                      G-code toolpath
                    </p>
                  </>
                ) : (
                  <>
                    <p className="font-mono text-2xs text-on-surface truncate">
                      Viewing: {meshFile?.original_filename}
                    </p>
                    <p className="font-mono text-3xs uppercase tracking-wider text-on-surface-variant">
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
            <div className="flex bg-surface-container-lowest/90 backdrop-blur border border-outline-variant rounded overflow-hidden shadow-sm">
              <button
                onClick={() => viewerControls.current?.zoomIn()}
                className="w-9 h-9 flex items-center justify-center text-on-surface-variant hover:bg-surface-container-high hover:text-primary transition-colors border-r border-outline-variant"
                title="Zoom in"
              >
                <Plus className="h-4 w-4" />
              </button>
              <button
                onClick={() => viewerControls.current?.zoomOut()}
                className="w-9 h-9 flex items-center justify-center text-on-surface-variant hover:bg-surface-container-high hover:text-primary transition-colors"
                title="Zoom out"
              >
                <Minus className="h-4 w-4" />
              </button>
            </div>
            <button
              onClick={() => viewerControls.current?.resetView()}
              className="h-9 px-3 bg-surface-container-lowest/90 backdrop-blur border border-outline-variant rounded shadow-sm flex items-center justify-center text-on-surface-variant hover:bg-surface-container-high hover:text-primary transition-colors"
              title="Reset view"
            >
              <RotateCcw className="h-4 w-4" />
            </button>
          </div>

          {/* Dimensions overlay (bottom-left) */}
          {meta?.bbox_x_mm && meta?.bbox_y_mm && meta?.bbox_z_mm && (
            <div className="absolute bottom-4 left-4 z-10">
              <div className="bg-surface-container-lowest/90 backdrop-blur border border-outline-variant rounded px-2 py-1 flex items-center gap-2">
                <span className="w-2 h-2 rounded-full bg-emerald-500" />
                <span className="font-mono text-[13px] text-on-surface">
                  {meta.bbox_x_mm}×{meta.bbox_y_mm}×{meta.bbox_z_mm} mm
                </span>
              </div>
            </div>
          )}
        </div>

        {/* Right: Settings & Files Panel */}
        <div
          data-testid="model-detail-sidebar"
          style={{ "--detail-sidebar-width": `${detailSidebarWidth}px` } as CSSProperties}
          className="relative flex h-auto min-h-0 w-full shrink-0 flex-col border-l-0 border-t border-outline-variant bg-surface-container-lowest md:h-full md:[width:var(--detail-sidebar-width)] md:border-l md:border-t-0"
        >
          <div
            role="separator"
            aria-label="Resize details panel"
            aria-orientation="vertical"
            aria-valuemin={DETAIL_SIDEBAR_MIN_WIDTH}
            aria-valuemax={DETAIL_SIDEBAR_MAX_WIDTH}
            aria-valuenow={detailSidebarWidth}
            tabIndex={0}
            title="Drag to resize · Double-click to reset"
            onPointerDown={startDetailSidebarResize}
            onDoubleClick={() => setDetailSidebarWidth(DETAIL_SIDEBAR_DEFAULT_WIDTH)}
            onKeyDown={(event) => {
              if (event.key === "ArrowLeft") setDetailSidebarWidth((width) => clampDetailSidebarWidth(width + 16));
              else if (event.key === "ArrowRight") setDetailSidebarWidth((width) => clampDetailSidebarWidth(width - 16));
              else if (event.key === "Home") setDetailSidebarWidth(DETAIL_SIDEBAR_MIN_WIDTH);
              else if (event.key === "End") setDetailSidebarWidth(clampDetailSidebarWidth(DETAIL_SIDEBAR_MAX_WIDTH));
              else return;
              event.preventDefault();
            }}
            className="group absolute inset-y-0 -left-1 z-20 hidden w-2 touch-none cursor-col-resize focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring md:block"
          >
            <span className="absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-border transition-colors duration-press group-hover:bg-primary group-focus-visible:bg-primary" />
            <span className="absolute left-1/2 top-1/2 h-10 w-1 -translate-x-1/2 -translate-y-1/2 rounded-full bg-border transition-colors duration-press group-hover:bg-primary group-focus-visible:bg-primary" />
          </div>
          {/* Segmented tab navigation */}
          <TabBar
            tabs={visibleTabs.map((tab) => ({
              key: tab.key,
              label: (
                <>
                  {tab.label}
                  {tab.key === "revisions" && gcodeFiles.length > 0 && <span className="ml-1 opacity-60">{gcodeFiles.length}</span>}
                  {tab.key === "files" && sourceFiles.length > 0 && <span className="ml-1 opacity-60">{sourceFiles.length}</span>}
                  {tab.key === "history" && printJobs.length > 0 && <span className="ml-1 opacity-60">{printJobs.length}</span>}
                </>
              ),
            }))}
            active={activeTab}
            onChange={setActiveTab}
            indicatorInset={8}
            className="shrink-0 border-b border-outline-variant bg-surface-container-lowest px-2 overflow-x-auto scrollbar-none [&::-webkit-scrollbar]:hidden [-ms-overflow-style:none] [scrollbar-width:none]"
            tabClassName="flex-1 px-2 py-3 font-mono text-2xs uppercase tracking-wider whitespace-nowrap transition-colors text-on-surface-variant hover:text-on-surface"
            activeTabClassName="text-primary"
          />
          <div key={activeTab} className="animate-panel-in flex-1 overflow-y-auto p-4 md:p-6 space-y-6 md:space-y-8 [scrollbar-width:thin] [scrollbar-color:var(--outline-variant)_transparent] [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-track]:bg-transparent [&::-webkit-scrollbar-thumb]:bg-outline-variant [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb:hover]:bg-primary/50">
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
                allFiles={model.files}
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
          <div className="p-4 md:p-6 border-t border-outline-variant bg-surface-container-low shrink-0 space-y-3">
            {printableGcodeFiles.length > 0 && canViewPrinters && (
              <SendToButtons
                gcodeFiles={printableGcodeFiles}
                printerFiles={printerFiles}
                open={sendOpen}
                onOpenChange={setSendOpen}
                preselectFileId={sendFileId}
              />
            )}
            {!hasGcode && (
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <span className="font-mono text-xs text-on-surface-variant uppercase tracking-wider">
                    Sync status
                  </span>
                  <div className="flex items-center gap-1.5 px-2 py-1 bg-surface-container-lowest border border-outline-variant rounded">
                    <Wifi className="h-3 w-3 text-on-surface-variant" />
                    <span className="font-mono text-xs text-on-surface-variant">
                      No G-code file
                    </span>
                  </div>
                </div>
              </div>
            )}
            <div className="flex items-center justify-between border-t border-surface-container-highest pt-3">
              <span className="font-mono text-xs text-on-surface-variant uppercase tracking-wider">Files</span>
              <span className="font-mono text-sm text-on-surface font-semibold">{model.files.length}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="font-mono text-xs text-on-surface-variant uppercase tracking-wider">Created</span>
              <span className="font-mono text-xs text-on-surface">{new Date(model.created_at).toLocaleDateString()}</span>
            </div>
          </div>
        </div>
      </div>
    </div></Localized>
  );
}
