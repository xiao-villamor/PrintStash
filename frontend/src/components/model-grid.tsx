"use client";

import { LibraryStatusFilters } from "@/components/library-status-filters";
import { CollectionHeaderTags } from "@/components/collection-header-tags";
import "./library-header.css";
import { LibraryEmptyCollection } from "@/components/library-empty-collection";
import { LibraryFilterControl } from "@/components/library-filter-control";
import { LibraryFindBar } from "@/components/library-find-bar";
import { LibraryCollectionsRegion } from "@/components/library-collections-region";
import { ModelGalleryDrag } from "@/components/model-gallery-drag";
import { NewCollectionCard, type CollectionCreateOutcome } from "@/components/new-collection-card";

import { arrivingKeys, entryKeys } from "@/lib/library-arrivals";
import { FOLDER_ENTRANCE_MS } from "@/lib/folder-transition";
import { useEntranceWindow } from "@/lib/entrance-window";
import { DURATION } from "@/lib/overlay";
import { deleteLibraryMultipart, moveLibraryMultipart } from "@/features/library/multipart";
import { useLibraryNavigationReads } from "@/features/library/navigation-reads";
import {
  collectionIdentity,
  treeModelFolderHref,
  useCollectionDestination,
  useRecentFolders,
} from "@/features/library/collection-navigation";
import { useTaxonomyCommands } from "@/features/library/taxonomy";

import { LibraryBatchRecovery } from "@/components/library-batch-recovery";

import {
  libraryEntryControl,
  libraryReadingSelector,
  useLibraryReadingPosition,
  type LibraryReadingRenderer,
} from "@/features/library/reading-position";
import { LibraryItems, type LibraryInspectorDock } from "@/components/library-items";
import {
  InspectorWillMoveEvent,
  LIBRARY_INSPECTOR_MAX_WIDTH,
  LIBRARY_INSPECTOR_MIN_WIDTH,
} from "@/components/use-inspector-motion";
import { useModelOpenIntent } from "@/features/library/model-detail";
import { useLibraryEntry, type LibraryEntry } from "@/features/library/navigation-state";
import {
  moveLibraryModels,
  tagLibraryModels,
  type LibraryEditReceipt,
  type LibraryBatchOutcome,
} from "@/features/library/batch-edits";
import { useSavedViews, type SavedViewCommand } from "@/features/library/saved-views";
import { getSessionVersion, requireSessionVersion } from "@/lib/session-transport";

import {
  libraryBrowseOptions,
  libraryBrowseKeys,
  useLibraryBrowse,
} from "@/features/library/browse";
import { useLibraryThumbnails } from "@/features/library/thumbnails";
import { modelSelection } from "@/components/library-collection-selection";
import { useLibraryAuthority } from "@/features/library/authority";
import { listLibraryPage } from "@/lib/api/library-browse";
import type { LibraryBrowseParams } from "@/types/library-browse";

import { historyFilters, historyKeys } from "@/lib/search-filters";
import {
  readLibraryFilters,
  writeLibraryFilters,
  effectiveLibraryView,
  narrowsLibrary,
  sameLibraryFilters,
  structuredLibraryFilterKeys as STRUCTURED_FILTER_KEYS,
  structuredLibraryFilterLabel,
} from "@/features/library/filters";

import { GettingStartedReminder } from "@/components/getting-started-reminder";

import {
  consumeCollectionRename,
  pendingCollectionRename,
  isTypingTarget,
  keyboardOverlayOpen,
  modalOverlayOpen,
  OUTLINER_KEYBOARD_ROW,
} from "@/lib/keyboard-commands";
import { uiText, type MessageKey } from "@/lib/locale";
import { getErrorMessage, parseApiError, userMessage } from "@/lib/errors";
import { filterValueText } from "@/lib/filter-labels";

import { useUiLocale } from "@/lib/i18n";
import { useIntentPrefetch } from "@/lib/use-intent-prefetch";
import { useLibraryStartup } from "@/lib/library-startup-context";
import { useNavigationShell } from "@/lib/navigation-shell";

