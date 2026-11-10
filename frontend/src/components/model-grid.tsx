"use client";

import { arrivingKeys, entryKeys } from "@/lib/library-arrivals";
import {
  FOLDER_CARDS_CLASS,
  FOLDER_MOVE_CLASS,
  folderMove,
  type FolderMove,
} from "@/lib/folder-transition";
import { deleteLibraryMultipart } from "@/features/library/multipart";
import { useLibraryNavigationReads } from "@/features/library/navigation-reads";
import {
  collectionIdentity,
  useCollectionDestination,
  useRecentFolders,
} from "@/features/library/collection-navigation";
import { useTaxonomyCommands } from "@/features/library/taxonomy";

import { LibraryCardSelection } from "@/components/library-card-selection";
import { LibraryBatchRecovery } from "@/components/library-batch-recovery";

import {
  useLibraryReadingPosition,
  type LibraryReadingRenderer,
} from "@/features/library/reading-position";
import { LibraryItems } from "@/components/library-items";
import { LibraryItemLink } from "@/features/library/navigation";
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
import { useLibraryAuthority } from "@/features/library/authority";
import { listLibraryPage } from "@/lib/api/library-browse";

import { historyFilters, historyKeys } from "@/lib/search-filters";
import {
  readLibraryFilters,
  writeLibraryFilters,
  effectiveLibraryView,
  narrowsLibrary,
  sameLibraryFilters,
  structuredLibraryFilterKeys as STRUCTURED_FILTER_KEYS,
  structuredLibraryFilterLabel,
  type StructuredLibraryFilterKey as StructuredFilterKey,
} from "@/features/library/filters";

import { GettingStartedReminder } from "@/components/getting-started-reminder";

import { uiText, type MessageKey } from "@/lib/locale";
import { getErrorMessage, parseApiError, userMessage } from "@/lib/errors";
import { filterValueText } from "@/lib/filter-labels";

import { useUiLocale } from "@/lib/i18n";
import { useIntentPrefetch } from "@/lib/use-intent-prefetch";
import { useLibraryStartup } from "@/lib/library-startup-context";
import { useNavigationShell } from "@/lib/navigation-shell";

import { useCallback, useEffect, useLayoutEffect, useMemo, useState, useRef } from "react";
import { createPortal } from "react-dom";
import { useQueryClient } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "@/lib/navigation";
import {
  CollectionNodeRead,
  CollectionRead,
  CollectionPage,
  ModelBatchResult,
  ModelListItem,
  ModelSort,
  MultipartModelListItem,
  PrinterRead,
  SavedViewRead,
  TagRead,
} from "@/types";
import { ModelCard } from "@/components/model-card";
import { TitleEditor } from "@/components/title-editor";
import { DeferredDialog } from "@/components/deferred-dialog";
import { lazyImport } from "@/lib/lazy-component";
import { useModelMoves } from "@/features/library/moves";
import { ModelMoveReview } from "@/components/model-move-review";
import { MODEL_DND_MIME, captureModelDrag, readModelDrag, type ModelDrag } from "@/lib/model-dnd";
import { BatchToolbar } from "@/components/batch-toolbar";
import { Checkbox } from "@/components/ui/checkbox";
import { CollectionReadme } from "@/components/collection-readme";
import { MultipartModelTagsDialog } from "@/components/multipart-model-tags-dialog";
import { MultipartModelCard } from "@/components/multipart-model-card";
import { EntityTagsDialog } from "@/components/entity-tags-dialog";
import { DocumentBrowser } from "@/components/document-browser";
import { useCollectionsPane } from "@/lib/collections-pane-context";
import { LibraryCollectionsPane, LibraryBreadcrumbs } from "@/components/collections-pane";
import { FilterSidebar } from "@/components/filter-sidebar";
import { readLibraryLocation, type LibraryViewMode } from "@/features/library/url";
import { MobileFilterDrawer } from "@/components/mobile-filter-drawer";
import { StructuredFilters } from "@/components/structured-filters";
import { LibraryBaseFilters } from "@/components/library-base-filters";
import type { UploadMode } from "@/components/upload-modal";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/ui/empty-state";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { DropdownMenu } from "@/components/ui/dropdown-menu";
import { SavedViewSelector } from "@/components/saved-view-selector";
import { Localized } from "@/components/ui/localized";
import { translateUiText } from "@/lib/locale";
import { useI18n } from "@/lib/i18n";
import { useMobileFilterDrawer } from "@/lib/mobile-filter-context";
import {
  SlidersHorizontal,
  Grid,
  List,
  FileText,
  X,
  Printer,
  Folder,
  ChevronRight,
  Plus,
  Star,
  ArrowUpDown,
  Rows3,
  History,
  Check,
  ChevronDown,
  MoreHorizontal,
  Boxes,
  ScanSearch,
  Pencil,
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
import { timeAgo } from "@/lib/format";
import { rememberLastCollection, readLastView, rememberLastView } from "@/lib/last-collection";
import { ProtectedThumbnail } from "@/components/protected-thumbnail";
import { useStartupThumbnails } from "@/lib/use-startup-thumbnails";
import { useThumbnailArrivals } from "@/lib/use-thumbnail-arrivals";
import { cn } from "@/lib/utils";
import { TabBar } from "@/components/ui/tabs";

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

/** Fetch the dialog chunks once the library is usable, so the first click opens
 *  a dialog instead of an empty frame while its code downloads. */
function useDialogPreload(ready: boolean) {
  useEffect(() => {
    if (!ready) return;
    const load = () => {
      // A failed warm-up changes nothing: opening the dialog retries the import.
      for (const loader of [loadModelTagsDialog, loadUploadModal, loadNewMultipartModelModal])
        void loader().catch(() => undefined);
    };
    // Late enough not to compete with the first thumbnails, early enough to be warm.
    const handle = setTimeout(load, 1500);
    return () => clearTimeout(handle);
  }, [ready]);
}

type SortKey = ModelSort;
type ViewMode = "grid" | "list";
type LibraryItem =
  | { kind: "model"; value: ModelListItem }
  | { kind: "multipart"; value: MultipartModelListItem };

// Fill the initial viewport without projecting/mounting sixty cards at once.
// Later pages keep the same bound and use the server cursor.
const PAGE_SIZE = 24;
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

type MenuTriggerSize = "xs" | "sm";
type MenuTriggerVariant = "outline" | "ghost";

interface SortMenuProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  sortKey: SortKey;
  onSelect: (sortKey: SortKey) => void;
  labelFor: (value: string) => string;
  triggerSize: MenuTriggerSize;
  triggerVariant?: MenuTriggerVariant;
  triggerRole?: "menuitem";
  triggerClassName?: string;
  wrapperClassName?: string;
}

function SortMenu({
  open,
  onOpenChange,
  sortKey,
  onSelect,
  labelFor,
  triggerSize,
  triggerVariant = "outline",
  triggerRole,
  triggerClassName,
  wrapperClassName,
}: SortMenuProps) {
  useUiLocale();
  return (
    <DropdownMenu
      open={open}
      onOpenChange={onOpenChange}
      align="end"
      className={wrapperClassName}
      trigger={
        <Button
          type="button"
          variant={triggerVariant}
          size={triggerSize}
          role={triggerRole}
          data-menu-trigger
          aria-haspopup="menu"
          aria-expanded={open}
          aria-label={labelFor("Sort models")}
          onClick={() => onOpenChange(!open)}
          className={cn(triggerClassName)}
        >
          <ArrowUpDown className="h-3.5 w-3.5 shrink-0" />
          <span className="min-w-0 flex-1 truncate">
            {labelFor(SORT_OPTIONS.find((option) => option.value === sortKey)?.label ?? "Newest")}
          </span>
          <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        </Button>
      }
      contentClassName="w-52 rounded border border-border bg-popover p-1 text-popover-foreground shadow-lg"
    >
      {SORT_OPTIONS.map((option) => (
        <button
          key={option.value}
          type="button"
          role="menuitem"
          onClick={() => {
            onSelect(option.value);
            onOpenChange(false);
          }}
          className={`flex w-full items-center gap-2 rounded px-2.5 py-2 text-left text-xs transition-colors hover:bg-popover-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${sortKey === option.value ? "bg-accent text-accent-foreground" : ""}`}
        >
          <span className="flex-1">{labelFor(option.label)}</span>
          {sortKey === option.value && <Check className="h-3.5 w-3.5" />}
        </button>
      ))}
    </DropdownMenu>
  );
}

interface DisplayMenuProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  viewMode: ViewMode;
  compact: boolean;
  onSelectMode: (mode: ViewMode) => void;
  onSelectDensity: (compact: boolean) => void;
  labelFor: (value: string) => string;
  triggerSize: MenuTriggerSize;
  triggerVariant?: MenuTriggerVariant;
  triggerRole?: "menuitem";
  triggerClassName?: string;
  onSelectComplete?: () => void;
}

