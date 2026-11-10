"use client";

import { useQueryClient } from "@tanstack/react-query";
import { refreshLibraryMetadata } from "@/features/library/metadata";
import { useTaxonomyCommands } from "@/features/library/taxonomy";

import { captureEditingBase } from "@/lib/api/editing";

import { useQuery } from "@tanstack/react-query";
import {
  useModelDetail,
  modelPrinterFilesOptions,
  useModelPrintJobs,
  type PublishModel,
} from "@/features/library/model-detail";
import { useLibraryStar } from "@/features/library/mutations";
import { ApiError } from "@/lib/errors";
import { getSessionVersion, requireSessionVersion } from "@/lib/session-transport";
import { Modal } from "@/components/ui/modal";
import { TitleEditor } from "@/components/title-editor";
import { ModelInformation } from "./model-information";
import { RevisionPicker } from "./revision-picker";
import { inspectedRevision } from "./inspected-revision";
import "./model-detail.css";
import { ModelSearchAction } from "@/components/model-search-action";

import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import { useRouter, useSearchParams } from "@/lib/navigation";
import { useLocation } from "@tanstack/react-router";
import { FileText, Link2, Loader2, MoreHorizontal, Pencil, Star, Trash2, Wifi } from "lucide-react";

import type {
  STLViewerControls,
  STLPreviewFeedback,
  ViewerDisplayMode,
} from "@/components/stl-viewer";
import type { ViewerMode } from "@/components/model-detail/viewer-toolbar";
import { SlicerOpenButton } from "@/components/slicer-open-button";
import {
  downloadAuthenticatedFile,
  deleteModel,
  getAssetUrl,
  getModel,
  updateModel,
} from "@/lib/api";
import { useTags } from "@/lib/queries";
import { readMetadataPreferences } from "@/lib/metadata-preferences";
import { toast } from "@/lib/toast";
import { useAuth } from "@/lib/auth-context";
import { useAuthenticatedAssetUrl } from "@/lib/use-authenticated-asset-url";
import { useRequireAuth } from "@/lib/use-require-auth";
import { modalOverlayOpen } from "@/lib/keyboard-commands";
import { ModelPrinterFileRead, ModelRead, TagRead } from "@/types";

import { ConfirmModal } from "@/components/ui/confirm-modal";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { DropdownMenu } from "@/components/ui/dropdown-menu";
import { TabBar } from "@/components/ui/tabs";
import { AddGcodeRevisionModal } from "./add-revision-modal";
import { SimilarityQueue } from "@/components/similarity-queue";
import { FilesTab } from "./files-tab";
import { OverviewTab, ModelMetadataFields, type ModelMetaEditor } from "./overview-tab";
import {
  TABS,
  TabKey,
  buildPrintSettingRows,
  normalizeRecommendedGcodeFiles,
  plateLabel,
} from "./presentation";
import { PrintHistorySection } from "./print-history-section";
import { RevisionsTab } from "./revisions-tab";
import { SendToButtons } from "./send-to-buttons";
import { ShareDialog } from "./share-dialog";
import { SettingsTab } from "./settings-tab";
import { SourceTab } from "./source-tab";
import { useDerivativeRefresh } from "./use-derivative-refresh";
import { ViewerToolbar } from "./viewer-toolbar";
import { PlatePicker } from "./plate-picker";
import { useFilePlates } from "@/lib/queries/file-plates";
import { Localized } from "@/components/ui/localized";
import { ModelTagsDialog } from "@/components/model-tags-dialog";

const STLViewer = lazy(() =>
  import("@/components/stl-viewer").then((m) => ({ default: m.STLViewer })),
);

const GcodePreview = lazy(() =>
  import("./gcode-preview").then((m) => ({ default: m.GcodePreview })),
);

const ViewerFallback = <Loader2 className="h-8 w-8 animate-spin text-on-surface-variant" />;

// The resizable sidebar publishes its width as a custom property so the
// `md:[width:var(--detail-sidebar-width)]` class can pick it up on desktop only.
// Custom properties aren't part of React's CSSProperties, so augment it the way
// upload-modal augments InputHTMLAttributes rather than asserting at the callsite.
declare module "react" {
  interface CSSProperties {
    "--detail-sidebar-width"?: string;
  }
}

/** A printer's usable bed footprint in millimetres. */
interface BedSize {
  x: number;
  y: number;
}

const DEFAULT_BED_SIZE: BedSize = { x: 235, y: 235 };

const DETAIL_SIDEBAR_STORAGE_KEY = "ps-model-detail-inspector-preferred-width";
const DETAIL_SIDEBAR_MIN_WIDTH = 400;
const DETAIL_SIDEBAR_MAX_WIDTH = 1100;

const isBrowser = (): boolean => "window" in globalThis;

function clampDetailSidebarWidth(width: number, availableWidth?: number): number {
  const viewportMax = Math.max(
    DETAIL_SIDEBAR_MIN_WIDTH,
    (availableWidth ?? (isBrowser() ? window.innerWidth - 93 : 1827)) - 376,
  );
  return Math.round(
    Math.min(DETAIL_SIDEBAR_MAX_WIDTH, viewportMax, Math.max(DETAIL_SIDEBAR_MIN_WIDTH, width)),
  );
}

function getBedSize(printerModel: string | null | undefined): BedSize {
  if (!printerModel) return DEFAULT_BED_SIZE;
  const m = printerModel.toLowerCase();
  if (m.includes("a1 mini") || (m.includes("bambu") && m.includes("mini")))
    return { x: 180, y: 180 };
  if (m.includes("bambu") || m.includes("x1") || m.includes("p1s") || m.includes("a1"))
    return { x: 256, y: 256 };
  if (m.includes("prusa") && m.includes("mini")) return { x: 180, y: 180 };
  if (m.includes("prusa") || m.includes("mk3") || m.includes("mk4")) return { x: 250, y: 210 };
  if (m.includes("voron") || m.includes("v2.4")) {
    if (m.includes("350")) return { x: 350, y: 350 };
    if (m.includes("300")) return { x: 300, y: 300 };
    return { x: 250, y: 250 };
  }
  if (m.includes("cr-10") || m.includes("cr10")) return { x: 300, y: 300 };
  if (m.includes("ender") || m.includes("k1")) return { x: 220, y: 220 };
  return DEFAULT_BED_SIZE;
}

