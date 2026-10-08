"use client";

import { useTaxonomyCommands } from "@/features/library/taxonomy";

import { LibraryBatchRecovery } from "@/components/library-batch-recovery";

import { useLibraryReadingPosition } from "@/features/library/reading-position";
import { LibraryItemLink } from "@/features/library/navigation";
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
  sameLibraryFilters,
  structuredLibraryFilterKeys as STRUCTURED_FILTER_KEYS,
  type StructuredLibraryFilterKey as StructuredFilterKey,
} from "@/features/library/filters";

import { GettingStartedReminder } from "@/components/getting-started-reminder";

import { knownUiText, uiText, type MessageKey } from "@/lib/locale";
import { getErrorMessage, parseApiError, userMessage } from "@/lib/errors";
import { filterValueText } from "@/lib/filter-labels";

import { useUiLocale } from "@/lib/i18n";
import { useIntentPrefetch } from "@/lib/use-intent-prefetch";
import { useLibraryStartup } from "@/lib/library-startup-context";

import { useCallback, useEffect, useLayoutEffect, useMemo, useState, useRef } from "react";
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
import { DeferredDialog } from "@/components/deferred-dialog";
import { lazyImport } from "@/lib/lazy-component";
import { useModelMoves } from "@/features/library/moves";
import { ModelMoveReview } from "@/components/model-move-review";
import { MODEL_DND_MIME, captureModelDrag, readModelDrag, type ModelDrag } from "@/lib/model-dnd";
import { BatchToolbar } from "@/components/batch-toolbar";
import { Checkbox } from "@/components/ui/checkbox";
import { CollectionReadme } from "@/components/collection-readme";
import { MultipartModelCard } from "@/components/multipart-model-card";
import { EntityTagsDialog } from "@/components/entity-tags-dialog";
import { DocumentBrowser } from "@/components/document-browser";
import { FilterSidebar } from "@/components/filter-sidebar";
import { readLibraryLocation, type LibraryViewMode } from "@/features/library/url";
import { MobileFilterDrawer } from "@/components/mobile-filter-drawer";
import { StructuredFilters } from "@/components/structured-filters";
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
  CheckSquare,
  Star,
  ArrowUpDown,
  Rows3,
  History,
  Check,
  ChevronDown,
  MoreHorizontal,
  Boxes,
  ScanSearch,
} from "lucide-react";
import {
  listCollectionChildren,
  searchCollections,
  batchDeleteModels,
  restoreModel,
} from "@/lib/api";
import {
  isMeshFile,
  isGcodeFile,
  extensionOf,
  walkEntries,
  entriesFromDataTransfer,
  BulkItem,
} from "@/lib/bulk-upload";
import {
  useCollectionChildren,
  useCollectionLookup,
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

const UploadModal = lazyImport(() =>
  import("@/components/upload-modal").then((module) => ({ default: module.UploadModal })),
);
const ModelTagsDialog = lazyImport(() =>
  import("@/components/model-tags-dialog").then((module) => ({ default: module.ModelTagsDialog })),
);
const NewMultipartModelModal = lazyImport(() =>
  import("@/components/new-multipart-model-modal").then((module) => ({
    default: module.NewMultipartModelModal,
  })),
);

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

/** Bare paths, as builds before #295 wrote them; read when nothing newer is stored. */
const RECENT_FOLDERS_KEY = "ps-recent-folders";
const RECENT_FOLDERS_LABELLED_KEY = "ps-recent-folders-labelled";
const RECENT_FOLDERS_LIMIT = 6;
const LIBRARY_VIEW_KEY = "ps-vault-library-view";

function readVaultPreference(key: string): string | null {
  if (!("window" in globalThis)) return null;
  return localStorage.getItem(key);
}

/** A folder visited recently: its path to navigate to, its names to show. */
interface RecentFolder {
  path: string;
  label: string;
}

/** Until a folder's names load, it is shown by the last part of its path. */
function provisionalLabel(path: string): string {
  return path.split("/").at(-1) ?? path;
}

/** Decode stored JSON that should be an array, or nothing if it is not one. */
function readStoredArray(raw: string | null): readonly StoredEntry[] {
  if (raw === null) return [];
  try {
    // `JSON.parse` is `any`; the annotation is the boundary, checked just below.
    const decoded: StoredEntry = JSON.parse(raw);
    return Array.isArray(decoded) ? decoded : [];
  } catch {
    return [];
  }
}

/** A value JSON can hold: what edited or older storage may contain. */
type StoredEntry =
  | string
  | number
  | boolean
  | null
  | readonly StoredEntry[]
  | { readonly [key: string]: StoredEntry };

/**
 * The recent-folder list is a UI convenience written by this component, kept as
 * `[path, label]` pairs so the menu needs no collection list to name them.
 * Builds before labels were kept wrote bare paths under the older key; those
 * are read once, until the next write, with a provisional label.
 */
function readRecentFolders(): RecentFolder[] {
  const labelled = readVaultPreference(RECENT_FOLDERS_LABELLED_KEY);
  if (labelled !== null) {
    return readStoredArray(labelled).flatMap((entry) =>
      Array.isArray(entry) && entry.length === 2
        ? [{ path: String(entry[0]), label: String(entry[1]) }]
        : [],
    );
  }
  return readStoredArray(readVaultPreference(RECENT_FOLDERS_KEY)).map((entry) => ({
    path: String(entry),
    label: provisionalLabel(String(entry)),
  }));
}

function writeRecentFolders(folders: RecentFolder[]): void {
  if (!("window" in globalThis)) return;
  localStorage.setItem(
    RECENT_FOLDERS_LABELLED_KEY,
    JSON.stringify(folders.map((folder) => [folder.path, folder.label])),
  );
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
  const searchParams = useSearchParams();
  const auth = useRequireAuth();
  const { user, refresh: refreshAuth } = useAuth();
  const {
    createCollection,
    moveCollection,
    renameCollection,
    deleteCollection,
    replaceCollectionTags,
  } = useTaxonomyCommands();
  const startup = useLibraryStartup();
  const settleStartup = startup.settle;
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
  const selectedTags = currentFilters.tag;
  const selectedPrinterId = currentFilters.printer_id;
  const selectedPrinterPresence = currentFilters.printer_presence;
  const favoritesOnly = currentFilters.favorites;
  const canonicalLibraryHref = `/?${libraryFilters.params}`;
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
  const [moreOpen, setMoreOpen] = useState(false);
  const [libraryToolsOpen, setLibraryToolsOpen] = useState(false);
  const [filtersExpanded, setFiltersExpanded] = useState<boolean | null>(null);
  const [compact, setCompact] = useState(
    () => readVaultPreference("ps-vault-density") === "compact",
  );
  const [recentFolders, setRecentFolders] = useState<RecentFolder[]>(readRecentFolders);
  const [recentFoldersOpen, setRecentFoldersOpen] = useState(false);
  const [multipartCreateOpen, setMultipartCreateOpen] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [tagTarget, setTagTarget] = useState<ModelListItem | null>(null);
  const [tagDialogOpen, setTagDialogOpen] = useState(false);
  const [tagDialogSession, setTagDialogSession] = useState(0);
  // `?upload=1` is a deep link into the upload dialog. Open it on the render that
  // first sees the param; the effect below strips the param again so a reload (or
  // a later deep link) behaves the same way.
  const uploadRequested = searchParams.get("upload") === "1";
  const [uploadDeepLinkSeen, setUploadDeepLinkSeen] = useState(false);
  if (uploadDeepLinkSeen !== uploadRequested) {
    setUploadDeepLinkSeen(uploadRequested);
    if (uploadRequested) setUploadOpen(true);
  }
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
    const meshes = files.filter((f) => isMeshFile(f.name));
    const gcodes = files.filter((f) => isGcodeFile(f.name));
    const zips = files.filter((f) => extensionOf(f.name) === ".zip");
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
  const { open: filterDrawerOpen, openDrawer, closeDrawer } = useMobileFilterDrawer();

  // Collection selection lives in the URL (`?c=<path>`) so it resets when the
  // user navigates away (e.g. to Settings) and clicks "Vault" again — that link
  // points at "/" with no param. Deriving it straight from the param (instead of
  // mirroring into state) means a folder switch just re-keys the model query;
  // `keepPreviousData` holds the old cards on screen until the new page lands, so
  // there's no manual clearing or loading flash.
  const selectedCollection = currentFilters.collection;
  // Entering a folder pushes it onto the recent list on the render that first
  // sees the new `?c=`, so the list is never a navigation behind the URL.
  const [recordedCollection, setRecordedCollection] = useState<string | null>(null);
  if (recordedCollection !== selectedCollection) {
    setRecordedCollection(selectedCollection);
    if (selectedCollection !== null)
      setRecentFolders((current) => {
        const known = current.find((item) => item.path === selectedCollection);
        return [
          known ?? { path: selectedCollection, label: provisionalLabel(selectedCollection) },
          ...current.filter((item) => item.path !== selectedCollection),
        ].slice(0, RECENT_FOLDERS_LIMIT);
      });
  }

  useEffect(() => {
    // Remember the folder we're in so the logo / post-delete nav can return
    // here instead of resetting to the root once the `?c=` param is dropped.
    rememberLastCollection(selectedCollection);
  }, [selectedCollection]);

  useEffect(() => {
    writeRecentFolders(recentFolders);
  }, [recentFolders]);

  // Remember the active tab so the logo / Back return to it (e.g. opening a
  // document from the Documents tab and coming back).
  useEffect(() => {
    rememberLastView(docView);
  }, [docView]);

  function handleCollectionChange(path: string | null) {
    if (path === selectedCollection) return;
    setSelectedIds(new Set());
    const params = new URLSearchParams(searchParams.toString());
    if (path) params.set("c", path);
    else params.delete("c");
    const qs = params.toString();
    router.push(qs ? `/?${qs}` : "/");
  }

  function handleLibraryViewChange(view: LibraryViewMode) {
    localStorage.setItem(LIBRARY_VIEW_KEY, view);
    setSelectedIds(new Set());
    const params = new URLSearchParams(searchParams.toString());
    params.set("v", "models");
    params.set("type", view);
    router.replace(params.size ? `/?${params}` : "/");
  }

  useEffect(() => {
    if (!uploadRequested) return;
    const params = new URLSearchParams(searchParams.toString());
    params.delete("upload");
    const qs = params.toString();
    router.replace(qs ? `/?${qs}` : "/");
  }, [uploadRequested, searchParams, router]);

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
    // A search spans the whole library; a folder view lists only its direct
    // children so subfolders' models don't leak into the parent (#30).
    direct: !searchQuery,
    q: searchQuery,
  });
  const facetQuery = useModelFacets(folderModelFilters(), { enabled: filtersEnabled });

  function writeFilterUrl(filters: SavedViewRead["filters"]) {
    const params = writeLibraryFilters(filters, canViewPrinters);
    router.replace(`/?${params}`);
  }

  function applySavedView(view: SavedViewRead) {
    setFiltersExpanded(null);
    setActiveSavedViewId(view.id);
    setSelectedIds(new Set());
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
  const modelQuery = useLibraryBrowse(browseParams);

  const selectedLookup = useCollectionLookup(selectedCollection);
  const selectedCollectionRow =
    selectedLookup.data?.collection.path === selectedCollection
      ? selectedLookup.data.collection
      : null;
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
    (selectedCollection === null || selectedCollectionRow !== null) &&
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
  // Commit one coherent browsing result. Independent requests may settle in any
  // order; neither placeholder models nor a cached root folder page belongs to
  // a destination whose lookup/children have not completed yet.
  const nextSnapshot = useMemo(() => {
    if (!browseReady) return null;
    return {
      entry,
      items: orderedItems,
      modelPages: modelQuery.data?.pages.length ?? 0,
      folderPages: folderPages.data?.pages.length ?? 0,
      collections: folderPages.data?.pages.flatMap((page) => page.items) ?? [],
      collection: selectedCollectionRow,
      breadcrumbs:
        selectedCollectionRow && selectedLookup.data
          ? [...selectedLookup.data.ancestors, selectedCollectionRow]
          : [],
      hasMore: modelQuery.hasNextPage ?? false,
      moreFolders: folderPages.hasNextPage ?? false,
    };
  }, [
    browseReady,
    entry,
    orderedItems,
    folderPages.data,
    folderPages.hasNextPage,
    selectedCollectionRow,
    selectedLookup.data,
    modelQuery.hasNextPage,
    modelQuery.data?.pages.length,
  ]);
  const [settledSnapshot, setSettledSnapshot] = useState(nextSnapshot);
  if (nextSnapshot !== null && nextSnapshot !== settledSnapshot) setSettledSnapshot(nextSnapshot);
  const snapshot = currentRefresh?.status === "denied" ? null : (nextSnapshot ?? settledSnapshot);
  const displayedCollection = snapshot?.collection ?? null;
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
      (!browseReady || (modelQuery.isFetching && !modelQuery.isFetchingNextPage)));
  const loadingMore = modelQuery.isFetchingNextPage || !browseReady;
  const hasMore = snapshot?.hasMore ?? false;

  useEffect(() => {
    if (browseReady) settleStartup("cards", "ready");
    else if (error !== null) settleStartup("cards", "failed");
  }, [browseReady, error, settleStartup]);
  const startupContent = useRef<HTMLElement>(null);
  const listContent = useRef<HTMLDivElement>(null);
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
        more: folderPages.hasNextPage ?? false,
        pending: folderPages.isFetching,
        failed: folderPages.isError,
        next: () => void folderPages.fetchNextPage({ cancelRefetch: false }),
      },
    },
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
          queryKey: queryKeys.collectionLookup(selectedCollection),
          exact: true,
        }),
      ]);
      if (!isCurrent()) return;
      await queryClient.invalidateQueries({ queryKey: libraryBrowseKeys.all, refetchType: "none" });
      if (!isCurrent()) return;
      // Resolve the path first: a replacement folder may have a different id.
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
  const [selectMode, setSelectMode] = useState(false);
  const showLibraryTools = libraryToolsOpen;

  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  // Selected folders are kept as the rows they were picked from: they may leave
  // the view (another folder opened) and still need their names and paths.
  const [selectedCollectionRows, setSelectedCollectionRows] = useState<
    Map<number, CollectionNodeRead>
  >(new Map());
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
    setSelectedCollectionRows(new Map());
    setSelectMode(false);
  }, []);

  function selectAllVisible() {
    setSelectedIds(new Set(sortedModels.map((m) => m.id)));
    selectedModelSnapshot.current = new Map(sortedModels.map((model) => [model.id, model]));
    setSelectedCollectionRows(
      new Map(visibleCollections.map((collection) => [collection.id, collection])),
    );
  }

  async function selectAllMatching() {
    setSelectingAll(true);
    try {
      const all: ModelListItem[] = [];
      let cursor: string | undefined;
      do {
        const page = await listLibraryPage({ ...browseParams, cursor });
        all.push(...page.items.flatMap((item) => (item.kind === "model" ? [item.model] : [])));
        cursor = page.next_cursor ?? undefined;
      } while (cursor !== undefined);
      setSelectedIds(new Set(all.map((model) => model.id)));
      selectedModelSnapshot.current = new Map(all.map((model) => [model.id, model]));
      toast.info(uiText("{value1} matching models selected", { value1: String(all.length) }));
    } catch (error) {
      toast.error(error);
    } finally {
      setSelectingAll(false);
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
  const selectionCount = selectedIds.size + selectedCollectionRows.size;

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
        setSelectMode(true);
      } else if (event.key === "Escape" && selectionCount > 0 && !typing) clearSelection();
    }
    window.addEventListener("keydown", onShortcut);
    return () => window.removeEventListener("keydown", onShortcut);
  }, [auth.isAuthenticated, clearSelection, selectionCount]);

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
    setBatchBusy(true);
    let succeeded = 0;
    let failed = 0;
    const deletedModelIds: number[] = [];
    const failureDetails: string[] = [];
    try {
      if (selectedIdList.length) {
        const result = await batchInChunks(batchDeleteModels);
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
          await deleteCollection(collection.id, true);
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
      refresh();
      clearSelection();
    } catch (error) {
      toast.error(error);
    } finally {
      setBatchBusy(false);
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
    !!selectedCollection ||
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
  // The folder being viewed and the way to it, from one lookup by path.
  // A folder view shows one bounded page of child folders at a time.
  const { isFetchingNextPage: fetchingFolders, fetchNextPage: fetchMoreFolders } = folderPages;
  // The open folder's names replace its provisional label once they load.
  if (
    selectedCollectionRow &&
    recentFolders.some(
      (folder) =>
        folder.path === selectedCollectionRow.path &&
        folder.label !== selectedCollectionRow.display_path,
    )
  ) {
    setRecentFolders((current) =>
      current.map((folder) =>
        folder.path === selectedCollectionRow.path
          ? { ...folder, label: selectedCollectionRow.display_path }
          : folder,
      ),
    );
  }
  const availableRecentFolders = recentFolders.filter(
    (folder) => folder.path !== selectedCollection,
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
  const canAdminSelectedCollection =
    user?.is_superuser || selectedCollectionRow?.effective_role === "admin";
  // Whether there is anywhere this reader may upload to, without listing it.
  const writableProbe = useCollectionSearch("", "edit", {
    enabled: auth.isAuthenticated && !user?.is_superuser,
  });
  const hasWritableCollection =
    !!user?.is_superuser || (writableProbe.data?.pages[0]?.items.length ?? 0) > 0;
  const canUploadToVault =
    auth.isAuthenticated &&
    (user?.is_superuser || canWriteCollection(selectedCollectionRow) || hasWritableCollection);
  const uploadDefaultCollection =
    user?.is_superuser || canWriteCollection(selectedCollectionRow) ? selectedCollection : null;

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

  function toggleSelectMode() {
    if (selectMode) clearSelection();
    else setSelectMode(true);
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
    const params = new URLSearchParams(searchParams.toString());
    params.delete("q");
    params.delete("c");
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
          label: `${knownUiText(key)}: ${filterValueText(key, value)}`,
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

  if (authority.authorizationChanged || thumbnails.authorizationChanged)
    return (
      <div aria-busy="true">
        <ModelGridSkeleton />
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
                autoFocus
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
        <DeferredDialog
          open={uploadOpen}
          title={uiText("Upload")}
          onClose={() => setUploadOpen(false)}
        >
          <UploadModal
            open={uploadOpen}
            onClose={() => {
              setUploadOpen(false);
              setDropPreload(null);
              setDropCollection(null);
            }}
            onUploaded={refreshVaultAfterIngest}
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
            collection={
              selectedCollectionRow
                ? { id: selectedCollectionRow.id, path: selectedCollectionRow.path }
                : null
            }
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
        <FilterSidebar
          filtersOpen={filtersExpanded ?? activeFilterItems.length > Number(!!query.trim())}
          outlinerFilters={baseFilters}
          tags={tags}
          printers={printers}
          selectedCollection={selectedCollection}
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

        <main
          ref={startupContent}
          aria-busy={reading.status === "restoring"}
          className="flex-1 overflow-y-auto bg-background flex flex-col relative pb-24 md:pb-0"
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
          <nav className="px-4 sm:px-6 py-3 bg-background border-b border-border flex items-center space-x-2 text-sm tracking-tight">
            {breadcrumbs.length > 0 ? (
              <>
                <button
                  onClick={() => handleCollectionChange(null)}
                  className="text-muted-foreground hover:text-foreground transition-colors"
                >
                  {uiText("All Models")}
                </button>
                {breadcrumbs.map((crumb) => (
                  <span key={crumb.id} className="flex items-center space-x-2">
                    <ChevronRight className="h-3 w-3 text-muted-foreground/40" />
                    <button
                      onClick={() => handleCollectionChange(crumb.path)}
                      className="text-foreground font-medium"
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
                open={recentFoldersOpen}
                onOpenChange={setRecentFoldersOpen}
                align="start"
                trigger={
                  <button
                    type="button"
                    data-menu-trigger
                    aria-haspopup="menu"
                    aria-expanded={recentFoldersOpen}
                    onClick={() => setRecentFoldersOpen(!recentFoldersOpen)}
                    className="ml-auto flex items-center gap-1.5 rounded px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                  >
                    <History className="h-3.5 w-3.5" /> {ui("Recent")}
                  </button>
                }
                contentClassName="w-64 rounded border border-border bg-popover p-1 text-popover-foreground shadow-lg"
              >
                <p className="px-2.5 py-1.5 font-mono text-3xs uppercase tracking-wider text-muted-foreground">
                  {uiText("Recent folders")}
                </p>
                {availableRecentFolders.map((folder) => (
                  <button
                    key={folder.path}
                    role="menuitem"
                    type="button"
                    onClick={() => {
                      handleCollectionChange(folder.path);
                      setRecentFoldersOpen(false);
                    }}
                    className="flex w-full items-center gap-2 rounded px-2.5 py-2 text-left text-xs transition-colors hover:bg-popover-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <Folder className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                    <span className="truncate">{folder.label}</span>
                  </button>
                ))}
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setRecentFolders([]);
                    setRecentFoldersOpen(false);
                  }}
                  className="mt-1 w-full border-t border-border px-2.5 py-2 text-left text-xs text-muted-foreground transition-colors hover:bg-popover-hover hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  {uiText("Clear recent folders")}
                </button>
              </DropdownMenu>
            )}
          </nav>

          {/* Content Top Bar */}
          <div className="sticky top-0 z-40 border-b border-border bg-background/95 px-4 py-3 backdrop-blur sm:px-6 sm:py-4">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between sm:gap-4">
              <div className="flex min-w-0 items-start justify-between gap-3 sm:block">
                <div className="min-w-0 flex-1 space-y-1">
                  <h1 className="break-words text-lg font-bold tracking-tight text-foreground sm:truncate sm:text-2xl">
                    {selectedName ?? uiText("All Models")}
                  </h1>
                  <p className="text-sm text-muted-foreground">
                    {loading
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
                  className="shrink-0 sm:hidden"
                >
                  {uiText("Upload")}
                </Button>
              </div>

              <div className="flex w-full min-w-0 flex-col gap-3 sm:w-auto sm:flex-row sm:items-center sm:justify-end">
                <div className="grid w-full min-w-0 grid-cols-3 gap-2 sm:hidden">
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => {
                      startup.request("filters");
                      openDrawer();
                    }}
                    className="w-full min-w-0 px-2"
                  >
                    <SlidersHorizontal className="h-4 w-4 text-muted-foreground" />
                    <span className="min-w-0 truncate">{uiText("Filters")}</span>
                  </Button>
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
                    <Button
                      variant="ghost"
                      role="menuitem"
                      className="w-full justify-start"
                      onClick={() => {
                        setLibraryToolsOpen(!showLibraryTools);
                        setMoreOpen(false);
                      }}
                    >
                      <SlidersHorizontal className="h-4 w-4" aria-hidden />
                      {t("vault.libraryTools")}
                    </Button>
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
                            manageSavedView(
                              { kind: "delete", id: view.id },
                              "savedView.deleteSuccess",
                            )
                          }
                          triggerClassName="max-w-none w-full justify-start px-2.5 py-2 text-sm"
                          triggerRole="menuitem"
                          triggerSize="sm"
                          triggerVariant="ghost"
                        />
                        <button
                          type="button"
                          role="menuitemcheckbox"
                          aria-checked={selectMode}
                          title={uiText("Select Models and folders (S)")}
                          onClick={() => {
                            toggleSelectMode();
                            setMoreOpen(false);
                          }}
                          className={`flex w-full items-center gap-2 rounded px-2.5 py-2 text-left text-sm transition-colors hover:bg-popover-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${selectMode ? "bg-accent text-accent-foreground" : ""}`}
                        >
                          <CheckSquare className="h-4 w-4" />
                          <span className="flex-1">
                            {selectMode ? uiText("Done") : uiText("Select")}
                          </span>
                          {selectMode && <Check className="h-3.5 w-3.5" />}
                        </button>
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

                <div className="hidden w-full flex-wrap items-center gap-2 sm:flex sm:w-auto">
                  <Button
                    type="button"
                    variant="outline"
                    size="xs"
                    onClick={() => {
                      startup.request("filters");
                      openDrawer();
                    }}
                    className="h-10 md:hidden sm:h-8"
                  >
                    <SlidersHorizontal className="h-4 w-4 text-muted-foreground" />
                    {uiText("Filters")}
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    size="xs"
                    className="hidden h-8 md:inline-flex"
                    aria-label={uiText("Filters")}
                    aria-expanded={
                      filtersExpanded ?? activeFilterItems.length > Number(!!query.trim())
                    }
                    onClick={() => {
                      startup.request("filters");
                      setFiltersExpanded(
                        !(filtersExpanded ?? activeFilterItems.length > Number(!!query.trim())),
                      );
                    }}
                  >
                    <SlidersHorizontal className="h-4 w-4" aria-hidden />
                    {uiText("Filters")}
                    {activeFilterItems.length > Number(!!query.trim()) && (
                      <span className="font-mono tabular-nums">
                        {activeFilterItems.length - Number(!!query.trim())}
                      </span>
                    )}
                  </Button>
                  <Button
                    size="xs"
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
                    className="h-10 sm:h-8"
                  >
                    {uiText("Upload")}
                  </Button>
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
                      className="h-10 sm:h-8"
                    >
                      <Plus className="h-4 w-4" aria-hidden />
                      {t("multipart.new")}
                    </Button>
                  )}
                </div>
                <Button
                  variant={showLibraryTools ? "secondary" : "outline"}
                  size="xs"
                  className="hidden h-8 sm:inline-flex"
                  aria-expanded={showLibraryTools}
                  aria-controls="library-tools"
                  aria-label={t("vault.libraryTools")}
                  onClick={() => setLibraryToolsOpen(!showLibraryTools)}
                >
                  <SlidersHorizontal className="h-4 w-4" aria-hidden />
                  {t("vault.libraryTools")}
                </Button>
                <div className="hidden h-6 w-px bg-muted mx-1 md:block" />
                <div className="hidden w-full flex-wrap items-center gap-2 border-t border-border pt-3 sm:flex sm:w-auto sm:border-t-0 sm:pt-0">
                  <SortMenu
                    open={sortOpen}
                    onOpenChange={setSortOpen}
                    sortKey={sortKey}
                    onSelect={selectSort}
                    labelFor={ui}
                    triggerSize="xs"
                    triggerClassName="h-10 sm:h-8"
                  />
                  <DisplayMenu
                    open={displayOpen}
                    onOpenChange={setDisplayOpen}
                    viewMode={viewMode}
                    compact={compact}
                    onSelectMode={selectViewMode}
                    onSelectDensity={selectDensity}
                    labelFor={ui}
                    triggerSize="xs"
                    triggerClassName="h-10 sm:h-8"
                  />
                </div>
              </div>
            </div>
          </div>

          {displayedCollection && (
            <div className="space-y-3 border-b border-border px-4 py-3 sm:px-6">
              <EntityTagsDialog
                entityLabel={displayedCollection.name}
                tags={displayedCollection.tags}
                availableTags={tags}
                canEdit={
                  browseReady && (!!user?.is_superuser || canWriteCollection(displayedCollection))
                }
                help={uiText(
                  "Collection tags are inherited by Models in this collection and every descendant for search and filtering.",
                )}
                onSave={(nextTags) => saveCollectionTags(displayedCollection, nextTags)}
              />
              <CollectionReadme
                key={displayedCollection.id}
                collectionId={displayedCollection.id}
                hasReadme={displayedCollection.has_readme}
                canEdit={
                  browseReady && (!!user?.is_superuser || canWriteCollection(displayedCollection))
                }
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
                  variant="outline"
                  size="xs"
                  onClick={item.onRemove}
                  className="gap-1.5"
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

          {/* Models / Documents tabs */}
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
            className="border-b border-border px-4 pt-3 sm:px-6"
            tabClassName="px-3 py-2 text-sm font-medium text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            activeTabClassName="text-foreground"
          />

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
                  disabled={!newCollectionName.trim() || !auth.isAuthenticated}
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

          {showLibraryTools && (
            <section
              id="library-tools"
              aria-label={t("vault.libraryTools")}
              className="space-y-3 border-b border-border bg-muted/20 px-4 py-3 sm:px-6"
            >
              <div className="flex flex-wrap items-center gap-2">
                <Button variant="outline" size="xs" asChild className="h-9">
                  <Link href="/library/similar">
                    <ScanSearch className="h-4 w-4" aria-hidden />
                    {t("similarity.title")}
                  </Link>
                </Button>
                <Button
                  variant="outline"
                  size="xs"
                  onClick={handleOpenCreateCollection}
                  disabled={!canAdminSelectedCollection}
                  title={
                    canAdminSelectedCollection
                      ? uiText("Create a collection")
                      : uiText("Admin access required for this collection")
                  }
                  className="h-9"
                >
                  <Plus className="w-4 h-4 text-muted-foreground" />
                  {uiText("New collection")}
                </Button>
                {auth.isAuthenticated && (
                  <div className="flex flex-wrap items-center gap-2">
                    <Button
                      variant={favoritesOnly ? "secondary" : "outline"}
                      size="xs"
                      aria-pressed={favoritesOnly}
                      onClick={toggleFavorites}
                      className="h-10 sm:h-8"
                    >
                      <Star className={`h-4 w-4 ${favoritesOnly ? "fill-current" : ""}`} />{" "}
                      {uiText("Favorites")}
                    </Button>
                    <SavedViewSelector
                      key={savedViewsOwner.session}
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
                      triggerClassName="h-10 sm:h-8"
                    />
                    {!selectMode && (
                      <Button
                        variant="outline"
                        size="xs"
                        aria-pressed={selectMode}
                        title={uiText("Select Models and folders (S)")}
                        onClick={toggleSelectMode}
                        className="h-10 sm:h-8"
                      >
                        <CheckSquare className="w-4 h-4" />
                        {uiText("Select")}
                      </Button>
                    )}
                  </div>
                )}
              </div>
            </section>
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
                    {sortedModels.length + visibleCollections.length})
                  </button>
                  <button
                    type="button"
                    onClick={() => void selectAllMatching()}
                    disabled={selectingAll}
                    className="font-medium text-primary hover:underline disabled:opacity-50"
                  >
                    {selectingAll ? uiText("Selecting…") : uiText("Select all matching models")}
                  </button>
                </>
              )}
              <Button size="xs" variant="outline" onClick={toggleSelectMode}>
                {uiText("Done")}
              </Button>
              {selectionCount > 0 && (
                <button
                  type="button"
                  onClick={() => {
                    setSelectedIds(new Set());
                    setSelectedCollectionRows(new Map());
                  }}
                  className="font-medium text-muted-foreground hover:text-foreground"
                >
                  {uiText("Clear")}
                </button>
              )}
            </div>
          )}

          {/* Content */}
          {docView === "docs" ? (
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
              {refreshRequired && (
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
                      void (currentRefresh
                        ? snapshot
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
                  {error}
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

              {loading ? (
                viewMode === "grid" ? (
                  <ModelGridSkeleton />
                ) : (
                  <ModelListSkeleton />
                )
              ) : error && snapshot === null ? null : sortedModels.length === 0 &&
                visibleMultipartModels.length === 0 &&
                visibleCollections.length === 0 &&
                !hasMore ? (
                <EmptyState
                  title={uiText("No models found")}
                  description={
                    query ||
                    selectedCollection ||
                    selectedTags.length ||
                    selectedPrinterId ||
                    selectedPrinterPresence
                      ? uiText("Try clearing some filters.")
                      : uiText(
                          "Upload a model when you're ready, or skim the wiki first if this is a new install.",
                        )
                  }
                  action={
                    hasActiveFilters ? (
                      <Button type="button" variant="outline" size="xs" onClick={clearAllFilters}>
                        {uiText("Clear all filters")}
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
                <div key="grid" className={compact ? "p-3" : "p-4 sm:p-6"}>
                  <div
                    className={`grid grid-cols-1 ${compact ? "gap-2 sm:grid-cols-[repeat(auto-fill,minmax(260px,260px))]" : "gap-4 sm:grid-cols-[repeat(auto-fill,minmax(340px,340px))]"}`}
                  >
                    {visibleCollections.map((collection) => (
                      <CollectionFolderCard
                        key={collection.id}
                        collection={collection}
                        onSelect={handleCollectionChange}
                        onIntent={prefetchFolder}
                        onDropModel={canUploadToVault ? handleMoveModel : undefined}
                        selectable={selectMode}
                        selected={selectedCollectionRows.has(collection.id)}
                        onToggleSelect={toggleCollectionSelect}
                      />
                    ))}
                    {libraryItems.map((item) =>
                      item.kind === "multipart" ? (
                        <MultipartModelCard
                          key={`multipart-${item.value.id}`}
                          item={item.value}
                          collectionLabel={item.value.collection_label}
                          returnTo={currentLibraryHref}
                          origin={snapshot?.entry}
                          availableTags={tags}
                          onDataChange={refresh}
                        />
                      ) : (
                        <ModelCard
                          key={item.value.id}
                          model={item.value}
                          origin={snapshot?.entry}
                          collectionLabel={item.value.collection_label}
                          selectable={selectMode}
                          selected={selectedIds.has(item.value.id)}
                          onToggleSelect={toggleSelect}
                          draggable={canUploadToVault && !selectMode}
                          onEditTags={openTagEditor}
                        />
                      ),
                    )}
                  </div>
                  <LoadMore
                    hasMore={snapshot?.moreFolders ?? false}
                    loading={fetchingFolders || currentRefresh !== null}
                    onClick={() => {
                      if (currentRefresh === null) void fetchMoreFolders();
                    }}
                    folders
                  />
                  <LoadMore
                    hasMore={hasMore && !refreshRequired}
                    loading={loadingMore}
                    onClick={loadMore}
                  />
                </div>
              ) : (
                <div key="list" ref={listContent} className="flex-1 overflow-y-auto">
                  <div className="flex flex-col">
                    <div className="flex items-center gap-3 px-4 py-2 border-b border-border text-xs font-mono text-muted-foreground uppercase tracking-wider bg-muted/50">
                      <span className="w-10 flex-shrink-0">{uiText("Thumb")}</span>
                      <span className="flex-1">{uiText("Name")}</span>
                      <span className="w-24 text-right hidden sm:block">
                        {uiText("Collection")}
                      </span>
                      <span className="w-20 text-right">{uiText("Contents")}</span>
                      <span className="w-24 text-right hidden md:block">{uiText("Updated")}</span>
                    </div>
                    {visibleCollections.map((collection) => (
                      <CollectionListRow
                        key={collection.id}
                        collection={collection}
                        displayPath={collection.display_path}
                        onSelect={handleCollectionChange}
                        onIntent={prefetchFolder}
                        onDropModel={canUploadToVault ? handleMoveModel : undefined}
                        selectable={selectMode}
                        selected={selectedCollectionRows.has(collection.id)}
                        onToggleSelect={toggleCollectionSelect}
                      />
                    ))}
                    <LoadMore
                      hasMore={snapshot?.moreFolders ?? false}
                      loading={fetchingFolders || currentRefresh !== null}
                      onClick={() => {
                        if (currentRefresh === null) void fetchMoreFolders();
                      }}
                      folders
                    />
                    {libraryItems.map((item) =>
                      item.kind === "multipart" ? (
                        <MultipartModelListRow
                          key={`multipart-${item.value.id}`}
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
                          onToggleSelect={toggleSelect}
                          draggable={canUploadToVault && !selectMode}
                        />
                      ),
                    )}
                  </div>
                  <LoadMore
                    hasMore={hasMore && !refreshRequired}
                    loading={loadingMore}
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
  onSelect,
  onIntent,
  onDropModel,
  selectable,
  selected,
  onToggleSelect,
}: {
  collection: CollectionRead;
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
        className={`group flex flex-col text-left bg-muted border rounded-lg hover:shadow-sm transition-[border-color,box-shadow,transform] duration-fast active:scale-[0.99] relative overflow-hidden ${
          selected
            ? "border-primary bg-accent"
            : dragOver
              ? "border-primary ring-2 ring-primary-soft"
              : "border-border hover:border-primary"
        }`}
      >
        <div className="flex-1 flex items-center justify-center bg-muted/60 dark:bg-surface-container-high min-h-[100px] sm:min-h-[140px]">
          {selectable && (
            <span className="absolute left-3 top-3">
              <Checkbox
                checked={!!selected}
                onChange={() => onToggleSelect?.(collection.id)}
                ariaLabel={uiText("Select folder {value1}", { value1: String(collection.name) })}
              />
            </span>
          )}
          <Folder className="w-12 h-12 sm:w-16 sm:h-16 text-primary/30" />
        </div>
        <div className="p-3 border-t border-border">
          <div className="flex items-center justify-end gap-2 mb-0.5">
            <span className="text-3xs text-muted-foreground font-mono">
              {uiText("{value1} models", { value1: String(collection.model_count ?? "") })}
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
        {selectable && (
          <Checkbox
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
  item,
  collectionLabel,
  returnTo,
  origin,
}: {
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
      className="group flex items-center gap-2 border-b border-border px-4 py-3 transition-colors hover:bg-muted active:bg-muted md:gap-3"
    >
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
        {selectable && (
          <Checkbox
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

function LoadMore({
  hasMore,
  loading,
  onClick,
  folders = false,
}: {
  hasMore: boolean;
  loading: boolean;
  onClick: () => void;
  folders?: boolean;
}) {
  useUiLocale();
  if (!hasMore) return null;
  return (
    <Localized>
      <div className="flex justify-center mt-6 pb-6">
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

export function ModelGridSkeleton() {
  useUiLocale();
  return (
    <div className="p-6">
      <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
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