function DisplayMenu({
  open,
  onOpenChange,
  viewMode,
  compact,
  onSelectMode,
  onSelectDensity,
  labelFor,
  triggerSize,
  triggerVariant = "outline",
  triggerRole,
  triggerClassName,
  onSelectComplete,
}: DisplayMenuProps) {
  useUiLocale();
  return (
    <DropdownMenu
      open={open}
      onOpenChange={onOpenChange}
      align="end"
      trigger={
        <Button
          type="button"
          variant={triggerVariant}
          size={triggerSize}
          role={triggerRole}
          data-menu-trigger
          aria-haspopup="menu"
          aria-expanded={open}
          onClick={() => onOpenChange(!open)}
          className={cn(triggerClassName)}
        >
          <Rows3 className="h-3.5 w-3.5 shrink-0" />
          <span className="min-w-0 flex-1 truncate text-left">{labelFor("Display")}</span>
          <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        </Button>
      }
      contentClassName="w-48 rounded border border-border bg-popover p-1 text-popover-foreground shadow-lg"
    >
      <p className="px-2.5 py-1.5 font-mono text-3xs uppercase tracking-wider text-muted-foreground">
        {labelFor("Layout")}
      </p>
      {(
        [
          ["grid", "Grid", Grid],
          ["list", "List", List],
        ] as const
      ).map(([mode, label, Icon]) => (
        <button
          key={mode}
          type="button"
          role="menuitem"
          aria-label={uiText("{value1} View", { value1: String(labelFor(label)) })}
          onClick={() => {
            onSelectMode(mode);
            onOpenChange(false);
            onSelectComplete?.();
          }}
          className={`flex w-full items-center gap-2 rounded px-2.5 py-2 text-left text-xs transition-colors hover:bg-popover-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${viewMode === mode ? "bg-accent text-accent-foreground" : ""}`}
        >
          <Icon className="h-3.5 w-3.5" />
          <span className="flex-1">{labelFor(label)}</span>
          {viewMode === mode && <Check className="h-3.5 w-3.5" />}
        </button>
      ))}
      <p className="mt-1 border-t border-border px-2.5 py-1.5 font-mono text-3xs uppercase tracking-wider text-muted-foreground">
        {labelFor("Density")}
      </p>
      {(
        [
          [false, "Comfortable"],
          [true, "Compact"],
        ] as const
      ).map(([isCompact, label]) => (
        <button
          key={label}
          type="button"
          role="menuitem"
          onClick={() => {
            onSelectDensity(isCompact);
            onOpenChange(false);
            onSelectComplete?.();
          }}
          className={`flex w-full items-center gap-2 rounded px-2.5 py-2 text-left text-xs transition-colors hover:bg-popover-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${compact === isCompact ? "bg-accent text-accent-foreground" : ""}`}
        >
          <span className="flex-1">{labelFor(label)}</span>
          {compact === isCompact && <Check className="h-3.5 w-3.5" />}
        </button>
      ))}
    </DropdownMenu>
  );
}

const LIBRARY_VIEW_KEY = "ps-vault-library-view";

function readVaultPreference(key: string): string | null {
  if (!("window" in globalThis)) return null;
  return localStorage.getItem(key);
}

function canWriteCollection(collection: CollectionRead | null | undefined): boolean {
  return collection?.effective_role === "edit" || collection?.effective_role === "admin";
}

type LibraryRefreshState = { entry: LibraryEntry } & (
  | { status: "pending" }
  | { status: "failed" | "denied"; error: string }
);

export interface BrowserInitialData {
  models: ModelListItem[];
  tags: TagRead[];
  printers: PrinterRead[];
}