import {
  Suspense,
  useCallback,
  useEffect,
  useLayoutEffect,
  useId,
  useMemo,
  useState,
  useRef,
  startTransition,
  type CSSProperties,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { isCancelledError, useQueryClient } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "@/lib/navigation";
import { useLocation } from "@tanstack/react-router";
import {
  CollectionNodeRead,
  CollectionRead,
  CollectionPage,
  ModelBatchResult,
  ModelListItem,
  OutlinerModelRead,
  ModelSort,
  MultipartModelListItem,
  PrinterRead,
  SavedViewRead,
  TagRead,
} from "@/types";
import { ModelCard } from "@/components/model-card";
import { LibraryModelInspector } from "@/components/library-model-inspector";
import { InlineTitleEditor } from "@/components/inline-title-editor";
import { CollectionOverviewCard } from "@/components/collection-overview";
import {
  CollectionCoverEditor,
  type CollectionCardAction,
} from "@/components/collection-card-editor";
import { CollectionBreadcrumbs } from "@/components/collection-breadcrumbs";
import { DeferredDialog } from "@/components/deferred-dialog";
import { lazyImport } from "@/lib/lazy-component";
import { useModelMoves } from "@/features/library/moves";
import { ModelMoveReview } from "@/components/model-move-review";
import type { ModelDrag } from "@/lib/model-dnd";
import { CollectionReadme } from "@/components/collection-readme";
import { useCollectionReadmeRenderer } from "@/components/use-collection-readme-renderer";
import { MultipartModelTagsDialog } from "@/components/multipart-model-tags-dialog";
import { MultipartModelCard } from "@/components/multipart-model-card";
import { useCollectionsPaneOpen, useHasCollectionsPane } from "@/lib/collections-pane-context";
import { subscribeUploadRequests } from "@/lib/upload-requests";
import { LibraryCollectionsPane, LibraryBreadcrumbs } from "@/components/collections-pane";
import { FilterSidebar } from "@/components/filter-sidebar";
import {
  libraryDestinationHref,
  readLibraryLocation,
  type LibraryViewMode,
} from "@/features/library/url";
import { MobileFilterDrawer } from "@/components/mobile-filter-drawer";
import { StructuredFilters } from "@/components/structured-filters";
import type { LibraryFilterField } from "@/components/library-filter-picker";
import type { UploadMode } from "@/components/upload-modal";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { FormField } from "@/components/ui/form-field";
import { Modal } from "@/components/ui/modal";
import { DropdownMenu } from "@/components/ui/dropdown-menu";
import { SavedViewSelector } from "@/components/saved-view-selector";
import { Localized } from "@/components/ui/localized";
import { translateUiText } from "@/lib/locale";
import { useI18n } from "@/lib/i18n";
import { useMobileFilterDrawer } from "@/lib/mobile-filter-context";
import {
  X,
  Folder,
  Plus,
  Star,
  History,
  Check,
  ChevronDown,
  MoreHorizontal,
  ListChecks,
  ScanSearch,
  FolderTree,
  FolderPlus,
  Boxes,
  Upload,
} from "lucide-react";
import {
  listCollectionChildren,
  searchCollections,
  batchDeleteModels,
  restoreModel,
} from "@/lib/api";
import {
  isSourceFile,
  isGcodeFile,
  isArchiveFile,
  walkEntries,
  entriesFromDataTransfer,
  BulkItem,
} from "@/lib/bulk-upload";
import {
  useCollectionReadme,
  useCollectionChildren,
  useCollectionSearch,
  useModelFacets,
  usePrinters,
  useTags,
  type ModelListFilters,
} from "@/lib/queries";
import { queryKeys, refreshVaultAfterIngest } from "@/lib/query-client";
import { toast } from "@/lib/toast";
import { useRequireAuth } from "@/lib/use-require-auth";
import { useAuth } from "@/lib/auth-context";
import { Link } from "@/lib/link";
import { rememberLastCollection, readLastView, rememberLastView } from "@/lib/last-collection";
import {
  LibraryCardFill,
  LibraryCardDetailsPlaceholder,
  LibraryPreviewPlaceholder,
} from "@/components/library-card-placeholder";
import { useStartupThumbnails } from "@/lib/use-startup-thumbnails";
import { useThumbnailArrivals } from "@/lib/use-thumbnail-arrivals";
import { cn } from "@/lib/utils";

const loadUploadModal = () =>
  import("@/components/upload-modal").then((module) => ({ default: module.UploadModal }));
const loadModelTagsDialog = () =>
  import("@/components/model-tags-dialog").then((module) => ({ default: module.ModelTagsDialog }));
const loadNewMultipartModelModal = () =>
  import("@/components/new-multipart-model-modal").then((module) => ({
    default: module.NewMultipartModelModal,
  }));
const UploadModal = lazyImport(loadUploadModal);
const ModelTagsDialog = lazyImport(loadModelTagsDialog);
const NewMultipartModelModal = lazyImport(loadNewMultipartModelModal);
const DocumentBrowser = lazyImport(() =>
  import("@/components/document-browser").then((module) => ({ default: module.DocumentBrowser })),
);
const BatchToolbar = lazyImport(() =>
  import("@/components/batch-toolbar").then((module) => ({ default: module.BatchToolbar })),
);

type SortKey = ModelSort;
type LibraryItem =
  | { kind: "model"; value: ModelListItem }
  | { kind: "multipart"; value: MultipartModelListItem };

// Layout-free hosts use a bounded fallback; browsers measure their first batch.
// Later pages keep the measured size and use the server cursor.
const UNMEASURED_PAGE_SIZE = 24;
const NO_ARRIVALS: ReadonlySet<string> = new Set();
const SORT_OPTIONS: { value: SortKey; label: string }[] = [
  {
    value: "relevance",
    get label() {
      return uiText("aiSearch.relevance");
    },
  },
  {
    value: "date-desc",
    get label() {
      return uiText("Newest");
    },
  },
  {
    value: "date-asc",
    get label() {
      return uiText("Oldest");
    },
  },
  {
    value: "name-asc",
    get label() {
      return uiText("Name A–Z");
    },
  },
  {
    value: "name-desc",
    get label() {
      return uiText("Name Z–A");
    },
  },
  {
    value: "success-desc",
    get label() {
      return uiText("Best success rate");
    },
  },
  {
    value: "printed-desc",
    get label() {
      return uiText("Recently printed");
    },
  },
  {
    value: "duration-asc",
    get label() {
      return uiText("Shortest print");
    },
  },
  {
    value: "filament-asc",
    get label() {
      return uiText("Least filament");
    },
  },
  {
    value: "cost-asc",
    get label() {
      return uiText("Lowest cost");
    },
  },
];

const COMPACT_COLLECTION_HEIGHT = 84;
const COLLECTION_NAME_LIMIT = 100;

/**
 * One row of collections above the models, expandable per folder. It owns its
 * column count, so a width change re-renders this strip only.
 */
function CollectionStrip({
  total,
  overview,
  hasCreateCard = false,
  resetKey,
  children,
}: {
  total: number;
  overview: boolean;
  hasCreateCard?: boolean;
  resetKey: string;
  children: (
    singleRow: boolean,
    onColumns: (columns: number, rowWidth: number) => void,
    visibleCount: number,
  ) => ReactNode;
}) {
  useUiLocale();
  const [expandedFor, setExpandedFor] = useState<string | null>(null);
  const [{ columns: rowSize, width: rowWidth }, setRowFit] = useState({ columns: 4, width: 0 });
  const updateRowFit = useCallback((columns: number, width: number) => {
    setRowFit((previous) =>
      previous.columns === columns && previous.width === width ? previous : { columns, width },
    );
  }, []);
  const expanded = expandedFor === resetKey;
  // How many collections the resting row leaves out, so "N more" fades in or
  // out together with the pane motion rather than after it.
  const visibleInRow = hasCreateCard ? Math.max(1, rowSize - 1) : rowSize;
  const hidden = Math.max(0, total - visibleInRow);
  return (
    <>
      {!overview && (
        <div
          className="mb-2.5 flex min-h-5 items-center justify-between gap-3"
          style={rowWidth > 0 ? { maxWidth: rowWidth } : undefined}
        >
          <h2 className="text-xs font-medium text-foreground">
            {uiText("Collections")} <span className="font-mono font-normal">{total}</span>
          </h2>
          {/* Always mounted: when every collection fits (e.g. the pane closed)
              it fades out in place instead of popping in and out at rest. */}
          <Button
            type="button"
            variant="ghost"
            size="xs"
            aria-expanded={expanded}
            aria-hidden={!expanded && hidden === 0 ? true : undefined}
            tabIndex={!expanded && hidden === 0 ? -1 : undefined}
            onClick={() => setExpandedFor(expanded ? null : resetKey)}
            className={`h-5 transition-opacity duration-press ease-out ${!expanded && hidden === 0 ? "pointer-events-none opacity-0" : "opacity-100"}`}
          >
            {expanded
              ? uiText("Show fewer")
              : uiText("library.collectionsHidden", { count: Math.max(hidden, 1) })}
            <ChevronDown aria-hidden className={`h-3.5 w-3.5 ${expanded ? "rotate-180" : ""}`} />
          </Button>
        </div>
      )}
      {children(
        !overview && !expanded && (!hasCreateCard || rowSize > 1),
        updateRowFit,
        overview || expanded ? total : visibleInRow,
      )}
    </>
  );
}

const LIBRARY_VIEW_KEY = "ps-vault-library-view";
const NO_LIST_SCROLL = { current: null };

function readVaultPreference(key: string): string | null {
  if (!("window" in globalThis)) return null;
  return localStorage.getItem(key);
}

function canWriteCollection(collection: CollectionRead | null | undefined): boolean {
  return collection?.effective_role === "edit" || collection?.effective_role === "admin";
}

type LibraryRefreshState = { entry: LibraryEntry } & (
  | { status: "pending" | "stale" }
  | { status: "failed" | "denied"; error: string }
);

export interface BrowserInitialData {
  models: ModelListItem[];
  tags: TagRead[];
  printers: PrinterRead[];
}

const INSPECTOR_WIDTH_KEY = "ps-library-inspector-width";

function storedInspectorWidth() {
  try {
    const width = Number(localStorage.getItem(INSPECTOR_WIDTH_KEY));
    return Number.isSafeInteger(width) &&
      width >= LIBRARY_INSPECTOR_MIN_WIDTH &&
      width <= LIBRARY_INSPECTOR_MAX_WIDTH
      ? width
      : null;
  } catch {
    return null;
  }
}

type InspectorFocusTarget = "details" | "collections";
type InspectorIntent = { id: number; open: boolean; focusTarget: InspectorFocusTarget };
const libraryInspectorIntents = new Map<string, InspectorIntent>();

export function ModelBrowser({ initial }: { initial?: BrowserInitialData }) {
  useUiLocale();
  const { locale, t } = useI18n();
  const ui = useCallback((value: string) => translateUiText(locale, value), [locale]);
  const router = useRouter();
  const hasCollectionsPane = useHasCollectionsPane();
  const { savedViewsHost, setSavedViewsAvailable } = useNavigationShell();
  const searchParams = useSearchParams();
  const auth = useRequireAuth();
  const { user, refresh: refreshAuth } = useAuth();
  useLayoutEffect(() => {
    if (!savedViewsHost || !auth.isAuthenticated) return;
    setSavedViewsAvailable(true);
    return () => setSavedViewsAvailable(false);
  }, [savedViewsHost, auth.isAuthenticated, setSavedViewsAvailable]);
  const {
    createCollection,
    moveCollection,
    renameCollection,
    deleteCollection,
    replaceCollectionTags,
    regenerateThumbnails,
  } = useTaxonomyCommands();
  const startup = useLibraryStartup();
  const settleStartup = startup.settle;
  const requestStartup = startup.request;
  const filtersEnabled = startup.canLoad("filters");
  const savedViewsEnabled = startup.canLoad("saved-views");
  // Shared taxonomy facets from the TanStack Query cache: one cache entry shared
  // with the detail/upload views, revalidated on focus, and refetched after any
  // collection/tag mutation (the api layer invalidates the query cache).
  // Collections are read a level, a lookup or a search at a time (#295); tags
  // are one small list shared with the detail/upload views.
  const tagsQuery = useTags({ enabled: filtersEnabled });
  const tags = tagsQuery.data ?? [];
  // Printers (superuser-only filter) share the same cache as the printers page
  // and send-to dialog; gated so non-admins don't fetch a list they can't use.
  const printers =
    usePrinters({ enabled: filtersEnabled && !!user?.is_superuser }).data ??
    initial?.printers ??
    [];
  const filterQuery = searchParams.toString();
  function setSelectedTags(value: string[]) {
    const params = new URLSearchParams(searchParams.toString());
    const tags = value;
    params.delete("tag");
    tags.forEach((tag) => params.append("tag", tag));
    router.replace(params.size ? `/?${params}` : "/");
  }
  function setSelectedPrinterId(value: number | null) {
    const params = new URLSearchParams(searchParams.toString());
    params.delete("printer_presence");
    if (value !== null) params.set("printer_id", String(value));
    else params.delete("printer_id");
    router.replace(params.size ? `/?${params}` : "/");
  }
  function setSelectedPrinterPresence(value: "any" | "none" | null) {
    const params = new URLSearchParams(searchParams.toString());
    params.delete("printer_id");
    if (value !== null) params.set("printer_presence", value);
    else params.delete("printer_presence");
    router.replace(params.size ? `/?${params}` : "/");
  }
  const savedViewsOwner = useSavedViews(savedViewsEnabled);
  const savedViews = savedViewsOwner.views;
  const [savedViewSelection, setSavedViewSelection] = useState<{
    session: number;
    id: number;
  } | null>(null);
  const activeSavedViewId =
    savedViewSelection?.session === savedViewsOwner.session ? savedViewSelection.id : null;
  const setActiveSavedViewId = useCallback(
    (id: number | null) => {
      setSavedViewSelection(id === null ? null : { session: savedViewsOwner.session, id });
    },
    [savedViewsOwner.session],
  );
  const [saveViewOpen, setSaveViewOpen] = useState(false);
  const [saveViewName, setSaveViewName] = useState("");
  const [saveViewBusy, setSaveViewBusy] = useState(false);
  const [saveViewSession, setSaveViewSession] = useState(savedViewsOwner.session);
  const saveViewNameInput = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (saveViewOpen && saveViewSession === savedViewsOwner.session)
      saveViewNameInput.current?.focus();
  }, [saveViewOpen, saveViewSession, savedViewsOwner.session]);
  const saveViewRevision = useRef(0);
  function openSaveView() {
    saveViewRevision.current += 1;
    setSaveViewSession(savedViewsOwner.session);
    setSaveViewName("");
    setSaveViewBusy(false);
    setSaveViewOpen(true);
  }
  const savedViewReadState = savedViewsOwner.query.isError
    ? {
        status: "error" as const,
        retry: () => {
          void savedViewsOwner.query.refetch();
        },
      }
    : { status: savedViewsOwner.query.isPending ? ("loading" as const) : ("ready" as const) };
  // Measure the loading cards before the first browse request. Keep this size
  // with the mounted view so resizing cannot replace its accepted cursors.
  const [pageSize, setPageSize] = useState<number | null>(null);
  const [firstRowSize, setFirstRowSize] = useState(0);
  const [initialLibraryPreferences] = useState(() => ({
    view: readVaultPreference(LIBRARY_VIEW_KEY),
    sort: readVaultPreference("ps-vault-sort"),
  }));
  const [initialLibrarySection] = useState<"models" | "docs">(() =>
    readLastView() === "docs" ? "docs" : "models",
  );
  const libraryLocation = useMemo(() => {
    const params = new URLSearchParams(filterQuery);
    if (params.get("favorites") === "true") params.set("v", "models");
    return readLibraryLocation(params, initialLibraryPreferences, initialLibrarySection);
  }, [filterQuery, initialLibraryPreferences, initialLibrarySection]);
  const { view: libraryView, sort: sortKey, section: docView } = libraryLocation;
  const canViewPrinters = !!user?.is_superuser;
  const libraryFilters = useMemo(
    () =>
      readLibraryFilters(
        new URLSearchParams(libraryLocation.href.split("?")[1]),
        libraryLocation,
        canViewPrinters,
      ),
    [libraryLocation, canViewPrinters],
  );
  const { filters: currentFilters, structured, baseFilters } = libraryFilters;
  const selectedCollection = currentFilters.collection;
  const selectedCollectionId = collectionIdentity(searchParams);
  const selectedLookup = useCollectionDestination(selectedCollection, selectedCollectionId);
  const destinationSettled = !selectedLookup.isFetching && !selectedLookup.error;
  const destinationCollection = destinationSettled ? selectedLookup.data?.collection : undefined;
  const selectedTags = currentFilters.tag;
  const selectedPrinterId = currentFilters.printer_id;
  const selectedPrinterPresence = currentFilters.printer_presence;
  const favoritesOnly = currentFilters.favorites;
  const canonicalParams = new URLSearchParams(libraryFilters.params);
  if (destinationCollection && selectedCollection !== null) {
    canonicalParams.set("c", destinationCollection.path);
    // Identity has one canonical position, including logo returns that begin
    // with preferences only. One replacement owns filters and collection alike.
    canonicalParams.delete("cid");
    canonicalParams.set("cid", String(destinationCollection.id));
  }
  const canonicalLibraryHref = `/?${canonicalParams}`;
  // Dialog parameters stay in the URL but never change what the Library shows.
  const destinationHref = libraryDestinationHref(canonicalLibraryHref);
  const currentLocationHref = searchParams.size ? `/?${searchParams}` : "/";
  useEffect(() => {
    if (currentLocationHref !== canonicalLibraryHref) router.replace(canonicalLibraryHref);
  }, [currentLocationHref, canonicalLibraryHref, router]);
  useEffect(() => {
    if (
      initialLibraryPreferences.view === "organized" ||
      initialLibraryPreferences.view === "components"
    )
      localStorage.setItem(LIBRARY_VIEW_KEY, "all");
  }, [initialLibraryPreferences.view]);
  const [moreOpen, setMoreOpen] = useState(false);
  const toolbarMenuOpener = useRef<HTMLElement | null>(null);
  const [filterPickerRevision, setFilterPickerRevision] = useState(0);
  // Documents still need the collection snapshot, but have no
  // Model placeholder to measure. Keep their existing bounded browse read.
  if (pageSize === null && docView === "docs") setPageSize(UNMEASURED_PAGE_SIZE);
  const [compact] = useState(() => readVaultPreference("ps-vault-density") === "compact");
  const [recentFoldersOpen, setRecentFoldersOpen] = useState(false);
  const [multipartCreateOpen, setMultipartCreateOpen] = useState(false);
  // Models handed to the dialog when it opens from the selection bar.
  const [multipartSeed, setMultipartSeed] = useState<ModelListItem[]>([]);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [tagTarget, setTagTarget] = useState<ModelListItem | null>(null);
  const [tagDialogOpen, setTagDialogOpen] = useState(false);
  const [tagDialogSession, setTagDialogSession] = useState(0);
  // The URL owns a deep-linked upload until the dialog is dismissed. Permission
  // still has to resolve before it can become visible to a member.
  const uploadRequested = searchParams.get("upload") === "1";
  const [dropPreload, setDropPreload] = useState<{
    files: File[];
    items?: BulkItem[];
    mode: UploadMode;
  } | null>(null);

  const [dropCollection, setDropCollection] = useState<string | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const dragEnterCount = useRef(0);

  function classifyDrop(files: File[]): { files: File[]; mode: UploadMode } | null {
    const meshes = files.filter((f) => isSourceFile(f.name) && !isGcodeFile(f.name));
    const gcodes = files.filter((f) => isGcodeFile(f.name));
    const zips = files.filter((f) => isArchiveFile(f.name));
    if (meshes.length >= 2) return { mode: "bulk", files: meshes };
    if (meshes.length === 1) return { mode: "files", files: [...meshes, ...gcodes.slice(0, 1)] };
    if (gcodes.length > 0) return { mode: "files", files: [gcodes[0]] };
    if (zips.length > 0) return { mode: "zip", files: [zips[0]] };
    return null;
  }

  // Tell an OS file-upload drag (carries "Files") apart from an internal
  // move-model drag (carries MODEL_DND_MIME) so each gets the right affordance.
  function isFileDrag(e: React.DragEvent) {
    return e.dataTransfer.types.includes("Files");
  }

  function onMainDragEnter(e: React.DragEvent) {
    if (!isFileDrag(e)) return; // model drags are handled by the folder drop targets
    e.preventDefault();
    if (++dragEnterCount.current === 1) setIsDragging(true);
  }
  function onMainDragOver(e: React.DragEvent) {
    if (!isFileDrag(e)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
  }
  function onMainDragLeave(e: React.DragEvent) {
    if (!isFileDrag(e)) return;
    e.preventDefault();
    if (--dragEnterCount.current <= 0) {
      dragEnterCount.current = 0;
      setIsDragging(false);
    }
  }
  async function onMainDrop(e: React.DragEvent) {
    if (!isFileDrag(e)) return; // a model dropped on empty space is a no-op
    e.preventDefault();
    dragEnterCount.current = 0;
    setIsDragging(false);
    if (!canUploadToVault) return;
    const collPath =
      e.target instanceof Element
        ? (e.target.closest("[data-collection-path]")?.getAttribute("data-collection-path") ?? null)
        : null;
    const entries = entriesFromDataTransfer(e.dataTransfer.items);
    let bulkItems: BulkItem[] | undefined;
    let files: File[];
    if (entries.length > 0) {
      bulkItems = await walkEntries(entries);
      files = bulkItems.map((it) => it.file);
    } else {
      files = Array.from(e.dataTransfer.files);
    }
    const result = classifyDrop(files);
    if (!result) return;
    setDropPreload({
      ...result,
      mode: bulkItems?.some((item) => item.relPath !== "") ? "bulk" : result.mode,
      items: bulkItems,
    });
    setDropCollection(collPath);
    setUploadOpen(true);
  }

  const collectionFormId = useId();
  const collectionNameId = useId();
  const [collectionNameError, setCollectionNameError] = useState<string | null>(null);
  const [isCreatingCollection, setIsCreatingCollection] = useState(false);
  const [newCollectionName, setNewCollectionName] = useState("");
  const createCollectionRequested = searchParams.get("newCollection") === "1";
  const [createCollectionSeen, setCreateCollectionSeen] = useState(false);
  if (createCollectionSeen !== createCollectionRequested) {
    setCreateCollectionSeen(createCollectionRequested);
    if (createCollectionRequested) setIsCreatingCollection(true);
  }
  useEffect(() => {
    if (!createCollectionRequested) return;
    const params = new URLSearchParams(searchParams);
    params.delete("newCollection");
    router.replace(`/?${params}`);
  }, [createCollectionRequested, searchParams, router]);
  const { open: filterDrawerOpen, closeDrawer } = useMobileFilterDrawer();

  // Collection selection lives in the URL (`?c=<path>`) so it resets when the
  // user navigates away (e.g. to Settings) and clicks "Vault" again — that link
  // points at "/" with no param. Deriving it straight from the param (instead of
  // mirroring into state) means a folder switch just re-keys the model query;
  // `keepPreviousData` holds the old cards on screen until the new page lands, so
  // there's no manual clearing or loading flash.
  useEffect(() => {
    // Remember the folder we're in so the logo / post-delete nav can return
    // here instead of resetting to the root once the `?c=` param is dropped.
    rememberLastCollection(selectedCollection);
  }, [selectedCollection]);

  // Remember the active tab so the logo / Back return to it (e.g. opening a
  // document from the Documents tab and coming back).
  useEffect(() => {
    rememberLastView(docView);
  }, [docView]);

  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [selectedMultipartRows, setSelectedMultipartRows] = useState<
    Map<number, MultipartModelListItem>
  >(new Map());
  const startNavigationReads = useLibraryNavigationReads();
  function handleCollectionChange(path: string | null, id: number | null = null) {
    if (
      path === selectedCollection &&
      !selectedLookup.isError &&
      (id === null || id === selectedCollectionId)
    )
      return;
    startNavigationReads(
      path,
      {
        ...folderModelFilters(path),
        view: libraryView,
        limit: pageSize ?? UNMEASURED_PAGE_SIZE,
        sort: sortKey,
      },
      id,
    );
    // An already empty selection must not render the departing grid urgently
    // before Router can commit the destination and start its critical reads.
    setSelectedIds((current) => (current.size === 0 ? current : new Set()));
    setSelectedMultipartRows((current) => (current.size === 0 ? current : new Map()));
    router.push(collectionHref(path, id));
  }

  function collectionHref(path: string | null, id: number | null) {
    const params = new URLSearchParams(searchParams.toString());
    params.delete("cid");
    if (id !== null) params.set("cid", String(id));
    if (path) params.set("c", path);
    else params.delete("c");
    const qs = params.toString();
    return qs ? `/?${qs}` : "/";
  }

  function handleLibraryViewChange(view: LibraryViewMode) {
    localStorage.setItem(LIBRARY_VIEW_KEY, view);
    setSelectedIds(new Set());
    setSelectedMultipartRows(new Map());
    const params = new URLSearchParams(searchParams.toString());
    params.set("v", "models");
    params.set("type", view);
    router.replace(params.size ? `/?${params}` : "/");
  }

  const query = searchParams.get("q") ?? "";
  const searchQuery = currentFilters.q ?? undefined;
  // Model scope: "here" lists only this folder's own models, "all" its whole
  // subtree. Unset follows the folder: one with no models of its own shows its
  // subtree, so models are never hidden behind folder cards.
  const scopeParam = searchParams.get("scope");
  const folderScopeKey = selectedCollection ?? "";
  const [autoSubtreeFor, setAutoSubtreeFor] = useState<string | null>(null);
  const requestedIncludeSubfolders =
    scopeParam === "all" || (scopeParam !== "here" && autoSubtreeFor === folderScopeKey);
  const queryClient = useQueryClient();

  function setModelScope(next: "here" | "all") {
    const params = new URLSearchParams(searchParams.toString());
    params.set("scope", next);
    router.replace(`/?${params}`);
  }

  function setStructuredFilter(key: LibraryFilterField, values: string[]) {
    const params = new URLSearchParams(searchParams.toString());
    params.delete(key);
    if (key === "printer_id") params.delete("printer_presence");
    if (key === "printer_presence") params.delete("printer_id");
    values.forEach((value) => params.append(key, value));
    const qs = params.toString();
    router.replace(qs ? `/?${qs}` : "/");
  }

  // One builder per folder-scoped query, shared by the live queries and the
  // hover prefetch so a warmed folder fills exactly the entries it will read.
  // They default to the current folder rather than taking it as an argument:
  // the compiler would otherwise treat the call as able to mutate it and drop
  // the memoization of everything derived from it below.
  const folderModelFilters = (
    collection: string | null = selectedCollection,
  ): ModelListFilters & Pick<LibraryBrowseParams, "auto_scope"> => ({
    ...baseFilters,
    collection: collection ?? undefined,
    // A search or a filter spans the whole subtree; an unfiltered folder view
    // lists only its direct children so subfolders' models don't leak into
    // the parent (#30).
    direct: !requestedIncludeSubfolders && !searchQuery && !narrowsLibrary(baseFilters),
    auto_scope: scopeParam === null && !searchQuery && !narrowsLibrary(baseFilters),
    q: searchQuery,
  });
  // Options and counts describe the whole subtree, so a folder that holds only
  // subfolders still offers every filter that would match inside it.
  const facetQuery = useModelFacets(
    { ...folderModelFilters(), direct: false },
    { enabled: filtersEnabled },
  );

  const writeFilterUrl = useCallback(
    (filters: SavedViewRead["filters"]) => {
      const params = writeLibraryFilters(filters, canViewPrinters);
      router.replace(`/?${params}`);
    },
    [canViewPrinters, router],
  );

  const applySavedView = useCallback(
    (view: SavedViewRead) => {
      setFilterPickerRevision((revision) => revision + 1);
      setActiveSavedViewId(view.id);
      setSelectedIds(new Set());
      setSelectedMultipartRows(new Map());
      writeFilterUrl(view.filters);
    },
    [setActiveSavedViewId, setSelectedIds, setSelectedMultipartRows, writeFilterUrl],
  );

  useEffect(() => {
    function selectFromPane(event: Event) {
      if (!(event instanceof CustomEvent) || !Number.isSafeInteger(event.detail)) return;
      const view = savedViews.find((item) => item.id === event.detail);
      if (view) applySavedView(view);
    }
    window.addEventListener("printstash:select-saved-view", selectFromPane);
    return () => window.removeEventListener("printstash:select-saved-view", selectFromPane);
  }, [savedViews, applySavedView]);

  async function saveCurrentView() {
    const name = saveViewName.trim();
    if (!name || saveViewBusy) return;
    const session = getSessionVersion();
    const revision = saveViewRevision.current;
    setSaveViewBusy(true);
    try {
      await savedViewsOwner.mutation.mutateAsync({
        kind: "create",
        name,
        filters: currentFilters,
      });
      requireSessionVersion(session);
      if (revision === saveViewRevision.current) {
        setSaveViewOpen(false);
        setSaveViewName("");
      }
      toast.success(uiText("View saved"));
    } catch (error) {
      if (session === getSessionVersion()) toast.error(error);
    } finally {
      if (session === getSessionVersion()) setSaveViewBusy(false);
    }
  }

  const activeSavedView = savedViews.find((view) => view.id === activeSavedViewId) ?? null;
  const savedViewModified =
    activeSavedView !== null &&
    !sameLibraryFilters(activeSavedView.filters, currentFilters, canViewPrinters);

  async function manageSavedView(command: SavedViewCommand, success: MessageKey) {
    const session = getSessionVersion();
    try {
      await savedViewsOwner.mutation.mutateAsync(command);
      requireSessionVersion(session);
      if (command.kind === "delete") {
        setSavedViewSelection((current) => (current?.id === command.id ? null : current));
      }
      toast.success(uiText(success));
    } catch (error) {
      if (session === getSessionVersion()) toast.error(error);
      throw error;
    }
  }

  function duplicateViewName(name: string): string {
    const used = new Set(savedViews.map((view) => view.name.toLowerCase()));
    let candidate = uiText("{value1} copy", { value1: String(name) });
    let suffix = 2;
    while (used.has(candidate.toLowerCase()))
      candidate = uiText("{value1} copy {value2}", {
        value1: String(name),
        value2: String(suffix++),
      });
    return candidate;
  }
  // The paginated grid. `keepPreviousData` (in the hook) holds the current page
  // on screen while a new search/folder loads, and results are cached per filter
  // set so backspacing a query or re-entering a folder is instant.
  const browseParams = {
    ...folderModelFilters(),
    view: libraryView,
    limit: pageSize ?? UNMEASURED_PAGE_SIZE,
    sort: sortKey,
  };
  const modelQuery = useLibraryBrowse(
    browseParams,
    pageSize !== null &&
      (selectedCollectionId === null || destinationCollection?.path === selectedCollection),
    firstRowSize,
  );
  const browsePage = modelQuery.isPlaceholderData ? undefined : modelQuery.data?.pages[0];
  const effectiveDirect = browsePage?.effective_direct ?? browseParams.direct;
  const includeSubfolders = requestedIncludeSubfolders || effectiveDirect === false;
  const statusFilterActive = structured.revision_status.length > 0;
  const canReusePickerFacets = effectiveDirect === false && !statusFilterActive;
  const statusFacetQuery = useModelFacets(
    { ...folderModelFilters(), direct: effectiveDirect, revision_status: undefined },
    {
      enabled: filtersEnabled && docView === "models" && !!browsePage && !canReusePickerFacets,
    },
  );
  const recent = useRecentFolders(
    destinationSettled ? selectedLookup.data : undefined,
    recentFoldersOpen,
  );
  const recentFolders = recent.folders;
  const selectedCollectionRow =
    destinationSettled && selectedLookup.data?.collection.path === selectedCollection
      ? selectedLookup.data.collection
      : null;
  // Read alongside the folder page, before committing the destination. A
  // description inserted after its cards would move the entire browsing surface.
  const collectionReadme = useCollectionReadme(selectedCollectionRow?.id ?? null, {
    enabled: selectedCollectionRow?.has_readme ?? false,
  });
  const readmeRenderer = useCollectionReadmeRenderer(selectedCollectionRow?.has_readme ?? false);
  const folderSearch = useCollectionSearch(searchQuery ?? "", "view", {
    enabled: searchQuery !== undefined,
  });
  const folderLevel = useCollectionChildren(selectedCollectionRow?.id ?? null, {
    enabled:
      searchQuery === undefined && (selectedCollection === null || selectedCollectionRow !== null),
  });
  const folderPages = searchQuery !== undefined ? folderSearch : folderLevel;
  const foldersReady =
    (searchQuery !== undefined || selectedCollection === null || selectedCollectionRow !== null) &&
    folderPages.data !== undefined &&
    !folderPages.isPlaceholderData;
  // The authorized lookup already reserves the child-folder strip. Its items
  // can load alongside the Model page instead of delaying viewport sizing.
  // Search and readme layouts still need their actual content before measuring.
  const capacityReady =
    foldersReady ||
    (searchQuery === undefined &&
      destinationCollection !== undefined &&
      !destinationCollection.has_readme);
  const receivePageSize = useCallback(
    (capacity: number, columns: number) => {
      // Include the reserved collection strip before measuring the Model area.
      if (capacityReady) {
        setPageSize((current) => current ?? capacity);
        setFirstRowSize(columns);
      }
    },
    [capacityReady],
  );
  // Canonical replacement creates a Router key. Publish the visit only once
  // that identity is settled, before the reader can establish its position.
  const projectionReady =
    currentLocationHref === canonicalLibraryHref &&
    (selectedCollection === null ||
      (selectedCollectionRow !== null && selectedCollectionId === selectedCollectionRow.id)) &&
    (!selectedCollectionRow?.has_readme || !collectionReadme.isPending) &&
    (!selectedCollectionRow?.has_readme || !readmeRenderer.isPending) &&
    folderPages.data !== undefined &&
    !folderPages.isPlaceholderData &&
    modelQuery.data !== undefined &&
    !modelQuery.isPlaceholderData;

  const entry = useLibraryEntry(destinationHref, projectionReady);
  const [refreshState, setRefreshState] = useState<LibraryRefreshState | null>(null);
  const refreshScope = useRef({ entry, sequence: 0 });
  useLayoutEffect(() => {
    const scope = refreshScope.current;
    scope.entry = entry;
    return () => {
      scope.sequence++;
    };
  }, [entry]);
  const currentRefresh = refreshState?.entry === entry ? refreshState : null;
  const browseReady = projectionReady && currentRefresh === null;
  const orderedItems = useMemo<LibraryItem[]>(
    () =>
      modelQuery.data?.pages.flatMap((page) =>
        page.items.map((entry): LibraryItem =>
          entry.kind === "model"
            ? { kind: "model", value: entry.model }
            : { kind: "multipart", value: entry.multipart },
        ),
      ) ?? [],
    [modelQuery.data],
  );
  // A filter lists matching Models from the whole subtree, so the open folder's
  // subfolders are not part of that result: showing them beside it mixes a
  // place to browse with a list of matches.
  const foldersHidden = narrowsLibrary(baseFilters);
  // Commit one coherent browsing result. Independent requests may settle in any
  // order; neither placeholder models nor a cached root folder page belongs to
  // a destination whose lookup/children have not completed yet.
  const nextSnapshot = useMemo(() => {
    if (!browseReady) return null;
    return {
      entry,
      favorites: favoritesOnly,
      items: orderedItems,
      previewSource:
        modelQuery.data?.pages[0] &&
        !foldersHidden &&
        !searchQuery &&
        libraryView === "all" &&
        sortKey === "date-desc"
          ? {
              page: modelQuery.data.pages[0],
              params: {
                view: "all" as const,
                sort: "date-desc" as const,
                limit: pageSize ?? UNMEASURED_PAGE_SIZE,
                collection: selectedCollection ?? undefined,
                direct: !includeSubfolders,
              },
            }
          : undefined,
      authorizationRevision: modelQuery.data?.pages[0]?.authorization_revision,
      modelPages: modelQuery.data?.pages.length ?? 0,
      folderPages: foldersHidden ? 0 : (folderPages.data?.pages.length ?? 0),
      collections: foldersHidden
        ? []
        : (folderPages.data?.pages.flatMap((page) => page.items) ?? []),
      collection: selectedCollectionRow,
      breadcrumbs:
        selectedCollectionRow && selectedLookup.data
          ? [...selectedLookup.data.ancestors, selectedCollectionRow]
          : [],
      hasMore: modelQuery.hasNextPage ?? false,
      moreFolders: foldersHidden ? false : (folderPages.hasNextPage ?? false),
    };
  }, [
    browseReady,
    searchQuery,
    libraryView,
    sortKey,
    pageSize,
    selectedCollection,
    includeSubfolders,
    entry,
    favoritesOnly,
    orderedItems,
    foldersHidden,
    folderPages.data,
    folderPages.hasNextPage,
    selectedCollectionRow,
    selectedLookup.data,
    modelQuery.hasNextPage,
    modelQuery.data,
  ]);
  const [settledSnapshot, setSettledSnapshot] = useState(nextSnapshot);
  const lookupStatus = selectedLookup.error ? parseApiError(selectedLookup.error).status : null;
  const unavailableCollection =
    selectedCollection !== null &&
    selectedLookup.isError &&
    (lookupStatus === 403 ||
      lookupStatus === 404 ||
      settledSnapshot === null ||
      settledSnapshot.entry.href !== destinationHref);
  if (unavailableCollection && settledSnapshot !== null) setSettledSnapshot(null);
  if (nextSnapshot !== null && nextSnapshot !== settledSnapshot) setSettledSnapshot(nextSnapshot);
  const snapshot =
    unavailableCollection || currentRefresh?.status === "denied"
      ? null
      : (nextSnapshot ?? settledSnapshot);
  const displayedCollection = snapshot?.collection ?? null;
  // Each displayed destination owns the real cards' entrance, including cached visits.
  const displayedPath = snapshot === null ? null : (displayedCollection?.path ?? "");
  const cardIdentity = `${snapshot?.entry.href ?? ""}:${snapshot?.items.length ? "populated" : "empty"}`;
  const cardEntrance = useEntranceWindow(cardIdentity, snapshot ? true : null, FOLDER_ENTRANCE_MS);
  const onAuthorityRetired = useCallback(() => {
    const session = getSessionVersion();
    void refreshAuth().catch((error) => {
      if (session === getSessionVersion()) toast.error(error);
    });
  }, [refreshAuth]);
  const refreshForAuthority = useRef<(() => Promise<void>) | null>(null);
  const authority = useLibraryAuthority(browseReady ? (modelQuery.data?.pages[0] ?? null) : null, {
    eventsReady: startup.canLoad("activity"),
    onRefresh: () => refreshForAuthority.current?.(),
    onAuthorityRetired,
  });
  async function refresh() {
    const session = getSessionVersion();
    if (
      entry.session !== session ||
      authority.authorizationChanged ||
      currentRefresh?.status === "pending"
    )
      return;
    const scope = refreshScope.current;
    const sequence = ++scope.sequence;
    const isCurrent = () =>
      getSessionVersion() === session && scope.entry === entry && scope.sequence === sequence;
    // The view below explicitly reloads its taxonomy and continuation pages.
    // Mark other metadata stale without starting duplicate reads of that view.
    for (const queryKey of [queryKeys.models, queryKeys.collections])
      void queryClient.invalidateQueries({ queryKey, refetchType: "none" });
    void queryClient.invalidateQueries({ queryKey: queryKeys.vaultStats });
    setRefreshState({ entry, status: "pending" });
    let lookupPending = selectedCollection !== null;
    try {
      const previousFolders =
        searchQuery === undefined
          ? queryKeys.collectionChildren(selectedCollectionRow?.id ?? null)
          : queryKeys.collectionSearch(searchQuery, "view");
      await Promise.all([
        queryClient.cancelQueries({ queryKey: libraryBrowseKeys.all }),
        queryClient.cancelQueries({ queryKey: previousFolders, exact: true }),
        queryClient.cancelQueries({
          queryKey:
            selectedCollectionId === null
              ? queryKeys.collectionLookup(selectedCollection)
              : queryKeys.collectionLookupById(selectedCollectionId),
          exact: true,
        }),
      ]);
      if (!isCurrent()) return;
      await queryClient.invalidateQueries({ queryKey: libraryBrowseKeys.all, refetchType: "none" });
      if (!isCurrent()) return;
      // Resolve the saved identity before refreshing its current children.
      let parentId: number | null = null;
      if (selectedCollection !== null) {
        const lookup = await selectedLookup.refetch({ throwOnError: true });
        if (!isCurrent()) return;
        if (!lookup.data) throw new Error("Collection refresh returned no lookup");
        parentId = lookup.data.collection.id;
      }
      lookupPending = false;
      const folderKey =
        searchQuery === undefined
          ? queryKeys.collectionChildren(parentId)
          : queryKeys.collectionSearch(searchQuery, "view");
      await queryClient.cancelQueries({ queryKey: folderKey, exact: true });
      if (!isCurrent()) return;
      await Promise.all([
        queryClient.resetQueries(
          { queryKey: libraryBrowseKeys.pages(browseParams), exact: true },
          { throwOnError: true },
        ),
        (async () => {
          await queryClient.resetQueries(
            { queryKey: folderKey, exact: true },
            { throwOnError: true },
          );
          if (!isCurrent()) return;
          // A new parent key may not have an observer yet. Fetch its same Query
          // entry explicitly; Infinity reuses the page resetQueries just read.
          await queryClient.fetchInfiniteQuery<
            CollectionPage,
            Error,
            CollectionPage,
            typeof folderKey,
            string | null
          >({
            queryKey: folderKey,
            queryFn: ({ pageParam, signal }) =>
              searchQuery === undefined
                ? listCollectionChildren(parentId, pageParam, undefined, { signal })
                : searchCollections(searchQuery, "view", pageParam, undefined, { signal }),
            initialPageParam: null,
            getNextPageParam: (page: CollectionPage) => page.next_cursor,
            staleTime: Infinity,
          });
        })(),
        queryClient.invalidateQueries({ queryKey: queryKeys.multipartModels }),
        queryClient.invalidateQueries({ queryKey: queryKeys.tags }),
      ]);
      if (isCurrent()) setRefreshState(null);
    } catch (error) {
      if (!isCurrent()) return;
      const interrupted = isCancelledError(error);
      const failure = parseApiError(error);
      if (interrupted || (failure.status === 409 && failure.code === "browse_refresh_required")) {
        // Ingest completion replaces ongoing reads; further catalog writes can
        // also reject both first-page attempts. Preserve the displayed snapshot
        // and reading anchor, then settle replaced reads through the same timer.
        setRefreshState({ entry, status: "stale" });
        return;
      }
      const denied = lookupPending && failure.status === 404;
      if (denied) setSettledSnapshot(null);
      setRefreshState({ entry, status: denied ? "denied" : "failed", error: userMessage(error) });
    }
  }

  const refreshRequired =
    authority.refreshRequired || modelQuery.refreshRequired || currentRefresh !== null;
  const libraryItems = snapshot?.items ?? [];
  const visibleModels = libraryItems.flatMap((item) => (item.kind === "model" ? [item.value] : []));
  const visibleMultipartModels = libraryItems.flatMap((item) =>
    item.kind === "multipart" ? [item.value] : [],
  );
  const visibleCollections = snapshot?.collections ?? [];
  const [collectionCardTarget, setCollectionCardTarget] = useState<{
    entry: LibraryEntry;
    id: number;
    action: CollectionCardAction;
  } | null>(null);
  const canManageCollectionCards = auth.isAuthenticated && !authority.authorizationChanged;
  function canManageCard(collection: CollectionRead, action: CollectionCardAction) {
    return (
      canManageCollectionCards &&
      (!!user?.is_superuser ||
        collection.effective_role === "admin" ||
        (action === "cover" && collection.effective_role === "edit"))
    );
  }
  const thumbnailRequests = useRef(new Map<number, number>());
  async function regenerateCardThumbnails(collection: CollectionRead) {
    const session = getSessionVersion();
    if (
      !canManageCard(collection, "cover") ||
      thumbnailRequests.current.get(collection.id) === session
    )
      return;
    thumbnailRequests.current.set(collection.id, session);
    try {
      const { queued } = await regenerateThumbnails(collection.id);
      if (session === getSessionVersion()) {
        if (queued > 0) toast.success(uiText("collection.thumbnailsQueued", { count: queued }));
        else toast.info(uiText("collection.noThumbnailsQueued"));
      }
    } catch (error) {
      if (session === getSessionVersion()) toast.error(error);
    } finally {
      if (thumbnailRequests.current.get(collection.id) === session)
        thumbnailRequests.current.delete(collection.id);
    }
  }
  const managedCollection =
    collectionCardTarget?.entry === entry
      ? visibleCollections.find(
          (collection) =>
            collection.id === collectionCardTarget.id &&
            canManageCard(collection, collectionCardTarget.action),
        )
      : undefined;
  const breadcrumbs = snapshot?.breadcrumbs ?? [];
  const selectedName = snapshot?.collection?.name ?? null;
  const canRenameCollection =
    auth.isAuthenticated &&
    displayedCollection !== null &&
    displayedCollection.path === selectedCollection &&
    (!!user?.is_superuser || displayedCollection.effective_role === "admin");
  const [collectionRename, setCollectionRename] = useState<{
    entry: LibraryEntry;
    collection: CollectionNodeRead;
  } | null>(null);
  const [collectionRenameName, setCollectionRenameName] = useState("");
  const [collectionRenameBusy, setCollectionRenameBusy] = useState(false);
  const renameLifetime = useRef(0);
  useLayoutEffect(
    () => () => {
      renameLifetime.current += 1;
    },
    [entry, canRenameCollection],
  );
  if (
    collectionRename !== null &&
    (collectionRename.entry !== entry ||
      !canRenameCollection ||
      entry.session !== getSessionVersion())
  ) {
    setCollectionRename(null);
    setCollectionRenameName("");
    setCollectionRenameBusy(false);
  }

  const openCollectionRename = useCallback(() => {
    if (!canRenameCollection || !displayedCollection || collectionRenameBusy || collectionRename) {
      return;
    }
    setCollectionRename({ entry, collection: displayedCollection });
    setCollectionRenameName(displayedCollection.name);
    setCollectionRenameBusy(false);
  }, [
    canRenameCollection,
    displayedCollection,
    collectionRenameBusy,
    collectionRename,
    entry,
    setCollectionRename,
    setCollectionRenameName,
  ]);
  useEffect(() => {
    function consumePendingRename() {
      if (!canRenameCollection || !displayedCollection || collectionRenameBusy || collectionRename)
        return;
      if (
        pendingCollectionRename()?.path === displayedCollection.path &&
        consumeCollectionRename(displayedCollection.path)
      )
        openCollectionRename();
    }
    function renameRequested(event: Event) {
      if (!(event instanceof CustomEvent) || event.detail !== displayedCollection?.path) return;
      if (pendingCollectionRename()) consumePendingRename();
      else openCollectionRename();
    }
    consumePendingRename();
    window.addEventListener("printstash:rename-collection", renameRequested);
    return () => window.removeEventListener("printstash:rename-collection", renameRequested);
  }, [
    canRenameCollection,
    displayedCollection,
    collectionRenameBusy,
    collectionRename,
    openCollectionRename,
  ]);

  async function saveCollectionRename() {
    const target = collectionRename;
    const name = collectionRenameName.trim();
    if (!target || !name || collectionRenameBusy || !canRenameCollection) return;
    const session = getSessionVersion();
    const lifetime = renameLifetime.current;
    const isCurrent = () => session === getSessionVersion() && renameLifetime.current === lifetime;
    setCollectionRenameBusy(true);
    try {
      const updated = await renameCollection(target.collection.id, name);
      requireSessionVersion(session);
      if (!isCurrent()) return;
      setCollectionRename(null);
      toast.success(uiText("collection.renameSuccess", { count: 1 }));
      if (updated.path === selectedCollection) void refresh();
      else {
        const params = new URLSearchParams(searchParams.toString());
        params.set("c", updated.path);
        params.set("cid", String(updated.id));
        router.replace(`/?${params}`);
      }
    } catch (error) {
      if (isCurrent()) toast.error(error);
    } finally {
      if (isCurrent()) setCollectionRenameBusy(false);
    }
  }
  const modelReadError = modelQuery.refreshRequired ? null : modelQuery.error?.message;
  const error =
    (currentRefresh?.status === "failed" || currentRefresh?.status === "denied"
      ? currentRefresh.error
      : null) ??
    modelReadError ??
    (selectedCollection !== null ? selectedLookup.error?.message : null) ??
    folderPages.error?.message ??
    null;
  const loading = snapshot === null && !browseReady && error === null;
  const refreshing =
    currentRefresh?.status === "pending" ||
    (!loading &&
      !error &&
      !modelQuery.refreshRequired &&
      (!browseReady || (modelQuery.isFetching && !modelQuery.isFetchingNextPage)));
  const loadingMore = modelQuery.isFetchingNextPage || !browseReady;
  const hasMore = snapshot?.hasMore ?? false;
  const modelsPending =
    !loading &&
    error === null &&
    !browseReady &&
    !favoritesOnly &&
    (snapshot?.items.length ?? 0) === 0;

  const selectionEntry = useRef<LibraryEntry | null>(entry);
  useLayoutEffect(() => {
    selectionEntry.current = entry;
    return () => {
      selectionEntry.current = null;
    };
  }, [entry]);
  const multipartSelectionIdentity = JSON.stringify([
    libraryView,
    selectedCollection,
    selectedCollectionId,
    docView,
  ]);
  const [multipartSelectionScope, setMultipartSelectionScope] = useState(
    multipartSelectionIdentity,
  );
  if (multipartSelectionScope !== multipartSelectionIdentity) {
    setMultipartSelectionScope(multipartSelectionIdentity);
    if (selectedMultipartRows.size > 0) setSelectedMultipartRows(new Map());
  }
  // Selected folders are kept as the rows they were picked from: they may leave
  // the view (another folder opened) and still need their names and paths.
  const [selectedCollectionRows, setSelectedCollectionRows] = useState<
    Map<number, CollectionNodeRead>
  >(new Map());
  const [keyboardSelection, setKeyboardSelection] = useState(false);
  const selectMode =
    keyboardSelection ||
    selectedIds.size > 0 ||
    selectedCollectionRows.size > 0 ||
    selectedMultipartRows.size > 0;

  // What turned up since this view was last read eases in once; everything
  // already on screen keeps its place. A different folder, filter or sort
  // starts a new reading: its results settle in together instead.
  const shownKeys = entryKeys(visibleCollections, libraryItems);
  const displayedHref = snapshot?.entry.href ?? null;
  const [arrival, setArrival] = useState<{
    href: string | null;
    keys: ReadonlySet<string>;
    arriving: ReadonlySet<string> | null;
  }>({ href: displayedHref, keys: new Set(shownKeys), arriving: null });
  if (displayedHref !== null) {
    if (arrival.href !== displayedHref) {
      setArrival({ href: displayedHref, keys: new Set(shownKeys), arriving: null });
    } else if (
      shownKeys.length !== arrival.keys.size ||
      shownKeys.some((key) => !arrival.keys.has(key))
    ) {
      const arriving = arrivingKeys(arrival.keys, shownKeys);
      setArrival({
        href: displayedHref,
        keys: new Set(shownKeys),
        arriving: arriving.size > 0 ? arriving : null,
      });
    }
  }
  // Only for the arrival itself: a card that later remounts (scrolled back
  // into view, or a reflowed row) keeps its place.
  const arriving = useEntranceWindow(arrival, arrival.arriving, DURATION.fast) ?? NO_ARRIVALS;

  const modelsContent = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (browseReady) settleStartup("cards", "ready");
    else if (error !== null) settleStartup("cards", "failed");
  }, [browseReady, error, settleStartup]);
  const startupContent = useRef<HTMLElement>(null);
  const readingRenderers = useRef(new Map<string, LibraryReadingRenderer>());
  const [findOpen, setFindOpen] = useState(false);
  const findHighlight = useRef<HTMLElement | null>(null);
  const findFrame = useRef(0);
  const findOpener = useRef<HTMLElement | null>(null);
  // Bring one find match into view and ring it. The list is virtual, so a match
  // far down is first scrolled into existence and then located.
  const revealFound = useCallback((id: number | null) => {
    cancelAnimationFrame(findFrame.current);
    findHighlight.current?.classList.remove("library-find-current");
    findHighlight.current = null;
    if (id === null) return;
    const key = `/models/${id}`;
    let attempts = 0;
    function reveal() {
      const anchor = startupContent.current?.querySelector<HTMLElement>(
        libraryReadingSelector(key),
      );
      const card = anchor?.closest<HTMLElement>("[data-library-key]") ?? anchor;
      if (card) {
        card.scrollIntoView({ block: "center", inline: "nearest", behavior: "instant" });
        // The ring belongs on the rounded card, not on the grid cell around it.
        const ringed = card.matches(".library-model-card")
          ? card
          : (card.querySelector<HTMLElement>(".library-model-card") ?? card);
        ringed.classList.add("library-find-current");
        findHighlight.current = ringed;
        return;
      }
      attempts += 1;
      if (attempts > 60) return;
      readingRenderers.current.get("models")?.reveal(key);
      findFrame.current = requestAnimationFrame(reveal);
    }
    reveal();
  }, []);
  useEffect(() => {
    function keydown(event: KeyboardEvent) {
      if (
        event.defaultPrevented ||
        event.isComposing ||
        event.key.toLowerCase() !== "f" ||
        !(event.metaKey || event.ctrlKey) ||
        event.shiftKey ||
        event.altKey ||
        modalOverlayOpen()
      )
        return;
      event.preventDefault();
      setFindOpen((open) => {
        // Remember where focus was so closing the bar gives it back there,
        // rather than ringing the whole collection.
        if (!open)
          findOpener.current =
            document.activeElement instanceof HTMLElement &&
            document.activeElement !== document.body
              ? document.activeElement
              : null;
        return true;
      });
    }
    window.addEventListener("keydown", keydown);
    return () => window.removeEventListener("keydown", keydown);
  }, []);
  const reading = useLibraryReadingPosition(
    snapshot?.entry,
    "grid",
    startupContent,
    NO_LIST_SCROLL,
    snapshot !== null && docView === "models" && !authority.authorizationChanged,
    {
      ready: browseReady,
      models: {
        count: snapshot?.modelPages ?? 0,
        more: modelQuery.hasNextPage ?? false,
        pending: modelQuery.isFetching,
        failed:
          modelQuery.isError &&
          !modelQuery.refreshRequired &&
          currentRefresh?.status !== "pending" &&
          currentRefresh?.status !== "stale",
        next: () => void modelQuery.loadMore(),
      },
      folders: {
        count: snapshot?.folderPages ?? 0,
        more: foldersHidden ? false : (folderPages.hasNextPage ?? false),
        pending: folderPages.isFetching,
        failed: folderPages.isError,
        next: () => void folderPages.fetchNextPage({ cancelRefetch: false }),
      },
    },
    readingRenderers,
  );
  useStartupThumbnails(startupContent, browseReady, libraryItems.length > 0);
  const thumbnails = useLibraryThumbnails(
    visibleModels,
    browseReady ? (modelQuery.data?.pages[0] ?? null) : null,
    onAuthorityRetired,
  );
  useThumbnailArrivals(visibleModels, thumbnails.refresh);

  function loadMore() {
    if (hasMore && !loadingMore && !refreshRequired && !authority.authorizationChanged)
      void modelQuery.loadMore();
  }
  // Reconcile changed pages in the background, including stale continuations.
  // Reading-position recovery reloads only the previously visited pages and
  // restores the visible anchor. Selection postpones reordering until it ends.
  const settlesByItself =
    !selectMode &&
    !authority.authorizationChanged &&
    ((currentRefresh === null && (authority.refreshRequired || modelQuery.refreshRequired)) ||
      currentRefresh?.status === "stale");
  const lastSettled = useRef(0);
  // The timer must survive re-renders, so it reads the latest refresh through a
  // ref instead of depending on an object that is new on every render.
  const settleNow = useRef<() => void>(() => undefined);
  useLayoutEffect(() => {
    settleNow.current = () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.outliner });
      void (snapshot?.entry === entry ? reading.refresh(refresh) : refresh());
    };
  });
  useEffect(() => {
    if (!settlesByItself) return;
    const wait = Math.max(800, lastSettled.current + 4000 - Date.now());
    const timer = setTimeout(() => {
      lastSettled.current = Date.now();
      settleNow.current();
    }, wait);
    return () => clearTimeout(timer);
  }, [settlesByItself]);

  useLayoutEffect(() => {
    refreshForAuthority.current = () => reading.refresh(refresh);
    return () => {
      refreshForAuthority.current = null;
    };
  });
  const modelMoves = useModelMoves(entry, async () => {
    await Promise.all([refresh(), queryClient.invalidateQueries({ queryKey: queryKeys.outliner })]);
  });

  // Multi-select for batch actions. The selected set is view-independent so it
  // survives load-more and search; backend per-model RBAC makes cross-collection
  // selections safe. We clear it when navigating folders (see below) so a hidden
  // off-screen selection doesn't linger.

  const [batchBusy, setBatchBusy] = useState(false);
  const [batchRecovery, setBatchRecovery] = useState<{
    entry: typeof entry;
    receipt: LibraryBatchOutcome & { undo: (() => Promise<LibraryBatchOutcome>) | null };
    intent: string[];
    models: ModelListItem[];
  } | null>(null);
  const currentBatchRecovery =
    batchRecovery?.entry === entry && entry.session === getSessionVersion() ? batchRecovery : null;
  function reviewInterruptedBatch(
    receipt: LibraryBatchOutcome & { undo: (() => Promise<LibraryBatchOutcome>) | null },
    intent: string[],
    models: ModelListItem[],
  ) {
    setBatchRecovery({ entry, receipt, intent, models });
  }
  const [selectingAll, setSelectingAll] = useState(false);
  const lastSelectedModelId = useRef<number | null>(null);
  const selectedModelSnapshot = useRef<Map<number, ModelListItem>>(new Map());
  const sortedModels = visibleModels;
  const [multipartTagTarget, setMultipartTagTarget] = useState<{
    item: MultipartModelListItem;
    intent: number;
  } | null>(null);
  const openMultipartTagEditor = useCallback(
    (item: MultipartModelListItem) => {
      requestStartup("filters");
      setMultipartTagTarget((previous) => ({ item, intent: (previous?.intent ?? 0) + 1 }));
    },
    [requestStartup],
  );
  const openTagEditor = useCallback(
    (model: ModelListItem) => {
      setTagTarget(model);
      setTagDialogSession((session) => session + 1);
      setTagDialogOpen(true);
    },
    [setTagTarget, setTagDialogOpen],
  );

  const selectionOrder = useRef(sortedModels);
  useLayoutEffect(() => {
    selectionOrder.current = sortedModels;
  }, [sortedModels]);
  const toggleSelect = useCallback(
    (id: number, range = false) => {
      // Capture this gesture once; React may replay the state updater.
      const sortedModels = Array.from(
        new Map(selectionOrder.current.map((model) => [model.id, model])).values(),
      );
      const clicked = sortedModels.find((model) => model.id === id);
      const selection = clicked
        ? modelSelection(clicked, selectedIds, selectedCollectionRows.values())
        : ({ kind: "none" } as const);
      if (selection.kind === "implied") {
        lastSelectedModelId.current = id;
        const remaining = new Map(selectedCollectionRows);
        for (const collection of selection.collections) remaining.delete(collection.id);
        setSelectedCollectionRows(remaining);
        const explicit = new Set(selectedIds);
        explicit.delete(id);
        for (const model of sortedModels) {
          if (
            model.id !== id &&
            modelSelection(model, selectedIds, selection.collections).kind === "implied" &&
            modelSelection(model, selectedIds, remaining.values()).kind !== "implied"
          ) {
            explicit.add(model.id);
            selectedModelSnapshot.current.set(model.id, model);
          }
        }
        setSelectedIds(explicit);
        return;
      }
      const anchor = lastSelectedModelId.current;
      const selectingRange = range && anchor !== null;
      const from = selectingRange ? sortedModels.findIndex((model) => model.id === anchor) : -1;
      const to = sortedModels.findIndex((model) => model.id === id);
      const rangeModels =
        from >= 0 && to >= 0 ? sortedModels.slice(Math.min(from, to), Math.max(from, to) + 1) : [];
      lastSelectedModelId.current = id;
      const explicitRange = rangeModels.filter(
        (model) =>
          modelSelection(model, selectedIds, selectedCollectionRows.values()).kind !== "implied",
      );
      for (const model of explicitRange) selectedModelSnapshot.current.set(model.id, model);
      if (clicked) selectedModelSnapshot.current.set(id, clicked);
      setSelectedIds((prev) => {
        const next = new Set(prev);
        if (selectingRange && rangeModels.length > 0)
          explicitRange.forEach((model) => next.add(model.id));
        else if (next.has(id)) next.delete(id);
        else next.add(id);
        return next;
      });
    },
    [selectedIds, selectedCollectionRows],
  );

  const lastSelectedMultipartId = useRef<number | null>(null);
  function toggleMultipartSelect(id: number, range = false) {
    const anchor = lastSelectedMultipartId.current;
    const from =
      anchor === null ? -1 : visibleMultipartModels.findIndex((item) => item.id === anchor);
    const to = visibleMultipartModels.findIndex((item) => item.id === id);
    const rows =
      range && from >= 0 && to >= 0
        ? visibleMultipartModels.slice(Math.min(from, to), Math.max(from, to) + 1)
        : visibleMultipartModels.filter((item) => item.id === id);
    lastSelectedMultipartId.current = id;
    setSelectedMultipartRows((current) => {
      const next = new Map(current);
      if (!range && next.has(id)) next.delete(id);
      else for (const row of rows) next.set(row.id, row);
      return next;
    });
  }

  function toggleCollectionSelect(id: number) {
    const row = visibleCollections.find((collection) => collection.id === id);
    if (row && !selectedCollectionRows.has(id)) {
      const explicit = new Set(selectedIds);
      for (const modelId of selectedIds) {
        const model = selectedModelSnapshot.current.get(modelId);
        if (model && modelSelection(model, selectedIds, [row]).kind === "implied") {
          explicit.delete(modelId);
        }
      }
      if (explicit.size !== selectedIds.size) setSelectedIds(explicit);
    }
    setSelectedCollectionRows((prev) => {
      const next = new Map(prev);
      if (next.has(id)) next.delete(id);
      else if (row) next.set(id, row);
      return next;
    });
  }

  const clearSelection = useCallback(() => {
    setSelectedIds(new Set());
    setSelectedMultipartRows(new Map());
    setSelectedCollectionRows(new Map());
    setKeyboardSelection(false);
    lastSelectedMultipartId.current = null;
    lastSelectedModelId.current = null;
  }, [setSelectedIds, setSelectedMultipartRows, setSelectedCollectionRows, setKeyboardSelection]);

  function selectAllVisible() {
    setSelectedMultipartRows(new Map(visibleMultipartModels.map((item) => [item.id, item])));
    setSelectedIds(
      new Set(
        sortedModels
          .filter(
            (model) => modelSelection(model, selectedIds, visibleCollections).kind !== "implied",
          )
          .map((model) => model.id),
      ),
    );
    selectedModelSnapshot.current = new Map(sortedModels.map((model) => [model.id, model]));
    setSelectedCollectionRows(
      new Map(visibleCollections.map((collection) => [collection.id, collection])),
    );
  }

  async function selectAllMatching() {
    const selectionOrigin = entry;
    const isCurrent = () =>
      selectionEntry.current === selectionOrigin && selectionOrigin.session === getSessionVersion();
    setSelectingAll(true);
    try {
      const all: ModelListItem[] = [];
      const multipart: MultipartModelListItem[] = [];
      let cursor: string | undefined;
      do {
        const page = await listLibraryPage({ ...browseParams, cursor });
        if (!isCurrent()) return;
        multipart.push(
          ...page.items.flatMap((item) => (item.kind === "multipart" ? [item.multipart] : [])),
        );
        all.push(...page.items.flatMap((item) => (item.kind === "model" ? [item.model] : [])));
        cursor = page.next_cursor ?? undefined;
      } while (cursor !== undefined);
      setSelectedIds(
        new Set(
          all
            .filter(
              (model) =>
                modelSelection(model, selectedIds, selectedCollectionRows.values()).kind !==
                "implied",
            )
            .map((model) => model.id),
        ),
      );
      setSelectedMultipartRows(new Map(multipart.map((item) => [item.id, item])));
      selectedModelSnapshot.current = new Map(all.map((model) => [model.id, model]));
      toast.info(uiText("library.matchingSelected", { count: all.length + multipart.length }));
    } catch (error) {
      if (isCurrent()) toast.error(error);
    } finally {
      if (isCurrent()) setSelectingAll(false);
    }
  }

  async function batchInChunks(
    operation: (ids: number[]) => Promise<ModelBatchResult>,
  ): Promise<ModelBatchResult> {
    const result: ModelBatchResult = {
      succeeded_ids: [],
      failed: [],
      succeeded_count: 0,
      failed_count: 0,
    };
    for (let index = 0; index < selectedIdList.length; index += 500) {
      const part = await operation(selectedIdList.slice(index, index + 500));
      result.succeeded_ids.push(...part.succeeded_ids);
      result.failed.push(...part.failed);
      result.succeeded_count += part.succeeded_count;
      result.failed_count += part.failed_count;
    }
    return result;
  }

  const selectedIdList = Array.from(selectedIds);
  const selectedCollections = useMemo(
    () => [...selectedCollectionRows.values()],
    [selectedCollectionRows],
  );
  const selectionCount =
    selectedIdList.length + selectedCollectionRows.size + selectedMultipartRows.size;
  // First selection loads the tools; retain their owner afterward so clearing
  // selection can finish the existing exit and dialog lifecycle.
  const [batchToolsActivated, setBatchToolsActivated] = useState(false);
  if (selectionCount > 0 && !batchToolsActivated) setBatchToolsActivated(true);

  async function runCollectionBatch(
    success: MessageKey,
    operation: (collection: CollectionNodeRead) => Promise<CollectionRead>,
  ) {
    setBatchBusy(true);
    let succeeded = 0;
    let failed = 0;
    const failedFolders: string[] = [];
    for (const collection of selectedCollections) {
      try {
        await operation(collection);
        succeeded += 1;
      } catch {
        failed += 1;
        failedFolders.push(collection.display_path);
      }
    }
    if (succeeded) toast.success(uiText(success, { count: succeeded }));
    if (failed)
      toast.warning(
        uiText("{value1} skipped", { value1: String(failed) }),
        failedFolders.join(" · "),
      );
    refresh();
    clearSelection();
    setBatchBusy(false);
  }

  async function moveSelection(target: string, parentId: number | null) {
    setBatchBusy(true);
    let succeeded = 0;
    let failed = 0;
    let modelReceipt: LibraryEditReceipt | null = null;
    const movedCollections: CollectionNodeRead[] = [];
    const failureDetails: string[] = [];
    const session = getSessionVersion();
    try {
      const selectedModels = selectedIdList.map((id) => {
        const model = selectedModelSnapshot.current.get(id);
        if (!model) throw new Error("selected_model_snapshot_missing");
        return model;
      });
      if (selectedIdList.length) {
        modelReceipt = await moveLibraryModels(selectedModels, target);
        requireSessionVersion(session);
        const result = modelReceipt.result;
        succeeded += result.succeeded_count;
        failed += result.failed_count;
        failureDetails.push(
          ...result.failed.map((failure) =>
            uiText("Model #{value1}: {value2}", {
              value1: String(failure.model_id),
              value2: getErrorMessage(failure.reason),
            }),
          ),
        );
      }
      if (modelReceipt?.completion.status === "interrupted") {
        reviewInterruptedBatch(
          modelReceipt,
          [uiText("Move to: {value1}", { value1: target || uiText("None (root)") })],
          selectedModels,
        );
        void refresh();
        return;
      }
      for (const collection of selectedCollections) {
        requireSessionVersion(session);
        try {
          await moveCollection(collection.id, parentId);
          requireSessionVersion(session);
          succeeded += 1;
          movedCollections.push(collection);
        } catch {
          requireSessionVersion(session);
          failed += 1;
          failureDetails.push(
            uiText("Folder: {value1}", {
              value1: collection.display_path,
            }),
          );
        }
      }
      if (succeeded)
        toast.undo(uiText("Moved {value1}", { value1: String(succeeded) }), async () => {
          try {
            requireSessionVersion(session);
            const outcome = await modelReceipt?.undo();
            requireSessionVersion(session);
            if (outcome?.completion.status === "interrupted") {
              reviewInterruptedBatch(
                { ...outcome, undo: null },
                [uiText("Undo result")],
                selectedModels,
              );
              void refresh();
              return;
            }
            const result = outcome?.result;
            for (const collection of movedCollections) {
              requireSessionVersion(session);
              await moveCollection(collection.id, collection.parent_id);
            }
            requireSessionVersion(session);
            refresh();
            if (result?.failed_count)
              toast.warning(
                uiText("{value1} skipped", { value1: String(result.failed_count) }),
                result.failed
                  .map((failure) => `#${failure.model_id}: ${getErrorMessage(failure.reason)}`)
                  .join(" · "),
              );
            else toast.success(uiText("Move undone"));
          } catch (error) {
            if (session === getSessionVersion()) toast.error(error);
          }
        });
      if (failed)
        toast.warning(
          uiText("{value1} skipped", { value1: String(failed) }),
          failureDetails.join(" · "),
        );
      refresh();
      clearSelection();
    } catch (error) {
      if (session === getSessionVersion()) toast.error(error);
    } finally {
      if (session === getSessionVersion()) setBatchBusy(false);
    }
  }

  async function deleteSelection() {
    if (batchBusy) return;
    const selectionOrigin = entry;
    const session = getSessionVersion();
    const isCurrent = () =>
      selectionEntry.current === selectionOrigin && session === getSessionVersion();
    const remainingMultipart = new Map(selectedMultipartRows);
    setBatchBusy(true);
    let succeeded = 0;
    let failed = 0;
    const deletedModelIds: number[] = [];
    const failureDetails: string[] = [];
    try {
      for (const item of selectedMultipartRows.values()) {
        if (!isCurrent()) return;
        try {
          await deleteLibraryMultipart(queryClient, item.id, session);
          if (!isCurrent()) return;
          remainingMultipart.delete(item.id);
          succeeded += 1;
        } catch (error) {
          if (!isCurrent()) return;
          failed += 1;
          failureDetails.push(`${item.name}: ${userMessage(error)}`);
        }
      }
      setSelectedMultipartRows(remainingMultipart);
      if (selectedIdList.length) {
        const result = await batchInChunks(batchDeleteModels);
        if (!isCurrent()) return;
        succeeded += result.succeeded_count;
        failed += result.failed_count;
        deletedModelIds.push(...result.succeeded_ids);
        failureDetails.push(
          ...result.failed.map((failure) =>
            uiText("Model #{value1}: {value2}", {
              value1: String(failure.model_id),
              value2: getErrorMessage(failure.reason),
            }),
          ),
        );
      }
      for (const collection of selectedCollections) {
        try {
          if (!isCurrent()) return;
          await deleteCollection(collection.id, true);
          if (!isCurrent()) return;
          succeeded += 1;
        } catch {
          failed += 1;
          failureDetails.push(
            uiText("Folder: {value1}", {
              value1: collection.display_path,
            }),
          );
        }
      }
      if (deletedModelIds.length)
        toast.undo(
          uiText("models.trashed", {
            value1: String(deletedModelIds.length),
            count: Number(deletedModelIds.length),
          }),
          async () => {
            await Promise.all(deletedModelIds.map((id) => restoreModel(id)));
            refresh();
            toast.success(uiText("Models restored"));
          },
        );
      else if (succeeded) toast.success(uiText("Deleted {value1}", { value1: String(succeeded) }));
      if (failed)
        toast.warning(
          uiText("{value1} skipped", { value1: String(failed) }),
          failureDetails.join(" · "),
        );
      clearSelection();
      setSelectedMultipartRows(remainingMultipart);
    } catch (error) {
      if (isCurrent()) toast.error(error);
    } finally {
      if (isCurrent()) {
        if (succeeded > 0) void refresh();
        setBatchBusy(false);
      }
    }
  }

  async function tagSelection(add: string[], remove: string[]) {
    setBatchBusy(true);
    const session = getSessionVersion();
    try {
      const selectedModels = selectedIdList.map((id) => {
        const model = selectedModelSnapshot.current.get(id);
        if (!model) throw new Error("selected_model_snapshot_missing");
        return model;
      });
      const receipt = await tagLibraryModels(selectedModels, add, remove);
      requireSessionVersion(session);
      if (receipt.completion.status === "interrupted") {
        reviewInterruptedBatch(
          receipt,
          [
            uiText("Add tags: {value1}", { value1: add.join(", ") }),
            uiText("Remove tags: {value1}", { value1: remove.join(", ") }),
          ],
          selectedModels,
        );
        void refresh();
        return;
      }
      const result = receipt.result;
      if (result.succeeded_count)
        toast.undo(
          uiText("Tagged {value1}", { value1: String(result.succeeded_count) }),
          async () => {
            try {
              const outcome = await receipt.undo();
              requireSessionVersion(session);
              refresh();
              if (outcome.completion.status === "interrupted") {
                reviewInterruptedBatch(
                  { ...outcome, undo: null },
                  [uiText("Undo result")],
                  selectedModels,
                );
                return;
              }
              const undone = outcome.result;
              if (undone.failed_count)
                toast.warning(
                  uiText("{value1} skipped", { value1: String(undone.failed_count) }),
                  undone.failed
                    .map((failure) => `#${failure.model_id}: ${getErrorMessage(failure.reason)}`)
                    .join(" · "),
                );
              else toast.success(uiText("Tags restored"));
            } catch (error) {
              if (session === getSessionVersion()) toast.error(error);
            }
          },
        );
      if (result.failed_count)
        toast.warning(
          uiText("{value1} skipped", { value1: String(result.failed_count) }),
          result.failed
            .map((failure) => `#${failure.model_id}: ${getErrorMessage(failure.reason)}`)
            .join(" · "),
        );
      refresh();
      clearSelection();
    } catch (error) {
      if (session === getSessionVersion()) toast.error(error);
    } finally {
      if (session === getSessionVersion()) setBatchBusy(false);
    }
  }
  const hasActiveFilters =
    favoritesOnly ||
    selectedTags.length > 0 ||
    selectedPrinterId !== null ||
    selectedPrinterPresence !== null ||
    !!query.trim() ||
    Object.values(structured).some((values) => values.length > 0) ||
    searchParams.has("uploaded_after") ||
    searchParams.has("uploaded_before") ||
    historyKeys.some((key) => searchParams.has(key));
  const browseTotal =
    unavailableCollection || currentRefresh?.status === "denied" ? undefined : browsePage?.total;
  const statusFacets = canReusePickerFacets ? facetQuery : statusFacetQuery;
  const statusCounts =
    browseTotal !== undefined && !statusFacets.isPlaceholderData ? statusFacets.data : undefined;
  const allStatusCount = statusFilterActive
    ? statusCounts?.printed.reduce((total, item) => total + item.count, 0)
    : browseTotal;
  const hasMultipartResults =
    browseTotal !== undefined && orderedItems.some((item) => item.kind === "multipart");
  const folderHoldsOnlyFolders =
    !favoritesOnly &&
    docView === "models" &&
    browseReady &&
    !loading &&
    !includeSubfolders &&
    visibleCollections.length > 0 &&
    browseTotal === 0 &&
    !hasMore &&
    !hasActiveFilters;
  const subtreeHasModels = (displayedCollection?.model_count ?? 1) > 0;
  if (
    folderHoldsOnlyFolders &&
    scopeParam !== "here" &&
    subtreeHasModels &&
    autoSubtreeFor !== folderScopeKey
  ) {
    setAutoSubtreeFor(folderScopeKey);
  }
  // The large folder overview remains only for folders whose subtree holds no models yet.
  const collectionOverview = folderHoldsOnlyFolders && !subtreeHasModels;
  // Reserve only the initial destination's known children. During navigation
  // the displayed snapshot owns its geometry until the next one commits. A
  // disabled root query can still return cached data while a leaf lookup runs.
  const reservedCollections =
    !foldersHidden && !collectionOverview && loading
      ? ((foldersReady ? folderPages.data?.pages[0]?.items.length : undefined) ??
        destinationCollection?.child_count ??
        0)
      : 0;
  const showScopeToggle =
    !favoritesOnly &&
    docView === "models" &&
    !searchQuery &&
    (visibleCollections.length > 0 || reservedCollections > 0);
  const SubfolderScopeIcon = includeSubfolders ? Check : FolderTree;
  // Links belong to the displayed result even while another destination loads.
  const currentLibraryHref = snapshot?.entry.href ?? destinationHref;
  const inspectorScope = `${entry.session}:${entry.key}:${currentLibraryHref}`;
  const [inspectorState, setInspectorState] = useState<{
    scope: string;
    intent: InspectorIntent;
  } | null>(null);
  const inspectorIntent =
    inspectorState?.scope === inspectorScope
      ? inspectorState.intent
      : libraryInspectorIntents.get(inspectorScope);
  // A background read retains this entry's displayed snapshot and controls.
  // Navigation and authorization changes still retire the previous inspector.
  const inspectorAvailable =
    snapshot?.entry === entry &&
    docView === "models" &&
    !authority.authorizationChanged &&
    (visibleModels.length > 0 || inspectorIntent?.open === true);
  const inspectorOpen = inspectorAvailable && !!inspectorIntent?.open;
  const [preferredInspectorWidth, setPreferredInspectorWidth] = useState(storedInspectorWidth);
  const chooseInspectorWidth = useCallback((width: number | null) => {
    setPreferredInspectorWidth(width);
    try {
      if (width === null) localStorage.removeItem(INSPECTOR_WIDTH_KEY);
      else localStorage.setItem(INSPECTOR_WIDTH_KEY, String(width));
    } catch {
      // The width remains usable for this session.
    }
  }, []);
  const [inspectorDock, setInspectorDock] = useState<LibraryInspectorDock | null>(null);
  const receiveInspectorDock = useCallback((dock: LibraryInspectorDock | null) => {
    setInspectorDock((previous) =>
      previous?.width === dock?.width &&
      previous?.paddingOffset === dock?.paddingOffset &&
      previous?.maxWidth === dock?.maxWidth
        ? previous
        : dock,
    );
  }, []);
  const [inspectorMotion, setInspectorMotion] = useState({
    open: inspectorOpen,
    moving: inspectorOpen,
  });
  if (inspectorMotion.open !== inspectorOpen)
    setInspectorMotion({ open: inspectorOpen, moving: true });
  const settleInspectorDock = useCallback(() => {
    setInspectorMotion((motion) => (motion.moving ? { ...motion, moving: false } : motion));
  }, []);
  // Publish the fitted dock before the pane slides. Settling enables the
  // controls and viewer without applying another grid width.
  const inspectorDockStyle: CSSProperties & {
    "--library-inspector-width"?: string;
    "--library-inspector-padding-offset"?: string;
  } = inspectorDock
    ? {
        "--library-inspector-width": `${inspectorDock.width}px`,
        "--library-inspector-padding-offset": `${inspectorDock.paddingOffset}px`,
      }
    : {};
  const inspectorOpener = useRef<HTMLElement | null>(null);
  const setInspector = useCallback(
    (intent: InspectorIntent) => {
      if (intent.open !== (libraryInspectorIntents.get(inspectorScope)?.open ?? false))
        window.dispatchEvent(new InspectorWillMoveEvent(intent.open ? "opening" : "closing"));
      libraryInspectorIntents.set(inspectorScope, intent);
      while (libraryInspectorIntents.size > 20)
        libraryInspectorIntents.delete(libraryInspectorIntents.keys().next().value!);
      setInspectorState({ scope: inspectorScope, intent });
    },
    [inspectorScope],
  );
  const inspectModel = useCallback(
    (id: number, opener: HTMLElement | null, focusTarget: InspectorFocusTarget = "details") => {
      inspectorOpener.current = opener;
      setInspector({ id, open: true, focusTarget });
    },
    [setInspector],
  );
  const historyTreeModelId = useLocation({ select: (location) => location.state.treeModelId });
  const [handledTreeEntry, setHandledTreeEntry] = useState<string | null>(null);
  const [treeReveal, setTreeReveal] = useState<{
    id: number;
    entryKey: string;
    opener: HTMLElement;
  } | null>(null);
  const [treeFocus, setTreeFocus] = useState<{ id: number; entryKey: string } | null>(null);
  function openTreeModel(model: OutlinerModelRead, opener: HTMLElement) {
    opener.focus({ preventScroll: true });
    if (
      docView === "models" &&
      libraryView === "all" &&
      !searchQuery &&
      !narrowsLibrary(baseFilters) &&
      model.collection_id === selectedCollectionId &&
      model.collection === selectedCollection
    ) {
      setTreeReveal({ id: model.id, entryKey: entry.key, opener });
      return;
    }
    router.push(treeModelFolderHref(model, sortKey), { state: { treeModelId: model.id } });
  }
  const localTreeReveal = treeReveal?.entryKey === entry.key ? treeReveal : null;
  const revealModelId =
    localTreeReveal?.id ??
    (handledTreeEntry !== entry.key && Number.isSafeInteger(historyTreeModelId)
      ? historyTreeModelId
      : undefined);
  useEffect(() => {
    if (revealModelId === undefined || !browseReady || snapshot?.entry !== entry) return;

    function finish() {
      setTreeReveal(null);
      setHandledTreeEntry(entry.key);
    }
    if (!visibleModels.some(({ id }) => id === revealModelId)) {
      if (hasMore && !modelQuery.isFetching && !modelQuery.isFetchNextPageError) {
        void modelQuery.loadMore();
      } else if (!hasMore || modelQuery.isFetchNextPageError) {
        const frame = requestAnimationFrame(finish);
        return () => cancelAnimationFrame(frame);
      }
      return;
    }
    const id = revealModelId;
    const key = `/models/${id}`;
    const element = startupContent.current;
    if (!element) return;
    let frame = 0;
    function reveal() {
      const anchor = element?.querySelector<HTMLElement>(libraryReadingSelector(key));
      const card = anchor?.closest<HTMLElement>("[data-library-key]") ?? anchor;
      // A mounted card can open while the virtual list is still measuring.
      if (!card && readingRenderers.current.get("models")?.reveal(key) === "pending") {
        frame = requestAnimationFrame(reveal);
        return;
      }
      if (card) {
        card.scrollIntoView({ block: "center", inline: "nearest", behavior: "instant" });
        const originalOpener = localTreeReveal?.opener;
        const treeOpener = originalOpener?.isConnected
          ? originalOpener
          : document.querySelector<HTMLElement>(
              `[data-collections-pane][data-open="true"] [data-outliner-entry="${CSS.escape(key)}"]`,
            );
        if (treeOpener && document.activeElement === document.body)
          treeOpener.focus({ preventScroll: true });
        inspectModel(id, treeOpener ?? anchor ?? null, "collections");
        setTreeFocus({ id, entryKey: entry.key });
      }
      finish();
    }
    frame = requestAnimationFrame(reveal);
    return () => cancelAnimationFrame(frame);
  }, [
    browseReady,
    entry,
    hasMore,
    inspectModel,
    modelQuery,
    revealModelId,
    snapshot,
    localTreeReveal,
    visibleModels,
  ]);

  // The dock can change the grid's column count and remount the targeted row.
  // Re-center and highlight the same card once its final geometry is available.
  useEffect(() => {
    if (
      treeFocus?.entryKey !== entry.key ||
      treeFocus?.id !== inspectorIntent?.id ||
      !inspectorOpen ||
      inspectorMotion.moving
    )
      return;
    const key = `/models/${treeFocus.id}`;
    let frame = 0;
    let highlightTimer: number | undefined;
    let highlighted: HTMLElement | undefined;
    function center() {
      const anchor = startupContent.current?.querySelector<HTMLElement>(
        libraryReadingSelector(key),
      );
      const card = anchor?.closest<HTMLElement>("[data-library-key]") ?? anchor;
      if (!card) {
        if (readingRenderers.current.get("models")?.reveal(key) === "pending") {
          frame = requestAnimationFrame(center);
        }
        return;
      }
      card.scrollIntoView({ block: "center", inline: "nearest", behavior: "instant" });
      card.classList.add("library-tree-spotlight");
      highlighted = card;
      highlightTimer = window.setTimeout(() => setTreeFocus(null), DURATION.slow);
    }
    frame = requestAnimationFrame(center);
    return () => {
      cancelAnimationFrame(frame);
      window.clearTimeout(highlightTimer);
      highlighted?.classList.remove("library-tree-spotlight");
    };
  }, [entry.key, inspectorIntent?.id, inspectorMotion.moving, inspectorOpen, treeFocus]);

  const closeInspector = useCallback(() => {
    if (inspectorIntent) setInspector({ ...inspectorIntent, open: false });
    requestAnimationFrame(() => {
      if (libraryInspectorIntents.get(inspectorScope)?.open) return;
      if (document.activeElement?.closest(OUTLINER_KEYBOARD_ROW)) return;
      const opener = inspectorOpener.current;
      const main = startupContent.current;
      if (
        opener?.isConnected &&
        opener !== document.body &&
        opener !== main &&
        !opener.closest("[inert]") &&
        opener.getClientRects().length > 0
      ) {
        opener.focus({ preventScroll: true });
        return;
      }
      // Reflow can replace a card's DOM node while the inspector is open.
      const card = inspectorIntent
        ? main?.querySelector<HTMLElement>(libraryReadingSelector(`/models/${inspectorIntent.id}`))
        : null;
      const control =
        card ?? document.querySelector<HTMLButtonElement>('button[aria-controls="model-drawer"]');
      control?.focus({ preventScroll: true });
    });
  }, [inspectorIntent, inspectorScope, setInspector]);
  const toggleInspector = useCallback(() => {
    if (!inspectorAvailable) return;
    if (inspectorOpen) closeInspector();
    else {
      const id = inspectorIntent?.id ?? visibleModels[0]?.id;
      if (id !== undefined) {
        const opener = document.activeElement;
        inspectModel(
          id,
          opener instanceof HTMLElement ? opener : null,
          opener?.closest(OUTLINER_KEYBOARD_ROW) ? "collections" : "details",
        );
      }
    }
  }, [
    inspectorAvailable,
    inspectorOpen,
    inspectorIntent,
    visibleModels,
    closeInspector,
    inspectModel,
  ]);
  useEffect(() => {
    window.dispatchEvent(
      new CustomEvent("printstash:library-inspector-state", {
        detail: { available: inspectorAvailable, open: inspectorOpen },
      }),
    );
  }, [inspectorAvailable, inspectorOpen]);
  useEffect(() => {
    window.addEventListener("printstash:toggle-library-inspector", toggleInspector);
    return () => window.removeEventListener("printstash:toggle-library-inspector", toggleInspector);
  }, [toggleInspector]);
  useEffect(
    () => () => {
      window.dispatchEvent(
        new CustomEvent("printstash:library-inspector-state", {
          detail: { available: false, open: false },
        }),
      );
    },
    [],
  );
  useEffect(() => {
    function onShortcut(event: KeyboardEvent) {
      if (
        event.repeat ||
        event.defaultPrevented ||
        event.isComposing ||
        event.getModifierState("AltGraph") ||
        modalOverlayOpen()
      )
        return;
      if (
        (event.metaKey || event.ctrlKey) &&
        event.altKey &&
        !event.shiftKey &&
        event.code === "KeyB"
      ) {
        if (inspectorAvailable) {
          event.preventDefault();
          toggleInspector();
        }
        return;
      }
      if (keyboardOverlayOpen() || isTypingTarget(event.target)) return;
      if (
        event.key.toLowerCase() === "g" &&
        !event.ctrlKey &&
        !event.metaKey &&
        !event.altKey &&
        !event.shiftKey
      ) {
        const results = startupContent.current;
        if (!results) return;
        const bounds = results.getBoundingClientRect();
        const controls = Array.from(results.querySelectorAll<HTMLElement>("[data-library-key]"))
          .map(libraryEntryControl)
          .filter((control) => control !== null);
        const firstVisible = controls.find((control) => {
          const rect = control.getBoundingClientRect();
          return rect.height > 0 && rect.top < bounds.bottom && rect.bottom > bounds.top;
        });
        const target = firstVisible ?? controls[0];
        if (target) {
          event.preventDefault();
          target.focus();
        }
        return;
      }
      if (
        event.key.toLowerCase() === "s" &&
        !event.ctrlKey &&
        !event.metaKey &&
        !event.altKey &&
        auth.isAuthenticated
      ) {
        event.preventDefault();
        setKeyboardSelection(true);
      } else if (event.key === "Escape" && inspectorOpen) {
        event.preventDefault();
        closeInspector();
      } else if (event.key === "Escape" && selectMode) {
        event.preventDefault();
        clearSelection();
      }
    }
    window.addEventListener("keydown", onShortcut);
    return () => window.removeEventListener("keydown", onShortcut);
  }, [
    auth.isAuthenticated,
    clearSelection,
    selectMode,
    inspectorAvailable,
    inspectorOpen,
    toggleInspector,
    closeInspector,
  ]);
  // While searching, the grid is a global result list, not a folder view: show
  // only collections whose name matches the query (anywhere in the tree), to
  // mirror the matching models. Without a query we fall back to the normal
  // folder explorer (immediate children of the selected collection).
  // The folder being viewed and the way to it, from one authorized lookup.
  // A folder view shows one bounded page of child folders at a time.
  const { isFetchingNextPage: fetchingFolders, fetchNextPage: fetchMoreFolders } = folderPages;
  const availableRecentFolders = recentFolders.filter((folder) =>
    selectedCollectionId === null
      ? folder.path !== selectedCollection
      : folder.id !== selectedCollectionId,
  );
  const prefetchFolder = useIntentPrefetch(
    (path, signal) => {
      if (path === selectedCollection) return;
      const options = libraryBrowseOptions({
        ...folderModelFilters(path),
        view: libraryView,
        limit: pageSize ?? UNMEASURED_PAGE_SIZE,
        sort: sortKey,
      });
      const cancel = () => {
        const query = queryClient.getQueryCache().find({ queryKey: options.queryKey, exact: true });
        if (query && query.getObserversCount() === 0)
          void queryClient.cancelQueries({ queryKey: options.queryKey, exact: true });
      };
      signal.addEventListener("abort", cancel, { once: true });
      void queryClient
        .prefetchInfiniteQuery(options)
        .finally(() => signal.removeEventListener("abort", cancel));
    },
    startup.complete && browseReady && !refreshing,
  );
  const warmModel = useModelOpenIntent(startup.complete && browseReady && !refreshing);
  const canAdminSelectedCollection =
    user?.is_superuser || selectedCollectionRow?.effective_role === "admin";
  // Even administrators must wait for the selected parent's identity before creating.
  const canCreateCollection =
    canAdminSelectedCollection && (selectedCollection === null || selectedCollectionRow !== null);
  // Whether there is anywhere this reader may upload to, without listing it.
  const writableProbe = useCollectionSearch("", "edit", {
    enabled: auth.isAuthenticated && !user?.is_superuser,
  });
  const hasWritableCollection =
    !!user?.is_superuser ||
    (!writableProbe.isError && (writableProbe.data?.pages[0]?.items.length ?? 0) > 0);
  const canUploadToVault =
    auth.isAuthenticated &&
    (user?.is_superuser || canWriteCollection(selectedCollectionRow) || hasWritableCollection);
  const canUploadHere =
    selectedCollection === null
      ? canUploadToVault
      : !!(user?.is_superuser || canWriteCollection(selectedCollectionRow));
  const uploadDefaultCollection =
    user?.is_superuser || canWriteCollection(selectedCollectionRow) ? selectedCollection : null;
  const showUpload = uploadOpen || (uploadRequested && canUploadToVault);
  useEffect(
    () =>
      subscribeUploadRequests(() => {
        setDropPreload(null);
        setDropCollection(null);
        setUploadOpen(true);
      }),
    [],
  );
  // Warm the dialog's code once the Library is usable, so the first Upload
  // does not wait for its download.
  useEffect(() => {
    if (!browseReady || !canUploadToVault) return;
    const id = window.setTimeout(() => void loadUploadModal(), 1500);
    return () => window.clearTimeout(id);
  }, [browseReady, canUploadToVault]);
  function closeUpload() {
    setUploadOpen(false);
    if (uploadRequested) {
      const params = new URLSearchParams(searchParams.toString());
      params.delete("upload");
      router.replace(params.size ? `/?${params}` : "/");
    }
  }
  useEffect(() => {
    if (
      !uploadRequested ||
      !auth.isAuthenticated ||
      canUploadToVault ||
      (!user?.is_superuser && writableProbe.isPending && !canWriteCollection(selectedCollectionRow))
    )
      return;
    if (writableProbe.isError) toast.error(writableProbe.error);
    const params = new URLSearchParams(searchParams.toString());
    params.delete("upload");
    router.replace(params.size ? `/?${params}` : "/");
  }, [
    uploadRequested,
    auth.isAuthenticated,
    user?.is_superuser,
    writableProbe.isPending,
    writableProbe.isError,
    writableProbe.error,
    selectedCollectionRow,
    canUploadToVault,
    searchParams,
    router,
  ]);

  async function saveNewCollection(name: string, inline = false): Promise<CollectionCreateOutcome> {
    if (!auth.isAuthenticated) {
      auth.showAuthRequiredToast();
      return { status: "reported" };
    }
    if (!canAdminSelectedCollection) {
      toast.warning(uiText("Admin access required"));
      return { status: "reported" };
    }
    // Until the open folder has loaded, a new folder would land at the root.
    if (selectedCollection !== null && selectedCollectionRow === null)
      return { status: "reported" };
    try {
      await createCollection({ name, parent_id: selectedCollectionRow?.id ?? null });
      toast.success(uiText('Collection "{value1}" created', { value1: String(name) }));
      return { status: "saved" };
    } catch (e: any) {
      if (inline && parseApiError(e).status === 409) {
        return {
          status: "invalid",
          message: uiText('A collection named "{value1}" already exists here.', {
            value1: String(name),
          }),
        };
      }
      toast.error(e);
      return { status: "reported" };
    }
  }

  async function handleCreateCollection() {
    const name = newCollectionName.trim();
    if (!name) {
      setCollectionNameError(uiText("Enter a collection name."));
      return;
    }
    setCollectionNameError(null);
    const saved = await saveNewCollection(name, true);
    if (saved.status === "invalid") {
      setCollectionNameError(saved.message);
      return;
    }
    if (saved.status === "saved") {
      setNewCollectionName("");
      setIsCreatingCollection(false);
    }
  }

  async function handleMoveModel(source: ModelDrag, targetCollection: string | null) {
    if (!auth.isAuthenticated) {
      auth.showAuthRequiredToast();
      return;
    }
    await modelMoves.move(source, targetCollection);
  }

  async function handleMoveMultipart(
    multipart: OutlinerModelRead,
    targetCollectionId: number | null,
  ) {
    if (!auth.isAuthenticated) {
      auth.showAuthRequiredToast();
      return;
    }
    const session = getSessionVersion();
    try {
      await moveLibraryMultipart(queryClient, multipart, targetCollectionId, session);
      toast.success(uiText("Moved"));
    } catch (error) {
      if (session === getSessionVersion()) toast.error(error);
    }
  }

  async function handleMoveCollection(collectionId: number, newParentId: number | null) {
    if (!auth.isAuthenticated) {
      auth.showAuthRequiredToast();
      return;
    }
    try {
      await moveCollection(collectionId, newParentId);
      toast.success(uiText("Moved"));
    } catch (e: any) {
      toast.error(e);
    }
  }

  async function handleDeleteCollection(id: number, recursive: boolean) {
    if (!auth.isAuthenticated) {
      auth.showAuthRequiredToast();
      return;
    }
    try {
      await deleteCollection(id, recursive);
      toast.success(uiText("Collection deleted"));
      refresh();
    } catch (e: any) {
      toast.error(e);
    }
  }

  async function saveCollectionTags(collection: CollectionRead, nextTags: string[]) {
    const session = getSessionVersion();
    try {
      // The write invalidates every loaded collection query (request.ts).
      const confirmed = await replaceCollectionTags(collection.id, nextTags);
      toast.success(uiText("Tags updated for {value1}", { value1: String(collection.name) }));
      return confirmed.tags;
    } catch (error) {
      if (session === getSessionVersion()) toast.error(error);
      throw error;
    }
  }

  function handleOpenCreateCollection() {
    if (isCreatingCollection) {
      setIsCreatingCollection(false);
      setNewCollectionName("");
    } else {
      setIsCreatingCollection(true);
    }
  }

  function toggleFavorites() {
    const next = !favoritesOnly;
    const params = new URLSearchParams(searchParams.toString());
    if (next) params.set("favorites", "true");
    else params.delete("favorites");
    router.replace(params.size ? `/?${params}` : "/");
  }

  function selectSort(value: SortKey) {
    const params = new URLSearchParams(searchParams);
    params.set("sort", value);
    router.replace(`/?${params}`);
    localStorage.setItem("ps-vault-sort", value);
  }

  function clearSearch() {
    const params = new URLSearchParams(searchParams.toString());
    params.delete("q");
    const qs = params.toString();
    router.replace(qs ? `/?${qs}` : "/");
  }

  function clearAllFilters(preserveLocation = false) {
    setSelectedIds(new Set());
    setSelectedMultipartRows(new Map());
    const params = new URLSearchParams(searchParams.toString());
    if (!preserveLocation) {
      params.delete("q");
      params.delete("c");
      params.delete("cid");
    }
    params.delete("tag");
    params.delete("printer_id");
    params.delete("printer_presence");
    params.delete("favorites");
    for (const key of [
      "file_type",
      "material_type",
      "slicer_name",
      "printer_model",
      "revision_status",
      "print_outcome",
      "storage",
      "printed",
      "has_similar_candidates",
      "uploaded_after",
      "uploaded_before",
      ...historyKeys,
    ])
      params.delete(key);
    const qs = params.toString();
    router.replace(qs ? `/?${qs}` : "/");
  }

  const activeFilterItems: { label: string; onRemove: () => void }[] = (() => {
    const items: { label: string; onRemove: () => void }[] = [];
    if (favoritesOnly) items.push({ label: uiText("Favorites"), onRemove: toggleFavorites });
    if (query.trim()) {
      items.push({ label: `${ui("Search")}: ${query.trim()}`, onRemove: clearSearch });
    }
    for (const slug of selectedTags) {
      const tag = tags.find((item) => item.slug === slug);
      items.push({
        label: `${ui("Tag")}: ${tag?.name ?? slug}`,
        onRemove: () => setSelectedTags(selectedTags.filter((item) => item !== slug)),
      });
    }
    if (selectedPrinterId !== null) {
      const printer = printers.find((item) => item.id === selectedPrinterId);
      items.push({
        label: `${ui("Printer")}: ${printer?.name ?? selectedPrinterId}`,
        onRemove: () => setSelectedPrinterId(null),
      });
    }
    if (selectedPrinterPresence !== null) {
      items.push({
        label: selectedPrinterPresence === "none" ? uiText("Vault only") : uiText("On a printer"),
        onRemove: () => setSelectedPrinterPresence(null),
      });
    }
    for (const key of STRUCTURED_FILTER_KEYS) {
      const values = structured[key];
      for (const value of values) {
        items.push({
          label: `${structuredLibraryFilterLabel(key)}: ${filterValueText(key, value)}`,
          onRemove: () =>
            setStructuredFilter(
              key,
              values.filter((item) => item !== value),
            ),
        });
      }
    }
    for (const key of ["uploaded_after", "uploaded_before", ...historyKeys] as const) {
      const value = searchParams.get(key);
      if (value)
        items.push({
          label: `${key === "uploaded_after" ? ui("Uploaded after") : key === "uploaded_before" ? ui("Uploaded before") : ui(`aiSearch.filter.${key}`)}: ${value}`,
          onRemove: () => {
            const params = new URLSearchParams(searchParams.toString());
            params.delete(key);
            router.replace(params.size ? `/?${params}` : "/");
          },
        });
    }
    return items;
  })();
  const emptyFavorites =
    favoritesOnly && selectedCollection === null && activeFilterItems.length === 1;

  const savedViewControl = (rail = savedViewsHost !== null) => (
    <SavedViewSelector
      key={savedViewsOwner.session}
      railTrigger={rail}
      readState={savedViewReadState}
      views={savedViews}
      activeId={activeSavedViewId}
      modified={savedViewModified}
      onSelect={applySavedView}
      onCreate={openSaveView}
      onUpdate={(view) =>
        manageSavedView(
          {
            kind: "update",
            id: view.id,
            payload: { filters: currentFilters },
          },
          "savedView.updateSuccess",
        )
      }
      onRename={(view, name) =>
        manageSavedView(
          { kind: "update", id: view.id, payload: { name } },
          "savedView.renameSuccess",
        )
      }
      onDuplicate={(view) =>
        manageSavedView(
          {
            kind: "create",
            name: duplicateViewName(view.name),
            filters: effectiveLibraryView(view.filters, canViewPrinters),
          },
          "savedView.duplicateSuccess",
        )
      }
      onDelete={(view) =>
        manageSavedView({ kind: "delete", id: view.id }, "savedView.deleteSuccess")
      }
      align="start"
      triggerClassName={
        rail
          ? "h-10 w-10 max-w-none justify-center p-0 text-muted-foreground hover:text-foreground"
          : "h-8 w-full max-w-none justify-start px-3 text-sm"
      }
      triggerVariant="ghost"
    />
  );

  if (authority.authorizationChanged || thumbnails.authorizationChanged)
    return (
      <div aria-busy="true">
        <ModelGridSkeleton compact={compact} />
      </div>
    );

  const libraryActions = auth.isAuthenticated ? (
    <div role="group" aria-label={t("vault.libraryTools")} className="flex shrink-0 items-center">
      <Button
        variant="ghost"
        size="xs"
        className="aria-pressed:bg-accent aria-pressed:text-accent-foreground"
        aria-pressed={selectMode}
        aria-label={uiText("Select")}
        onClick={() => (selectMode ? clearSelection() : setKeyboardSelection(true))}
      >
        <ListChecks className="h-3.5 w-3.5 shrink-0" aria-hidden />
        <span className="library-toolbar-label">{uiText("Select")}</span>
      </Button>
    </div>
  ) : null;

  // Creating a collection acts on the folder, not on the models being filtered.
  const newCollectionButton = (
    <Button
      variant="outline"
      size="xs"
      className="shrink-0 @3xl/library:h-8"
      disabled={!canCreateCollection}
      title={
        canAdminSelectedCollection
          ? uiText("Create a collection")
          : uiText("Admin access required for this collection")
      }
      onClick={handleOpenCreateCollection}
    >
      <FolderPlus className="h-4 w-4" aria-hidden />
      {uiText("New collection")}
    </Button>
  );
  const canCreateMultipart =
    auth.isAuthenticated &&
    docView === "models" &&
    (!!user?.is_superuser || canWriteCollection(selectedCollectionRow));
  const newMultipartButton = canCreateMultipart ? (
    <Button
      variant="outline"
      size="xs"
      className="shrink-0 @3xl/library:h-8"
      onClick={() => {
        setMultipartSeed([]);
        setMultipartCreateOpen(true);
      }}
    >
      <Boxes className="h-4 w-4" aria-hidden />
      {t("multipart.new")}
    </Button>
  ) : null;
  const showNewCollectionCard = canCreateCollection && !loading && visibleCollections.length > 0;

  const libraryMoreActions = (
    <>
      <Button variant="ghost" role="menuitem" className="w-full justify-start" asChild>
        <Link href="/library/similar" onClick={() => setMoreOpen(false)}>
          <ScanSearch className="h-4 w-4" aria-hidden />
          {t("similarity.title")}
        </Link>
      </Button>
      <Button
        variant="ghost"
        role="menuitem"
        className="w-full justify-start"
        onClick={() => {
          setMoreOpen(false);
          const params = new URLSearchParams(searchParams.toString());
          params.set("v", "docs");
          router.replace(`/?${params}`);
        }}
      >
        {t("vault.documents")}
      </Button>
      {docView === "models" && (
        <Button
          variant="ghost"
          role="menuitem"
          className="w-full justify-start"
          disabled={!user?.is_superuser && !canWriteCollection(selectedCollectionRow)}
          onClick={() => {
            setMoreOpen(false);
            setMultipartSeed([]);
            setMultipartCreateOpen(true);
          }}
        >
          <Plus className="h-4 w-4" aria-hidden />
          {t("multipart.new")}
        </Button>
      )}
    </>
  );

  function filterControl(className: string) {
    return (
      <LibraryFilterControl
        key={filterPickerRevision}
        className={className}
        activeCount={activeFilterItems.length - Number(!!query.trim())}
        onOpen={() => {
          if (!filtersEnabled) startTransition(() => requestStartup("filters"));
        }}
        filters={currentFilters}
        facets={facetQuery.data}
        tags={tags}
        printers={printers}
        canViewPrinters={canViewPrinters}
        loading={!filtersEnabled || facetQuery.isLoading || tagsQuery.isLoading}
        error={facetQuery.isError || tagsQuery.isError}
        onRetry={() => {
          void facetQuery.refetch();
          void tagsQuery.refetch();
        }}
        onChange={setStructuredFilter}
        onReset={() => clearAllFilters(true)}
      />
    );
  }

  const libraryToolbar = collectionOverview ? (
    <div className="collection-overview-toolbar flex flex-wrap items-center justify-between gap-4">
      <div className="min-w-0">
        <h2 className="text-base font-semibold">{uiText("library.collectionOverview.title")}</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          {uiText("library.collectionOverview.help")}
        </p>
      </div>
      <div className="collection-overview-actions flex flex-wrap items-center gap-2">
        {filterControl("shrink-0")}
        {libraryActions}
        <DropdownMenu
          open={moreOpen}
          onOpenChange={setMoreOpen}
          align="end"
          portal
          trigger={
            <Button
              variant="ghost"
              size="icon-sm"
              className="h-9 w-9"
              aria-label={t("nav.more")}
              aria-haspopup="menu"
              aria-expanded={moreOpen}
              onClick={(event) => {
                toolbarMenuOpener.current = event.currentTarget;
                setMoreOpen(!moreOpen);
              }}
            >
              <MoreHorizontal className="h-4 w-4" aria-hidden />
            </Button>
          }
          contentClassName="w-64 border border-border bg-popover p-1 shadow-lg"
        >
          {libraryMoreActions}
        </DropdownMenu>
      </div>
    </div>
  ) : (
    <DropdownMenu
      open={moreOpen}
      returnFocusRef={toolbarMenuOpener}
      onOpenChange={setMoreOpen}
      align="end"
      contentClassName="w-64 border border-border bg-popover p-1 text-popover-foreground shadow-lg"
      trigger={
        <div
          onContextMenu={(event) => {
            event.preventDefault();
            toolbarMenuOpener.current =
              document.activeElement instanceof HTMLElement ? document.activeElement : null;
            setMoreOpen(true);
          }}
          onKeyDown={(event) => {
            if (event.shiftKey && event.key === "F10") {
              event.preventDefault();
              toolbarMenuOpener.current = event.target instanceof HTMLElement ? event.target : null;
              setMoreOpen(true);
            }
          }}
          className="library-model-toolbar flex min-w-0 flex-wrap items-center gap-2"
        >
          <h2 className="sr-only">{t("vault.models")}</h2>
          <div className="library-toolbar-filters flex min-w-0 flex-wrap items-center gap-2">
            {docView === "models" && (
              <LibraryStatusFilters
                value={structured.revision_status}
                counts={statusCounts?.revision_status}
                total={allStatusCount}
                onChange={(value) => setStructuredFilter("revision_status", value)}
                onIntent={() => startup.request("filters")}
              />
            )}
            {showScopeToggle && (
              <Button
                type="button"
                variant="outline"
                size="xs"
                aria-pressed={includeSubfolders}
                aria-label={uiText("Include subfolders")}
                disabled={loading}
                onClick={() => setModelScope(includeSubfolders ? "here" : "all")}
                className="aria-pressed:bg-accent aria-pressed:text-accent-foreground aria-pressed:hover:bg-muted dark:hover:bg-surface-container-highest dark:aria-pressed:bg-surface-bright dark:aria-pressed:hover:bg-surface-bright"
              >
                <SubfolderScopeIcon className="h-3.5 w-3.5 shrink-0" aria-hidden />
                <span className="library-toolbar-label">{uiText("Include subfolders")}</span>
              </Button>
            )}
            {filterControl("shrink-0")}
          </div>
          <div className="library-display-controls ml-auto flex min-w-0 flex-wrap items-center gap-2">
            <select
              aria-label={uiText("Sort models")}
              className="library-toolbar-sort h-8 w-32 max-w-full px-2 text-xs text-foreground"
              value={sortKey}
              onChange={(event) => {
                const option = SORT_OPTIONS.find((item) => item.value === event.target.value);
                if (option) selectSort(option.value);
              }}
            >
              {SORT_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {ui(option.label)}
                </option>
              ))}
            </select>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              data-menu-trigger
              aria-haspopup="menu"
              aria-expanded={moreOpen}
              aria-label={t("nav.more")}
              onClick={(event) => {
                toolbarMenuOpener.current = event.currentTarget;
                setMoreOpen(!moreOpen);
              }}
            >
              <MoreHorizontal className="h-4 w-4" aria-hidden />
            </Button>
          </div>
        </div>
      }
    >
      {libraryMoreActions}
    </DropdownMenu>
  );

  const layout = (
    <Localized>
      <>
        <ModelMoveReview moves={modelMoves} />
        <Modal
          open={saveViewOpen && saveViewSession === savedViewsOwner.session}
          onClose={() => {
            if (!saveViewBusy) {
              setSaveViewOpen(false);
              setSaveViewName("");
            }
          }}
          title={uiText("Save current view")}
          className="max-w-md"
        >
          <form
            className="space-y-4"
            onSubmit={(event) => {
              event.preventDefault();
              void saveCurrentView();
            }}
          >
            <label className="block space-y-1.5">
              <span className="text-sm font-medium text-foreground">{uiText("View name")}</span>
              <Input
                ref={saveViewNameInput}
                value={saveViewName}
                onChange={(event) => {
                  saveViewRevision.current += 1;
                  setSaveViewName(event.target.value);
                }}
                maxLength={128}
                placeholder={uiText("Ready to print")}
              />
            </label>
            <div className="flex justify-end gap-2">
              <Button
                type="button"
                variant="outline"
                onClick={() => {
                  setSaveViewOpen(false);
                  setSaveViewName("");
                }}
                disabled={saveViewBusy}
              >
                {uiText("Cancel")}
              </Button>
              <Button type="submit" loading={saveViewBusy} disabled={!saveViewName.trim()}>
                {uiText("Save view")}
              </Button>
            </div>
          </form>
        </Modal>
        <DeferredDialog open={showUpload} title={uiText("Upload")} onClose={closeUpload}>
          <UploadModal
            open={showUpload}
            onClose={() => {
              closeUpload();
              setDropPreload(null);
              setDropCollection(null);
            }}
            onUploaded={async (result) => {
              const session = getSessionVersion();
              await refreshVaultAfterIngest();
              if (session !== getSessionVersion() || !result) return;
              toast.successAction(
                uiText(
                  result.created
                    ? "Created a new Model."
                    : "Saved to an existing Model. Open it to review files and revisions.",
                ),
                uiText("Open Model"),
                () => {
                  if (session === getSessionVersion()) router.push(`/models/${result.id}`);
                },
              );
            }}
            defaultCollection={dropCollection ?? uploadDefaultCollection}
            preloadFiles={dropPreload?.files ?? null}
            preloadItems={dropPreload?.items ?? null}
            initialMode={dropPreload?.mode}
          />
        </DeferredDialog>
        <DeferredDialog
          open={multipartCreateOpen}
          title={t("multipart.new")}
          onClose={() => setMultipartCreateOpen(false)}
        >
          <NewMultipartModelModal
            key={`${selectedCollectionRow?.id ?? "vault"}:${multipartSeed.map((model) => model.id).join(",")}`}
            open={multipartCreateOpen}
            initialModels={multipartSeed}
            onClose={() => setMultipartCreateOpen(false)}
            collection={selectedCollectionRow ?? null}
            returnTo={currentLibraryHref}
          />
        </DeferredDialog>
        <MobileFilterDrawer
          outlinerFilters={baseFilters}
          open={filterDrawerOpen}
          onClose={closeDrawer}
          tags={tags}
          printers={printers}
          selectedCollection={selectedCollection}
          selectedCollectionId={selectedCollectionId}
          selectedTags={selectedTags}
          selectedPrinterId={selectedPrinterId}
          selectedPrinterPresence={selectedPrinterPresence}
          onCollectionChange={handleCollectionChange}
          onModelOpen={openTreeModel}
          onTagsChange={setSelectedTags}
          onPrinterChange={setSelectedPrinterId}
          onPrinterPresenceChange={setSelectedPrinterPresence}
          onCreateCollection={handleOpenCreateCollection}
          canViewPrinters={canViewPrinters}
          structuredFilters={
            <StructuredFilters
              facets={facetQuery.data}
              loading={facetQuery.isLoading}
              error={facetQuery.isError}
              active={structured}
              onChange={setStructuredFilter}
              uploadedAfter={searchParams.get("uploaded_after") ?? undefined}
              uploadedBefore={searchParams.get("uploaded_before") ?? undefined}
              history={historyFilters(searchParams)}
              onDateChange={(key, value) => {
                const params = new URLSearchParams(searchParams.toString());
                if (value) params.set(key, value);
                else params.delete(key);
                router.replace(params.size ? `/?${params}` : "/");
              }}
              onClearAll={clearAllFilters}
            />
          }
          libraryView={libraryView}
          onLibraryViewChange={handleLibraryViewChange}
        />

        {/* Stitch layout: filter sidebar + main content */}
        <LibraryCollectionsPane>
          <FilterSidebar
            collectionsOnly={hasCollectionsPane}
            filtersOpen={false}
            outlinerFilters={baseFilters}
            tags={tags}
            printers={printers}
            selectedCollection={selectedCollection}
            selectedCollectionId={selectedCollectionId}
            selectedTags={selectedTags}
            selectedPrinterId={selectedPrinterId}
            selectedPrinterPresence={selectedPrinterPresence}
            onCollectionChange={handleCollectionChange}
            onModelOpen={openTreeModel}
            onCollectionIntent={prefetchFolder}
            onTagsChange={setSelectedTags}
            onPrinterChange={setSelectedPrinterId}
            onPrinterPresenceChange={setSelectedPrinterPresence}
            onCreateCollection={handleOpenCreateCollection}
            onMoveModel={handleMoveModel}
            onMoveMultipart={handleMoveMultipart}
            onMoveCollection={handleMoveCollection}
            onDeleteCollection={handleDeleteCollection}
            canViewPrinters={canViewPrinters}
            canManageLibrary={!!user?.is_superuser}
            quickAccess={
              auth.isAuthenticated ? (
                <>
                  <Button
                    variant="ghost"
                    size="xs"
                    aria-pressed={favoritesOnly}
                    onClick={toggleFavorites}
                    className={cn(
                      " w-full justify-start px-3 text-sm",
                      favoritesOnly && "bg-accent text-accent-foreground",
                    )}
                  >
                    <Star className={cn("h-4 w-4", favoritesOnly && "fill-current")} aria-hidden />
                    {uiText("Favorites")}
                  </Button>
                  {!savedViewsHost && savedViewControl()}
                </>
              ) : null
            }
            libraryView={libraryView}
            onLibraryViewChange={handleLibraryViewChange}
          />
        </LibraryCollectionsPane>
        {savedViewsHost && auth.isAuthenticated && createPortal(savedViewControl(), savedViewsHost)}

        {findOpen && docView === "models" && (
          <LibraryFindBar
            models={visibleModels}
            onReveal={revealFound}
            onClose={() => {
              setFindOpen(false);
              const opener = findOpener.current;
              findOpener.current = null;
              if (opener?.isConnected && opener !== startupContent.current)
                opener.focus({ preventScroll: true });
            }}
          />
        )}
        <main
          ref={startupContent}
          tabIndex={-1}
          aria-busy={reading.status === "restoring"}
          data-inspector-open={inspectorOpen || undefined}
          data-inspector-moving={inspectorMotion.moving || undefined}
          data-inspector-grid={docView === "models" || undefined}
          data-inspector-layout={docView === "models" ? "grid" : undefined}
          data-inspector-compact-dock={(compact && !!inspectorDock) || undefined}
          style={inspectorDockStyle}
          className="@container/library relative flex min-w-0 flex-1 flex-col overflow-y-auto bg-background pb-24 md:pb-0"
          onDragEnter={onMainDragEnter}
          onDragOver={onMainDragOver}
          onDragLeave={onMainDragLeave}
          onDrop={onMainDrop}
        >
          {/* Selection controls float over the top of the content: entering
              selection mode never moves the item that was just pressed. */}
          {selectMode && (
            <div className="sticky top-0 z-20 h-0">
              <div className="absolute inset-x-0 top-0 flex min-h-14 flex-wrap items-center gap-3 border-b border-border bg-muted px-4 py-2 text-xs sm:px-6">
                <span className="font-mono text-muted-foreground">
                  {uiText("{value1} selected", { value1: String(selectionCount ?? "") })}
                </span>
                {docView === "models" && (
                  <>
                    <button
                      type="button"
                      onClick={selectAllVisible}
                      className="font-medium text-primary hover:underline"
                    >
                      {uiText("Select all on screen (")}
                      {sortedModels.length +
                        visibleMultipartModels.length +
                        visibleCollections.length}
                      )
                    </button>
                    <button
                      type="button"
                      onClick={() => void selectAllMatching()}
                      disabled={selectingAll}
                      className="font-medium text-primary hover:underline disabled:opacity-50"
                    >
                      {selectingAll ? uiText("Selecting…") : uiText("library.selectAllMatching")}
                    </button>
                  </>
                )}
                <Button size="xs" variant="outline" onClick={clearSelection}>
                  {uiText("Done")}
                </Button>
                {selectionCount > 0 && (
                  <button
                    type="button"
                    onClick={clearSelection}
                    className="font-medium text-muted-foreground hover:text-foreground"
                  >
                    {uiText("Clear")}
                  </button>
                )}
              </div>
            </div>
          )}
          {isDragging && canUploadToVault && (
            <div className="pointer-events-none absolute inset-0 z-floating flex items-center justify-center border-2 border-dashed border-primary bg-primary/5">
              <span className="bg-background border border-border rounded px-4 py-2 font-mono text-xs uppercase tracking-widest shadow">
                {uiText("Drop to upload")}
              </span>
            </div>
          )}
          {/* Breadcrumb */}
          <LibraryBreadcrumbs>
            <CollectionBreadcrumbs items={breadcrumbs} onNavigate={handleCollectionChange}>
              {availableRecentFolders.length > 0 && (
                <DropdownMenu
                  className="ml-2 shrink-0 border-l border-border pl-2"
                  open={recentFoldersOpen}
                  onOpenChange={setRecentFoldersOpen}
                  align="start"
                  trigger={
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      data-menu-trigger
                      aria-haspopup="menu"
                      aria-expanded={recentFoldersOpen}
                      aria-label={uiText("Recent")}
                      onClick={() => setRecentFoldersOpen(!recentFoldersOpen)}
                      className="h-7 gap-1.5 px-2 text-xs font-normal text-muted-foreground"
                    >
                      <History className="h-3.5 w-3.5" aria-hidden />
                      <span className="hidden @xl/trail:inline">{ui("Recent")}</span>
                    </Button>
                  }
                  contentClassName="w-64 max-w-[calc(100vw-2rem)] border border-border bg-popover p-1 text-popover-foreground shadow-lg"
                >
                  <p className="px-2.5 py-1.5 font-mono text-3xs uppercase tracking-wider text-muted-foreground">
                    {uiText("Recent folders")}
                  </p>
                  {availableRecentFolders.map((folder) => (
                    <button
                      key={folder.id ?? folder.path}
                      title={folder.label}
                      disabled={folder.unavailable}
                      role="menuitem"
                      type="button"
                      onClick={() => {
                        handleCollectionChange(folder.path, folder.id);
                        setRecentFoldersOpen(false);
                      }}
                      className="flex w-full items-center gap-2 rounded px-2.5 py-2 text-left text-xs transition-colors hover:bg-popover-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    >
                      <Folder className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                      <span className="min-w-0 truncate">
                        {folder.label}
                        {folder.unavailable ? ` — ${uiText("Unavailable")}` : ""}
                      </span>
                    </button>
                  ))}
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      recent.clear();
                      setRecentFoldersOpen(false);
                    }}
                    className="mt-1 w-full border-t border-border px-2.5 py-2 text-left text-xs text-muted-foreground transition-colors hover:bg-popover-hover hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    {uiText("Clear recent folders")}
                  </button>
                </DropdownMenu>
              )}
            </CollectionBreadcrumbs>
          </LibraryBreadcrumbs>

          {/* The pane owns breakpoints: an open outliner also consumes horizontal room. */}
          <div className="library-collection-header px-4 sm:px-6">
            <div className="flex min-w-0 flex-wrap items-start justify-between gap-x-4 gap-y-1">
              <div className="library-collection-identity min-w-0 flex-[1_1_100%] @2xl/library:flex-1">
                <InlineTitleEditor
                  fieldLabel={uiText("inbox.collectionName")}
                  maxLength={COLLECTION_NAME_LIMIT}
                  title={
                    unavailableCollection
                      ? uiText("library.collectionUnavailable")
                      : (selectedName ?? uiText("All Models"))
                  }
                  canEdit={canRenameCollection}
                  open={collectionRename !== null}
                  onOpen={openCollectionRename}
                  onCancel={() => {
                    if (!collectionRenameBusy) setCollectionRename(null);
                  }}
                  value={collectionRenameName}
                  onValueChange={setCollectionRenameName}
                  onSave={saveCollectionRename}
                  busy={collectionRenameBusy}
                />
                <div className="library-collection-meta flex flex-wrap items-center">
                  {collectionOverview && !displayedCollection && (
                    <span className="library-meta-part">
                      {uiText("library.header.collections", { count: visibleCollections.length })}
                    </span>
                  )}
                  {displayedCollection && displayedCollection.child_count > 0 && (
                    <span className="library-meta-part">
                      {uiText("library.header.collections", {
                        count: displayedCollection.child_count,
                      })}
                    </span>
                  )}
                  {browseTotal !== undefined &&
                    (displayedCollection?.path ?? null) === selectedCollection && (
                      <span className="library-meta-part">
                        {displayedCollection && (searchQuery || foldersHidden)
                          ? uiText("library.header.filteredCount", {
                              count: browseTotal,
                              total: displayedCollection.model_count,
                            })
                          : uiText(hasMultipartResults ? "library.header.items" : "counts.models", {
                              count: browseTotal,
                            })}
                      </span>
                    )}
                  {displayedCollection && (
                    <>
                      <CollectionHeaderTags
                        key={`tags:${displayedCollection.id}`}
                        tags={displayedCollection.tags}
                        canEdit={!!user?.is_superuser || canWriteCollection(displayedCollection)}
                        onSave={(nextTags) => saveCollectionTags(displayedCollection, nextTags)}
                      />
                      <CollectionReadme
                        key={displayedCollection.id}
                        collectionId={displayedCollection.id}
                        compact
                        hasReadme={displayedCollection.has_readme}
                        canEdit={!!user?.is_superuser || canWriteCollection(displayedCollection)}
                        disabled={!browseReady}
                      />
                    </>
                  )}
                  {selectedCollection !== null && loading && (
                    <CollectionHeaderSkeleton hasDescription={destinationCollection?.has_readme} />
                  )}
                </div>
              </div>
              {!showNewCollectionCard && newCollectionButton}
              {newMultipartButton}
              <Button
                onClick={() => {
                  setDropPreload(null);
                  setDropCollection(null);
                  setUploadOpen(true);
                }}
                disabled={!canUploadToVault}
                title={
                  canUploadToVault
                    ? uiText("Upload artifacts")
                    : uiText("Sign in and get edit access to upload")
                }
                className="shrink-0 @3xl/library:h-8 @3xl/library:text-xs"
              >
                <Upload className="h-4 w-4" aria-hidden />
                {uiText("Upload")}
              </Button>
            </div>
          </div>

          {activeFilterItems.length > 0 && (
            <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-3 sm:px-6">
              <span className="text-3xs font-mono uppercase tracking-wider text-muted-foreground">
                {uiText("Filters")}
              </span>
              {activeFilterItems.map((item) => (
                <Button
                  key={item.label}
                  type="button"
                  variant="ghost"
                  size="xs"
                  onClick={item.onRemove}
                  className="gap-1.5 bg-accent text-accent-foreground"
                  title={uiText("Remove {value1}", { value1: String(item.label) })}
                >
                  {item.label}
                  <X className="h-3 w-3" aria-hidden />
                </Button>
              ))}
              <Button type="button" variant="ghost" size="xs" onClick={() => clearAllFilters()}>
                {uiText("Clear all")}
              </Button>
            </div>
          )}

          <Modal
            open={isCreatingCollection}
            onClose={() => {
              setIsCreatingCollection(false);
              setNewCollectionName("");
            }}
            title={uiText("New collection")}
            description={uiText("Create a collection inside the current collection.")}
            className="max-w-[480px]"
            footer={
              <>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => {
                    setIsCreatingCollection(false);
                    setNewCollectionName("");
                    setCollectionNameError(null);
                  }}
                >
                  {uiText("Cancel")}
                </Button>
                <Button
                  type="submit"
                  form={collectionFormId}
                  disabled={!auth.isAuthenticated || !canCreateCollection}
                >
                  {uiText("Create collection")}
                </Button>
              </>
            }
          >
            <form
              id={collectionFormId}
              noValidate
              onSubmit={(event) => {
                event.preventDefault();
                void handleCreateCollection();
              }}
            >
              <FormField
                label={uiText("Name")}
                htmlFor={collectionNameId}
                error={collectionNameError}
                errorId={`${collectionNameId}-error`}
              >
                <Input
                  id={collectionNameId}
                  autoFocus
                  value={newCollectionName}
                  aria-invalid={collectionNameError ? true : undefined}
                  aria-describedby={collectionNameError ? `${collectionNameId}-error` : undefined}
                  onChange={(event) => {
                    setNewCollectionName(event.target.value);
                    setCollectionNameError(null);
                  }}
                  disabled={!auth.isAuthenticated}
                  maxLength={100}
                />
              </FormField>
            </form>
          </Modal>

          {docView === "docs" && !favoritesOnly && (
            <div className="flex items-center gap-4 px-4 pt-4 text-sm sm:px-6">
              <button
                type="button"
                className="text-muted-foreground hover:text-foreground"
                onClick={() => {
                  const params = new URLSearchParams(searchParams.toString());
                  params.set("v", "models");
                  router.replace(`/?${params}`);
                }}
              >
                {t("vault.models")}
              </button>
              <span className="font-semibold">{t("vault.documents")}</span>
            </div>
          )}

          {/* Content */}
          {docView === "docs" &&
          !favoritesOnly &&
          (selectedCollection === null || selectedCollectionRow !== null) ? (
            <Suspense
              fallback={
                <p role="status" className="p-6 text-sm">
                  {uiText("Loading...")}
                </p>
              }
            >
              <DocumentBrowser
                collectionId={selectedCollectionRow?.id ?? null}
                collectionPath={selectedCollection}
                canCreate={!!user?.is_superuser || canWriteCollection(selectedCollectionRow)}
              />
            </Suspense>
          ) : (
            <div className="library-content-reveal flex-1 flex flex-col bg-background">
              {reading.status === "reset" && (
                <p
                  role="status"
                  className="mx-6 mt-4 rounded-md border border-border bg-muted p-3 text-sm"
                >
                  {uiText("library.readingPositionReset")}
                </p>
              )}
              {authority.error && (
                <div
                  role="status"
                  className="mx-6 mt-4 flex items-center justify-between gap-3 rounded-md border border-border p-3 text-sm"
                >
                  <p>{uiText("library.authorityCheckFailed")}</p>
                  <Button
                    variant="outline"
                    size="sm"
                    loading={authority.checking}
                    onClick={() => void authority.recheck()}
                  >
                    {uiText("library.checkAgain")}
                  </Button>
                </div>
              )}
              {error && (
                <ErrorState
                  className="mx-4 mt-4 sm:mx-6"
                  message={
                    unavailableCollection ? (
                      uiText("library.collectionUnavailableHelp")
                    ) : (
                      <>
                        {uiText("Could not load Models.")}{" "}
                        <span className="text-muted-foreground">{error}</span>
                      </>
                    )
                  }
                  actions={
                    unavailableCollection ? (
                      <Button
                        variant="outline"
                        size="sm"
                        className="shrink-0"
                        onClick={() => handleCollectionChange(null)}
                      >
                        {uiText("aiSearch.backToLibrary")}
                      </Button>
                    ) : undefined
                  }
                  retryLabel={uiText("Retry")}
                  retrying={currentRefresh?.status === "pending" || modelQuery.isFetching}
                  onRetry={
                    currentRefresh ||
                    (!unavailableCollection && modelReadError && !modelQuery.isFetchNextPageError)
                      ? () => {
                          if (currentRefresh)
                            void (snapshot ? reading.refresh(refresh) : refresh());
                          else void modelQuery.refetch();
                        }
                      : undefined
                  }
                />
              )}

              {collectionOverview && (
                <div className="collection-overview-toolbar-wrap px-4 sm:px-6">
                  {libraryToolbar}
                </div>
              )}

              <LibraryCollectionsRegion
                followingContent={modelsContent}
                present={reservedCollections > 0 || (!loading && visibleCollections.length > 0)}
                enabled={snapshot !== null && !collectionOverview}
              >
                {reservedCollections > 0 && (
                  <section
                    className="library-subcollections library-loading-placeholder mx-4 sm:mx-6"
                    aria-hidden
                  >
                    <CollectionStrip
                      total={reservedCollections}
                      overview={false}
                      resetKey={selectedCollection ?? ""}
                    >
                      {(singleRow, onColumns) => (
                        <LibraryItems
                          items={Array.from({ length: reservedCollections }, (_, index) => index)}
                          itemKey={(index) => `collection-placeholder:${index}`}
                          renderItem={() => <div className="library-collection-placeholder" />}
                          scrollRef={startupContent}
                          readingRenderers={readingRenderers}
                          readingId="collections-placeholder"
                          layout="grid"
                          freezeInspectorTracks={inspectorOpen}
                          compact={compact}
                          estimateSize={COMPACT_COLLECTION_HEIGHT}
                          virtualize={false}
                          singleRow={singleRow}
                          onColumnsChange={onColumns}
                        />
                      )}
                    </CollectionStrip>
                  </section>
                )}

                {!loading && visibleCollections.length > 0 && (
                  <section
                    className={`library-subcollections mx-4 sm:mx-6 ${collectionOverview ? "library-subcollections-overview" : ""}`}
                    aria-label={uiText("Collections")}
                  >
                    <CollectionStrip
                      total={visibleCollections.length}
                      overview={collectionOverview}
                      hasCreateCard={showNewCollectionCard}
                      resetKey={displayedPath ?? ""}
                    >
                      {(singleRow, onColumns, visibleCount) => (
                        <LibraryItems
                          key={`collections:${displayedPath ?? ""}`}
                          items={[
                            ...visibleCollections.slice(0, visibleCount),
                            ...(showNewCollectionCard ? ["create" as const] : []),
                          ]}
                          itemKey={(collection) =>
                            collection === "create"
                              ? "create-collection"
                              : `collection:${collection.id}`
                          }
                          renderItem={(collection) =>
                            collection === "create" ? (
                              <NewCollectionCard
                                maxLength={COLLECTION_NAME_LIMIT}
                                onCreate={(name) => saveNewCollection(name, true)}
                              />
                            ) : (
                              <LibraryCardFill
                                identity={cardIdentity}
                                animateOnMount={cardEntrance !== null}
                                className="library-collection-reveal"
                              >
                                <CollectionOverviewCard
                                  collection={collection}
                                  compact={!collectionOverview}
                                  arriving={arriving.has(`c:${collection.id}`)}
                                  authorizationRevision={snapshot?.authorizationRevision}
                                  onAuthorityRetired={onAuthorityRetired}
                                  previewsReady={browseReady}
                                  previewSource={snapshot?.previewSource}
                                  onSelect={handleCollectionChange}
                                  onIntent={prefetchFolder}
                                  onDropModel={canUploadToVault ? handleMoveModel : undefined}
                                  selectable={selectMode}
                                  selected={selectedCollectionRows.has(collection.id)}
                                  onToggleSelect={
                                    auth.isAuthenticated ? toggleCollectionSelect : undefined
                                  }
                                  onRename={
                                    canManageCard(collection, "rename")
                                      ? () =>
                                          setCollectionCardTarget({
                                            entry,
                                            id: collection.id,
                                            action: "rename",
                                          })
                                      : undefined
                                  }
                                  onEditCover={
                                    canManageCard(collection, "cover")
                                      ? () =>
                                          setCollectionCardTarget({
                                            entry,
                                            id: collection.id,
                                            action: "cover",
                                          })
                                      : undefined
                                  }
                                  onRegenerateThumbnails={
                                    canManageCard(collection, "cover")
                                      ? () => void regenerateCardThumbnails(collection)
                                      : undefined
                                  }
                                  renaming={
                                    managedCollection?.id === collection.id &&
                                    collectionCardTarget?.action === "rename"
                                  }
                                  onRenameEnd={(saved) => {
                                    setCollectionCardTarget((target) =>
                                      target?.entry === entry &&
                                      target.id === collection.id &&
                                      target.action === "rename"
                                        ? null
                                        : target,
                                    );
                                    if (saved) void refresh();
                                  }}
                                />
                              </LibraryCardFill>
                            )
                          }
                          scrollRef={startupContent}
                          readingRenderers={readingRenderers}
                          readingId="collections"
                          layout="grid"
                          freezeInspectorTracks={inspectorOpen}
                          compact={!collectionOverview && compact}
                          estimateSize={collectionOverview ? 266 : COMPACT_COLLECTION_HEIGHT}
                          singleRow={singleRow}
                          onColumnsChange={onColumns}
                        />
                      )}
                    </CollectionStrip>
                  </section>
                )}
              </LibraryCollectionsRegion>

              <div ref={modelsContent} className="library-model-content flex flex-1 flex-col">
                {!collectionOverview && (
                  <div className="library-model-toolbar-wrap px-4 sm:px-6">{libraryToolbar}</div>
                )}

                {loading || modelsPending ? (
                  // Hold the Models' space while a folder's collections arrive first.
                  <ModelGridSkeleton compact={compact} onCapacityChange={receivePageSize} />
                ) : (error && snapshot === null) ||
                  collectionOverview ? null : sortedModels.length === 0 &&
                  visibleMultipartModels.length === 0 &&
                  visibleCollections.length === 0 &&
                  !hasMore ? (
                  selectedCollection && canUploadHere && !emptyFavorites && !hasActiveFilters ? (
                    <LibraryEmptyCollection
                      disabled={!canUploadToVault}
                      onUpload={() => {
                        setDropPreload({ files: [], mode: "files" });
                        setUploadOpen(true);
                      }}
                    />
                  ) : (
                    <EmptyState
                      title={uiText(
                        emptyFavorites
                          ? "No favorites yet"
                          : selectedCollection && !hasActiveFilters
                            ? "library.collectionEmpty"
                            : "No models found",
                      )}
                      description={
                        emptyFavorites
                          ? uiText("Star a model or multipart set to see it here.")
                          : hasActiveFilters
                            ? uiText("Try clearing some filters.")
                            : selectedCollection
                              ? uiText(
                                  canUploadHere
                                    ? "library.collectionEmptyHelp"
                                    : "library.collectionReadOnlyEmptyHelp",
                                )
                              : uiText(
                                  "Upload a model when you're ready, or skim the wiki first if this is a new install.",
                                )
                      }
                      action={
                        emptyFavorites ? (
                          <Button
                            type="button"
                            variant="outline"
                            size="xs"
                            onClick={toggleFavorites}
                          >
                            {uiText("aiSearch.backToLibrary")}
                          </Button>
                        ) : hasActiveFilters ? (
                          <Button
                            type="button"
                            variant="outline"
                            size="xs"
                            onClick={() => clearAllFilters()}
                          >
                            {uiText("Clear all filters")}
                          </Button>
                        ) : selectedCollection && !canUploadHere ? (
                          <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            onClick={() => handleCollectionChange(null)}
                          >
                            {uiText("aiSearch.backToLibrary")}
                          </Button>
                        ) : (
                          <div className="flex flex-wrap items-center justify-center gap-2">
                            <GettingStartedReminder />
                            <Button
                              type="button"
                              size="sm"
                              disabled={!canUploadToVault}
                              onClick={() => {
                                setDropPreload({ files: [], mode: "files" });
                                setUploadOpen(true);
                              }}
                            >
                              {uiText("Upload files")}
                            </Button>
                            {canUploadToVault && (
                              <Button asChild variant="outline" size="sm">
                                <Link href="/inbox">{uiText("Import from URL")}</Link>
                              </Button>
                            )}
                            {user?.is_superuser && (
                              <Button asChild variant="outline" size="sm">
                                <Link href="/settings">{uiText("Connect folder or NAS")}</Link>
                              </Button>
                            )}
                          </div>
                        )
                      }
                      className="flex-1 py-20"
                    />
                  )
                ) : (
                  <div
                    // One grid across folders: a Model shown in both (subfolders
                    // included) keeps its card, thumbnail and state instead of
                    // every card being rebuilt on each navigation. The surrounding
                    // content reveal settles the committed visit as one surface.
                    key="grid"
                    className={compact ? "p-3" : "px-4 pb-4 pt-3 sm:px-6 sm:pb-6"}
                  >
                    <LibraryItems
                      items={libraryItems}
                      itemKey={(item) =>
                        item.kind === "model"
                          ? `/models/${item.value.id}`
                          : `/multipart-models/${item.value.id}`
                      }
                      renderItem={(item) => (
                        <LibraryCardFill
                          identity={cardIdentity}
                          animateOnMount={cardEntrance !== null}
                        >
                          {item.kind === "multipart" ? (
                            <MultipartModelCard
                              key={`multipart-${item.value.id}`}
                              item={item.value}
                              arriving={arriving.has(`p:${item.value.id}`)}
                              collectionLabel={item.value.collection_label}
                              returnTo={currentLibraryHref}
                              origin={snapshot?.entry}
                              availableTags={tags}
                              onDataChange={refresh}
                              onEditTags={openMultipartTagEditor}
                              selectable={selectMode}
                              selected={selectedMultipartRows.has(item.value.id)}
                              onToggleSelect={
                                auth.isAuthenticated ? toggleMultipartSelect : undefined
                              }
                            />
                          ) : (
                            <ModelCard
                              key={item.value.id}
                              model={item.value}
                              origin={snapshot?.entry}
                              arriving={arriving.has(`m:${item.value.id}`)}
                              collectionLabel={
                                includeSubfolders ? item.value.collection_label : null
                              }
                              selectable={selectMode}
                              selected={
                                modelSelection(
                                  item.value,
                                  selectedIds,
                                  selectedCollectionRows.values(),
                                ).kind !== "none"
                              }
                              onToggleSelect={auth.isAuthenticated ? toggleSelect : undefined}
                              draggable={canUploadToVault && !selectMode}
                              onEditTags={openTagEditor}
                              onIntent={warmModel}
                              onInspect={inspectModel}
                              inspected={inspectorOpen && inspectorIntent?.id === item.value.id}
                            />
                          )}
                        </LibraryCardFill>
                      )}
                      scrollRef={startupContent}
                      readingRenderers={readingRenderers}
                      readingId="models"
                      layout="grid"
                      freezeInspectorTracks={inspectorOpen}
                      onInspectorDockChange={receiveInspectorDock}
                      preferredInspectorWidth={preferredInspectorWidth}
                      estimateSize={compact ? 290 : 250}
                      compact={compact}
                    />
                    <LoadMore
                      paused={reading.status === "restoring" || !browseReady}
                      hasMore={snapshot?.moreFolders ?? false}
                      loading={fetchingFolders || currentRefresh !== null}
                      failed={folderPages.isFetchNextPageError}
                      onClick={() => {
                        if (currentRefresh === null) void fetchMoreFolders();
                      }}
                      folders
                    />
                    <LoadMore
                      paused={reading.status === "restoring" || !browseReady}
                      hasMore={hasMore && !refreshRequired}
                      loading={loadingMore}
                      failed={modelQuery.isFetchNextPageError}
                      onClick={loadMore}
                    />
                  </div>
                )}
              </div>
            </div>
          )}
        </main>
        {inspectorAvailable && inspectorIntent && (
          <LibraryModelInspector
            key={entry.session}
            id={inspectorIntent.id}
            open={inspectorOpen}
            focusOnOpen={inspectorIntent.focusTarget === "details"}
            motionLocked={inspectorMotion.moving}
            summary={visibleModels.find((model) => model.id === inspectorIntent.id)}
            style={inspectorDockStyle}
            onMotionSettled={settleInspectorDock}
            resize={
              inspectorDock && inspectorDock.maxWidth >= LIBRARY_INSPECTOR_MIN_WIDTH
                ? {
                    current: inspectorDock.width,
                    max: inspectorDock.maxWidth,
                    locked: inspectorMotion.moving,
                    onResize: chooseInspectorWidth,
                  }
                : null
            }
            origin={snapshot?.entry}
            canWrite={auth.isAuthenticated && !authority.authorizationChanged && inspectorOpen}
            onOpenPage={() => reading.rememberAnchor(`/models/${inspectorIntent.id}`)}
            onClose={closeInspector}
          />
        )}

        {managedCollection && collectionCardTarget?.action === "cover" && (
          <CollectionCoverEditor
            key={managedCollection.id}
            collection={managedCollection}
            onClose={() => setCollectionCardTarget(null)}
            onSaved={() => {
              setCollectionCardTarget(null);
              void refresh();
            }}
          />
        )}
        {currentBatchRecovery && (
          <LibraryBatchRecovery
            key={currentBatchRecovery.entry.key}
            receipt={currentBatchRecovery.receipt}
            intent={currentBatchRecovery.intent}
            models={currentBatchRecovery.models}
            onChanged={() => void refresh()}
            onClose={() => {
              setBatchRecovery(null);
              clearSelection();
            }}
          />
        )}
        {docView === "models" && batchToolsActivated && (
          <Suspense fallback={null}>
            <BatchToolbar
              modelCount={selectedIdList.length}
              multipartCount={selectedMultipartRows.size}
              selectedCollections={selectedCollections}
              tags={tags}
              busy={batchBusy || currentBatchRecovery !== null}
              canMoveToRoot={!!user?.is_superuser}
              onMoveSelection={moveSelection}
              onRenameCollections={(names) =>
                runCollectionBatch("collection.renameSuccess", (collection) =>
                  renameCollection(collection.id, names[collection.id]),
                )
              }
              onApplyTags={tagSelection}
              onDeleteSelection={deleteSelection}
              onGroupAsMultipart={
                canCreateMultipart && selectedIdList.length > 0
                  ? () => {
                      setMultipartSeed(visibleModels.filter((model) => selectedIds.has(model.id)));
                      setMultipartCreateOpen(true);
                    }
                  : undefined
              }
              onClear={clearSelection}
            />
          </Suspense>
        )}
        {multipartTagTarget && (
          <MultipartModelTagsDialog
            key={`${multipartTagTarget.item.id}:${multipartTagTarget.intent}`}
            item={multipartTagTarget.item}
            availableTags={tags}
            onDataChange={refresh}
            canEdit={
              !authority.authorizationChanged &&
              visibleMultipartModels.some(
                (item) =>
                  item.id === multipartTagTarget.item.id &&
                  (item.effective_role === "edit" || item.effective_role === "admin"),
              )
            }
            initialOpen
            triggerMode="none"
          />
        )}
        {tagTarget && (
          <DeferredDialog
            open={tagDialogOpen}
            title={uiText("Model tags")}
            onClose={() => setTagDialogOpen(false)}
          >
            <ModelTagsDialog
              key={`${tagTarget.id}:${tagDialogSession}`}
              model={tagTarget}
              suggestions={tags}
              open={tagDialogOpen}
              onClose={() => setTagDialogOpen(false)}
              onSaved={(nextTags, editVersion) => {
                setTagTarget((current) =>
                  current ? { ...current, tags: nextTags, ...editVersion } : current,
                );
                refresh();
              }}
            />
          </DeferredDialog>
        )}
      </>
    </Localized>
  );
  return <ModelGalleryDrag onMove={handleMoveModel}>{layout}</ModelGalleryDrag>;
}

