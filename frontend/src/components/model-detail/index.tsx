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
import { InlineTitleEditor } from "@/components/inline-title-editor";
import { GeometryFacts } from "./geometry-facts";
import { ModelInformation } from "./model-information";
import { ModelSourceSummary } from "./source-summary";
import { RelatedProfiles } from "./related-profiles";
import { useRelatedProfiles } from "./use-related-profiles";
import { ModelPrintSummary } from "./model-print-summary";
import { summarizePrintEstimate } from "./print-estimate";
import { ProjectProfile } from "./project-profile";
import { useMeshGeometryMeasurement } from "./use-mesh-geometry-facts";
import { RevisionPicker } from "./revision-picker";
import { inspectedRevision } from "./inspected-revision";
import "./model-detail.css";
import { ModelSearchAction } from "@/components/model-search-action";

import { uiText } from "@/lib/locale";
import { formatNumber } from "@/lib/format";
import { useUiLocale } from "@/lib/i18n";

import {
  Suspense,
  lazy,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import { useRouter, useSearchParams } from "@/lib/navigation";
import { useLocation } from "@tanstack/react-router";
import {
  Files,
  FileText,
  Loader2,
  MoreHorizontal,
  Pencil,
  Share2,
  Star,
  Trash2,
} from "lucide-react";

import type {
  STLViewerControls,
  ViewerControls,
  ViewerCameraPreset,
  STLPreviewFeedback,
} from "@/components/stl-viewer";
import { useToolpathSources } from "@/lib/toolpath-preview";
import type { ViewerMode } from "@/components/model-detail/viewer-toolbar";
import { deleteModel, getAssetUrl, getModel, updateModel } from "@/lib/api";
import { usePrinters, useTags } from "@/lib/queries";
import { readMetadataPreferences } from "@/lib/metadata-preferences";
import { toast } from "@/lib/toast";
import { useAuth } from "@/lib/auth-context";
import { useAuthenticatedAssetUrl } from "@/lib/use-authenticated-asset-url";
import { useRequireAuth } from "@/lib/use-require-auth";
import { keyboardOverlayOpen, modalOverlayOpen } from "@/lib/keyboard-commands";
import { ModelPrinterFileRead, ModelRead, TagRead } from "@/types";

import { ConfirmModal } from "@/components/ui/confirm-modal";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { DropdownMenu } from "@/components/ui/dropdown-menu";
import { TabBar } from "@/components/ui/tabs";
import { AddGcodeRevisionModal } from "./add-revision-modal";
import { SimilarityQueue } from "@/components/similarity-queue";
import { DocumentBrowser } from "@/components/document-browser";
import { FilesTab } from "./files-tab";
import { ModelMetadataFields, type ModelMetaEditor } from "./model-metadata-fields";
import {
  DETAIL_SECTIONS,
  modelDetailParent,
  modelDetailSectionLabel,
  TabKey,
  buildPrintSettingRows,
  normalizeRecommendedGcodeFiles,
  plateLabel,
} from "./presentation";
import { PrintHistorySection } from "./print-history-section";
import { ProductionCosting } from "./production-costing";
import { RevisionsTab } from "./revisions-tab";
import { RevisionDeliveryActions } from "./revision-delivery-actions";
import { useRevisionUpdater } from "./use-revision-updater";
import { SendToButtons } from "./send-to-buttons";
import { ShareDialog } from "./share-dialog";
import { SettingsTab } from "./settings-tab";
import { safeHttpUrl } from "./source-url";
import { SourceTab } from "./source-tab";
import { useContentReveal } from "@/lib/use-content-reveal";
import { DetailTabLoading } from "./tab-loading";
import { useDerivativeRefresh } from "./use-derivative-refresh";
import { ViewerToolbar } from "./viewer-toolbar";
import { ModelCoverEditor } from "./cover-editor";
import { PlatePicker } from "./plate-picker";
import { useFilePlates, useGcodePlates } from "@/lib/queries/file-plates";
import { matchSlicedPlate } from "./plate-mapping";
import { Localized } from "@/components/ui/localized";

const STLViewer = lazy(() =>
  import("@/components/stl-viewer").then((m) => ({ default: m.STLViewer })),
);

const GcodePreview = lazy(() =>
  import("./gcode-preview").then((m) => ({ default: m.GcodePreview })),
);

const compareBackLabel = {
  files: "Back to files",
  settings: "Back to print settings",
} as const;

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

const DETAIL_SIDEBAR_STORAGE_KEY = "ps-model-detail-inspector-preferred-width";
const DETAIL_PRIMARY_TABS = ["overview", "files", "revisions", "history"] as const;
const PRINT_DETAIL_SECTIONS = ["revisions", "settings", "costing"] as const;
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

function getBedSize(printerModel: string | null | undefined): BedSize | null {
  if (!printerModel) return null;
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
    return null;
  }
  if (m.includes("cr-10") || m.includes("cr10")) return { x: 300, y: 300 };
  if (m.includes("ender") || m.includes("k1")) return { x: 220, y: 220 };
  return null;
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
  const [editMode, setEditMode] = useState<"details" | "name" | "source" | null>(null);
  const addingSource = editMode === "source";
  const editing = editMode === "details" || addingSource;
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
  const [coverOpen, setCoverOpen] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);
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
  const requestedTab = DETAIL_SECTIONS.find((tab) => tab === searchParams.get("tab")) ?? "overview";
  const tabIntent = useRef(requestedTab);
  function selectTab(tab: TabKey) {
    // A click back to the visible tab must still supersede a pending navigation.
    if (tab === activeTab && tabIntent.current === tab) return;
    tabIntent.current = tab;
    if (tab !== "revisions" && tab !== "files") setCompareOrigin(null);
    if (tab !== "history") {
      setHistoryDraft(null);
      setHistoryRevisionId(null);
    }
    const params = new URLSearchParams(searchParams);
    params.set("tab", tab);
    router.replace(`/models/${model.id}?${params}`, {
      resetScroll: false,
      state: location.state,
    });
  }
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
    preferredDetailSidebarWidth ?? 490,
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
          detailLayout.current?.querySelector<HTMLElement>("[data-preview-controls]")?.focus(),
        );
      }
    }
    window.addEventListener("keydown", toggleDetails);
    return () => window.removeEventListener("keydown", toggleDetails);
  }, []);
  useEffect(() => {
    if (!previewExpanded) return;
    function collapse(event: KeyboardEvent) {
      if (event.key !== "Escape" || event.defaultPrevented || keyboardOverlayOpen()) return;
      event.preventDefault();
      setPreviewExpanded(false);
      detailLayout.current?.querySelector<HTMLElement>("[data-preview-controls]")?.focus();
    }
    window.addEventListener("keydown", collapse, true);
    return () => window.removeEventListener("keydown", collapse, true);
  }, [previewExpanded]);
  // The plate chosen for one 3MF; another mesh file shows its whole build.
  const [inspectedSlicedPlate, setInspectedSlicedPlate] = useState<{
    fileId: number;
    number: number;
  } | null>(null);
  const [chosenPlate, setChosenPlate] = useState<{ fileId: number; number: number } | null>(null);
  const [plateDimensions, setPlateDimensions] = useState<{
    fileId: number;
    number: number;
    dimensions: { x: number; y: number; z: number };
  } | null>(null);
  const [sendOpen, setSendOpen] = useState(false);
  const [compareOrigin, setCompareOrigin] = useState<keyof typeof compareBackLabel | null>(null);
  const compareFocus = useRef<TabKey | null>(null);
  function openComparison(origin: keyof typeof compareBackLabel) {
    setCompareOrigin(origin);
    if (origin === "settings") selectTab("revisions");
  }
  const [historyRevisionId, setHistoryRevisionId] = useState<number | null>(null);
  const [sentModelId, setSentModelId] = useState<number | null>(null);
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
  const actionsOpener = useRef<HTMLElement | null>(null);
  const { createTag, deleteTag } = useTaxonomyCommands();
  const metadataClient = useQueryClient();
  const [deleteTagTarget, setDeleteTagTarget] = useState<TagRead | null>(null);
  const [deleteTagBusy, setDeleteTagBusy] = useState(false);
  const [requestedViewerMode, setViewerMode] = useState<ViewerMode>("model");
  const [cameraPreset, setCameraPreset] = useState<ViewerCameraPreset | null>("isometric");
  const clearCameraPreset = useCallback(() => setCameraPreset(null), [setCameraPreset]);
  const [plateContainer, setPlateContainer] = useState<HTMLDivElement | null>(null);
  const [slicedPlatePickerReady, setSlicedPlatePickerReady] = useState(false);
  const [revisionSelection, setRevisionSelection] = useState<{
    modelId: number;
    fileId: number;
  } | null>(null);
  const [viewerReady, setViewerReady] = useState(false);
  const [previewFeedback, setPreviewFeedback] = useState<STLPreviewFeedback | null>(null);
  const viewerControls = useRef<STLViewerControls | null>(null);
  const toolpathControls = useRef<ViewerControls | null>(null);
  const [toolpathReady, setToolpathReady] = useState(false);
  const connectToolpathControls = useCallback((controls: ViewerControls | null) => {
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
  const printers = usePrinters({ enabled: canViewPrinters });
  const printerless = printers.isSuccess && printers.data.length === 0;
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
  const activePrimary = modelDetailParent(activeTab);
  useLayoutEffect(() => {
    if (compareFocus.current !== activeTab) return;
    detailLayout.current?.querySelector<HTMLButtonElement>("[data-compare-origin]")?.focus();
    compareFocus.current = null;
  }, [activeTab, compareOrigin]);
  const detailTabBody = useContentReveal(activeTab, { scroll: "parent" });
  const detailTabPanel = useRef<HTMLDivElement>(null);
  const focusSection = useRef(false);
  useLayoutEffect(() => {
    if (!focusSection.current) return;
    focusSection.current = false;
    detailTabPanel.current?.focus({ preventScroll: true });
  }, [activeTab]);

  function openSection(tab: TabKey) {
    if (tab !== activeTab) focusSection.current = true;
    selectTab(tab);
  }

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

  function enterEdit(mode: "details" | "name" | "source" = "details") {
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
    setEditMode(mode);
  }

  useEffect(() => {
    function rename(event: KeyboardEvent) {
      if (
        event.key !== "F2" ||
        event.defaultPrevented ||
        event.repeat ||
        event.isComposing ||
        event.ctrlKey ||
        event.metaKey ||
        event.altKey ||
        event.shiftKey ||
        keyboardOverlayOpen() ||
        !auth.isAuthenticated ||
        !canEditModel ||
        editMode ||
        saving ||
        (event.target instanceof HTMLElement &&
          event.target.closest('input, textarea, select, [contenteditable="true"]'))
      )
        return;
      event.preventDefault();
      enterEdit("name");
    }
    window.addEventListener("keydown", rename, true);
    return () => window.removeEventListener("keydown", rename, true);
  });

  function cancelEdit() {
    editLifetime.current += 1;
    setSaving(false);
    setReviewing(false);
    setEditProblem(null);
    setReviewedModel(null);
    setEditMode(null);
  }

  async function saveEdit(version = editBase) {
    if (
      !editName.trim() ||
      (addingSource && !safeHttpUrl(editSourceUrl.trim())) ||
      saving ||
      !canEditModel ||
      reviewedModel?.effective_role === "view"
    )
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
          : addingSource
            ? { source_url: editSourceUrl.trim() }
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
  const tabItems = DETAIL_PRIMARY_TABS.filter((tab) => tab !== "history" || canViewPrinters).map(
    (key) => {
      const count =
        key === "files"
          ? sourceFiles.length
          : key === "revisions"
            ? gcodeFiles.length
            : key === "history"
              ? printJobs.length
              : 0;
      return {
        key,
        label: (
          <>
            {modelDetailSectionLabel(key)}
            {count > 0 && <span className="model-detail-tab-count"> {count}</span>}
          </>
        ),
      };
    },
  );

  const recommendedGcode = gcodeFiles.find((f) => f.is_recommended) ?? null;
  const inspectedGcode = inspectedRevision(
    gcodeFiles,
    revisionSelection?.modelId === model.id ? revisionSelection.fileId : null,
  );
  const overviewRevision = recommendedGcode ?? inspectedGcode;
  const requestedArtifact = Number(searchParams.get("artifact"));
  const meshCandidates = model.files.filter(
    (f) =>
      f.sliced_geometry !== "gcode_only" &&
      (f.file_type === "stl" ||
        f.file_type === "3mf" ||
        f.file_type === "obj" ||
        f.file_type === "glb" ||
        f.file_type === "ply" ||
        f.file_type === "gltf" ||
        f.file_type === "3dm" ||
        f.file_type === "fbx" ||
        f.file_type === "step"),
  );
  const meshFile =
    meshCandidates.find((file) => file.id === requestedArtifact) ?? meshCandidates[0];
  const geometryMeasurement = useMeshGeometryMeasurement(meshFile ?? null);
  const hasGcode = gcodeFiles.length > 0;
  const sliceableFile = sourceFiles.find((file) => ["stl", "3mf", "obj"].includes(file.file_type));
  const { sources: toolpathFiles, availability: toolpathAvailability } =
    useToolpathSources(sortedFiles);
  const hasToolpathPreview = toolpathAvailability === "available";
  const gcodeOnly =
    !meshFile && toolpathFiles.some((file) => file.sliced_geometry === "gcode_only");
  const viewerMode = gcodeOnly ? "gcode" : hasToolpathPreview ? requestedViewerMode : "model";
  const previewSourceId =
    toolpathSource?.modelId === model.id &&
    toolpathSource.revisionId === (inspectedGcode?.id ?? null)
      ? toolpathSource.fileId
      : inspectedGcode?.id;
  const previewSource =
    toolpathFiles.find((file) => file.id === previewSourceId) ?? inspectedGcode ?? toolpathFiles[0];
  function selectToolpathSource(fileId: number) {
    const file = sortedFiles.find((source) => source.id === fileId);
    if (file?.file_type === "gcode") {
      setRevisionSelection({ modelId: model.id, fileId });
      setToolpathSource(null);
    } else if (file?.file_type === "3mf") {
      setToolpathSource({ modelId: model.id, revisionId: inspectedGcode?.id ?? null, fileId });
    }
  }
  const previewRevision = gcodeFiles.find((file) => file.id === previewSource?.id);
  const toolpathPlate =
    inspectedSlicedPlate?.fileId === previewSource?.id ? inspectedSlicedPlate : null;
  const toolpathLabel = [
    previewRevision &&
      uiText("Rev {value1}", {
        value1: String(previewRevision.gcode_revision_number ?? previewRevision.version),
      }),
    toolpathPlate &&
      uiText("Plate {value1}", { value1: String(toolpathPlate.number).padStart(2, "0") }),
    previewSource?.original_filename,
  ]
    .filter(Boolean)
    .join(" · ");
  const filePlates = useFilePlates(model.id, meshFile?.file_type === "3mf" ? meshFile.id : null);
  // A single plate is the whole build; only a multi-plate project offers a choice.
  const plates = filePlates !== null && filePlates.plates.length > 1 ? filePlates.plates : [];
  // Keep the same row in place until the sliced picker is ready to take over.
  const pickerShown = plates.length > 1 && (viewerMode === "model" || !slicedPlatePickerReady);
  // The viewer's corner controls sit above the plate strip when it is shown.
  const shownPlate =
    chosenPlate !== null && chosenPlate.fileId === meshFile?.id
      ? (plates.find((plate) => plate.number === chosenPlate.number) ?? null)
      : null;
  const slicedMetadataFile =
    !inspectedGcode && previewSource?.sliced_geometry === "gcode_only"
      ? previewSource
      : inspectedGcode;
  const printSettingsFile =
    slicedMetadataFile ?? sortedFiles.findLast((file) => file.file_type === "resin");
  const slicedEstimates = useGcodePlates(
    model.id,
    slicedMetadataFile?.original_filename.toLowerCase().endsWith(".3mf")
      ? slicedMetadataFile.id
      : null,
  );
  const selectedEstimatePlate =
    inspectedSlicedPlate?.fileId === slicedMetadataFile?.id && inspectedSlicedPlate
      ? inspectedSlicedPlate.number
      : shownPlate && slicedEstimates
        ? (matchSlicedPlate(shownPlate, plates, slicedEstimates.identities ?? [])?.number ?? null)
        : null;
  const dispatchPlate = inspectedSlicedPlate
    ? inspectedSlicedPlate.fileId === inspectedGcode?.id
      ? inspectedSlicedPlate.number
      : null
    : selectedEstimatePlate;
  const costingPlate =
    dispatchPlate === null || !inspectedGcode
      ? undefined
      : slicedEstimates?.plate_metadata?.find((entry) => entry.number === dispatchPlate);
  const summaryEstimates = useGcodePlates(
    model.id,
    overviewRevision?.original_filename.toLowerCase().endsWith(".3mf") ? overviewRevision.id : null,
  );
  const revisionEstimate =
    overviewRevision && summarizePrintEstimate(overviewRevision, summaryEstimates);
  const summaryProfiles = useRelatedProfiles(model.id, activeTab === "overview").data?.items;
  function inspectRecommended() {
    if (!overviewRevision) return;
    setCompareOrigin(null);
    setRevisionSelection({ modelId: model.id, fileId: overviewRevision.id });
    openSection("revisions");
  }
  const revisionUpdater = useRevisionUpdater(model.id, setModel);
  function prepareRecommended() {
    if (!overviewRevision) return;
    setRevisionSelection({ modelId: model.id, fileId: overviewRevision.id });
    setSendOpen(true);
  }
  const thumbUrl = useAuthenticatedAssetUrl(
    !meshFile && !hasToolpathPreview
      ? (model.previews?.large_cover_url ?? model.thumbnail_url)
      : null,
  );
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

  const logPrint =
    canViewPrinters && canEditModel && auth.isAuthenticated
      ? (fileId: number, state: "completed" | "failed") => {
          setRevisionSelection({ modelId: model.id, fileId });
          setHistoryRevisionId(fileId);
          setHistoryDraft({ fileId, state });
        }
      : undefined;

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
        {coverOpen && canEditModel && auth.isAuthenticated && (
          <ModelCoverEditor
            model={model}
            publish={setModel}
            capture={
              viewerReady && viewerMode === "model" && meshFile
                ? async () => {
                    const controls = viewerControls.current;
                    if (!controls) throw new Error("preview_not_ready");
                    return controls.captureImage();
                  }
                : null
            }
            onClose={() => setCoverOpen(false)}
          />
        )}
        <ShareDialog
          modelName={model.name}
          modelId={model.id}
          files={model.files}
          open={shareOpen}
          onClose={() => setShareOpen(false)}
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
          title={uiText(addingSource ? "Add source" : "Edit Model details")}
          className={
            addingSource
              ? undefined
              : "absolute inset-y-0 right-0 h-dvh max-h-none max-w-[680px] rounded-none"
          }
          bodyClassName="flex-1"
          footer={
            <>
              <Button variant="outline" onClick={cancelEdit} disabled={saving}>
                {uiText("Cancel")}
              </Button>
              <Button
                onClick={() => void saveEdit()}
                loading={saving}
                disabled={
                  !editName.trim() ||
                  (addingSource && !safeHttpUrl(editSourceUrl.trim())) ||
                  editProblem !== null ||
                  !canEditModel
                }
              >
                {uiText("Save changes")}
              </Button>
            </>
          }
        >
          <div className="space-y-4">
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

            {addingSource ? (
              <div className="space-y-1.5">
                <label className="block space-y-1.5 text-sm font-medium">
                  {uiText("Source link")}
                  <Input
                    type="url"
                    value={editSourceUrl}
                    placeholder="https://www.printables.com/model/..."
                    onChange={(event) => setEditSourceUrl(event.target.value)}
                    disabled={saving}
                  />
                </label>
                <p className="text-sm text-muted-foreground">
                  {uiText(
                    "PrintStash reads the title, creator and license from the page. Nothing in this Model changes until you save.",
                  )}
                </p>
              </div>
            ) : (
              <>
                <label className="block space-y-1.5 text-sm font-medium">
                  {uiText("Name (required)")}
                  <Input
                    ref={nameInput}
                    required
                    value={editName}
                    onChange={(event) => setEditName(event.target.value)}
                    aria-invalid={!editName.trim()}
                    aria-describedby={!editName.trim() ? "model-name-error" : undefined}
                    disabled={saving}
                  />
                </label>
                {!editName.trim() && (
                  <p id="model-name-error" className="text-sm text-destructive">
                    {uiText("Model name is required.")}
                  </p>
                )}
                <div className="space-y-1.5">
                  <span className="block text-sm font-medium">{uiText("Cover")}</span>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={saving}
                    onClick={() => setCoverOpen(true)}
                  >
                    {uiText("modelCover.edit")}
                  </Button>
                </div>
                <ModelMetadataFields editor={modelMetadataEditor} />
              </>
            )}
          </div>
        </Modal>
        {/* Detail Header */}
        <DropdownMenu
          open={actionsOpen}
          returnFocusRef={actionsOpener}
          onOpenChange={setActionsOpen}
          portal
          className="shrink-0"
          aria-label={uiText("Model actions")}
          contentClassName="w-48 border border-border bg-popover p-1 text-popover-foreground shadow-lg"
          trigger={
            <header
              onContextMenu={(event) => {
                event.preventDefault();
                actionsOpener.current = null;
                setActionsOpen(true);
              }}
              onKeyDown={(event) => {
                if (event.key === "ContextMenu" || (event.shiftKey && event.key === "F10")) {
                  event.preventDefault();
                  actionsOpener.current = event.target instanceof HTMLElement ? event.target : null;
                  setActionsOpen(true);
                }
              }}
              className="model-detail-heading flex flex-wrap items-center justify-between px-5 pt-4 pb-2.5 gap-2 bg-background shrink-0"
            >
              <div className="flex min-w-0 items-start gap-3 md:gap-4">
                <div className="min-w-0 flex-1">
                  <InlineTitleEditor
                    title={model.name}
                    fieldLabel={uiText("Model name")}
                    canEdit={canEditModel && auth.isAuthenticated}
                    open={renaming && canEditModel && auth.isAuthenticated}
                    value={editName}
                    onValueChange={setEditName}
                    onOpen={() => enterEdit("name")}
                    onCancel={cancelEdit}
                    onSave={() => saveEdit()}
                    saveBlocked={editProblem !== null}
                    busy={saving}
                  />
                  {renaming && editProblem && (
                    <div className="space-y-2 border-t border-border pt-3 text-xs">
                      <Button size="sm" variant="outline" onClick={cancelEdit} disabled={saving}>
                        {uiText("Cancel")}
                      </Button>
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
                </div>
              </div>
              <div
                className={`ms-auto flex min-w-0 flex-wrap items-center justify-end gap-2 ${editing ? "w-full md:w-auto md:self-start" : ""}`}
              >
                <>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
                    className="model-detail-favorite"
                    aria-pressed={model.starred}
                    aria-label={
                      model.starred
                        ? uiText("Remove {value1} from favorites", { value1: model.name })
                        : uiText("Add {value1} to favorites", { value1: model.name })
                    }
                    title={model.starred ? uiText("Favorited") : uiText("Favorite")}
                    onClick={() => void toggleFavorite()}
                    disabled={starBusy || !auth.isAuthenticated}
                  >
                    <Star
                      aria-hidden
                      className={
                        model.starred
                          ? "h-5 w-5 fill-primary text-primary"
                          : "h-5 w-5 text-muted-foreground"
                      }
                    />
                  </Button>
                  {canEditModel && auth.isAuthenticated && !editing && (
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      className="model-detail-favorite"
                      aria-label={uiText("Edit details")}
                      title={uiText("Edit details")}
                      onClick={() => enterEdit()}
                    >
                      <Pencil className="h-5 w-5 shrink-0 text-muted-foreground" aria-hidden />
                    </Button>
                  )}
                  <Button
                    data-menu-trigger
                    variant="outline"
                    size="sm"
                    aria-label={uiText("More model actions")}
                    title={uiText("Model actions")}
                    aria-haspopup="menu"
                    aria-expanded={actionsOpen}
                    onClick={(event) => {
                      actionsOpener.current = event.currentTarget;
                      setActionsOpen(!actionsOpen);
                    }}
                  >
                    <MoreHorizontal className="h-4 w-4" />
                    {uiText("nav.more")}
                  </Button>
                </>
              </div>
            </header>
          }
        >
          <button
            type="button"
            role="menuitem"
            disabled={!auth.isAuthenticated || !canEditModel}
            onClick={() => {
              setActionsOpen(false);
              setShareOpen(true);
            }}
            className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm hover:bg-popover-hover focus-visible:bg-popover-hover focus-visible:outline-none disabled:pointer-events-none disabled:opacity-50"
          >
            <Share2 className="size-4" aria-hidden />
            {uiText("Share")}
          </button>
          <ModelSearchAction modelId={model.id} onSelect={() => setActionsOpen(false)} />
          {canEditModel && (
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setActionsOpen(false);
                openSection("similar");
              }}
              className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm hover:bg-popover-hover focus-visible:bg-popover-hover focus-visible:outline-none"
            >
              <Files className="size-4" aria-hidden />
              {uiText("similarity.title")}
            </button>
          )}
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
              className="model-detail-preview relative min-h-80 overflow-hidden rounded-lg border border-outline-variant bg-surface-container-low"
            >
              <div className="detail-preview-canvas relative flex min-h-80 w-full items-center justify-center md:min-h-0">
                {viewerMode === "gcode" && hasToolpathPreview ? (
                  <Suspense fallback={ViewerFallback}>
                    <GcodePreview
                      files={toolpathFiles}
                      archiveImages={model.previews?.archive_images}
                      bedSizeFor={(file) => getBedSize(file.metadata?.printer_model)}
                      selectedFileId={previewSource?.id}
                      onSelectFile={selectToolpathSource}
                      onInspectSlicedPlate={(fileId, number) =>
                        setInspectedSlicedPlate({ fileId, number })
                      }
                      sourcePlates={plates}
                      plateContainer={plateContainer}
                      onPlatePickerReady={setSlicedPlatePickerReady}
                      selectedSourcePlate={shownPlate?.number ?? null}
                      onSelectSourcePlate={(number) =>
                        setChosenPlate(
                          number === null || !meshFile ? null : { fileId: meshFile.id, number },
                        )
                      }
                      viewerProps={{
                        screenshotName: model.slug || model.name,
                        sharedControls: true,
                        showGrid,
                        showDimensions,
                        onShowGridChange: setShowGrid,
                        onShowDimensionsChange: setShowDimensions,
                        onControlsReady: connectToolpathControls,
                        onOrbit: clearCameraPreset,
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
                      onOrbit={clearCameraPreset}
                      onFailureChange={setPreviewFeedback}
                      showGrid={showGrid}
                      screenshotName={
                        shownPlate
                          ? `${model.slug || model.name}-plate-${plateLabel(shownPlate)}`
                          : model.slug || model.name
                      }
                      plate={shownPlate?.region}
                      onGeometryDimensions={(dimensions) => {
                        if (shownPlate && meshFile && dimensions) {
                          setPlateDimensions({
                            fileId: meshFile.id,
                            number: shownPlate.number,
                            dimensions,
                          });
                        }
                      }}
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
                {/* Source plate bounds are measured from the shown mesh, separately from
                    the whole-source metadata in Source info. */}
                {viewerMode === "model" &&
                  showDimensions &&
                  viewerReady &&
                  shownPlate &&
                  plateDimensions !== null &&
                  plateDimensions.fileId === meshFile?.id &&
                  plateDimensions.number === shownPlate.number && (
                    <div className="absolute bottom-4 left-4 z-10 bg-surface-container-lowest/90 backdrop-blur border border-outline-variant rounded px-2 py-1 text-xs text-on-surface">
                      <span>{uiText("Selected plate mesh bounds")}: </span>
                      <span className="font-mono">
                        {uiText("{value1}×{value2}×{value3} mm", {
                          value1: formatNumber(plateDimensions.dimensions.x, {
                            maximumFractionDigits: 1,
                          }),
                          value2: formatNumber(plateDimensions.dimensions.y, {
                            maximumFractionDigits: 1,
                          }),
                          value3: formatNumber(plateDimensions.dimensions.z, {
                            maximumFractionDigits: 1,
                          }),
                        })}
                      </span>
                    </div>
                  )}
                {/* Whole-source dimensions do not describe an individual plate. */}
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
                        <span>{uiText("Whole source geometry")}: </span>
                        <span className="font-mono text-[13px] text-on-surface">
                          {uiText("{value1}×{value2}×{value3} mm", {
                            value1: formatNumber(meshFile.metadata.bbox_x_mm, {
                              maximumFractionDigits: 1,
                            }),
                            value2: formatNumber(meshFile.metadata.bbox_y_mm, {
                              maximumFractionDigits: 1,
                            }),
                            value3: formatNumber(meshFile.metadata.bbox_z_mm, {
                              maximumFractionDigits: 1,
                            }),
                          })}
                        </span>
                      </div>
                    </div>
                  )}
              </div>
              {(meshFile || hasToolpathPreview) && (
                <div className="contents">
                  <ViewerToolbar
                    camera={cameraPreset}
                    setCamera={setCameraPreset}
                    showGrid={showGrid}
                    setShowGrid={setShowGrid}
                    controls={viewerMode === "gcode" ? toolpathControls : viewerControls}
                    viewerMode={viewerMode}
                    setViewerMode={setViewerMode}
                    hasGcode={hasToolpathPreview}
                    availability={toolpathAvailability}
                    gcodeOnly={gcodeOnly}
                    viewerReady={viewerMode === "gcode" ? toolpathReady : viewerReady}
                    filename={
                      viewerMode === "gcode"
                        ? toolpathLabel
                        : shownPlate && meshFile
                          ? `${meshFile.original_filename} · ${uiText("Plate {value1}", { value1: plateLabel(shownPlate) })}`
                          : meshFile?.original_filename
                    }
                    showDimensions={showDimensions}
                    onDimensions={setShowDimensions}
                  />
                </div>
              )}
              <div className="contents" ref={setPlateContainer} />
              {pickerShown && meshFile && (
                <PlatePicker
                  plates={plates}
                  allowAll={viewerMode === "model"}
                  selected={shownPlate?.number ?? null}
                  onSelect={(number) => {
                    setInspectedSlicedPlate(null);
                    setChosenPlate(number === null ? null : { fileId: meshFile.id, number });
                  }}
                />
              )}
            </div>
          </div>

          {/* Right: Settings & Files Panel */}
          <div
            data-testid="model-detail-sidebar"
            style={{ "--detail-sidebar-width": `${detailSidebarWidth}px` }}
            className="model-detail-inspector relative flex min-h-0 w-full shrink-0 flex-col rounded-lg border border-outline-variant bg-surface-container-lowest md:[width:var(--detail-sidebar-width)]"
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

            <div className="model-detail-tabs shrink-0 overflow-x-auto border-b py-2">
              <TabBar
                tabs={tabItems}
                active={activePrimary}
                onChange={(tab) => {
                  setCompareOrigin(null);
                  selectTab(tab);
                }}
                showIndicator={false}
                className="model-detail-tab-list w-fit gap-0.5 rounded-lg border border-border bg-card p-0.75"
                tabClassName="model-detail-tab-label relative h-7.5 rounded-md px-2.5 py-0 text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
                activeTabClassName="bg-foreground/12 text-foreground"
              />
            </div>
            {activePrimary === "revisions" && (
              <div className="mx-5 flex flex-wrap items-center justify-between gap-2 border-b border-border py-2">
                <nav
                  aria-label={uiText("modelDetail.printSections")}
                  className="inline-flex flex-wrap gap-0.5 rounded-lg border border-border bg-card p-0.75"
                >
                  {PRINT_DETAIL_SECTIONS.map((section) => (
                    <Button
                      key={section}
                      type="button"
                      variant="ghost"
                      size="sm"
                      aria-current={section === activeTab ? "page" : undefined}
                      className={
                        section === activeTab
                          ? "h-7.5 bg-foreground/12 px-2.5 text-foreground"
                          : "h-7.5 px-2.5 text-muted-foreground"
                      }
                      onClick={() => {
                        setCompareOrigin(null);
                        openSection(section);
                      }}
                    >
                      {section === "revisions"
                        ? uiText("Revisions")
                        : modelDetailSectionLabel(section)}
                    </Button>
                  ))}
                </nav>
                {hasGcode && canViewPrinters && (
                  <RevisionDeliveryActions className="ms-auto" onSend={() => setSendOpen(true)} />
                )}
              </div>
            )}
            <div
              ref={detailTabPanel}
              role="tabpanel"
              tabIndex={0}
              aria-label={modelDetailSectionLabel(activeTab)}
              className="model-detail-content focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring flex-1 min-h-0 overflow-y-auto p-4"
            >
              <div ref={detailTabBody} className="model-detail-tab-body space-y-4">
                {activeTab === "overview" && (
                  <div className="space-y-3.5">
                    <ModelPrintSummary
                      revision={overviewRevision}
                      estimates={summaryEstimates}
                      profiles={summaryProfiles?.filter((item) => item.model_id === model.id)}
                      jobs={canViewPrinters && printJobQuery.isSuccess ? printJobs : null}
                      source={sliceableFile}
                      onReview={inspectRecommended}
                      onMarkKnownGood={
                        canEditModel &&
                        auth.isAuthenticated &&
                        overviewRevision &&
                        revisionUpdater.saving === null
                          ? () =>
                              void revisionUpdater.update(overviewRevision, {
                                revision_status: "known_good",
                              })
                          : undefined
                      }
                      sent={sentModelId === model.id}
                      onPrepare={canViewPrinters ? prepareRecommended : undefined}
                      onRecordResult={
                        overviewRevision && logPrint
                          ? () => logPrint(overviewRevision.id, "completed")
                          : undefined
                      }
                      onPlateEstimates={() => {
                        if (overviewRevision)
                          setRevisionSelection({ modelId: model.id, fileId: overviewRevision.id });
                        openSection("settings");
                      }}
                      onAddRevision={
                        canEditModel && auth.isAuthenticated ? requestAddRevision : undefined
                      }
                      onFiles={() => openSection("files")}
                    />
                    <div className="grid items-start gap-3.5 @2xl:grid-cols-2">
                      <ModelInformation
                        model={model}
                        canEdit={canEditModel && auth.isAuthenticated}
                        tagSuggestions={tags}
                        onTagsSaved={(nextTags, editVersion) => {
                          void setModel((current) => ({
                            ...current,
                            tags: nextTags,
                            ...editVersion,
                          }));
                        }}
                        onEditDetails={
                          canEditModel && auth.isAuthenticated ? () => enterEdit() : undefined
                        }
                      />
                      <ModelSourceSummary
                        modelId={model.id}
                        sourceUrl={model.source_url}
                        onAddSource={
                          canEditModel && auth.isAuthenticated
                            ? () => enterEdit("source")
                            : undefined
                        }
                      />
                    </div>
                  </div>
                )}

                {activeTab === "costing" && (
                  <ProductionCosting
                    key={`costing-${model.id}`}
                    modelId={model.id}
                    files={model.files.filter(
                      (file) => file.file_type === "stl" || file.file_type === "gcode",
                    )}
                    initialFileId={inspectedGcode?.id ?? meshFile?.id ?? null}
                    plateEstimate={
                      costingPlate && inspectedGcode
                        ? {
                            fileId: inspectedGcode.id,
                            number: costingPlate.number,
                            metadata: costingPlate.metadata,
                          }
                        : undefined
                    }
                    jobs={printJobs}
                    canEdit={canEditModel && auth.isAuthenticated}
                    admin={canViewPrinters}
                    onAddRevision={
                      canEditModel && auth.isAuthenticated ? requestAddRevision : undefined
                    }
                    onFiles={() => openSection("files")}
                  />
                )}

                {activeTab === "settings" && (
                  <SettingsTab
                    meta={printSettingsFile?.metadata}
                    printSettingRows={buildPrintSettingRows(
                      printSettingsFile?.metadata,
                      metadataPreferences,
                    )}
                    preferences={metadataPreferences}
                    hasSlicedRevision={gcodeFiles.length > 0 || gcodeOnly}
                    emptyAction={
                      !printSettingsFile ? (
                        <Button size="sm" variant="outline" onClick={() => openSection("files")}>
                          {uiText("View source files")}
                        </Button>
                      ) : undefined
                    }
                    sourceProject={
                      meshFile?.file_type === "3mf" ? (
                        <ProjectProfile
                          modelId={model.id}
                          fileId={meshFile.id}
                          showPlateEstimates={!printSettingsFile}
                        />
                      ) : undefined
                    }
                    fileName={inspectedGcode?.original_filename}
                    plateEstimates={slicedEstimates}
                    selectedSlicedPlate={selectedEstimatePlate}
                    headerActions={
                      gcodeFiles.length > 1 ? (
                        <Button
                          data-compare-origin
                          variant="ghost"
                          size="sm"
                          onClick={() => openComparison("settings")}
                        >
                          {uiText("Compare")}
                        </Button>
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

                {activeTab === "similar" && (
                  <Button size="sm" variant="outline" onClick={() => openSection("overview")}>
                    {uiText("Back to overview")}
                  </Button>
                )}
                {activeTab === "similar" && canEditModel && <SimilarityQueue modelId={model.id} />}
                {activeTab === "source" && (
                  <SourceTab
                    modelId={model.id}
                    canEdit={canEditModel}
                    onBack={() => openSection("overview")}
                  />
                )}

                {activeTab === "revisions" && canViewPrinters && printerFileQuery.isError && (
                  <Button variant="outline" onClick={() => void printerFileQuery.refetch()}>
                    {uiText("Retry")}
                  </Button>
                )}
                {(activeTab === "revisions" ||
                  (activeTab === "files" && compareOrigin === "files")) && (
                  <RevisionsTab
                    key={activeTab}
                    onFiles={() => openSection("files")}
                    onActivity={
                      canViewPrinters
                        ? (fileId) => {
                            setHistoryRevisionId(fileId);
                            openSection("history");
                          }
                        : undefined
                    }
                    onPreview={(fileId) => {
                      selectToolpathSource(fileId);
                      setViewerMode("gcode");
                    }}
                    inspectedFileId={inspectedGcode?.id}
                    onInspect={(fileId) => setRevisionSelection({ modelId: model.id, fileId })}
                    startComparing={compareOrigin !== null}
                    backLabel={
                      compareOrigin ? compareBackLabel[compareOrigin] : "Back to revisions"
                    }
                    onReturnToOrigin={
                      compareOrigin
                        ? () => {
                            compareFocus.current = compareOrigin;
                            setCompareOrigin(null);
                            selectTab(compareOrigin);
                          }
                        : undefined
                    }
                    comparisonMode={compareOrigin === "files" ? "source" : "gcode"}
                    jobs={canViewPrinters && printJobQuery.isSuccess ? printJobs : null}
                    onSettings={() => selectTab("settings")}
                    onLogPrint={logPrint}
                    modelId={model.id}
                    gcodeFiles={gcodeFiles}
                    allFiles={model.files}
                    printerFilesByFileId={printerFilesByFileId}
                    onModel={setModel}
                    onAddRevision={requestAddRevision}
                    canEdit={canEditModel}
                  />
                )}

                {activeTab === "files" && compareOrigin !== "files" && (
                  <div className="space-y-5">
                    <FilesTab
                      modelId={model.id}
                      onCompare={
                        sourceFiles.length >= 2 ? () => openComparison("files") : undefined
                      }
                      fileFacts={(file) =>
                        meshFile?.id === file.id ? (
                          <GeometryFacts
                            modelId={model.id}
                            sourceFile={file}
                            measurement={geometryMeasurement}
                          />
                        ) : null
                      }
                      sourcePlateCount={
                        meshFile && plates.length > 1
                          ? { fileId: meshFile.id, count: plates.length }
                          : null
                      }
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
                    <RelatedProfiles
                      modelId={model.id}
                      revisionNumber={
                        overviewRevision
                          ? (overviewRevision.gcode_revision_number ?? overviewRevision.version)
                          : undefined
                      }
                      revisionEstimate={
                        revisionEstimate && "time" in revisionEstimate
                          ? revisionEstimate
                          : undefined
                      }
                    />
                    {auth.isAuthenticated && (
                      <DocumentBrowser key={model.id} modelId={model.id} canCreate={canEditModel} />
                    )}
                  </div>
                )}

                {activeTab === "history" && canViewPrinters && printJobQuery.isError && (
                  <Button variant="outline" onClick={() => void printJobQuery.refetch()}>
                    {uiText("Retry")}
                  </Button>
                )}
                {canViewPrinters && printJobQuery.data && (
                  <PrintHistorySection
                    key={
                      historyDraft
                        ? `history-${historyDraft.fileId}-${historyDraft.state}`
                        : `history-${model.id}`
                    }
                    showHistory={activeTab === "history"}
                    modelName={model.name}
                    initialEntry={historyDraft}
                    jobs={printJobs}
                    modelId={model.id}
                    gcodeFiles={gcodeFiles}
                    revisionFilter={historyRevisionId}
                    onRevisionFilter={setHistoryRevisionId}
                    onAddRevision={
                      canEditModel && auth.isAuthenticated ? requestAddRevision : undefined
                    }
                    onOpenRevision={(fileId) => {
                      setRevisionSelection({ modelId: model.id, fileId });
                      openSection("revisions");
                    }}
                    onViewActivity={() => openSection("history")}
                    onDraftClosed={() => setHistoryDraft(null)}
                    onJobCreated={(job) => void printJobQuery.publish(job)}
                  />
                )}
                {activeTab === "history" && canViewPrinters && printJobQuery.isPending && (
                  <DetailTabLoading pending variant="history">
                    {null}
                  </DetailTabLoading>
                )}
              </div>
            </div>

            {hasGcode && canViewPrinters && (
              <SendToButtons
                modelName={model.name}
                gcodeFiles={gcodeFiles}
                printerFiles={printerFiles}
                open={sendOpen}
                onOpenChange={setSendOpen}
                preselectFileId={inspectedGcode?.id}
                preselectPlate={dispatchPlate}
                showPrinterStatus={false}
                initialDeliveryMode={printerless ? "queue" : "send"}
                dialogTitle={uiText("modelDetail.preparePrint")}
                onDelivered={(fileId, message) => {
                  setSentModelId(model.id);
                  if (logPrint)
                    toast.successAction(message, `${uiText("modelDetail.recordResult")} ›`, () =>
                      logPrint(fileId, "completed"),
                    );
                  else toast.success(message);
                }}
              />
            )}
          </div>
        </div>
      </div>
    </Localized>
  );
}