export function ModelBrowser({ initial }: { initial?: BrowserInitialData }) {
  useUiLocale();
  const { locale, t } = useI18n();
  const ui = useCallback((value: string) => translateUiText(locale, value), [locale]);
  const router = useRouter();
  const pane = useCollectionsPane();
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
  function setActiveSavedViewId(id: number | null) {
    setSavedViewSelection(id === null ? null : { session: savedViewsOwner.session, id });
  }
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
  const [viewMode, setViewMode] = useState<ViewMode>(() =>
    readVaultPreference("ps-vault-view") === "list" ? "list" : "grid",
  );
  const [initialLibraryPreferences] = useState(() => ({
    view: readVaultPreference(LIBRARY_VIEW_KEY),
    sort: readVaultPreference("ps-vault-sort"),
  }));
  const [initialLibrarySection] = useState<"models" | "docs">(() =>
    readLastView() === "docs" ? "docs" : "models",
  );
  const libraryLocation = useMemo(
    () =>
      readLibraryLocation(
        new URLSearchParams(filterQuery),
        initialLibraryPreferences,
        initialLibrarySection,
      ),
    [filterQuery, initialLibraryPreferences, initialLibrarySection],
  );
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
  const [sortOpen, setSortOpen] = useState(false);
  const [displayOpen, setDisplayOpen] = useState(false);
  const [desktopSortOpen, setDesktopSortOpen] = useState(false);
  const [desktopDisplayOpen, setDesktopDisplayOpen] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const [filtersExpanded, setFiltersExpanded] = useState<boolean | null>(null);
  const [compact, setCompact] = useState(
    () => readVaultPreference("ps-vault-density") === "compact",
  );
  const [recentFoldersOpen, setRecentFoldersOpen] = useState(false);
  const [multipartCreateOpen, setMultipartCreateOpen] = useState(false);
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

  useEffect(() => {
    const reviewImport = () => {
      setDropPreload({ files: [], mode: "url" });
      setUploadOpen(true);
    };
    window.addEventListener("printstash:review-import", reviewImport);
    return () => window.removeEventListener("printstash:review-import", reviewImport);
  }, []);
  const [dropCollection, setDropCollection] = useState<string | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const dragEnterCount = useRef(0);

  function classifyDrop(files: File[]): { files: File[]; mode: UploadMode } | null {
    const meshes = files.filter((f) => isSourceFile(f.name));
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
    setDropPreload({ ...result, items: bulkItems });
    setDropCollection(collPath);
    setUploadOpen(true);
  }

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
  const { open: filterDrawerOpen, openDrawer, closeDrawer } = useMobileFilterDrawer();

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

  const startNavigationReads = useLibraryNavigationReads();
  function handleCollectionChange(path: string | null, id: number | null = null) {
    if (
      path === selectedCollection &&
      !selectedLookup.isError &&
      (id === null || id === selectedCollectionId)
    )
      return;
    startNavigationReads(path, {
      ...folderModelFilters(path),
      view: libraryView,
      limit: PAGE_SIZE,
      sort: sortKey,
    });
    // An already empty selection must not render the departing grid urgently
    // before Router can commit the destination and start its critical reads.
    setSelectedIds((current) => (current.size === 0 ? current : new Set()));
    setSelectedMultipartRows((current) => (current.size === 0 ? current : new Map()));
    const params = new URLSearchParams(searchParams.toString());
    params.delete("cid");
    if (id !== null) params.set("cid", String(id));
    if (path) params.set("c", path);
    else params.delete("c");
    const qs = params.toString();
    router.push(qs ? `/?${qs}` : "/");
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
  const queryClient = useQueryClient();

  function setStructuredFilter(key: StructuredFilterKey, values: string[]) {
    const params = new URLSearchParams(searchParams.toString());
    params.delete(key);
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
  ): ModelListFilters => ({
    ...baseFilters,
    collection: collection ?? undefined,
    // A search or a filter spans the whole subtree; an unfiltered folder view
    // lists only its direct children so subfolders' models don't leak into
    // the parent (#30).
    direct: !searchQuery && !narrowsLibrary(baseFilters),
    q: searchQuery,
  });
  // Options and counts describe the whole subtree, so a folder that holds only
  // subfolders still offers every filter that would match inside it.
  const facetQuery = useModelFacets(
    { ...folderModelFilters(), direct: false },
    { enabled: filtersEnabled },
  );

  function writeFilterUrl(filters: SavedViewRead["filters"]) {
    const params = writeLibraryFilters(filters, canViewPrinters);
    router.replace(`/?${params}`);
  }

  function applySavedView(view: SavedViewRead) {
    setFiltersExpanded(null);
    setActiveSavedViewId(view.id);
    setSelectedIds(new Set());
    setSelectedMultipartRows(new Map());
    writeFilterUrl(view.filters);
  }

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
    limit: PAGE_SIZE,
    sort: sortKey,
  };
  const modelQuery = useLibraryBrowse(
    browseParams,
    selectedCollectionId === null || destinationCollection?.path === selectedCollection,
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
  const folderSearch = useCollectionSearch(searchQuery ?? "", "view", {
    enabled: searchQuery !== undefined,
  });
  const folderLevel = useCollectionChildren(selectedCollectionRow?.id ?? null, {
    enabled:
      searchQuery === undefined && (selectedCollection === null || selectedCollectionRow !== null),
  });
  const folderPages = searchQuery !== undefined ? folderSearch : folderLevel;
  // Canonical replacement creates a Router key. Publish the visit only once
  // that identity is settled, before the reader can establish its position.
  const projectionReady =
    currentLocationHref === canonicalLibraryHref &&
    (selectedCollection === null ||
      (selectedCollectionRow !== null && selectedCollectionId === selectedCollectionRow.id)) &&
    (!selectedCollectionRow?.has_readme || !collectionReadme.isPending) &&
    folderPages.data !== undefined &&
    !folderPages.isPlaceholderData &&
    modelQuery.data !== undefined &&
    !modelQuery.isPlaceholderData;

  const entry = useLibraryEntry(canonicalLibraryHref, projectionReady);
  const modelMoves = useModelMoves(entry, refresh);
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
      items: orderedItems,
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
    entry,
    orderedItems,
    foldersHidden,
    folderPages.data,
    folderPages.hasNextPage,
    selectedCollectionRow,
    selectedLookup.data,
    modelQuery.hasNextPage,
    modelQuery.data?.pages.length,
  ]);
  const [settledSnapshot, setSettledSnapshot] = useState(nextSnapshot);
  const lookupStatus = selectedLookup.error ? parseApiError(selectedLookup.error).status : null;
  const unavailableCollection =
    selectedCollection !== null &&
    selectedLookup.isError &&
    (lookupStatus === 403 ||
      lookupStatus === 404 ||
      settledSnapshot === null ||
      settledSnapshot.entry.href !== canonicalLibraryHref);
  if (unavailableCollection && settledSnapshot !== null) setSettledSnapshot(null);
  if (nextSnapshot !== null && nextSnapshot !== settledSnapshot) setSettledSnapshot(nextSnapshot);
  const snapshot =
    unavailableCollection || currentRefresh?.status === "denied"
      ? null
      : (nextSnapshot ?? settledSnapshot);
  const displayedCollection = snapshot?.collection ?? null;
  // `null` until the first snapshot lands: opening a folder by URL is arriving,
  // not moving, so the first settled folder must not animate.
  const displayedPath = snapshot === null ? null : (displayedCollection?.path ?? "");
  const [folderEntrance, setFolderEntrance] = useState<{
    path: string | null;
    move: FolderMove | null;
  }>({ path: displayedPath, move: null });
  if (displayedPath !== null && folderEntrance.path !== displayedPath) {
    setFolderEntrance({
      path: displayedPath,
      move: folderEntrance.path === null ? null : folderMove(folderEntrance.path, displayedPath),
    });
  }
  const onAuthorityRetired = useCallback(() => {
    const session = getSessionVersion();
    void refreshAuth().catch((error) => {
      if (session === getSessionVersion()) toast.error(error);
    });
  }, [refreshAuth]);
  const authority = useLibraryAuthority(browseReady ? (modelQuery.data?.pages[0] ?? null) : null, {
    eventsReady: startup.canLoad("activity"),
    onRefresh: () => reading.refresh(refresh),
    onAuthorityRetired,
  });
  const refreshRequired =
    authority.refreshRequired || modelQuery.refreshRequired || currentRefresh !== null;
  const libraryItems = snapshot?.items ?? [];
  const visibleModels = libraryItems.flatMap((item) => (item.kind === "model" ? [item.value] : []));
  const visibleMultipartModels = libraryItems.flatMap((item) =>
    item.kind === "multipart" ? [item.value] : [],
  );
  const visibleCollections = snapshot?.collections ?? [];
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

  function openCollectionRename() {
    if (!canRenameCollection || !displayedCollection || collectionRenameBusy || collectionRename) {
      return;
    }
    setCollectionRename({ entry, collection: displayedCollection });
    setCollectionRenameName(displayedCollection.name);
    setCollectionRenameBusy(false);
  }

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
  const error =
    (currentRefresh && currentRefresh.status !== "pending" ? currentRefresh.error : null) ??
    (modelQuery.refreshRequired ? null : modelQuery.error?.message) ??
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

  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [selectedMultipartRows, setSelectedMultipartRows] = useState<
    Map<number, MultipartModelListItem>
  >(new Map());
  const selectionEntry = useRef<LibraryEntry | null>(entry);
  useLayoutEffect(() => {
    selectionEntry.current = entry;
    return () => {
      selectionEntry.current = null;
    };
  }, [entry]);
  useLayoutEffect(() => {
    setSelectedMultipartRows((current) => (current.size === 0 ? current : new Map()));
  }, [libraryView, selectedCollection, selectedCollectionId, docView]);
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
  // already on screen keeps its place. A different folder starts a new reading.
  const shownKeys = entryKeys(visibleCollections, libraryItems);
  const [arrival, setArrival] = useState<{
    path: string | null;
    keys: ReadonlySet<string>;
    arriving: ReadonlySet<string>;
  }>({ path: displayedPath, keys: new Set(shownKeys), arriving: new Set() });
  if (displayedPath !== null) {
    if (arrival.path !== displayedPath) {
      setArrival({ path: displayedPath, keys: new Set(shownKeys), arriving: new Set() });
    } else if (
      shownKeys.length !== arrival.keys.size ||
      shownKeys.some((key) => !arrival.keys.has(key))
    ) {
      setArrival({
        path: displayedPath,
        keys: new Set(shownKeys),
        arriving: arrivingKeys(arrival.keys, shownKeys),
      });
    }
  }

  // A change to the library applies by itself while the view is a single page:
  // new Models ease in where they belong and a change elsewhere only moves the
  // counts. Deeper in the list the notice stays, since reloading every page
  // read so far on each change would move the place being read.
  const settlesByItself =
    authority.refreshRequired &&
    !modelQuery.refreshRequired &&
    currentRefresh === null &&
    !selectMode &&
    (snapshot?.modelPages ?? 0) <= 1 &&
    (snapshot?.folderPages ?? 0) <= 1;
  const lastSettled = useRef(0);
  // The timer must survive re-renders, so it reads the latest refresh through a
  // ref instead of depending on an object that is new on every render.
  const settleNow = useRef<() => void>(() => undefined);
  useLayoutEffect(() => {
    settleNow.current = () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.outliner });
      void authority.refresh();
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

  useEffect(() => {
    if (browseReady) settleStartup("cards", "ready");
    else if (error !== null) settleStartup("cards", "failed");
  }, [browseReady, error, settleStartup]);
  const startupContent = useRef<HTMLElement>(null);
  const listContent = useRef<HTMLDivElement>(null);
  const readingRenderers = useRef(new Map<string, LibraryReadingRenderer>());
  const reading = useLibraryReadingPosition(
    snapshot?.entry,
    viewMode,
    startupContent,
    listContent,
    snapshot !== null && docView === "models" && !authority.authorizationChanged,
    {
      ready: browseReady,
      models: {
        count: snapshot?.modelPages ?? 0,
        more: modelQuery.hasNextPage ?? false,
        pending: modelQuery.isFetching,
        failed: modelQuery.isError,
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
      const denied = lookupPending && parseApiError(error).status === 404;
      if (denied) setSettledSnapshot(null);
      setRefreshState({ entry, status: denied ? "denied" : "failed", error: userMessage(error) });
    }
  }

  // Multi-select for batch actions. The selected set is view-independent so it
  // survives load-more and search; backend per-model RBAC makes cross-collection
  // selections safe. We clear it when navigating folders (see below) so a hidden
  // off-screen selection doesn't linger.

  function toggleStructuredFilters() {
    startup.request("filters");
    setFiltersExpanded(
      (current) => !(current ?? activeFilterItems.length > Number(!!query.trim())),
    );
  }

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
  const toggleSelect = useCallback((id: number, range = false) => {
    // Capture this gesture once; React may replay the state updater.
    const sortedModels = selectionOrder.current;
    const anchor = lastSelectedModelId.current;
    const selectingRange = range && anchor !== null;
    const from = selectingRange ? sortedModels.findIndex((model) => model.id === anchor) : -1;
    const to = sortedModels.findIndex((model) => model.id === id);
    const rangeModels =
      from >= 0 && to >= 0 ? sortedModels.slice(Math.min(from, to), Math.max(from, to) + 1) : [];
    lastSelectedModelId.current = id;
    for (const model of rangeModels) selectedModelSnapshot.current.set(model.id, model);
    const model = sortedModels.find((item) => item.id === id);
    if (model) selectedModelSnapshot.current.set(id, model);
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (selectingRange) rangeModels.forEach((model) => next.add(model.id));
      else if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

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
  }, []);

  function selectAllVisible() {
    setSelectedMultipartRows(new Map(visibleMultipartModels.map((item) => [item.id, item])));
    setSelectedIds(new Set(sortedModels.map((m) => m.id)));
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
      setSelectedIds(new Set(all.map((model) => model.id)));
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
    selectedIds.size + selectedCollectionRows.size + selectedMultipartRows.size;

  useEffect(() => {
    function onShortcut(event: KeyboardEvent) {
      const typing =
        event.target instanceof HTMLElement &&
        event.target.matches("input, textarea, select, [contenteditable=true]");
      if (event.key === "/" && !typing) {
        event.preventDefault();
        document.querySelector<HTMLInputElement>("[data-model-search]")?.focus();
      } else if (event.key.toLowerCase() === "s" && !typing && auth.isAuthenticated) {
        event.preventDefault();
        setKeyboardSelection(true);
      } else if (event.key === "Escape" && selectMode && !typing) clearSelection();
    }
    window.addEventListener("keydown", onShortcut);
    return () => window.removeEventListener("keydown", onShortcut);
  }, [auth.isAuthenticated, clearSelection, selectMode]);

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
  const displayCount = libraryItems.length;
  // Links belong to the displayed result even while another destination loads.
  const currentLibraryHref = snapshot?.entry.href ?? canonicalLibraryHref;
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
        limit: PAGE_SIZE,
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
  useDialogPreload(startup.complete && browseReady);
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

  async function handleCreateCollection() {
    const name = newCollectionName.trim();
    if (!name) return;
    if (!auth.isAuthenticated) {
      auth.showAuthRequiredToast();
      return;
    }
    if (!canAdminSelectedCollection) {
      toast.warning(uiText("Admin access required"));
      return;
    }
    // Until the open folder has loaded, a new folder would land at the root.
    if (selectedCollection !== null && selectedCollectionRow === null) return;
    try {
      await createCollection({ name, parent_id: selectedCollectionRow?.id ?? null });
      setNewCollectionName("");
      setIsCreatingCollection(false);
      toast.success(uiText('Collection "{value1}" created', { value1: String(name) }));
    } catch (e: any) {
      toast.error(e);
    }
  }

  function handleMoveModel(source: ModelDrag, targetCollection: string | null) {
    if (!auth.isAuthenticated) {
      auth.showAuthRequiredToast();
      return;
    }
    void modelMoves.move(source, targetCollection);
  }

  async function handleMoveCollection(collectionId: number, newParentId: number | null) {
    if (!auth.isAuthenticated) {
      auth.showAuthRequiredToast();
      return;
    }
    try {
      await moveCollection(collectionId, newParentId);
      toast.success(uiText("Moved"));
      refresh();
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
    try {
      // The write invalidates every loaded collection query (request.ts).
      await replaceCollectionTags(collection.id, nextTags);
      toast.success(uiText("Tags updated for {value1}", { value1: String(collection.name) }));
    } catch (error) {
      toast.error(error);
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

  function selectViewMode(mode: ViewMode) {
    setViewMode(mode);
    localStorage.setItem("ps-vault-view", mode);
  }

  function selectDensity(isCompact: boolean) {
    setCompact(isCompact);
    localStorage.setItem("ps-vault-density", isCompact ? "compact" : "comfortable");
  }

  function clearSearch() {
    const params = new URLSearchParams(searchParams.toString());
    params.delete("q");
    const qs = params.toString();
    router.replace(qs ? `/?${qs}` : "/");
  }

  function clearAllFilters() {
    setSelectedIds(new Set());
    setSelectedMultipartRows(new Map());
    const params = new URLSearchParams(searchParams.toString());
    params.delete("q");
    params.delete("c");
    params.delete("cid");
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

  const savedViewControl = (
    <SavedViewSelector
      key={savedViewsOwner.session}
      railTrigger={savedViewsHost !== null}
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
        savedViewsHost
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

  const libraryActions = (
    <div
      role="group"
      aria-label={t("vault.libraryTools")}
      className="order-3 flex basis-full flex-wrap items-center gap-2 @3xl/library:order-2 @3xl/library:basis-auto"
    >
      <Button variant="outline" size="xs" className="h-10 @3xl/library:h-8" asChild>
        <Link href="/library/similar">
          <ScanSearch className="h-4 w-4 shrink-0" aria-hidden />
          {t("similarity.title")}
        </Link>
      </Button>
      <Button
        variant="outline"
        size="xs"
        className="h-10 @3xl/library:h-8"
        disabled={!canCreateCollection}
        title={
          canAdminSelectedCollection
            ? uiText("Create a collection")
            : uiText("Admin access required for this collection")
        }
        onClick={handleOpenCreateCollection}
      >
        <Plus className="h-4 w-4 shrink-0" aria-hidden />
        {uiText("New collection")}
      </Button>
    </div>
  );

  const libraryToolbar = (
    <div className="flex min-w-0 flex-wrap items-center gap-2">
      <TabBar
        tabs={[
          { key: "models", label: t("vault.models") },
          { key: "docs", label: t("vault.documents") },
        ]}
        active={docView}
        onChange={(view) => {
          const params = new URLSearchParams(searchParams.toString());
          params.set("v", view);
          router.replace(params.size ? `/?${params}` : "/");
        }}
        className="order-0 w-full shrink-0 items-center @3xl/library:w-auto"
        tabClassName="px-2 py-1.5 text-sm font-medium text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        activeTabClassName="font-semibold text-foreground"
        showIndicator={false}
      />
      {docView === "models" && (
        <div className="order-1 flex w-full min-w-0 items-center gap-1 overflow-x-auto @3xl/library:w-auto @3xl/library:shrink-0">
          <Button
            type="button"
            variant="ghost"
            size="xs"
            aria-pressed={!structured.revision_status?.length}
            onClick={() => setStructuredFilter("revision_status", [])}
            className={!structured.revision_status?.length ? "bg-accent" : undefined}
          >
            {uiText("All")}
          </Button>
          {(["known_good", "needs_test"] as const).map((status) => {
            const facet = facetQuery.data?.revision_status.find((value) => value.value === status);
            if (!facet || facet.count === 0) return null;
            const active = structured.revision_status?.includes(status) ?? false;
            return (
              <Button
                key={status}
                type="button"
                variant="ghost"
                size="xs"
                aria-pressed={active}
                onClick={() => setStructuredFilter("revision_status", active ? [] : [status])}
                className={active ? "bg-accent" : undefined}
              >
                <span
                  className={cn(
                    "h-1.5 w-1.5 rounded-full",
                    status === "known_good" ? "bg-success" : "bg-warning",
                  )}
                />
                {uiText(status === "known_good" ? "Known Good" : "Needs Test")}
                <span className="font-mono text-3xs text-muted-foreground">{facet.count}</span>
              </Button>
            );
          })}
        </div>
      )}
      <Button
        type="button"
        variant="outline"
        onClick={openDrawer}
        className="order-1 h-10 min-w-0 flex-1 px-2 md:hidden"
      >
        <SlidersHorizontal className="h-4 w-4 text-muted-foreground" />
        <span className="min-w-0 truncate">{uiText("Filters")}</span>
      </Button>
      <Button
        type="button"
        variant="outline"
        size="xs"
        onClick={toggleStructuredFilters}
        aria-expanded={filtersExpanded ?? activeFilterItems.length > Number(!!query.trim())}
        aria-controls="library-filter-panel"
        className="order-1 hidden h-10 min-w-0 flex-1 px-2 md:inline-flex @3xl/library:h-8 @3xl/library:flex-none @3xl/library:px-3"
      >
        <SlidersHorizontal className="h-4 w-4 text-muted-foreground" />
        <span className="min-w-0 truncate">{uiText("Filters")}</span>
        {activeFilterItems.length > Number(!!query.trim()) && (
          <span className="font-mono tabular-nums">
            {activeFilterItems.length - Number(!!query.trim())}
          </span>
        )}
      </Button>
      <div className="order-2 grid min-w-0 flex-2 grid-cols-2 gap-2 @3xl/library:hidden">
        <SortMenu
          open={sortOpen}
          onOpenChange={setSortOpen}
          sortKey={sortKey}
          onSelect={selectSort}
          labelFor={ui}
          triggerSize="sm"
          triggerClassName="h-10 w-full min-w-0 px-2 text-xs"
          wrapperClassName="min-w-0"
        />
        <DropdownMenu
          open={moreOpen}
          onOpenChange={setMoreOpen}
          align="end"
          className="min-w-0"
          trigger={
            <Button
              type="button"
              variant="outline"
              data-menu-trigger
              aria-haspopup="menu"
              aria-expanded={moreOpen}
              aria-label={t("nav.more")}
              onClick={() => setMoreOpen(!moreOpen)}
              className="w-full min-w-0 px-2"
            >
              <MoreHorizontal className="h-4 w-4 shrink-0" />
              <span className="min-w-0 flex-1 truncate">{t("nav.more")}</span>
              <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            </Button>
          }
          contentClassName="w-64 rounded border border-border bg-popover p-1 text-popover-foreground shadow-lg"
        >
          {docView === "models" && (
            <Button
              variant="ghost"
              role="menuitem"
              className="w-full justify-start"
              disabled={!user?.is_superuser && !canWriteCollection(selectedCollectionRow)}
              onClick={() => {
                setMoreOpen(false);
                setMultipartCreateOpen(true);
              }}
            >
              <Plus className="h-4 w-4" aria-hidden />
              {t("multipart.new")}
            </Button>
          )}
          {auth.isAuthenticated && (
            <>
              <button
                type="button"
                role="menuitemcheckbox"
                aria-checked={favoritesOnly}
                onClick={() => {
                  toggleFavorites();
                  setMoreOpen(false);
                }}
                className={`flex w-full items-center gap-2 rounded px-2.5 py-2 text-left text-sm transition-colors hover:bg-popover-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${favoritesOnly ? "bg-accent text-accent-foreground" : ""}`}
              >
                <Star className={`h-4 w-4 ${favoritesOnly ? "fill-current" : ""}`} />
                <span className="flex-1">{uiText("Favorites")}</span>
                {favoritesOnly && <Check className="h-3.5 w-3.5" />}
              </button>
              <SavedViewSelector
                key={savedViewsOwner.session}
                readState={savedViewReadState}
                views={savedViews}
                activeId={activeSavedViewId}
                modified={savedViewModified}
                onSelect={(view) => {
                  applySavedView(view);
                  setMoreOpen(false);
                }}
                onCreate={() => {
                  setMoreOpen(false);
                  openSaveView();
                }}
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
                triggerClassName="max-w-none w-full justify-start px-2.5 py-2 text-sm"
                triggerRole="menuitem"
                triggerSize="sm"
                triggerVariant="ghost"
              />
            </>
          )}
          <DisplayMenu
            open={displayOpen}
            onOpenChange={setDisplayOpen}
            viewMode={viewMode}
            compact={compact}
            onSelectMode={selectViewMode}
            onSelectDensity={selectDensity}
            labelFor={ui}
            triggerSize="sm"
            triggerVariant="ghost"
            triggerRole="menuitem"
            triggerClassName="w-full justify-start"
            onSelectComplete={() => setMoreOpen(false)}
          />
        </DropdownMenu>
      </div>
      {libraryActions}
      {docView === "models" && (
        <Button
          type="button"
          variant="outline"
          size="xs"
          onClick={() => setMultipartCreateOpen(true)}
          disabled={!user?.is_superuser && !canWriteCollection(selectedCollectionRow)}
          title={
            user?.is_superuser || canWriteCollection(selectedCollectionRow)
              ? undefined
              : t("multipart.editAccess")
          }
          className="order-3 hidden h-8 @3xl/library:inline-flex"
        >
          <Plus className="h-4 w-4" aria-hidden />
          {t("multipart.new")}
        </Button>
      )}

      <div className="order-4 ml-auto hidden shrink-0 items-center gap-2 @3xl/library:flex">
        <SortMenu
          open={desktopSortOpen}
          onOpenChange={setDesktopSortOpen}
          sortKey={sortKey}
          onSelect={selectSort}
          labelFor={ui}
          triggerSize="xs"
          triggerClassName="h-8 w-40"
        />
        <div className="flex items-center bg-muted/50">
          {(["grid", "list"] as const).map((mode) => (
            <Button
              key={mode}
              type="button"
              variant="ghost"
              size="icon-sm"
              aria-label={uiText("{value1} View", {
                value1: ui(mode === "grid" ? "Grid" : "List"),
              })}
              aria-pressed={viewMode === mode}
              onClick={() => selectViewMode(mode)}
              className={
                viewMode === mode ? "bg-accent text-accent-foreground" : "text-muted-foreground"
              }
            >
              {mode === "grid" ? (
                <Grid className="h-3.5 w-3.5" />
              ) : (
                <List className="h-3.5 w-3.5" />
              )}
            </Button>
          ))}
        </div>
        <DisplayMenu
          open={desktopDisplayOpen}
          onOpenChange={setDesktopDisplayOpen}
          viewMode={viewMode}
          compact={compact}
          onSelectMode={selectViewMode}
          onSelectDensity={selectDensity}
          labelFor={ui}
          triggerSize="xs"
          triggerClassName="h-8 [&>span]:sr-only [&>svg:last-child]:hidden"
        />
      </div>
    </div>
  );

  return (
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
            key={selectedCollectionRow?.id ?? "vault"}
            open={multipartCreateOpen}
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
            collectionsOnly={pane !== null}
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
            onCollectionIntent={prefetchFolder}
            onTagsChange={setSelectedTags}
            onPrinterChange={setSelectedPrinterId}
            onPrinterPresenceChange={setSelectedPrinterPresence}
            onCreateCollection={handleOpenCreateCollection}
            onMoveModel={handleMoveModel}
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
                      "h-8 w-full justify-start px-3 text-sm",
                      favoritesOnly && "bg-accent text-accent-foreground",
                    )}
                  >
                    <Star className={cn("h-4 w-4", favoritesOnly && "fill-current")} aria-hidden />
                    {uiText("Favorites")}
                  </Button>
                  {!savedViewsHost && savedViewControl}
                </>
              ) : null
            }
            libraryView={libraryView}
            onLibraryViewChange={handleLibraryViewChange}
          />
        </LibraryCollectionsPane>
        {savedViewsHost && auth.isAuthenticated && createPortal(savedViewControl, savedViewsHost)}

        <main
          ref={startupContent}
          aria-busy={reading.status === "restoring"}
          className="@container/library relative flex min-w-0 flex-1 flex-col overflow-y-auto bg-background pb-24 md:pb-0"
          onDragEnter={onMainDragEnter}
          onDragOver={onMainDragOver}
          onDragLeave={onMainDragLeave}
          onDrop={onMainDrop}
        >
          {isDragging && canUploadToVault && (
            <div className="pointer-events-none absolute inset-0 z-40 flex items-center justify-center border-2 border-dashed border-primary bg-primary/5">
              <span className="bg-background border border-border rounded px-4 py-2 font-mono text-xs uppercase tracking-widest shadow">
                {uiText("Drop to upload")}
              </span>
            </div>
          )}
          {/* Breadcrumb */}
          <LibraryBreadcrumbs>
            <nav
              className={cn(
                "flex min-w-0 flex-1 items-center gap-2 text-sm tracking-tight",
                !pane && "border-b border-border bg-background px-4 py-2.5 sm:px-6",
              )}
            >
              {breadcrumbs.length > 0 ? (
                <>
                  <button
                    onClick={() => handleCollectionChange(null)}
                    className="shrink-0 text-muted-foreground transition-colors hover:text-foreground"
                  >
                    {uiText("All Models")}
                  </button>
                  {breadcrumbs.map((crumb) => (
                    <span key={crumb.id} className="flex min-w-0 items-center gap-2">
                      <ChevronRight className="h-3 w-3 shrink-0 text-muted-foreground/40" />
                      <button
                        onClick={() => handleCollectionChange(crumb.path)}
                        className="min-w-0 truncate font-medium text-foreground"
                        title={crumb.name}
                      >
                        {crumb.name}
                      </button>
                    </span>
                  ))}
                </>
              ) : (
                <button
                  onClick={() => handleCollectionChange(null)}
                  className="text-foreground font-medium"
                >
                  {uiText("All Models")}
                </button>
              )}
              {availableRecentFolders.length > 0 && (
                <DropdownMenu
                  className="ml-auto shrink-0"
                  open={recentFoldersOpen}
                  onOpenChange={setRecentFoldersOpen}
                  align="end"
                  trigger={
                    <button
                      type="button"
                      data-menu-trigger
                      aria-haspopup="menu"
                      aria-expanded={recentFoldersOpen}
                      aria-label={uiText("Recent")}
                      onClick={() => setRecentFoldersOpen(!recentFoldersOpen)}
                      className="ml-auto flex items-center gap-1.5 rounded px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                    >
                      <History className="h-3.5 w-3.5" aria-hidden />
                      <span className="hidden sm:inline">{ui("Recent")}</span>
                    </button>
                  }
                  contentClassName="w-64 max-w-[calc(100vw-2rem)] rounded border border-border bg-popover p-1 text-popover-foreground shadow-lg"
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
            </nav>
          </LibraryBreadcrumbs>

          {/* The pane owns breakpoints: an open outliner also consumes horizontal room. */}
          <div className="flex flex-col gap-3 px-4 pb-1 pt-6 sm:px-6">
            <div className="flex min-w-0 items-start justify-between gap-4">
              <div className="min-w-0 flex-1 space-y-1">
                <TitleEditor
                  open={collectionRename !== null}
                  onOpenChange={(open) => {
                    if (open) openCollectionRename();
                    else if (!collectionRenameBusy) setCollectionRename(null);
                  }}
                  label={uiText("Rename {value1}", { value1: selectedName ?? "" })}
                  fieldLabel={uiText("inbox.collectionName")}
                  value={collectionRenameName}
                  onValueChange={setCollectionRenameName}
                  onSave={() => void saveCollectionRename()}
                  busy={collectionRenameBusy}
                  maxLength={100}
                  requiredMessage={uiText("{field} is required.", {
                    field: uiText("inbox.collectionName"),
                  })}
                  trigger={
                    <div className="flex min-w-0 items-center gap-1">
                      <h1
                        onDoubleClick={openCollectionRename}
                        className="min-w-0 break-words text-lg font-bold tracking-tight text-foreground sm:text-2xl"
                      >
                        {unavailableCollection
                          ? uiText("library.collectionUnavailable")
                          : (selectedName ?? uiText(favoritesOnly ? "Favorites" : "All Models"))}
                      </h1>
                      {canRenameCollection && (
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon-sm"
                          className="shrink-0 text-muted-foreground"
                          data-menu-trigger
                          aria-haspopup="dialog"
                          aria-expanded={collectionRename !== null}
                          aria-label={uiText("Rename {value1}", { value1: selectedName ?? "" })}
                          title={uiText("Rename")}
                          onClick={openCollectionRename}
                        >
                          <Pencil className="h-3.5 w-3.5" aria-hidden />
                        </Button>
                      )}
                    </div>
                  }
                />
                <p className="text-sm text-muted-foreground">
                  {unavailableCollection
                    ? uiText("library.collectionUnavailableHelp")
                    : loading
                      ? uiText("Loading...")
                      : uiText(selectedName ? "counts.collectionItems" : "counts.items", {
                          count: displayCount,
                        })}
                  {refreshing && (
                    <span className="ml-2 font-mono text-xs text-muted-foreground">
                      {uiText("Updating...")}
                    </span>
                  )}
                </p>
              </div>
              {!pane && (
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
                  {uiText("Upload")}
                </Button>
              )}
            </div>
          </div>

          {(filtersExpanded ?? activeFilterItems.length > Number(!!query.trim())) && (
            <div
              id="library-filter-panel"
              role="region"
              aria-label={uiText("Filters")}
              className="hidden border-b border-border bg-muted/30 px-4 py-3 md:block sm:px-6"
              onFocusCapture={() => startup.request("filters")}
              onPointerDownCapture={() => startup.request("filters")}
            >
              <StructuredFilters
                layout="inline"
                extraControls={
                  <LibraryBaseFilters
                    tags={tags}
                    printers={printers}
                    selectedTags={selectedTags}
                    selectedPrinterId={selectedPrinterId}
                    selectedPrinterPresence={selectedPrinterPresence}
                    onTagsChange={setSelectedTags}
                    onPrinterChange={setSelectedPrinterId}
                    onPrinterPresenceChange={setSelectedPrinterPresence}
                    canViewPrinters={canViewPrinters}
                  />
                }
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
            </div>
          )}

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
              <Button type="button" variant="ghost" size="xs" onClick={clearAllFilters}>
                {uiText("Clear all")}
              </Button>
            </div>
          )}

          {selectedCollection !== null && !displayedCollection ? (
            <CollectionHeaderSkeleton />
          ) : (
            displayedCollection && (
              <div className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-2 border-b border-border px-4 py-2 sm:px-6">
                <EntityTagsDialog
                  entityLabel={displayedCollection.name}
                  tags={displayedCollection.tags}
                  availableTags={tags}
                  canEdit={!!user?.is_superuser || canWriteCollection(displayedCollection)}
                  help={uiText(
                    "Collection tags are inherited by Models in this collection and every descendant for search and filtering.",
                  )}
                  onSave={(nextTags) => saveCollectionTags(displayedCollection, nextTags)}
                />
                <CollectionReadme
                  key={displayedCollection.id}
                  collectionId={displayedCollection.id}
                  hasReadme={displayedCollection.has_readme}
                  canEdit={!!user?.is_superuser || canWriteCollection(displayedCollection)}
                  disabled={!browseReady}
                />
              </div>
            )
          )}

          {isCreatingCollection && (
            <div className="px-6 py-3 bg-muted border-b border-border">
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  handleCreateCollection();
                }}
                className="flex items-center gap-2"
              >
                <input
                  autoFocus
                  value={newCollectionName}
                  onChange={(e) => setNewCollectionName(e.target.value)}
                  placeholder={
                    auth.isAuthenticated
                      ? selectedCollection
                        ? uiText('New subcollection in "{value1}"...', {
                            value1: String(selectedName ?? selectedCollection),
                          })
                        : uiText("Collection name...")
                      : uiText("Sign in to add")
                  }
                  disabled={!auth.isAuthenticated}
                  className="flex-1 max-w-xs bg-background text-foreground text-sm border border-border rounded px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent disabled:opacity-50"
                />
                <button
                  type="submit"
                  disabled={
                    !newCollectionName.trim() || !auth.isAuthenticated || !canCreateCollection
                  }
                  className="px-3 py-1.5 text-xs font-medium text-primary-foreground bg-primary rounded hover:bg-primary-hover transition-colors disabled:opacity-50"
                >
                  {uiText("Create")}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setIsCreatingCollection(false);
                    setNewCollectionName("");
                  }}
                  className="px-3 py-1.5 text-xs font-medium text-foreground bg-background border border-border rounded hover:bg-muted transition-colors"
                >
                  {uiText("Cancel")}
                </button>
              </form>
            </div>
          )}

          {selectMode && (
            <div className="px-4 sm:px-6 py-2 bg-muted border-b border-border flex flex-wrap items-center gap-3 text-xs">
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
          )}

          {docView === "docs" && <div className="px-4 py-3 sm:px-6">{libraryToolbar}</div>}

          {/* Content */}
          {docView === "docs" && (selectedCollection === null || selectedCollectionRow !== null) ? (
            <DocumentBrowser
              collectionId={selectedCollectionRow?.id ?? null}
              collectionPath={selectedCollection}
              canCreate={!!user?.is_superuser || canWriteCollection(selectedCollectionRow)}
            />
          ) : (
            <div className="flex-1 flex flex-col bg-background">
              {reading.status === "reset" && (
                <p
                  role="status"
                  className="mx-6 mt-4 rounded-md border border-border bg-muted p-3 text-sm"
                >
                  {uiText("library.readingPositionReset")}
                </p>
              )}
              {refreshRequired && !settlesByItself && (
                <div
                  role="status"
                  className="mx-6 mt-4 flex items-center justify-between gap-3 rounded-md border border-border bg-muted p-3 text-sm"
                >
                  <p>{uiText("library.updatesAvailable")}</p>
                  <Button
                    variant="outline"
                    size="sm"
                    loading={refreshing}
                    onClick={() =>
                      void (currentRefresh || modelQuery.refreshRequired
                        ? snapshot?.entry === entry
                          ? reading.refresh(refresh)
                          : refresh()
                        : authority.refresh())
                    }
                  >
                    {uiText("library.refresh")}
                  </Button>
                </div>
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
                <div className="mx-6 mt-4 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
                  {unavailableCollection ? uiText("library.collectionUnavailableHelp") : error}
                  {unavailableCollection && (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => handleCollectionChange(null)}
                    >
                      {uiText("aiSearch.backToLibrary")}
                    </Button>
                  )}
                  {currentRefresh && currentRefresh.status !== "pending" && (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => void (snapshot ? reading.refresh(refresh) : refresh())}
                    >
                      {uiText("Retry")}
                    </Button>
                  )}
                </div>
              )}

              {viewMode === "grid" && !loading && visibleCollections.length > 0 && (
                <section className="mx-4 mb-3 mt-5 sm:mx-6" aria-label={uiText("Collections")}>
                  <h2 className="mb-2 text-xs font-semibold text-muted-foreground">
                    {uiText("Collections")}{" "}
                    <span className="font-mono font-normal">{visibleCollections.length}</span>
                  </h2>
                  <LibraryItems
                    items={visibleCollections}
                    itemKey={(collection) => `collection:${collection.id}`}
                    renderItem={(collection) => (
                      <CollectionFolderCard
                        key={collection.id}
                        collection={collection}
                        arriving={arrival.arriving.has(`c:${collection.id}`)}
                        onSelect={handleCollectionChange}
                        onIntent={prefetchFolder}
                        onDropModel={canUploadToVault ? handleMoveModel : undefined}
                        selectable={selectMode}
                        selected={selectedCollectionRows.has(collection.id)}
                        onToggleSelect={auth.isAuthenticated ? toggleCollectionSelect : undefined}
                      />
                    )}
                    scrollRef={startupContent}
                    readingRenderers={readingRenderers}
                    readingId="collections"
                    layout="grid"
                    compact={compact}
                    estimateSize={82}
                  />
                </section>
              )}

              <div className="px-4 pb-0 pt-3 sm:px-6">{libraryToolbar}</div>

              {loading ? (
                viewMode === "grid" ? (
                  <ModelGridSkeleton compact={compact} />
                ) : (
                  <ModelListSkeleton />
                )
              ) : error && snapshot === null ? null : sortedModels.length === 0 &&
                visibleMultipartModels.length === 0 &&
                visibleCollections.length === 0 &&
                !hasMore ? (
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
                      <Button type="button" variant="outline" size="xs" onClick={toggleFavorites}>
                        {uiText("aiSearch.backToLibrary")}
                      </Button>
                    ) : hasActiveFilters ? (
                      <Button type="button" variant="outline" size="xs" onClick={clearAllFilters}>
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
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          disabled={!canUploadToVault}
                          onClick={() => {
                            setDropPreload({ files: [], mode: "url" });
                            setUploadOpen(true);
                          }}
                        >
                          {uiText("Import from URL")}
                        </Button>
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
              ) : viewMode === "grid" ? (
                <div
                  key={`grid:${folderEntrance.path ?? ""}`}
                  className={compact ? "p-3" : "p-4 sm:p-6"}
                >
                  <LibraryItems
                    items={libraryItems}
                    itemKey={(item) =>
                      item.kind === "model"
                        ? `/models/${item.value.id}`
                        : `/multipart-models/${item.value.id}`
                    }
                    renderItem={(item) =>
                      item.kind === "multipart" ? (
                        <MultipartModelCard
                          key={`multipart-${item.value.id}`}
                          item={item.value}
                          arriving={arrival.arriving.has(`p:${item.value.id}`)}
                          collectionLabel={item.value.collection_label}
                          returnTo={currentLibraryHref}
                          origin={snapshot?.entry}
                          availableTags={tags}
                          onDataChange={refresh}
                          onEditTags={openMultipartTagEditor}
                          selectable={selectMode}
                          selected={selectedMultipartRows.has(item.value.id)}
                          onToggleSelect={auth.isAuthenticated ? toggleMultipartSelect : undefined}
                        />
                      ) : (
                        <ModelCard
                          key={item.value.id}
                          model={item.value}
                          origin={snapshot?.entry}
                          arriving={arrival.arriving.has(`m:${item.value.id}`)}
                          collectionLabel={item.value.collection_label}
                          selectable={selectMode}
                          selected={selectedIds.has(item.value.id)}
                          onToggleSelect={auth.isAuthenticated ? toggleSelect : undefined}
                          draggable={canUploadToVault && !selectMode}
                          onEditTags={openTagEditor}
                          onIntent={warmModel}
                        />
                      )
                    }
                    scrollRef={startupContent}
                    readingRenderers={readingRenderers}
                    readingId="models"
                    layout="grid"
                    compact={compact}
                    className={
                      folderEntrance.move ? FOLDER_CARDS_CLASS[folderEntrance.move] : undefined
                    }
                  />
                  <LoadMore
                    paused={reading.status === "restoring"}
                    hasMore={snapshot?.moreFolders ?? false}
                    loading={fetchingFolders || currentRefresh !== null}
                    failed={folderPages.isFetchNextPageError}
                    onClick={() => {
                      if (currentRefresh === null) void fetchMoreFolders();
                    }}
                    folders
                  />
                  <LoadMore
                    paused={reading.status === "restoring"}
                    hasMore={hasMore && !refreshRequired}
                    loading={loadingMore}
                    failed={modelQuery.isFetchNextPageError}
                    onClick={loadMore}
                  />
                </div>
              ) : (
                <div
                  key={`list:${folderEntrance.path ?? ""}`}
                  ref={listContent}
                  data-library-list
                  className={`${folderEntrance.move ? FOLDER_MOVE_CLASS[folderEntrance.move] : ""} flex-1 overflow-y-auto`}
                >
                  <div className="flex flex-col">
                    <div className="flex items-center gap-3 px-4 py-2 border-b border-border text-xs font-mono text-muted-foreground uppercase tracking-wider bg-muted/50">
                      {auth.isAuthenticated && <span className="w-5 shrink-0" aria-hidden />}
                      <span className="w-10 flex-shrink-0">{uiText("Thumb")}</span>
                      <span className="flex-1">{uiText("Name")}</span>
                      <span className="w-24 text-right hidden sm:block">
                        {uiText("Collection")}
                      </span>
                      <span className="w-20 text-right">{uiText("Contents")}</span>
                      <span className="w-24 text-right hidden md:block">{uiText("Updated")}</span>
                    </div>
                    <LibraryItems
                      items={visibleCollections}
                      itemKey={(collection) => `collection:${collection.id}`}
                      renderItem={(collection) => (
                        <CollectionListRow
                          key={collection.id}
                          collection={collection}
                          displayPath={collection.display_path}
                          onSelect={handleCollectionChange}
                          onIntent={prefetchFolder}
                          onDropModel={canUploadToVault ? handleMoveModel : undefined}
                          selectable={selectMode}
                          selected={selectedCollectionRows.has(collection.id)}
                          onToggleSelect={auth.isAuthenticated ? toggleCollectionSelect : undefined}
                        />
                      )}
                      scrollRef={startupContent}
                      readingRenderers={readingRenderers}
                      readingId="collections"
                      layout="list"
                      estimateSize={65}
                    />
                    <LoadMore
                      paused={reading.status === "restoring"}
                      hasMore={snapshot?.moreFolders ?? false}
                      loading={fetchingFolders || currentRefresh !== null}
                      failed={folderPages.isFetchNextPageError}
                      onClick={() => {
                        if (currentRefresh === null) void fetchMoreFolders();
                      }}
                      folders
                    />
                    <LibraryItems
                      items={libraryItems}
                      itemKey={(item) =>
                        item.kind === "model"
                          ? `/models/${item.value.id}`
                          : `/multipart-models/${item.value.id}`
                      }
                      renderItem={(item) =>
                        item.kind === "multipart" ? (
                          <MultipartModelListRow
                            key={`multipart-${item.value.id}`}
                            selectable={selectMode}
                            selected={selectedMultipartRows.has(item.value.id)}
                            onToggleSelect={
                              auth.isAuthenticated ? toggleMultipartSelect : undefined
                            }
                            item={item.value}
                            collectionLabel={item.value.collection_label}
                            returnTo={currentLibraryHref}
                            origin={snapshot?.entry}
                          />
                        ) : (
                          <ModelListRow
                            key={item.value.id}
                            model={item.value}
                            origin={snapshot?.entry}
                            collectionLabel={item.value.collection_label}
                            selectable={selectMode}
                            selected={selectedIds.has(item.value.id)}
                            onToggleSelect={auth.isAuthenticated ? toggleSelect : undefined}
                            draggable={canUploadToVault && !selectMode}
                          />
                        )
                      }
                      scrollRef={startupContent}
                      readingRenderers={readingRenderers}
                      readingId="models"
                      layout="list"
                    />
                  </div>
                  <LoadMore
                    paused={reading.status === "restoring"}
                    hasMore={hasMore && !refreshRequired}
                    loading={loadingMore}
                    failed={modelQuery.isFetchNextPageError}
                    onClick={loadMore}
                  />
                </div>
              )}
            </div>
          )}
        </main>

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
        {docView === "models" && (
          <BatchToolbar
            modelCount={selectedIds.size}
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
            onClear={clearSelection}
          />
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
}

// Makes a collection card accept a dragged model: highlights on hover and calls
// onDropModel(source, path) on drop. Ignores OS file drags (no MODEL_DND_MIME)
// so those still bubble to the main upload handler.
function useModelDropTarget(path: string, onDropModel?: (source: ModelDrag, path: string) => void) {
  const [dragOver, setDragOver] = useState(false);
  const handlers = {
    onDragOver: (e: React.DragEvent) => {
      if (!onDropModel || !e.dataTransfer.types.includes(MODEL_DND_MIME)) return;
      e.preventDefault();
      e.stopPropagation();
      e.dataTransfer.dropEffect = "move";
      setDragOver(true);
    },
    onDragLeave: () => setDragOver(false),
    onDrop: (e: React.DragEvent) => {
      if (!onDropModel || !e.dataTransfer.types.includes(MODEL_DND_MIME)) return;
      e.preventDefault();
      e.stopPropagation();
      setDragOver(false);
      const source = readModelDrag(e.dataTransfer.getData(MODEL_DND_MIME));
      if (source) onDropModel(source, path);
    },
  };
  return { dragOver, handlers };
}

function CollectionFolderCard({
  collection,
  arriving = false,
  onSelect,
  onIntent,
  onDropModel,
  selectable,
  selected,
  onToggleSelect,
}: {
  collection: CollectionRead;
  arriving?: boolean;
  onSelect: (path: string) => void;
  /** The user is about to open this folder (hover or focus): warm its data. */
  onIntent?: (path: string | null) => void;
  onDropModel?: (source: ModelDrag, path: string) => void;
  selectable?: boolean;
  selected?: boolean;
  onToggleSelect?: (id: number) => void;
}) {
  useUiLocale();
  const { dragOver, handlers } = useModelDropTarget(collection.path, onDropModel);
  return (
    <Localized>
      <div
        role="button"
        tabIndex={0}
        data-collection-path={collection.path}
        data-library-anchor={`collection:${collection.id}`}
        onPointerEnter={() => !selectable && onIntent?.(collection.path)}
        onFocus={() => !selectable && onIntent?.(collection.path)}
        onPointerLeave={() => onIntent?.(null)}
        onBlur={() => onIntent?.(null)}
        onClick={() => (selectable ? onToggleSelect?.(collection.id) : onSelect(collection.path))}
        onKeyDown={(event) => {
          if (event.target !== event.currentTarget) return;
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            if (selectable) onToggleSelect?.(collection.id);
            else onSelect(collection.path);
          }
        }}
        {...handlers}
        className={`${arriving ? "animate-card-arrive" : ""} group group/selection-card flex min-h-20 flex-row items-center text-left bg-card border rounded transition-[border-color,box-shadow,transform] duration-fast active:scale-[0.99] relative overflow-hidden ${
          selected
            ? "border-primary bg-accent"
            : dragOver
              ? "border-primary ring-2 ring-primary-soft"
              : "border-border hover:border-primary"
        }`}
      >
        <div className="library-preview m-1.5 flex h-16 w-24 shrink-0 items-center justify-center border border-border">
          {onToggleSelect && (
            <LibraryCardSelection
              selecting={!!selectable}
              checked={!!selected}
              onChange={() => onToggleSelect(collection.id)}
              label={uiText("Select folder {value1}", { value1: String(collection.name) })}
            />
          )}
          <Folder className="h-7 w-7 text-primary/70" />
        </div>
        <div className="flex min-w-0 flex-1 flex-col-reverse gap-1 px-2 py-3">
          <div className="flex items-center gap-2">
            <span className="text-3xs text-muted-foreground font-mono">
              {uiText("{value1} models", { value1: String(collection.model_count) })}
            </span>
          </div>
          <p className="text-sm font-bold text-foreground truncate tracking-tight">
            {collection.name}
          </p>
        </div>
      </div>
    </Localized>
  );
}