/**
 * The next page of a view loads as the list is scrolled toward its end, so
 * a sentinel reaching up 600px above the end starts the read before the reader
 * gets there. A failed read retires the observer and offers an explicit retry,
 * so a sentinel still in view cannot retry without end.
 */
function LoadMore({
  hasMore,
  loading,
  failed = false,
  paused = false,
  onClick,
  folders = false,
}: {
  hasMore: boolean;
  paused?: boolean;
  loading: boolean;
  failed?: boolean;
  onClick: () => void;
  folders?: boolean;
}) {
  useUiLocale();
  const paneOpen = useCollectionsPaneOpen();
  const layoutKey = paneOpen ?? true;
  const sentinel = useRef<HTMLDivElement>(null);
  const load = useRef(onClick);
  useLayoutEffect(() => {
    load.current = onClick;
  });
  // The measured first batch fills the viewport. Anticipate later pages only
  // after the reader scrolls, even when this view has no initial media pause.
  const [awaitScroll, setAwaitScroll] = useState(true);
  const [readingLayout, setReadingLayout] = useState(layoutKey);
  if (readingLayout !== layoutKey) {
    setReadingLayout(layoutKey);
    setAwaitScroll(true);
  }
  if (paused && !awaitScroll) setAwaitScroll(true);
  // Reconstruct only the saved pages. Resume anticipation after the reader moves,
  // so a restored near-end sentinel cannot fetch an unvisited cursor on its own.
  useEffect(() => {
    if (!awaitScroll || paused) return;
    const main = sentinel.current?.closest("main");
    if (!main) return;
    const resume = (event: Event) => {
      if (event instanceof KeyboardEvent && !["PageDown", "End", "ArrowDown"].includes(event.key))
        return;
      setAwaitScroll(false);
    };
    main.addEventListener("wheel", resume, { passive: true });
    main.addEventListener("touchmove", resume, { passive: true });
    // Scroll does not bubble: capture also covers the table's inner scroller.
    // Scrollbar dragging and assistive scrolling need no wheel or key event.
    main.addEventListener("scroll", resume, { capture: true, passive: true });
    main.addEventListener("keydown", resume);
    return () => {
      main.removeEventListener("wheel", resume);
      main.removeEventListener("touchmove", resume);
      main.removeEventListener("scroll", resume, true);
      main.removeEventListener("keydown", resume);
    };
  }, [awaitScroll, paused]);
  const automatic = "IntersectionObserver" in globalThis && !failed && !paused && !awaitScroll;
  const mayLoad = useRef(false);
  useLayoutEffect(() => {
    mayLoad.current = automatic && hasMore && !loading;
  });
  useEffect(() => {
    const main = sentinel.current?.closest("main");
    if (!main || !hasMore || loading || failed || paused || !awaitScroll) return;
    // A short first page has no scrollbar to move. Fill only that viewport;
    // the larger anticipation area belongs to subsequent reader scrolling.
    let filling = false;
    const fill = () => {
      const table = main.querySelector<HTMLElement>("[data-library-list]");
      const tableScrollable = table !== null && table.scrollHeight > table.clientHeight + 1;
      if (
        !filling &&
        !tableScrollable &&
        main.clientHeight > 0 &&
        main.scrollHeight <= main.clientHeight + 1
      ) {
        filling = true;
        load.current();
      }
    };
    fill();
    if (!("ResizeObserver" in globalThis)) return;
    const observer = new ResizeObserver(fill);
    observer.observe(main);
    return () => observer.disconnect();
  }, [awaitScroll, failed, hasMore, loading, paused, layoutKey]);
  useEffect(() => {
    const element = sentinel.current;
    if (!automatic || !hasMore || loading || element === null) return;
    // Observing reports the current state at once, so a list shorter than the
    // reach keeps loading until it fills it.
    const observer = new IntersectionObserver((entries) => {
      if (mayLoad.current && entries.some((entry) => entry.isIntersecting)) {
        mayLoad.current = false;
        load.current();
      }
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [automatic, hasMore, loading]);
  if (!hasMore) return null;
  return (
    <Localized>
      <div
        data-library-pagination={folders ? "folders" : "models"}
        className="relative flex justify-center mt-6 pb-6"
      >
        {"IntersectionObserver" in globalThis && (
          <div
            ref={sentinel}
            data-load-more-sentinel
            aria-hidden
            className="pointer-events-none absolute bottom-0 h-[600px] w-full"
          />
        )}
        {loading ? (
          <span role="status" className="text-xs text-muted-foreground">
            {uiText("Loading...")}
          </span>
        ) : failed ? (
          <Button variant="outline" onClick={onClick}>
            {uiText("Retry")}
          </Button>
        ) : null}
      </div>
    </Localized>
  );
}

/** Reserve the compact metadata line while the collection snapshot loads. */
export function CollectionHeaderSkeleton({ hasDescription = false }: { hasDescription?: boolean }) {
  useUiLocale();
  return (
    <div
      role="status"
      aria-label={uiText("Loading…")}
      className="library-loading-placeholder w-full min-w-0"
    >
      {hasDescription && (
        <div className="mb-1 flex h-[18px] items-center">
          <Skeleton className="h-3 w-60 max-w-full" />
        </div>
      )}
      <div className="collection-header-meta-placeholder">
        <Skeleton className="h-3 w-60 max-w-full" />
      </div>
    </div>
  );
}

/** Two full rows: a partial last row reads as content, not as loading. */
const SKELETON_ROWS = 2;

function viewportPageSize(node: HTMLElement, columns: number, selector: string): number {
  const main = node.closest("main");
  const first = node.querySelector<HTMLElement>(selector);
  if (!main || !first || main.clientHeight === 0 || first.clientHeight === 0)
    return UNMEASURED_PAGE_SIZE;
  const firstRect = first.getBoundingClientRect();
  const rowGap = Number.parseFloat(getComputedStyle(first.parentElement!).rowGap);
  const gap = Number.isFinite(rowGap) ? rowGap : 0;
  const available = Math.max(0, main.getBoundingClientRect().bottom - firstRect.top);
  const rows = Math.max(1, Math.ceil((available + gap) / (firstRect.height + gap)));
  // Browse's maximum page size is 100; a taller surface continues automatically.
  return Math.min(100, columns * rows);
}

export function ModelGridSkeleton({
  compact = false,
  onRetire,
  onCapacityChange,
}: {
  compact?: boolean;
  onRetire?: (rects: readonly DOMRect[]) => void;
  onCapacityChange?: (capacity: number, columns: number) => void;
}) {
  useUiLocale();
  const [scrollNode, setScrollNode] = useState<HTMLDivElement | null>(null);
  const scroll = useMemo(() => ({ current: scrollNode }), [scrollNode]);
  const renderers = useRef(new Map<string, LibraryReadingRenderer>());
  const [columns, setColumns] = useState(4);
  const capacityFrame = useRef<number | null>(null);
  const receiveColumns = useCallback(
    (count: number) => {
      setColumns(count);
      if (capacityFrame.current !== null) cancelAnimationFrame(capacityFrame.current);
      // The reserved collection strip measures its columns in the same commit.
      // Read after that reflow so an early nested-folder request still fills
      // the viewport, rather than sizing against temporary stacked folders.
      capacityFrame.current = requestAnimationFrame(() => {
        if (scrollNode?.isConnected)
          onCapacityChange?.(viewportPageSize(scrollNode, count, "[data-library-key]"), count);
      });
    },
    [onCapacityChange, scrollNode],
  );
  useLayoutEffect(
    () => () => {
      if (capacityFrame.current !== null) cancelAnimationFrame(capacityFrame.current);
    },
    [],
  );
  useLayoutEffect(() => {
    // Non-layout hosts and hidden surfaces cannot provide a viewport yet.
    if (scrollNode?.clientWidth === 0) onCapacityChange?.(UNMEASURED_PAGE_SIZE, 0);
  }, [onCapacityChange, scrollNode]);
  const cards = useMemo(
    () => Array.from({ length: columns * SKELETON_ROWS }, (_, index) => index),
    [columns],
  );
  useLayoutEffect(() => {
    if (!scrollNode || !onRetire) return;
    let rects: readonly DOMRect[] = [];
    const measure = () => {
      rects = Array.from(scrollNode.querySelectorAll("[data-library-key]")).map((node) =>
        node.getBoundingClientRect(),
      );
    };
    let tick = requestAnimationFrame(measure);
    const schedule = () => {
      cancelAnimationFrame(tick);
      tick = requestAnimationFrame(measure);
    };
    const observer = "ResizeObserver" in globalThis ? new ResizeObserver(schedule) : null;
    observer?.observe(scrollNode);
    window.addEventListener("scroll", schedule, true);
    return () => {
      cancelAnimationFrame(tick);
      observer?.disconnect();
      window.removeEventListener("scroll", schedule, true);
      const grid = scrollNode.querySelector(".library-loading-placeholder");
      // A suppressed fast-load placeholder never established a visual position;
      // forget older ones too, so cards never travel from a stale placeholder.
      if (!grid || Number(getComputedStyle(grid).opacity) === 0) {
        onRetire([]);
        return;
      }
      // Keep the last painted positions: earlier siblings may already have
      // changed layout during the commit that retires this placeholder.
      onRetire(rects);
    };
  }, [scrollNode, onRetire]);
  return (
    <div
      ref={setScrollNode}
      role="status"
      aria-label={uiText("Loading...")}
      className={compact ? "p-3" : "px-4 pb-4 pt-3 sm:px-6 sm:pb-6"}
    >
      {scrollNode && (
        <LibraryItems
          items={cards}
          onColumnsChange={receiveColumns}
          itemKey={(index) => `skeleton:${index}`}
          scrollRef={scroll}
          readingRenderers={renderers}
          readingId="skeleton"
          layout="grid"
          className="library-loading-placeholder"
          compact={compact}
          virtualize={false}
          renderItem={() => (
            <div
              className="library-model-card flex h-full flex-col overflow-hidden rounded-md border border-border bg-card"
              aria-hidden
            >
              <div className="library-preview">
                <LibraryPreviewPlaceholder />
              </div>
              <LibraryCardDetailsPlaceholder />
            </div>
          )}
        />
      )}
    </div>
  );
}