export function ModelDetail({ model: initialModel }: { model: ModelRead }) {
  const resource = useModelDetail(initialModel.id, initialModel);
  const denied =
    resource.error instanceof ApiError && [401, 403, 404].includes(resource.error.status);
  if (!resource.data || denied) return null;
  return (
    <ModelDetailPresentation
      key={resource.data.id}
      model={resource.data}
      setModel={resource.publish}
      readError={resource.isError}
      retryRead={() => void resource.refetch()}
    />
  );
}

function ModelDetailPresentation({
  model,
  setModel,
  readError,
  retryRead,
}: {
  model: ModelRead;
  setModel: PublishModel;
  readError: boolean;
  retryRead: () => void;
}) {
  useUiLocale();
  const router = useRouter();
  const searchParams = useSearchParams();
  const location = useLocation();
  const auth = useRequireAuth();
  const { user } = useAuth();
  const [deleting, setDeleting] = useState(false);
  const [editMode, setEditMode] = useState<"details" | "name" | null>(null);
  const editing = editMode === "details";
  const renaming = editMode === "name";
  const editLifetime = useRef(0);
  useEffect(
    () => () => {
      editLifetime.current += 1;
    },
    [],
  );
  const [editBase, setEditBase] = useState(() => captureEditingBase(model));
  const [editProblem, setEditProblem] = useState<"conflict" | "unconfirmed" | null>(null);
  const reviewPublication = useRef<typeof setModel | null>(null);
  const [reviewedModel, setReviewedModel] = useState<ModelRead | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);
  const [tagDialogOpen, setTagDialogOpen] = useState(false);
  const [tagDialogSession, setTagDialogSession] = useState(0);
  const [saving, setSaving] = useState(false);
  const star = useLibraryStar();
  const starBusy = star.isPending;
  const [editName, setEditName] = useState(model.name);
  const nameInput = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (editing) {
      nameInput.current?.focus();
      nameInput.current?.select();
    }
  }, [editing]);
  const [editDescription, setEditDescription] = useState(model.description || "");
  const [editSourceUrl, setEditSourceUrl] = useState(model.source_url || "");
  const [editCollection, setEditCollection] = useState(model.collection || "");
  const [editCollectionLabel, setEditCollectionLabel] = useState(model.collection_label);
  const [editTags, setEditTags] = useState<string[]>([...model.tags]);
  const [catOpen, setCatOpen] = useState(false);
  const [tagInput, setTagInput] = useState("");
  const { data: tags = [] } = useTags();
  // Read once at mount; the panel that edits these lives on another route, so
  // there is nothing to re-sync while this view is open.
  const [metadataPreferences] = useState(readMetadataPreferences);
  const [addRevisionOpen, setAddRevisionOpen] = useState(false);
  const requestedTab = TABS.find((tab) => tab.key === searchParams.get("tab"))?.key ?? "overview";
  function selectTab(tab: TabKey) {
    if (tab !== "revisions") setCompareRequested(false);
    if (tab !== "history") setHistoryDraft(null);
    const params = new URLSearchParams(searchParams);
    params.set("tab", tab);
    router.replace(`/models/${model.id}?${params}`, { state: location.state });
  }
  const [displayMode, setDisplayMode] = useState<ViewerDisplayMode>("solid");
  const detailLayout = useRef<HTMLDivElement>(null);
  const [detailLayoutWidth, setDetailLayoutWidth] = useState(() =>
    isBrowser() ? window.innerWidth - 93 : 1827,
  );
  const [preferredDetailSidebarWidth, setPreferredDetailSidebarWidth] = useState<number | null>(
    () => {
      try {
        const stored = Number(localStorage.getItem(DETAIL_SIDEBAR_STORAGE_KEY));
        return Number.isFinite(stored) && stored > 0 ? stored : null;
      } catch {
        return null;
      }
    },
  );
  const detailSidebarWidth = clampDetailSidebarWidth(
    preferredDetailSidebarWidth ?? Math.min(1000, detailLayoutWidth * 0.45),
    detailLayoutWidth,
  );
  function setDetailSidebarWidth(width: number | ((current: number) => number)) {
    setPreferredDetailSidebarWidth(width instanceof Function ? width(detailSidebarWidth) : width);
  }

  const [showGrid, setShowGrid] = useState(true);
  const [showDimensions, setShowDimensions] = useState(true);
  const [previewExpanded, setPreviewExpanded] = useState(false);
  useEffect(() => {
    function toggleDetails(event: KeyboardEvent) {
      if (
        event.defaultPrevented ||
        event.isComposing ||
        event.repeat ||
        modalOverlayOpen() ||
        event.getModifierState("AltGraph") ||
        !(event.ctrlKey || event.metaKey) ||
        !event.altKey ||
        event.shiftKey ||
        event.code !== "KeyB"
      )
        return;
      event.preventDefault();
      setPreviewExpanded((expanded) => !expanded);
      if (event.target instanceof HTMLElement && event.target.closest(".model-detail-inspector")) {
        requestAnimationFrame(() =>
          detailLayout.current?.querySelector<HTMLButtonElement>("[data-preview-expand]")?.focus(),
        );
      }
    }
    window.addEventListener("keydown", toggleDetails);
    return () => window.removeEventListener("keydown", toggleDetails);
  }, []);
  // The plate chosen for one 3MF; another mesh file shows its whole build.
  const [chosenPlate, setChosenPlate] = useState<{ fileId: number; number: number } | null>(null);
  const [sendOpen, setSendOpen] = useState(false);
  const [compareRequested, setCompareRequested] = useState(false);
  const [comparisonMode, setComparisonMode] = useState<"gcode" | "source" | "all">("gcode");
  const [deliveryMode, setDeliveryMode] = useState<"send" | "queue">("send");
  const [historyDraft, setHistoryDraft] = useState<{
    fileId: number;
    state: "completed" | "failed";
  } | null>(null);
  const [toolpathSource, setToolpathSource] = useState<{
    modelId: number;
    revisionId: number | null;
    fileId: number;
  } | null>(null);
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);
  const [actionsOpen, setActionsOpen] = useState(false);
  const { createTag, deleteTag } = useTaxonomyCommands();
  const metadataClient = useQueryClient();
  const [deleteTagTarget, setDeleteTagTarget] = useState<TagRead | null>(null);
  const [deleteTagBusy, setDeleteTagBusy] = useState(false);
  const [viewerMode, setViewerMode] = useState<ViewerMode>("model");
  const [revisionSelection, setRevisionSelection] = useState<{
    modelId: number;
    fileId: number;
  } | null>(null);
  const [viewerReady, setViewerReady] = useState(false);
  const [previewFeedback, setPreviewFeedback] = useState<STLPreviewFeedback | null>(null);
  const viewerControls = useRef<STLViewerControls | null>(null);
  const toolpathControls = useRef<STLViewerControls | null>(null);
  const [toolpathReady, setToolpathReady] = useState(false);
  const connectToolpathControls = useCallback((controls: STLViewerControls | null) => {
    toolpathControls.current = controls;
    setToolpathReady(controls !== null);
  }, []);
  const canEditModel = model.effective_role === "edit" || model.effective_role === "admin";
  useEffect(() => {
    if (!canEditModel) editLifetime.current += 1;
  }, [canEditModel]);
  if (!canEditModel && editMode !== null) {
    setEditMode(null);
    setEditProblem(null);
    setReviewedModel(null);
    setSaving(false);
    setReviewing(false);
  }
  const canViewPrinters = !!user?.is_superuser;
  const printerFileQuery = useQuery({
    ...modelPrinterFilesOptions(model.id),
    enabled: canViewPrinters,
  });
  const printJobQuery = useModelPrintJobs(model.id, canViewPrinters);
  const printerFiles = useMemo(
    () => (canViewPrinters ? (printerFileQuery.data ?? []) : []),
    [canViewPrinters, printerFileQuery.data],
  );
  const printJobs = canViewPrinters ? (printJobQuery.data ?? []) : [];
  const activeTab =
    (!canViewPrinters && requestedTab === "history") ||
    (!canEditModel && requestedTab === "similar")
      ? "overview"
      : requestedTab;
  const visibleTabs = useMemo(
    () =>
      TABS.filter(
        (tab) =>
          (tab.key !== "history" || canViewPrinters) && (tab.key !== "similar" || canEditModel),
      ),
    [canViewPrinters, canEditModel],
  );

  // Quick actions on the Overview card (mark failed / recommend).
  // Thumbnails and metadata arrive after upload, derived in the background.
  useDerivativeRefresh(model.id);

  async function toggleFavorite() {
    if (!auth.isAuthenticated || starBusy) {
      auth.showAuthRequiredToast();
      return;
    }
    const session = getSessionVersion();
    try {
      await star.mutateAsync({ kind: "model", id: model.id, starred: !model.starred });
    } catch (error) {
      if (session === getSessionVersion()) toast.error(error);
    }
  }

  useEffect(() => {
    try {
      if (preferredDetailSidebarWidth === null) localStorage.removeItem(DETAIL_SIDEBAR_STORAGE_KEY);
      else localStorage.setItem(DETAIL_SIDEBAR_STORAGE_KEY, String(preferredDetailSidebarWidth));
    } catch {
      /* Storage can be unavailable in hardened browser contexts. */
    }
  }, [preferredDetailSidebarWidth]);

  useEffect(() => {
    const element = detailLayout.current;
    if (!element) return;
    const observer = new ResizeObserver(() => setDetailLayoutWidth(element.clientWidth - 40));
    observer.observe(element);
    return () => observer.disconnect();
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

  async function doDelete() {
    setDeleting(true);
    try {
      const deletionSession = getSessionVersion();
      await deleteModel(model.id);
      refreshLibraryMetadata(metadataClient, deletionSession);
      toast.success(uiText("Model deleted"));
      // Return to the folder the model lived in, not the root — deleting one
      // model shouldn't kick the user out of the collection they were browsing.
      router.push(model.collection ? `/?c=${encodeURIComponent(model.collection)}` : "/");
    } catch (e) {
      toast.error(e);
      setDeleting(false);
    }
  }

  function enterEdit(mode: "details" | "name" = "details") {
    if (saving || mode === editMode) return;
    editLifetime.current += 1;
    setEditBase(captureEditingBase(model));
    setEditProblem(null);
    setReviewedModel(null);
    setReviewing(false);
    setSaving(false);
    setEditName(model.name);
    setEditDescription(model.description || "");
    setEditSourceUrl(model.source_url || "");
    setEditCollection(model.collection || "");
    setEditCollectionLabel(model.collection_label);
    setEditTags([...model.tags]);
    setTagInput("");
    setCatOpen(false);
    if (mode === "details") selectTab("overview");
    setEditMode(mode);
  }

  function cancelEdit() {
    editLifetime.current += 1;
    setSaving(false);
    setReviewing(false);
    setEditProblem(null);
    setReviewedModel(null);
    setEditMode(null);
  }

  async function saveEdit(version = editBase) {
    if (!editName.trim() || saving || !canEditModel || reviewedModel?.effective_role === "view")
      return;
    const session = getSessionVersion();
    const lifetime = editLifetime.current;
    const isCurrent = () => session === getSessionVersion() && lifetime === editLifetime.current;
    setSaving(true);
    try {
      const updated = await updateModel(
        model.id,
        renaming
          ? { name: editName.trim() }
          : {
              name: editName.trim() || undefined,
              description: editDescription.trim() || null,
              source_url: editSourceUrl.trim() || null,
              collection: editCollection,
              tags: editTags,
            },
        version,
      );
      requireSessionVersion(session);
      if (!isCurrent() || !(await setModel(updated)) || !isCurrent()) return;
      setEditProblem(null);
      setReviewedModel(null);
      setEditMode(null);
      toast.success(uiText("Model updated"));
    } catch (e) {
      if (!isCurrent()) return;
      if (e instanceof ApiError && e.status === 412) {
        setEditProblem("conflict");
        setReviewedModel(null);
      } else if (
        !(e instanceof ApiError) ||
        e.status === 0 ||
        e.status === 408 ||
        e.status >= 500
      ) {
        setEditProblem("unconfirmed");
        setReviewedModel(null);
      } else toast.error(e);
    } finally {
      if (isCurrent()) setSaving(false);
    }
  }

  async function reviewLatest() {
    const session = getSessionVersion();
    const lifetime = editLifetime.current;
    const isCurrent = () => session === getSessionVersion() && lifetime === editLifetime.current;
    setReviewing(true);
    try {
      reviewPublication.current = setModel;
      const latest = await getModel(model.id);
      requireSessionVersion(session);
      if (isCurrent()) setReviewedModel(latest);
    } catch (error) {
      if (isCurrent()) toast.error(error);
    } finally {
      if (isCurrent()) setReviewing(false);
    }
  }

  async function useReviewedVersion() {
    const lifetime = editLifetime.current;
    if (
      !reviewedModel ||
      !reviewPublication.current ||
      !(await reviewPublication.current(reviewedModel)) ||
      lifetime !== editLifetime.current
    )
      return;
    setEditBase(captureEditingBase(reviewedModel));
    setEditName(reviewedModel.name);
    setEditDescription(reviewedModel.description ?? "");
    setEditSourceUrl(reviewedModel.source_url ?? "");
    setEditCollection(reviewedModel.collection ?? "");
    setEditCollectionLabel(reviewedModel.collection_label);
    setEditTags([...reviewedModel.tags]);
    setEditProblem(null);
    setReviewedModel(null);
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
    const existing = tags.find((t) => t.name.toLowerCase() === trimmed.toLowerCase());
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
      setEditTags((p) => p.filter((n) => n.toLowerCase() !== tag.name.toLowerCase()));
      toast.success(uiText('Tag "{value1}" deleted', { value1: String(tag.name) }));
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
    !tags.find((t) => t.name.toLowerCase() === tagInput.trim().toLowerCase());

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
  const recommendedGcode = gcodeFiles.find((f) => f.is_recommended) ?? null;
  const inspectedGcode = inspectedRevision(
    gcodeFiles,
    revisionSelection?.modelId === model.id ? revisionSelection.fileId : null,
  );
  const latestFile = inspectedGcode ?? sortedFiles[sortedFiles.length - 1];
  const meta = latestFile?.metadata;
  const printSettingRows = buildPrintSettingRows(meta, metadataPreferences);
  const meshFile = model.files.find(
    (f) =>
      f.file_type === "stl" ||
      f.file_type === "3mf" ||
      f.file_type === "obj" ||
      f.file_type === "glb" ||
      f.file_type === "ply" ||
      f.file_type === "gltf" ||
      f.file_type === "3dm" ||
      f.file_type === "fbx" ||
      f.file_type === "step",
  );
  const hasGcode = gcodeFiles.length > 0;
  const hasToolpathPreview = hasGcode || sourceFiles.some((file) => file.file_type === "3mf");
  const previewSourceId =
    toolpathSource?.modelId === model.id &&
    toolpathSource.revisionId === (inspectedGcode?.id ?? null)
      ? toolpathSource.fileId
      : inspectedGcode?.id;
  const previewSource =
    sortedFiles.find((file) => file.id === previewSourceId) ??
    inspectedGcode ??
    sourceFiles.find((file) => file.file_type === "3mf");
  function selectToolpathSource(fileId: number) {
    const file = sortedFiles.find((source) => source.id === fileId);
    if (file?.file_type === "gcode") {
      setRevisionSelection({ modelId: model.id, fileId });
      setToolpathSource(null);
    } else if (file?.file_type === "3mf") {
      setToolpathSource({ modelId: model.id, revisionId: inspectedGcode?.id ?? null, fileId });
    }
  }
  const filePlates = useFilePlates(model.id, meshFile?.file_type === "3mf" ? meshFile.id : null);
  // A single plate is the whole build; only a multi-plate project offers a choice.
  const plates = filePlates !== null && filePlates.plates.length > 1 ? filePlates.plates : [];
  const pickerShown = viewerMode === "model" && plates.length > 1;
  // The viewer's corner controls sit above the plate strip when it is shown.
  const shownPlate =
    chosenPlate !== null && chosenPlate.fileId === meshFile?.id
      ? (plates.find((plate) => plate.number === chosenPlate.number) ?? null)
      : null;
  const thumbUrl = useAuthenticatedAssetUrl(model.thumbnail_url);
  const printerFilesByFileId = useMemo(() => {
    const grouped = new Map<number, ModelPrinterFileRead[]>();
    for (const row of printerFiles) {
      if (row.missing_since) continue;
      grouped.set(row.file_id, [...(grouped.get(row.file_id) ?? []), row]);
    }
    return grouped;
  }, [printerFiles]);

  function requestAddRevision() {
    if (!auth.isAuthenticated || !canEditModel) {
      auth.showAuthRequiredToast();
      return;
    }
    setAddRevisionOpen(true);
  }

  const modelMetadataEditor: ModelMetaEditor = {
    collection: editCollection,
    collectionLabel: editCollectionLabel,
    setCollection: (path, label) => {
      setEditCollection(path);
      setEditCollectionLabel(label);
    },
    catOpen,
    setCatOpen,
    allowRoot: !!user?.is_superuser,
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
  };

  return (
    <Localized>
      <div className="model-detail-page flex flex-col h-full">
        {readError && (
          <div role="alert" className="border-b border-border bg-muted px-4 py-3 text-sm">
            <p>{uiText("Couldn’t load this model")}</p>
            <Button variant="outline" size="sm" onClick={retryRead}>
              {uiText("Retry")}
            </Button>
          </div>
        )}
        <ConfirmModal
          open={confirmDeleteOpen}
          onClose={() => setConfirmDeleteOpen(false)}
          onConfirm={doDelete}
          busy={deleting}
          title={uiText("Delete model?")}
          description={uiText(
            "This will move the model to trash. Files will be permanently removed after the retention period.",
          )}
          confirmLabel={uiText("Delete")}
        />
        <ConfirmModal
          open={!!deleteTagTarget}
          onClose={() => setDeleteTagTarget(null)}
          onConfirm={confirmDeleteTag}
          busy={deleteTagBusy}
          title={uiText("Delete tag?")}
          description={
            deleteTagTarget
              ? uiText("tags.removeFromModels", {
                  value1: String(deleteTagTarget.name),
                  value2: String(deleteTagTarget.model_count),
                  count: Number(deleteTagTarget.model_count),
                })
              : uiText("This tag will be removed from the model.")
          }
        />
        {addRevisionOpen && (
          <AddGcodeRevisionModal
            modelName={model.name}
            currentRecommendedRevision={
              gcodeFiles.find((file) => file.is_recommended)?.gcode_revision_number ?? undefined
            }
            modelId={model.id}
            onClose={() => setAddRevisionOpen(false)}
            onUploaded={async (updated) => {
              if (!(await setModel(updated))) return;
              setAddRevisionOpen(false);
              toast.success(uiText("G-code revision added"));
            }}
          />
        )}
        <ShareDialog
          modelName={model.name}
          modelId={model.id}
          files={model.files}
          open={shareOpen}
          onClose={() => setShareOpen(false)}
        />
        <ModelTagsDialog
          key={`${model.id}:${tagDialogSession}`}
          model={model}
          suggestions={tags}
          open={tagDialogOpen}
          onClose={() => setTagDialogOpen(false)}
          onSaved={(nextTags, editVersion) => {
            void setModel((current) => ({ ...current, tags: nextTags, ...editVersion }));
          }}
        />
        <Modal
          open={editing && reviewedModel !== null}
          onClose={() => setReviewedModel(null)}
          title={uiText("library.latestVersion")}
        >
          {reviewedModel && (
            <div className="space-y-4">
              <dl className="space-y-2">
                <dt className="font-semibold">{uiText("Name")}</dt>
                <dd>{reviewedModel.name}</dd>
                <dt className="font-semibold">{uiText("Description")}</dt>
                <dd className="whitespace-pre-wrap">{reviewedModel.description ?? "—"}</dd>
                <dt className="font-semibold">{uiText("Collection")}</dt>
                <dd>{reviewedModel.collection_label ?? "—"}</dd>
                <dt className="font-semibold">{uiText("Tags")}</dt>
                <dd>{reviewedModel.tags.join(", ") || "—"}</dd>
                <dt className="font-semibold">{uiText("Source URL")}</dt>
                <dd>{reviewedModel.source_url ?? "—"}</dd>
              </dl>
              <div className="flex flex-wrap gap-2">
                <Button variant="outline" onClick={() => setReviewedModel(null)}>
                  {uiText("library.keepDraft")}
                </Button>
                <Button variant="outline" onClick={useReviewedVersion}>
                  {uiText("library.useLatest")}
                </Button>
                <Button
                  loading={saving}
                  disabled={
                    reviewedModel.effective_role !== "edit" &&
                    reviewedModel.effective_role !== "admin"
                  }
                  onClick={() => void saveEdit(reviewedModel)}
                >
                  {uiText("library.retryDraft")}
                </Button>
              </div>
            </div>
          )}
        </Modal>
        <Modal
          open={editing && reviewedModel === null}
          onClose={() => {
            if (!saving) cancelEdit();
          }}
          title={uiText("Edit Model details")}
          className="absolute inset-y-0 right-0 h-dvh max-h-none max-w-[680px] rounded-none"
          bodyClassName="flex-1"
          description={uiText(
            "Edit the Model’s own metadata. Captured source fields and Artifact tags remain separate.",
          )}
          footer={
            <>
              <Button variant="outline" onClick={cancelEdit} disabled={saving}>
                {uiText("Cancel")}
              </Button>
              <Button
                onClick={() => void saveEdit()}
                loading={saving}
                disabled={!editName.trim() || editProblem !== null || !canEditModel}
              >
                {uiText("Save changes")}
              </Button>
            </>
          }
        >
          <div className="space-y-5">
            <div className="grid grid-cols-[1fr_2fr] gap-4 bg-plate p-4 text-[13px]">
              <span className="text-muted-foreground">{uiText("Model")}</span>
              <span className="break-words">{model.name}</span>
            </div>
            <p className="text-[13px] text-muted-foreground">
              {uiText("Changes stay a draft until you save. Cancel restores the previous values.")}
            </p>
            {editing && editProblem && (
              <div role="alert" className="border-b border-border bg-muted px-4 py-3 text-sm">
                <p>
                  {uiText(
                    editProblem === "conflict" ? "library.editConflict" : "library.saveUnconfirmed",
                  )}
                </p>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => void reviewLatest()}
                  loading={reviewing}
                >
                  {uiText("library.reviewLatest")}
                </Button>
              </div>
            )}

            <h3 className="text-sm font-semibold">{uiText("Model")}</h3>
            <label className="block space-y-1.5 text-[13px] font-medium">
              {uiText("Name (required)")}
              <Input
                ref={nameInput}
                value={editName}
                onChange={(event) => setEditName(event.target.value)}
                aria-invalid={!editName.trim()}
                aria-describedby={!editName.trim() ? "model-name-error" : undefined}
                disabled={saving}
              />
            </label>
            <p className="text-xs text-muted-foreground">
              {uiText("Required; separate quick Rename uses the same validation.")}
            </p>
            {!editName.trim() && (
              <p id="model-name-error" className="text-sm text-destructive">
                {uiText("Model name is required.")}
              </p>
            )}
            <ModelMetadataFields editor={modelMetadataEditor} />
          </div>
        </Modal>
        {/* Detail Header */}
        <header className="model-detail-heading flex flex-wrap items-center justify-between px-5 pt-4 pb-2.5 gap-2 bg-background shrink-0">
          <div className="flex w-full min-w-0 items-start gap-3 md:w-auto md:flex-1 md:gap-4">
            <div className="min-w-0 flex-1">
              <TitleEditor
                open={renaming && canEditModel && auth.isAuthenticated}
                onOpenChange={(open) => {
                  if (!open) cancelEdit();
                }}
                label={uiText("Rename {value1}", { value1: model.name })}
                fieldLabel={uiText("Model name")}
                value={editName}
                onValueChange={setEditName}
                onSave={() => void saveEdit()}
                busy={saving}
                requiredMessage={uiText("Model name is required.")}
                saveDisabled={editProblem !== null || !canEditModel}
                trigger={
                  <div className="flex min-w-0 items-center gap-1">
                    <h1
                      onDoubleClick={() => {
                        if (auth.isAuthenticated && canEditModel) enterEdit("name");
                      }}
                      className="min-w-0 break-words text-2xl font-semibold text-on-surface leading-tight"
                    >
                      {model.name}
                    </h1>
                    {auth.isAuthenticated && canEditModel && (
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon-sm"
                        className="shrink-0 text-on-surface-variant"
                        aria-label={uiText("Rename {value1}", { value1: model.name })}
                        title={uiText("Rename")}
                        onClick={() => {
                          if (!saving) {
                            if (renaming) cancelEdit();
                            else enterEdit("name");
                          }
                        }}
                        data-menu-trigger
                        aria-haspopup="dialog"
                        aria-expanded={renaming}
                      >
                        <Pencil className="h-3.5 w-3.5" aria-hidden />
                      </Button>
                    )}
                  </div>
                }
              >
                {renaming && editProblem && (
                  <div className="space-y-2 border-t border-border pt-3 text-xs">
                    <p role="alert">
                      {uiText(
                        editProblem === "conflict"
                          ? "library.editConflict"
                          : "library.saveUnconfirmed",
                      )}
                    </p>
                    {reviewedModel ? (
                      <>
                        <p className="font-medium">{uiText("library.latestVersion")}</p>
                        <p className="break-words">{reviewedModel.name}</p>
                        <div className="flex flex-wrap gap-2">
                          <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            onClick={useReviewedVersion}
                            disabled={saving}
                          >
                            {uiText("library.useLatest")}
                          </Button>
                          <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            onClick={() => setReviewedModel(null)}
                            disabled={saving}
                          >
                            {uiText("library.keepDraft")}
                          </Button>
                          <Button
                            type="button"
                            size="sm"
                            className="h-auto min-h-9 whitespace-normal"
                            loading={saving}
                            disabled={
                              reviewedModel.effective_role !== "edit" &&
                              reviewedModel.effective_role !== "admin"
                            }
                            onClick={() => void saveEdit(reviewedModel)}
                          >
                            {uiText("library.retryDraft")}
                          </Button>
                        </div>
                      </>
                    ) : (
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => void reviewLatest()}
                        loading={reviewing}
                      >
                        {uiText("library.reviewLatest")}
                      </Button>
                    )}
                  </div>
                )}
              </TitleEditor>
            </div>
          </div>
          <div
            className={`flex items-center gap-2 ${editing ? "w-full justify-end md:w-auto md:self-start" : ""}`}
          >
            <>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => void toggleFavorite()}
                disabled={starBusy || !auth.isAuthenticated}
              >
                <Star className={`h-4 w-4 ${model.starred ? "fill-current text-primary" : ""}`} />
                {model.starred ? uiText("Favorited") : uiText("Favorite")}
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={!auth.isAuthenticated || !canEditModel}
                onClick={() => setShareOpen(true)}
              >
                {uiText("Share")}
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
                    aria-label={uiText("Model actions")}
                  >
                    <MoreHorizontal className="h-4 w-4" />
                  </Button>
                }
                contentClassName="w-48 rounded-lg border border-border bg-popover p-1 text-popover-foreground shadow-lg"
              >
                <ModelSearchAction modelId={model.id} onSelect={() => setActionsOpen(false)} />
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
                  <Link2 className="h-4 w-4" />
                  {uiText(" Share")}
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
                  <Pencil className="h-4 w-4" />
                  {uiText(" Edit details")}
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
                  <Trash2 className="h-4 w-4" />
                  {uiText(" Delete model")}
                </button>
              </DropdownMenu>
            </>
          </div>
        </header>

        {/* Segmented tab navigation */}
        <TabBar
          tabs={visibleTabs.map((tab) => ({
            key: tab.key,
            label: (
              <>
                {tab.label}
                {tab.key === "revisions" && gcodeFiles.length > 0 && (
                  <span className="ml-1 opacity-60">{gcodeFiles.length}</span>
                )}
                {tab.key === "files" && sourceFiles.length > 0 && (
                  <span className="ml-1 opacity-60">{sourceFiles.length}</span>
                )}
                {tab.key === "history" && printJobs.length > 0 && (
                  <span className="ml-1 opacity-60">{printJobs.length}</span>
                )}
              </>
            ),
          }))}
          active={activeTab}
          onChange={selectTab}
          showIndicator={false}
          className="model-detail-tabs shrink-0 overflow-x-auto border-b border-outline-variant"
          tabClassName="h-10 min-w-max whitespace-nowrap border-b-2 border-transparent px-0 py-2 text-sm font-medium text-on-surface-variant hover:text-on-surface focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          activeTabClassName="border-foreground text-foreground"
        />

        {/* Two-Column Layout. On mobile the panels stack and the whole area
          scrolls; on desktop each pane keeps its own fixed height. */}
        <div
          ref={detailLayout}
          className="model-detail-layout flex-1 min-h-0"
          data-expanded={previewExpanded}
        >
          {/* Source preview and Model facts remain mounted across inspector tabs. */}
          <div className="model-detail-visual">
            <div
              role="region"
              aria-label={uiText("Preview")}
              className={`model-detail-preview relative min-h-80 overflow-hidden border border-outline-variant bg-surface-container-low ${pickerShown ? "pb-20" : ""}`}
            >
              <div className="detail-preview-canvas relative flex min-h-80 w-full items-center justify-center md:min-h-0">
                {viewerMode === "gcode" && hasToolpathPreview ? (
                  <Suspense fallback={ViewerFallback}>
                    <GcodePreview
                      files={sortedFiles}
                      bedSizeFor={(file) => getBedSize(file.metadata?.printer_model)}
                      selectedFileId={previewSource?.id}
                      onSelectFile={selectToolpathSource}
                      viewerProps={{
                        screenshotName: model.slug || model.name,
                        showGrid,
                        showDimensions,
                        onControlsReady: connectToolpathControls,
                      }}
                    />
                  </Suspense>
                ) : meshFile ? (
                  <Suspense key={meshFile.id} fallback={ViewerFallback}>
                    <STLViewer
                      url={getAssetUrl(`/api/v1/files/${meshFile.id}/stl`)}
                      modelId={model.id}
                      onControlsReady={(api) => {
                        viewerControls.current = api;
                      }}
                      onReadyChange={setViewerReady}
                      onFailureChange={setPreviewFeedback}
                      displayMode={displayMode}
                      showGrid={showGrid}
                      screenshotName={
                        shownPlate
                          ? `${model.slug || model.name}-plate-${plateLabel(shownPlate)}`
                          : model.slug || model.name
                      }
                      plate={shownPlate?.region}
                    />
                  </Suspense>
                ) : thumbUrl ? (
                  <img
                    src={thumbUrl}
                    alt={model.name}
                    className="max-w-full max-h-full object-contain"
                  />
                ) : (
                  <div className="flex flex-col items-center justify-center gap-2 text-on-surface-variant">
                    <FileText className="h-20 w-20 opacity-20" />
                    {sourceFiles.some((file) => file.file_type === "dxf") && (
                      <span>
                        {uiText(
                          "DXF preview is not supported yet. Download the original file below.",
                        )}
                      </span>
                    )}
                  </div>
                )}
                {/* Dimensions overlay (bottom-left). They measure the whole build, so a
                single plate does not claim them. */}
                {viewerMode === "model" &&
                  showDimensions &&
                  viewerReady &&
                  shownPlate === null &&
                  meshFile?.metadata?.bbox_x_mm != null &&
                  meshFile?.metadata?.bbox_y_mm != null &&
                  meshFile?.metadata?.bbox_z_mm != null && (
                    <div className="absolute bottom-4 left-4 z-10">
                      <div className="bg-surface-container-lowest/90 backdrop-blur border border-outline-variant rounded px-2 py-1 flex items-center gap-2">
                        <span className="w-2 h-2 rounded-full bg-emerald-500" />
                        <span className="font-mono text-[13px] text-on-surface">
                          {uiText("{value1}×{value2}×{value3} mm", {
                            value1: String(meshFile.metadata.bbox_x_mm),
                            value2: String(meshFile.metadata.bbox_y_mm),
                            value3: String(meshFile.metadata.bbox_z_mm),
                          })}
                        </span>
                      </div>
                    </div>
                  )}
              </div>
              {pickerShown && meshFile && (
                <PlatePicker
                  plates={plates}
                  selected={shownPlate?.number ?? null}
                  onSelect={(number) =>
                    setChosenPlate(number === null ? null : { fileId: meshFile.id, number })
                  }
                />
              )}
              {(meshFile || hasToolpathPreview) && (
                <div className="contents">
                  <ViewerToolbar
                    displayMode={displayMode}
                    setDisplayMode={setDisplayMode}
                    showGrid={showGrid}
                    setShowGrid={setShowGrid}
                    controls={viewerMode === "gcode" ? toolpathControls : viewerControls}
                    viewerMode={viewerMode}
                    setViewerMode={setViewerMode}
                    hasGcode={hasToolpathPreview}
                    viewerReady={viewerMode === "gcode" ? toolpathReady : viewerReady}
                    filename={
                      viewerMode === "gcode"
                        ? previewSource?.original_filename
                        : shownPlate && meshFile
                          ? `${meshFile.original_filename} · ${uiText("Plate {value1}", { value1: plateLabel(shownPlate) })}`
                          : meshFile?.original_filename
                    }
                    expanded={previewExpanded}
                    onExpand={() => setPreviewExpanded((value) => !value)}
                    showDimensions={showDimensions}
                    onDimensions={setShowDimensions}
                  />
                </div>
              )}
            </div>

            <ModelInformation
              model={model}
              sourceFile={meshFile ?? null}
              canEdit={canEditModel}
              onEdit={() => enterEdit("details")}
              onTags={() => {
                setTagDialogSession((value) => value + 1);
                setTagDialogOpen(true);
              }}
            />
          </div>

          {/* Right: Settings & Files Panel */}
          <div
            data-testid="model-detail-sidebar"
            style={{ "--detail-sidebar-width": `${detailSidebarWidth}px` }}
            className="model-detail-inspector relative flex min-h-0 w-full shrink-0 flex-col border border-outline-variant bg-surface-container-lowest md:[width:var(--detail-sidebar-width)]"
          >
            <div
              role="separator"
              aria-label={uiText("Resize details panel")}
              aria-orientation="vertical"
              aria-valuemin={DETAIL_SIDEBAR_MIN_WIDTH}
              aria-valuemax={DETAIL_SIDEBAR_MAX_WIDTH}
              aria-valuenow={detailSidebarWidth}
              tabIndex={0}
              title={uiText("Drag to resize · Double-click to reset")}
              onPointerDown={startDetailSidebarResize}
              onDoubleClick={() => setPreferredDetailSidebarWidth(null)}
              onKeyDown={(event) => {
                if (event.key === "ArrowLeft")
                  setDetailSidebarWidth((width) => clampDetailSidebarWidth(width + 16));
                else if (event.key === "ArrowRight")
                  setDetailSidebarWidth((width) => clampDetailSidebarWidth(width - 16));
                else if (event.key === "Home") setDetailSidebarWidth(DETAIL_SIDEBAR_MIN_WIDTH);
                else if (event.key === "End")
                  setDetailSidebarWidth(clampDetailSidebarWidth(DETAIL_SIDEBAR_MAX_WIDTH));
                else return;
                event.preventDefault();
              }}
              className="group absolute inset-y-0 -left-1 z-20 hidden w-2 touch-none cursor-col-resize focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring md:block"
            >
              <span className="absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-border transition-colors duration-press group-hover:bg-primary group-focus-visible:bg-primary" />
              <span className="absolute left-1/2 top-1/2 h-10 w-1 -translate-x-1/2 -translate-y-1/2 rounded-full bg-border transition-colors duration-press group-hover:bg-primary group-focus-visible:bg-primary" />
            </div>

            <div
              key={activeTab}
              className="model-detail-content flex-1 min-h-0 overflow-y-auto p-4 space-y-4"
            >
              {activeTab === "overview" && (
                <div className="space-y-4">
                  <OverviewTab
                    model={model}
                    editing={false}
                    editor={modelMetadataEditor}
                    revision={{
                      file: inspectedGcode,
                      recommended: recommendedGcode,
                      revisionCount: gcodeFiles.length,
                      canEdit: canEditModel,
                      canSend: canViewPrinters,
                      jobs: canViewPrinters && printJobQuery.isSuccess ? printJobs : null,
                      onRevisions: () => {
                        setCompareRequested(false);
                        selectTab("revisions");
                      },
                      onSettings: () => selectTab("settings"),
                      onHistory: () => selectTab("history"),
                      onCompare: () => {
                        setComparisonMode("gcode");
                        setCompareRequested(true);
                        selectTab("revisions");
                      },
                      onInspect: (fileId) => setRevisionSelection({ modelId: model.id, fileId }),
                      onSend: (mode) => {
                        setDeliveryMode(mode);
                        setSendOpen(true);
                      },
                      onLogPrint: (state) => {
                        if (inspectedGcode) setHistoryDraft({ fileId: inspectedGcode.id, state });
                        selectTab("history");
                      },
                      onAddRevision: requestAddRevision,
                    }}
                  />
                  {!editing && !hasGcode && latestFile?.file_type === "resin" && (
                    <SettingsTab
                      meta={meta}
                      printSettingRows={printSettingRows}
                      preferences={metadataPreferences}
                    />
                  )}
                </div>
              )}

              {activeTab === "settings" && (
                <SettingsTab
                  meta={meta}
                  printSettingRows={printSettingRows}
                  preferences={metadataPreferences}
                  headerActions={
                    gcodeFiles.length > 1 ? (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => {
                          setComparisonMode("gcode");
                          setCompareRequested(true);
                          selectTab("revisions");
                        }}
                      >
                        {uiText("Compare")}
                      </Button>
                    ) : undefined
                  }
                  actions={
                    inspectedGcode ? (
                      <div className="flex flex-wrap items-center gap-2">
                        <SlicerOpenButton
                          fileId={inspectedGcode.id}
                          fileType={
                            inspectedGcode.original_filename.toLowerCase().endsWith(".bgcode")
                              ? "bgcode"
                              : inspectedGcode.file_type
                          }
                          showLabel
                        />
                        <Button
                          variant="outline"
                          onClick={() =>
                            void downloadAuthenticatedFile(
                              `/api/v1/files/${inspectedGcode.id}/download`,
                              inspectedGcode.original_filename,
                            ).catch((error) => toast.error(error))
                          }
                        >
                          {uiText("Download")}
                        </Button>
                      </div>
                    ) : undefined
                  }
                  revisionPicker={
                    <RevisionPicker
                      files={gcodeFiles}
                      selected={inspectedGcode}
                      onSelect={(fileId) => setRevisionSelection({ modelId: model.id, fileId })}
                    />
                  }
                />
              )}

              {activeTab === "similar" && canEditModel && <SimilarityQueue modelId={model.id} />}
              {activeTab === "source" && (
                <SourceTab modelName={model.name} modelId={model.id} canEdit={canEditModel} />
              )}

              {activeTab === "revisions" && canViewPrinters && printerFileQuery.isError && (
                <Button variant="outline" onClick={() => void printerFileQuery.refetch()}>
                  {uiText("Retry")}
                </Button>
              )}
              {activeTab === "revisions" && (
                <RevisionsTab
                  inspectedFileId={inspectedGcode?.id}
                  onInspect={(fileId) => setRevisionSelection({ modelId: model.id, fileId })}
                  startComparing={compareRequested}
                  comparisonMode={comparisonMode}
                  jobs={canViewPrinters && printJobQuery.isSuccess ? printJobs : null}
                  onSend={
                    canViewPrinters
                      ? (mode) => {
                          setDeliveryMode(mode);
                          setSendOpen(true);
                        }
                      : undefined
                  }
                  onSettings={() => selectTab("settings")}
                  onLogPrint={
                    canViewPrinters && canEditModel && auth.isAuthenticated
                      ? (fileId, state) => {
                          setHistoryDraft({ fileId, state });
                          selectTab("history");
                        }
                      : undefined
                  }
                  modelId={model.id}
                  gcodeFiles={gcodeFiles}
                  allFiles={model.files}
                  printerFilesByFileId={printerFilesByFileId}
                  onModel={setModel}
                  onAddRevision={requestAddRevision}
                  canEdit={canEditModel}
                />
              )}

              {activeTab === "files" && (
                <>
                  <FilesTab
                    modelId={model.id}
                    onCompare={() => {
                      setComparisonMode("source");
                      setCompareRequested(true);
                      selectTab("revisions");
                    }}
                    sourceFiles={sourceFiles}
                    previewFailure={
                      meshFile &&
                      previewFeedback?.url === getAssetUrl(`/api/v1/files/${meshFile.id}/stl`) &&
                      previewFeedback.reason !== null
                        ? { fileId: meshFile.id, reason: previewFeedback.reason }
                        : null
                    }
                    trashedSourceFiles={model.trashed_source_files ?? []}
                    canEdit={canEditModel}
                    onModel={setModel}
                  />
                </>
              )}

              {activeTab === "history" && canViewPrinters && printJobQuery.isError && (
                <Button variant="outline" onClick={() => void printJobQuery.refetch()}>
                  {uiText("Retry")}
                </Button>
              )}
              {activeTab === "history" && canViewPrinters && printJobQuery.isPending && (
                <Loader2 className="h-5 w-5 animate-spin" />
              )}
              {activeTab === "history" &&
                canViewPrinters &&
                printJobQuery.data &&
                !printJobQuery.isError && (
                  <PrintHistorySection
                    modelName={model.name}
                    initialEntry={historyDraft}
                    jobs={printJobs}
                    modelId={model.id}
                    gcodeFiles={gcodeFiles}
                    onJobCreated={(job) => {
                      setHistoryDraft(null);
                      void printJobQuery.publish(job);
                    }}
                  />
                )}
            </div>

            {/* Klipper Sync Panel */}
            <div className="detail-context-footer border-t border-outline-variant bg-surface-container-low shrink-0 space-y-2">
              {gcodeFiles.length > 0 && canViewPrinters && (
                <SendToButtons
                  modelName={model.name}
                  gcodeFiles={gcodeFiles}
                  printerFiles={printerFiles}
                  open={sendOpen}
                  onOpenChange={setSendOpen}
                  preselectFileId={inspectedGcode?.id}
                  compact
                  initialDeliveryMode={deliveryMode}
                />
              )}
              {!hasGcode && (
                <div className="space-y-3">
                  <div className="flex items-center justify-between">
                    <span className="font-mono text-xs text-on-surface-variant uppercase tracking-wider">
                      {uiText("Sync status")}
                    </span>
                    <div className="flex items-center gap-1.5 px-2 py-1 bg-surface-container-lowest border border-outline-variant rounded">
                      <Wifi className="h-3 w-3 text-on-surface-variant" />
                      <span className="font-mono text-xs text-on-surface-variant">
                        {uiText("No G-code revision")}
                      </span>
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </Localized>
  );
}