function CollectionListRow({
  collection,
  displayPath,
  onSelect,
  onIntent,
  onDropModel,
  selectable,
  selected,
  onToggleSelect,
}: {
  collection: CollectionRead;
  displayPath: string | null;
  onSelect: (path: string) => void;
  /** The user is about to open this folder (hover or focus): warm its data. */
  onIntent?: (path: string | null) => void;
  onDropModel?: (source: ModelDrag, path: string) => void;
  selectable?: boolean;
  selected?: boolean;
  onToggleSelect?: (id: number) => void;
}) {
  useUiLocale();
  const { dragOver, handlers } = useModelDropTarget(collection.path, onDropModel);
  return (
    <Localized>
      <div
        role="button"
        tabIndex={0}
        data-collection-path={collection.path}
        data-library-anchor={`collection:${collection.id}`}
        onPointerEnter={() => !selectable && onIntent?.(collection.path)}
        onFocus={() => !selectable && onIntent?.(collection.path)}
        onPointerLeave={() => onIntent?.(null)}
        onBlur={() => onIntent?.(null)}
        onClick={() => (selectable ? onToggleSelect?.(collection.id) : onSelect(collection.path))}
        onKeyDown={(event) => {
          if (event.target !== event.currentTarget) return;
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            if (selectable) onToggleSelect?.(collection.id);
            else onSelect(collection.path);
          }
        }}
        {...handlers}
        className={`flex items-center gap-2 md:gap-3 px-4 py-3 border-b text-left transition-colors group ${
          selected
            ? "border-primary bg-accent"
            : dragOver
              ? "border-primary ring-2 ring-inset ring-primary-soft bg-muted"
              : "border-border hover:bg-muted"
        }`}
      >
        {onToggleSelect && (
          <Checkbox
            className="shrink-0"
            checked={!!selected}
            onChange={() => onToggleSelect?.(collection.id)}
            ariaLabel={uiText("Select folder {value1}", { value1: String(collection.name) })}
          />
        )}
        <span className="w-8 h-8 md:w-10 md:h-10 rounded bg-accent flex-shrink-0 border border-primary-soft flex items-center justify-center text-primary">
          <Folder className="h-4 w-4 md:h-5 md:w-5" />
        </span>
        <span className="flex-1 min-w-0">
          <span className="block text-sm font-medium text-foreground truncate">
            {collection.name}
          </span>
          <span className="block font-mono text-3xs text-muted-foreground truncate">
            {displayPath}
          </span>
        </span>
        <span className="w-24 text-right text-xs font-mono text-muted-foreground truncate hidden sm:block">
          {uiText("Folder")}
        </span>
        <span className="w-20 text-right text-xs font-mono text-muted-foreground">
          {collection.model_count}
        </span>
        <span className="w-24 hidden md:block" />
        <span className="w-8 flex justify-center">
          <ChevronRight className="h-4 w-4 text-muted-foreground/50 opacity-60 group-hover:opacity-100" />
        </span>
      </div>
    </Localized>
  );
}

function MultipartModelListRow({
  selectable,
  selected,
  onToggleSelect,
  item,
  collectionLabel,
  returnTo,
  origin,
}: {
  selectable: boolean;
  selected: boolean;
  onToggleSelect?: (id: number, range?: boolean) => void;
  item: MultipartModelListItem;
  collectionLabel: string | null;
  returnTo: string;
  origin?: LibraryEntry;
}) {
  useUiLocale();
  const { t } = useI18n();
  return (
    <LibraryItemLink
      origin={origin}
      href={`/multipart-models/${item.id}?${new URLSearchParams({ return: returnTo }).toString()}`}
      aria-label={item.name}
      onClick={(event) => {
        if (selectable) {
          event.preventDefault();
          onToggleSelect?.(item.id, event.shiftKey);
        }
      }}
      className={cn(
        "group flex items-center gap-2 border-b border-border px-4 py-3 transition-colors active:bg-muted md:gap-3",
        selected ? "bg-accent text-accent-foreground" : "hover:bg-muted",
      )}
    >
      {onToggleSelect && (
        <Checkbox
          className="shrink-0"
          checked={selected}
          onChange={() => onToggleSelect(item.id)}
          ariaLabel={uiText("Select {value1}", { value1: item.name })}
        />
      )}
      <ProtectedThumbnail
        path={item.cover_thumbnail_url}
        alt=""
        className="flex h-8 w-8 shrink-0 items-center justify-center overflow-hidden rounded border border-primary/30 bg-muted md:h-10 md:w-10"
        imageClassName="h-full w-full object-cover"
        placeholder={<Boxes className="h-4 w-4 text-primary" aria-hidden />}
      />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium text-foreground">{item.name}</span>
        <span className="mt-0.5 inline-flex rounded border border-primary/30 bg-card px-1.5 py-px font-mono text-3xs font-semibold uppercase tracking-wider text-primary">
          {t("multipart.badge")}
        </span>
      </span>
      <span className="hidden w-24 truncate text-right font-mono text-xs text-muted-foreground sm:block">
        {collectionLabel || "—"}
      </span>
      <span className="w-20 text-right font-mono text-xs text-muted-foreground">
        {item.model_count}
      </span>
      <span className="hidden w-24 text-right font-mono text-xs text-muted-foreground md:block">
        {timeAgo(item.updated_at)}
      </span>
    </LibraryItemLink>
  );
}

function ModelListRow({
  model,
  origin,
  collectionLabel,
  selectable = false,
  selected = false,
  onToggleSelect,
  draggable = false,
}: {
  model: ModelListItem;
  origin?: LibraryEntry;
  collectionLabel: string | null;
  selectable?: boolean;
  selected?: boolean;
  onToggleSelect?: (id: number, range?: boolean) => void;
  draggable?: boolean;
}) {
  useUiLocale();
  const printerPresence = model.printer_presence ?? [];
  return (
    <Localized>
      <LibraryItemLink
        origin={origin}
        href={`/models/${model.id}`}
        draggable={draggable}
        onDragStart={
          draggable
            ? (e) => {
                e.dataTransfer.setData(MODEL_DND_MIME, JSON.stringify(captureModelDrag(model)));
                e.dataTransfer.effectAllowed = "move";
              }
            : undefined
        }
        onClick={(e) => {
          if (selectable) {
            e.preventDefault();
            onToggleSelect?.(model.id, e.shiftKey);
          }
        }}
        className={`flex items-center gap-2 md:gap-3 px-4 py-3 border-b border-border transition-colors group active:bg-muted ${
          draggable ? "cursor-grab active:cursor-grabbing" : ""
        } ${selected ? "bg-accent" : "hover:bg-muted"}`}
      >
        {onToggleSelect && (
          <Checkbox
            className="shrink-0"
            checked={selected}
            onChange={() => onToggleSelect?.(model.id)}
            ariaLabel={uiText("Select {value1}", { value1: String(model.name) })}
          />
        )}
        <ProtectedThumbnail
          path={model.thumbnail_url}
          alt={model.name}
          className="w-8 h-8 md:w-10 md:h-10 rounded bg-muted flex-shrink-0 overflow-hidden border border-border"
          imageClassName="h-full w-full object-cover"
          placeholder={
            <div className="flex h-full w-full items-center justify-center">
              <FileText className="h-4 w-4 text-muted-foreground/50" />
            </div>
          }
        />
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-foreground truncate">{model.name}</p>
          {model.tags.length > 0 && (
            <div className="flex gap-1 mt-0.5">
              {model.tags.slice(0, 2).map((tag) => (
                <span
                  key={tag}
                  className="bg-accent text-accent-foreground px-1 py-px rounded font-mono text-3xs tracking-wider"
                >
                  {tag}
                </span>
              ))}
            </div>
          )}
          {printerPresence.length > 0 && (
            <div className="flex gap-1 mt-1">
              {printerPresence.slice(0, 2).map((p) => (
                <span
                  key={p.printer_id}
                  className="inline-flex items-center gap-1 rounded bg-emerald-50 px-1 py-px font-mono text-3xs uppercase tracking-wider text-emerald-600"
                >
                  <Printer className="h-3 w-3" />
                  {p.printer_name}
                </span>
              ))}
            </div>
          )}
        </div>
        <span className="w-24 text-right text-xs font-mono text-muted-foreground truncate hidden sm:block">
          {collectionLabel || "—"}
        </span>
        <span className="w-20 text-right text-xs font-mono text-muted-foreground">
          {model.file_count}
        </span>
        <span className="w-24 text-right text-xs font-mono text-muted-foreground hidden md:block">
          {timeAgo(model.updated_at)}
        </span>
      </LibraryItemLink>
    </Localized>
  );
}

/**
 * The next page of a view loads as the list is scrolled toward its end, so
 * a sentinel reaching up 600px above the end starts the read before the reader
 * gets there. The explicit button is always available. A failed read retires
 * the observer so a sentinel still in view cannot retry without end.
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
  const sentinel = useRef<HTMLDivElement>(null);
  const load = useRef(onClick);
  useLayoutEffect(() => {
    load.current = onClick;
  });
  const [awaitScroll, setAwaitScroll] = useState(paused);
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
    main.addEventListener("keydown", resume);
    return () => {
      main.removeEventListener("wheel", resume);
      main.removeEventListener("touchmove", resume);
      main.removeEventListener("keydown", resume);
    };
  }, [awaitScroll, paused]);
  const automatic = "IntersectionObserver" in globalThis && !failed && !paused && !awaitScroll;
  useEffect(() => {
    const element = sentinel.current;
    if (!automatic || !hasMore || loading || element === null) return;
    // Observing reports the current state at once, so a list shorter than the
    // reach keeps loading until it fills it.
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) load.current();
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [automatic, hasMore, loading]);
  if (!hasMore) return null;
  return (
    <Localized>
      <div className="relative flex justify-center mt-6 pb-6">
        {"IntersectionObserver" in globalThis && (
          <div
            ref={sentinel}
            data-load-more-sentinel
            aria-hidden
            className="pointer-events-none absolute bottom-0 h-[600px] w-full"
          />
        )}
        <button
          onClick={onClick}
          disabled={loading}
          className="px-4 py-2 rounded border border-border bg-background text-foreground hover:bg-muted disabled:opacity-50 font-mono text-[13px] uppercase tracking-wider transition-colors"
        >
          {loading
            ? uiText("Loading...")
            : folders
              ? uiText("Show more folders")
              : uiText("Load more")}
        </button>
      </div>
    </Localized>
  );
}

/** The tags row and description of a Collection, before its row and readme arrive. */
export function CollectionHeaderSkeleton() {
  useUiLocale();
  return (
    <div
      role="status"
      aria-label={uiText("Loading…")}
      className="flex flex-wrap items-center gap-4 border-b border-border px-4 py-2 sm:px-6"
    >
      <Skeleton className="h-8 w-28" />
      <Skeleton className="h-8 w-60 max-w-full" />
    </div>
  );
}

const MODEL_GRID_LAYOUT = {
  comfortable: "grid-cols-[repeat(auto-fill,minmax(min(100%,260px),1fr))] gap-3.5",
  compact: "grid-cols-[repeat(auto-fill,minmax(min(100%,200px),1fr))] gap-2",
};

export function ModelGridSkeleton({ compact = false }: { compact?: boolean }) {
  useUiLocale();
  return (
    <div role="status" aria-label={uiText("Loading...")} className={compact ? "p-3" : "p-4 sm:p-6"}>
      <div
        className={cn("grid", compact ? MODEL_GRID_LAYOUT.compact : MODEL_GRID_LAYOUT.comfortable)}
      >
        {Array.from({ length: 8 }).map((_, i) => (
          <div key={i} className="space-y-3 rounded-lg border border-border p-3 bg-card">
            <Skeleton className="h-40 w-full rounded" />
            <Skeleton className="h-4 w-3/4" />
            <Skeleton className="h-3 w-1/2" />
            <Skeleton className="h-12 w-full rounded" />
          </div>
        ))}
      </div>
    </div>
  );
}

function ModelListSkeleton() {
  useUiLocale();
  return (
    <div className="flex flex-col">
      {Array.from({ length: 6 }).map((_, i) => (
        <div key={i} className="flex items-center gap-3 px-4 py-3 border-b border-border">
          <Skeleton className="w-10 h-10 rounded flex-shrink-0" />
          <div className="flex-1 space-y-1">
            <Skeleton className="h-4 w-1/3" />
            <Skeleton className="h-3 w-1/4" />
          </div>
          <Skeleton className="h-4 w-16 hidden sm:block" />
          <Skeleton className="h-4 w-8" />
          <Skeleton className="h-4 w-16 hidden md:block" />
        </div>
      ))}
    </div>
  );
}
