"use client";

import { useCallback, useEffect, useMemo, useState, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "@/lib/navigation";
import {
  ArtifactFileType,
  CollectionRead,
  FileRevisionStatus,
  ModelBatchResult,
  ModelListItem,
  ModelSort,
  MultipartModelListItem,
  PrintJobState,
  PrinterRead,
  SavedViewRead,
  TagRead,
} from "@/types";
import { ModelCard } from "@/components/model-card";
import { ModelTagsDialog } from "@/components/model-tags-dialog";
import { MODEL_DND_MIME } from "@/lib/model-dnd";
import { BatchToolbar } from "@/components/batch-toolbar";
import { Checkbox } from "@/components/ui/checkbox";
import { CollectionReadme } from "@/components/collection-readme";
import { MultipartModelCard, NewMultipartModelModal } from "@/components/multipart-model-browser";
import { EntityTagsDialog } from "@/components/entity-tags-dialog";
import { DocumentBrowser } from "@/components/document-browser";
import { FilterSidebar, type LibraryViewMode } from "@/components/filter-sidebar";
import { MobileFilterDrawer } from "@/components/mobile-filter-drawer";
import { StructuredFilters } from "@/components/structured-filters";
import { UploadModal, UploadMode } from "@/components/upload-modal";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/ui/empty-state";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { DropdownMenu } from "@/components/ui/dropdown-menu";
import { SavedViewSelector } from "@/components/saved-view-selector";
import { Localized, translateUiText } from "@/components/ui/localized";
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
} from "lucide-react";
import {
  createCollection,
  updateModel,
  moveCollection,
  renameCollection,
  deleteCollection,
  batchMoveModels,
  batchTagModels,
  batchDeleteModels,
  createSavedView,
  updateSavedView,
  deleteSavedView,
  listSavedViews,
  listModels,
  restoreModel,
  replaceCollectionTags,
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
  useCollections,
  useModelFacets,
  useModelList,
  useMultipartModels,
  useOutlinerModels,
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
import { useAuthenticatedAssetUrl } from "@/lib/use-authenticated-asset-url";
import { useMediaQuery } from "@/lib/use-media-query";
import { cn } from "@/lib/utils";
import { TabBar } from "@/components/ui/tabs";

type SortKey = ModelSort;
type ViewMode = "grid" | "list";
type LibraryItem =
  | { kind: "model"; value: ModelListItem }
  | { kind: "multipart"; value: MultipartModelListItem };

const PAGE_SIZE = 60;
const SORT_OPTIONS: { value: SortKey; label: string }[] = [
  { value: "date-desc", label: "Newest" },
  { value: "date-asc", label: "Oldest" },
  { value: "name-asc", label: "Name A–Z" },
  { value: "name-desc", label: "Name Z–A" },
  { value: "success-desc", label: "Best success rate" },
  { value: "printed-desc", label: "Recently printed" },
  { value: "duration-asc", label: "Shortest print" },
  { value: "filament-asc", label: "Least filament" },
  { value: "cost-asc", label: "Lowest cost" },
];

/** Sort the two card types as one library whenever they share a meaningful key. */
function sortLibraryItems(items: LibraryItem[], sortKey: SortKey): LibraryItem[] {
  return [...items].sort((left, right) => {
    if (sortKey === "name-asc" || sortKey === "name-desc") {
      const order = left.value.name.localeCompare(right.value.name);
      return sortKey === "name-asc" ? order : -order;
    }
    if (sortKey === "date-asc" || sortKey === "date-desc") {
      const order = Date.parse(left.value.updated_at) - Date.parse(right.value.updated_at);
      return sortKey === "date-asc" ? order : -order;
    }
    // Print-result sorts have no aggregate equivalent yet. Preserve the API's
    // Model order and put sets after it instead of implying a fabricated score.
    if (left.kind !== right.kind) return left.kind === "model" ? -1 : 1;
    if (left.kind === "model") return 0;
    return Date.parse(right.value.updated_at) - Date.parse(left.value.updated_at);
  });
}

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
          aria-label={`${labelFor(label)} View`}
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

// Every model filter that lives in the URL as a repeated query parameter.
const STRUCTURED_FILTER_KEYS = [
  "file_type",
  "material_type",
  "slicer_name",
  "printer_model",
  "revision_status",
  "print_outcome",
  "storage",
  "printed",
] as const;
type StructuredFilterKey = (typeof STRUCTURED_FILTER_KEYS)[number];

const RECENT_FOLDERS_KEY = "ps-recent-folders";
const RECENT_FOLDERS_LIMIT = 6;
const LIBRARY_VIEW_KEY = "ps-vault-library-view";
// A signed-out session has no saved views; a shared constant keeps the derived
// list referentially stable across renders.
const NO_SAVED_VIEWS: SavedViewRead[] = [];

// The values each enum-valued filter accepts. The URL is user-editable, so a
// `?file_type=nonsense` has to be dropped before it reaches a query.
type StorageKind = NonNullable<ModelListFilters["storage"]>[number];
const ARTIFACT_FILE_TYPES: readonly ArtifactFileType[] = ["stl", "3mf", "gcode", "obj", "step"];
const FILE_REVISION_STATUSES: readonly FileRevisionStatus[] = [
  "known_good",
  "needs_test",
  "failed",
  "archived",
];
const PRINT_JOB_STATES: readonly PrintJobState[] = [
  "queued",
  "uploading",
  "started",
  "printing",
  "paused",
  "completed",
  "cancelled",
  "failed",
];
const STORAGE_KINDS: readonly StorageKind[] = ["vault", "external"];

/** Keep the URL values the API recognises, in the order the URL listed them. */
function parseFilterValues<T extends string>(values: string[], allowed: readonly T[]): T[] {
  const parsed: T[] = [];
  for (const value of values) {
    const match = allowed.find((option) => option === value);
    if (match !== undefined) parsed.push(match);
  }
  return parsed;
}

function readVaultPreference(key: string): string | null {
  if (!("window" in globalThis)) return null;
  return localStorage.getItem(key);
}

/**
 * The recent-folder list is a UI convenience written by this component. Paths are
 * re-checked against the live collection list before they're offered (see
 * `availableRecentFolders`), so decoding only has to survive edited JSON.
 */
function readRecentFolders(): string[] {
  const raw = readVaultPreference(RECENT_FOLDERS_KEY);
  if (raw === null) return [];
  try {
    const decoded: unknown = JSON.parse(raw);
    return Array.isArray(decoded) ? decoded.map((entry) => String(entry)) : [];
  } catch {
    return [];
  }
}

function writeRecentFolders(paths: string[]): void {
  if (!("window" in globalThis)) return;
  localStorage.setItem(RECENT_FOLDERS_KEY, JSON.stringify(paths));
}

/** The remembered sort, resolved back to one of the options we render. */
function readSortKey(): SortKey {
  const stored = readVaultPreference("ps-vault-sort");
  return SORT_OPTIONS.find((option) => option.value === stored)?.value ?? "date-desc";
}

/** Decode the persisted Library view without trusting browser-owned storage. */
function readLibraryView(): LibraryViewMode | null {
  const stored = readVaultPreference(LIBRARY_VIEW_KEY);
  if (
    stored === "organized" ||
    stored === "all" ||
    stored === "multipart" ||
    stored === "components"
  ) {
    return stored;
  }
  return null;
}

function childCollections(
  collections: CollectionRead[],
  selectedPath: string | null,
): CollectionRead[] {
  const selected = selectedPath ? collections.find((c) => c.path === selectedPath) : null;
  const parentId = selectedPath ? (selected?.id ?? -1) : null;
  return collections
    .filter((c) => c.parent_id === parentId)
    .sort((a, b) => a.name.localeCompare(b.name));
}

function collectionBreadcrumbs(
  collections: CollectionRead[],
  selectedPath: string | null,
): CollectionRead[] {
  if (!selectedPath) return [];
  const byPath = new Map(collections.map((c) => [c.path, c]));
  const parts = selectedPath.split("/");
  const crumbs: CollectionRead[] = [];
  for (let i = 1; i <= parts.length; i += 1) {
    const c = byPath.get(parts.slice(0, i).join("/"));
    if (c) crumbs.push(c);
  }
  return crumbs;
}

function selectedCollectionName(
  collections: CollectionRead[],
  selectedPath: string | null,
): string | null {
  if (!selectedPath) return null;
  const byPath = new Map(collections.map((c) => [c.path, c]));
  return byPath.get(selectedPath)?.name ?? null;
}

function canWriteCollection(collection: CollectionRead | null | undefined): boolean {
  return collection?.effective_role === "edit" || collection?.effective_role === "admin";
}

export interface BrowserInitialData {
  models: ModelListItem[];
  collections: CollectionRead[];
  tags: TagRead[];
  printers: PrinterRead[];
}

export function ModelBrowser({ initial }: { initial?: BrowserInitialData }) {
  const { locale, t } = useI18n();
  const ui = useCallback((value: string) => translateUiText(locale, value), [locale]);
  const router = useRouter();
  const searchParams = useSearchParams();
  const auth = useRequireAuth();
  const { user } = useAuth();
  const desktopOutliner = useMediaQuery("(min-width: 768px)");
  // Shared taxonomy facets from the TanStack Query cache: one cache entry shared
  // with the detail/upload views, revalidated on focus, and refetched after any
  // collection/tag mutation (the api layer invalidates the query cache).
  const collectionsQuery = useCollections();
  const tagsQuery = useTags();
  // Stable empty-array fallback: `data ?? []` would allocate a new array every
  // render, which cascaded into the useMemo hooks below re-running on every
  // render even when the underlying query data hadn't changed.
  const collections = useMemo(() => collectionsQuery.data ?? [], [collectionsQuery.data]);
  const tags = tagsQuery.data ?? [];
  // Printers (superuser-only filter) share the same cache as the printers page
  // and send-to dialog; gated so non-admins don't fetch a list they can't use.
  const printers = usePrinters({ enabled: !!user?.is_superuser }).data ?? initial?.printers ?? [];
  const [selectedTags, setSelectedTags] = useState<string[]>(() => searchParams.getAll("tag"));
  const [selectedPrinterId, setSelectedPrinterId] = useState<number | null>(() => {
    const value = searchParams.get("printer_id");
    return value ? Number(value) : null;
  });
  const [selectedPrinterPresence, setSelectedPrinterPresence] = useState<"any" | "none" | null>(
    () => {
      const value = searchParams.get("printer_presence");
      return value === "any" || value === "none" ? value : null;
    },
  );
  const [favoritesOnly, setFavoritesOnly] = useState(searchParams.get("favorites") === "true");
  const [loadedSavedViews, setLoadedSavedViews] = useState<SavedViewRead[]>([]);
  // Saved views belong to an account, so a signed-out session simply has none.
  const savedViews = auth.isAuthenticated ? loadedSavedViews : NO_SAVED_VIEWS;
  const [activeSavedViewId, setActiveSavedViewId] = useState<number | null>(null);
  const [saveViewOpen, setSaveViewOpen] = useState(false);
  const [saveViewName, setSaveViewName] = useState("");
  const [saveViewBusy, setSaveViewBusy] = useState(false);
  const [viewMode, setViewMode] = useState<ViewMode>(() =>
    readVaultPreference("ps-vault-view") === "list" ? "list" : "grid",
  );
  const [sortKey, setSortKey] = useState<SortKey>(readSortKey);
  const [sortOpen, setSortOpen] = useState(false);
  const [displayOpen, setDisplayOpen] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const [compact, setCompact] = useState(
    () => readVaultPreference("ps-vault-density") === "compact",
  );
  const [recentFolders, setRecentFolders] = useState<string[]>(readRecentFolders);
  const [recentFoldersOpen, setRecentFoldersOpen] = useState(false);
  // Seed from the URL (`?v=docs`), falling back to the remembered tab, so
  // returning from a document (Back or the logo) lands on the Documents tab
  // instead of resetting to Models.
  const requestedVaultView = searchParams.get("v");
  const initialVaultView = requestedVaultView === "docs" ? "docs" : readLastView();
  const [docView, setDocView] = useState<"models" | "docs">(
    initialVaultView === "docs" ? "docs" : "models",
  );
  const requestedLibraryView = searchParams.get("type");
  const [libraryView, setLibraryView] = useState<LibraryViewMode>(() => {
    if (
      requestedLibraryView === "all" ||
      requestedLibraryView === "multipart" ||
      requestedLibraryView === "components"
    ) {
      return requestedLibraryView;
    }
    if (requestedVaultView === "multipart") return "multipart";
    return readLibraryView() ?? "all";
  });
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

  const facetsLoading = collectionsQuery.isLoading || tagsQuery.isLoading;
  const [isCreatingCollection, setIsCreatingCollection] = useState(false);
  const [newCollectionName, setNewCollectionName] = useState("");
  const { open: filterDrawerOpen, openDrawer, closeDrawer } = useMobileFilterDrawer();

  useEffect(() => {
    function restoreFiltersFromHistory() {
      const params = new URLSearchParams(window.location.search);
      setSelectedTags(params.getAll("tag"));
      const printerId = params.get("printer_id");
      setSelectedPrinterId(printerId ? Number(printerId) : null);
      const presence = params.get("printer_presence");
      setSelectedPrinterPresence(presence === "any" || presence === "none" ? presence : null);
      setFavoritesOnly(params.get("favorites") === "true");
      const nextLibraryView = params.get("type");
      setLibraryView(
        nextLibraryView === "all" ||
          nextLibraryView === "multipart" ||
          nextLibraryView === "components"
          ? nextLibraryView
          : params.get("v") === "multipart"
            ? "multipart"
            : "organized",
      );
    }
    window.addEventListener("popstate", restoreFiltersFromHistory);
    return () => window.removeEventListener("popstate", restoreFiltersFromHistory);
  }, []);

  // Keep every filter URL-backed. Saved views, reload, Back, and copied links now
  // restore the same result set instead of only preserving search/folder state.
  useEffect(() => {
    const params = new URLSearchParams(searchParams.toString());
    params.delete("tag");
    selectedTags.forEach((tag) => params.append("tag", tag));
    if (selectedPrinterId !== null) params.set("printer_id", String(selectedPrinterId));
    else params.delete("printer_id");
    if (selectedPrinterPresence !== null) params.set("printer_presence", selectedPrinterPresence);
    else params.delete("printer_presence");
    const next = params.toString();
    if (next !== searchParams.toString())
      router.replace(next ? `/?${next}` : "/", { scroll: false });
  }, [router, searchParams, selectedPrinterId, selectedPrinterPresence, selectedTags]);

  useEffect(() => {
    if (!auth.isAuthenticated) return;
    listSavedViews()
      .then(setLoadedSavedViews)
      .catch(() => setLoadedSavedViews([]));
  }, [auth.isAuthenticated]);

  // Collection selection lives in the URL (`?c=<path>`) so it resets when the
  // user navigates away (e.g. to Settings) and clicks "Vault" again — that link
  // points at "/" with no param. Deriving it straight from the param (instead of
  // mirroring into state) means a folder switch just re-keys the model query;
  // `keepPreviousData` holds the old cards on screen until the new page lands, so
  // there's no manual clearing or loading flash.
  const selectedCollection = searchParams.get("c") || null;
  // Entering a folder pushes it onto the recent list on the render that first
  // sees the new `?c=`, so the list is never a navigation behind the URL.
  const [recordedCollection, setRecordedCollection] = useState<string | null>(null);
  if (recordedCollection !== selectedCollection) {
    setRecordedCollection(selectedCollection);
    if (selectedCollection !== null)
      setRecentFolders((current) =>
        [selectedCollection, ...current.filter((item) => item !== selectedCollection)].slice(
          0,
          RECENT_FOLDERS_LIMIT,
        ),
      );
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
    setSelectedIds(new Set());
    const params = new URLSearchParams(searchParams.toString());
    if (path) params.set("c", path);
    else params.delete("c");
    const qs = params.toString();
    router.replace(qs ? `/?${qs}` : "/", { scroll: false });
  }

  function handleLibraryViewChange(view: LibraryViewMode) {
    setLibraryView(view);
    localStorage.setItem(LIBRARY_VIEW_KEY, view);
    setDocView("models");
    setSelectedIds(new Set());
    const params = new URLSearchParams(searchParams.toString());
    params.delete("v");
    if (view === "organized") params.delete("type");
    else params.set("type", view);
    router.replace(params.size ? `/?${params}` : "/", { scroll: false });
  }

  useEffect(() => {
    if (!uploadRequested) return;
    const params = new URLSearchParams(searchParams.toString());
    params.delete("upload");
    const qs = params.toString();
    router.replace(qs ? `/?${qs}` : "/", { scroll: false });
  }, [uploadRequested, searchParams, router]);

  const query = searchParams.get("q") ?? "";
  const searchQuery = query.trim() || undefined;
  const canViewPrinters = !!user?.is_superuser;
  const queryClient = useQueryClient();
  // Raw URL values, one entry per filter key; `satisfies` makes a missing key a
  // type error instead of a silently absent filter.
  const structured = {
    file_type: searchParams.getAll("file_type"),
    material_type: searchParams.getAll("material_type"),
    slicer_name: searchParams.getAll("slicer_name"),
    printer_model: searchParams.getAll("printer_model"),
    revision_status: searchParams.getAll("revision_status"),
    print_outcome: searchParams.getAll("print_outcome"),
    storage: searchParams.getAll("storage"),
    printed: searchParams.getAll("printed"),
  } satisfies Record<StructuredFilterKey, string[]>;
  // The enum-valued filters, parsed down to the values the API accepts.
  const fileTypes = parseFilterValues(structured.file_type, ARTIFACT_FILE_TYPES);
  const revisionStatuses = parseFilterValues(structured.revision_status, FILE_REVISION_STATUSES);
  const printOutcomes = parseFilterValues(structured.print_outcome, PRINT_JOB_STATES);
  const storageKinds = parseFilterValues(structured.storage, STORAGE_KINDS);

  function setStructuredFilter(key: StructuredFilterKey, values: string[]) {
    const params = new URLSearchParams(searchParams.toString());
    params.delete(key);
    values.forEach((value) => params.append(key, value));
    const qs = params.toString();
    router.replace(qs ? `/?${qs}` : "/", { scroll: false });
  }

  function clearStructuredFilters() {
    const params = new URLSearchParams(searchParams.toString());
    STRUCTURED_FILTER_KEYS.forEach((key) => params.delete(key));
    params.delete("uploaded_after");
    params.delete("uploaded_before");
    const qs = params.toString();
    router.replace(qs ? `/?${qs}` : "/", { scroll: false });
  }

  // Filters shared by the grid + outliner queries; only the search query and
  // pagination differ between them.
  const baseFilters: ModelListFilters = {
    tag: selectedTags.length ? selectedTags : undefined,
    printer_id: canViewPrinters ? (selectedPrinterId ?? undefined) : undefined,
    printer_presence:
      canViewPrinters && selectedPrinterId === null
        ? (selectedPrinterPresence ?? undefined)
        : undefined,
    favorites: favoritesOnly || undefined,
    file_type: fileTypes,
    material_type: structured.material_type,
    slicer_name: structured.slicer_name,
    printer_model: structured.printer_model,
    revision_status: revisionStatuses,
    print_outcome: printOutcomes,
    storage: storageKinds,
    printed: structured.printed[0] ? structured.printed[0] === "yes" : undefined,
    uploaded_after: searchParams.get("uploaded_after") || undefined,
    uploaded_before: searchParams.get("uploaded_before") || undefined,
  };
  const facetQuery = useModelFacets({
    ...baseFilters,
    collection: selectedCollection ?? undefined,
    direct: !searchQuery,
    q: searchQuery,
  });

  function writeFilterUrl(filters: SavedViewRead["filters"]) {
    const params = new URLSearchParams();
    if (filters.collection) params.set("c", filters.collection);
    if (filters.q) params.set("q", filters.q);
    filters.tag.forEach((tag) => params.append("tag", tag));
    if (filters.printer_id) params.set("printer_id", String(filters.printer_id));
    if (filters.printer_presence) params.set("printer_presence", filters.printer_presence);
    if (filters.favorites) params.set("favorites", "true");
    for (const key of [
      "file_type",
      "material_type",
      "slicer_name",
      "printer_model",
      "revision_status",
      "print_outcome",
      "storage",
    ] as const) {
      for (const value of filters[key] ?? []) params.append(key, value);
    }
    if (filters.printed != null) params.set("printed", filters.printed ? "yes" : "no");
    if (filters.uploaded_after) params.set("uploaded_after", filters.uploaded_after);
    if (filters.uploaded_before) params.set("uploaded_before", filters.uploaded_before);
    router.replace(params.size ? `/?${params}` : "/", { scroll: false });
  }

  function applySavedView(view: SavedViewRead) {
    setActiveSavedViewId(view.id);
    setSelectedTags(view.filters.tag);
    setSelectedPrinterId(view.filters.printer_id ?? null);
    setSelectedPrinterPresence(view.filters.printer_presence ?? null);
    setFavoritesOnly(view.filters.favorites);
    setSelectedIds(new Set());
    writeFilterUrl(view.filters);
  }

  async function saveCurrentView() {
    const name = saveViewName.trim();
    if (!name) return;
    setSaveViewBusy(true);
    try {
      const created = await createSavedView(name, currentViewFilters());
      setLoadedSavedViews((current) =>
        [...current, created].sort((a, b) => a.name.localeCompare(b.name)),
      );
      setSaveViewOpen(false);
      setSaveViewName("");
      toast.success("View saved");
    } catch (error) {
      toast.error(error);
    } finally {
      setSaveViewBusy(false);
    }
  }

  function currentViewFilters(): SavedViewRead["filters"] {
    return {
      collection: selectedCollection,
      direct: !searchQuery,
      tag: selectedTags,
      q: searchQuery ?? null,
      printer_id: selectedPrinterId,
      printer_presence: selectedPrinterPresence,
      favorites: favoritesOnly,
      file_type: fileTypes,
      material_type: structured.material_type,
      slicer_name: structured.slicer_name,
      printer_model: structured.printer_model,
      revision_status: revisionStatuses,
      print_outcome: printOutcomes,
      storage: storageKinds,
      printed: structured.printed[0] ? structured.printed[0] === "yes" : null,
      uploaded_after: searchParams.get("uploaded_after"),
      uploaded_before: searchParams.get("uploaded_before"),
    };
  }

  const activeSavedView = savedViews.find((view) => view.id === activeSavedViewId) ?? null;
  const savedViewModified =
    activeSavedView !== null &&
    JSON.stringify({
      ...activeSavedView.filters,
      collection: activeSavedView.filters.collection ?? null,
      q: activeSavedView.filters.q ?? null,
      printer_id: activeSavedView.filters.printer_id ?? null,
      printer_presence: activeSavedView.filters.printer_presence ?? null,
      tag: [...activeSavedView.filters.tag].sort(),
    }) !==
      JSON.stringify({
        ...currentViewFilters(),
        tag: [...selectedTags].sort(),
      });

  async function manageSavedView(action: () => Promise<SavedViewRead | void>, success: string) {
    try {
      await action();
      setLoadedSavedViews(await listSavedViews());
      toast.success(success);
    } catch (error) {
      toast.error(error);
      throw error;
    }
  }

  function duplicateViewName(name: string): string {
    const used = new Set(savedViews.map((view) => view.name.toLowerCase()));
    let candidate = `${name} copy`;
    let suffix = 2;
    while (used.has(candidate.toLowerCase())) candidate = `${name} copy ${suffix++}`;
    return candidate;
  }
  // The paginated grid. `keepPreviousData` (in the hook) holds the current page
  // on screen while a new search/folder loads, and results are cached per filter
  // set so backspacing a query or re-entering a folder is instant.
  const modelQuery = useModelList(
    {
      ...baseFilters,
      collection: selectedCollection ?? undefined,
      // A search spans the whole library; a folder view lists only its direct
      // children so subfolders' models don't leak into the parent (#30).
      direct: !searchQuery,
      q: searchQuery,
    },
    PAGE_SIZE,
    sortKey,
  );
  const multipartQuery = useMultipartModels(
    {
      collection: selectedCollection ?? undefined,
      direct: !searchQuery,
      q: searchQuery,
      tag: selectedTags.length ? selectedTags : undefined,
      favorites: favoritesOnly || undefined,
      limit: 500,
    },
    { enabled: libraryView !== "components" },
  );
  const multipartMembershipQuery = useMultipartModels(
    { limit: 500 },
    { enabled: libraryView === "components" },
  );
  const multipartGroupingQuery = useMultipartModels(
    {
      tag: selectedTags.length ? selectedTags : undefined,
      favorites: favoritesOnly || undefined,
      limit: 500,
    },
    { enabled: libraryView === "organized" && !searchQuery },
  );
  const multipartOutlinerQuery = useMultipartModels(
    {
      tag: selectedTags.length ? selectedTags : undefined,
      favorites: favoritesOnly || undefined,
      limit: 500,
    },
    { enabled: desktopOutliner && libraryView !== "components" },
  );
  const outlinerQuery = useOutlinerModels(baseFilters, 500, {
    enabled: desktopOutliner,
  });

  const models = useMemo(
    () => modelQuery.data?.pages.flatMap((page) => page.items) ?? [],
    [modelQuery.data],
  );
  const multipartModels = useMemo(() => multipartQuery.data ?? [], [multipartQuery.data]);
  const multipartMembership = useMemo(
    () => multipartMembershipQuery.data ?? [],
    [multipartMembershipQuery.data],
  );
  const memberModelIds = useMemo(
    () => new Set(multipartMembership.flatMap((item) => item.member_model_ids)),
    [multipartMembership],
  );
  const groupedModelIds = useMemo(
    () => new Set((multipartGroupingQuery.data ?? []).flatMap((item) => item.member_model_ids)),
    [multipartGroupingQuery.data],
  );
  const visibleMultipartModels = libraryView === "components" ? [] : multipartModels;
  const visibleModels = (() => {
    if (libraryView === "multipart") return [];
    if (libraryView === "components") {
      return models.filter((model) => memberModelIds.has(model.id));
    }
    if (libraryView === "organized" && !searchQuery) {
      return models.filter((model) => !groupedModelIds.has(model.id));
    }
    return models;
  })();
  const outlinerModels = outlinerQuery.data ?? [];
  const outlinerMultipartModels =
    libraryView === "components"
      ? (multipartMembershipQuery.data ?? [])
      : (multipartOutlinerQuery.data ?? []);
  // First load shows skeletons; a filter change keeps the previous page visible
  // and just flags `refreshing` for the subtle "Updating…" hint.
  const loading =
    modelQuery.isLoading ||
    multipartQuery.isLoading ||
    (libraryView === "organized" && !searchQuery && multipartGroupingQuery.isLoading) ||
    (libraryView === "components" && multipartMembershipQuery.isLoading);
  const refreshing = modelQuery.isFetching && !modelQuery.isFetchingNextPage && !loading;
  const loadingMore = modelQuery.isFetchingNextPage;
  const hasMore = modelQuery.hasNextPage ?? false;
  const fetchNextPage = modelQuery.fetchNextPage;
  const error =
    modelQuery.error?.message ??
    multipartQuery.error?.message ??
    multipartGroupingQuery.error?.message ??
    multipartMembershipQuery.error?.message ??
    null;
  function loadMore() {
    if (hasMore && !loadingMore) fetchNextPage();
  }
  function refresh() {
    void Promise.all([
      queryClient.invalidateQueries({ queryKey: queryKeys.models }),
      queryClient.invalidateQueries({ queryKey: queryKeys.multipartModels }),
      queryClient.invalidateQueries({ queryKey: queryKeys.tags }),
    ]);
  }

  // Multi-select for batch actions. The selected set is view-independent so it
  // survives load-more and search; backend per-model RBAC makes cross-collection
  // selections safe. We clear it when navigating folders (see below) so a hidden
  // off-screen selection doesn't linger.
  const [selectMode, setSelectMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [selectedCollectionIds, setSelectedCollectionIds] = useState<Set<number>>(new Set());
  const [batchBusy, setBatchBusy] = useState(false);
  const [selectingAll, setSelectingAll] = useState(false);
  const lastSelectedModelId = useRef<number | null>(null);
  const selectedModelSnapshot = useRef<Map<number, ModelListItem>>(new Map());
  const sortedModels = visibleModels;
  const libraryItems = sortLibraryItems(
    [
      ...visibleModels.map((value) => ({ kind: "model" as const, value })),
      ...visibleMultipartModels.map((value) => ({ kind: "multipart" as const, value })),
    ],
    sortKey,
  );

  const openTagEditor = useCallback((model: ModelListItem) => {
    setTagTarget(model);
    setTagDialogSession((session) => session + 1);
    setTagDialogOpen(true);
  }, []);

  const toggleSelect = useCallback(
    (id: number, range = false) => {
      setSelectedIds((prev) => {
        const next = new Set(prev);
        if (range && lastSelectedModelId.current !== null) {
          const from = sortedModels.findIndex((model) => model.id === lastSelectedModelId.current);
          const to = sortedModels.findIndex((model) => model.id === id);
          if (from >= 0 && to >= 0)
            sortedModels.slice(Math.min(from, to), Math.max(from, to) + 1).forEach((model) => {
              next.add(model.id);
              selectedModelSnapshot.current.set(model.id, model);
            });
        } else if (next.has(id)) next.delete(id);
        else next.add(id);
        lastSelectedModelId.current = id;
        const model = sortedModels.find((item) => item.id === id);
        if (model) selectedModelSnapshot.current.set(id, model);
        return next;
      });
    },
    [sortedModels],
  );

  function toggleCollectionSelect(id: number) {
    setSelectedCollectionIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const clearSelection = useCallback(() => {
    setSelectedIds(new Set());
    setSelectedCollectionIds(new Set());
    setSelectMode(false);
  }, []);

  function selectAllVisible() {
    setSelectedIds(new Set(sortedModels.map((m) => m.id)));
    selectedModelSnapshot.current = new Map(sortedModels.map((model) => [model.id, model]));
    setSelectedCollectionIds(new Set(visibleCollections.map((collection) => collection.id)));
  }

  async function selectAllMatching() {
    setSelectingAll(true);
    try {
      const all: ModelListItem[] = [];
      for (let offset = 0; ; offset += 500) {
        const page = await listModels({
          ...baseFilters,
          collection: selectedCollection ?? undefined,
          direct: !searchQuery,
          q: searchQuery,
          limit: 500,
          offset,
        });
        all.push(...page);
        if (page.length < 500) break;
      }
      setSelectedIds(new Set(all.map((model) => model.id)));
      selectedModelSnapshot.current = new Map(all.map((model) => [model.id, model]));
      toast.info(`${all.length} matching models selected`);
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
    () => collections.filter((collection) => selectedCollectionIds.has(collection.id)),
    [collections, selectedCollectionIds],
  );
  const selectionCount = selectedIds.size + selectedCollectionIds.size;

  useEffect(() => {
    function onShortcut(event: KeyboardEvent) {
      const typing =
        event.target instanceof HTMLElement &&
        event.target.matches("input, textarea, select, [contenteditable=true]");
      if (event.key === "/" && !typing) {
        event.preventDefault();
        document.querySelector<HTMLInputElement>('[aria-label="Search models"]')?.focus();
      } else if (event.key.toLowerCase() === "s" && !typing && auth.isAuthenticated) {
        event.preventDefault();
        setSelectMode(true);
      } else if (event.key === "Escape" && selectionCount > 0 && !typing) clearSelection();
    }
    window.addEventListener("keydown", onShortcut);
    return () => window.removeEventListener("keydown", onShortcut);
  }, [auth.isAuthenticated, clearSelection, selectionCount]);

  async function runCollectionBatch(
    verb: string,
    operation: (collection: CollectionRead) => Promise<CollectionRead>,
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
        failedFolders.push(collection.path);
      }
    }
    if (succeeded) toast.success(`${verb} ${succeeded}`);
    if (failed) toast.warning(`${failed} skipped`, failedFolders.join(" · "));
    refresh();
    clearSelection();
    setBatchBusy(false);
  }

  async function moveSelection(target: string, parentId: number | null) {
    setBatchBusy(true);
    let succeeded = 0;
    let failed = 0;
    const movedModelIds: number[] = [];
    const movedCollections: CollectionRead[] = [];
    const failureDetails: string[] = [];
    const originalModels = new Map(selectedModelSnapshot.current);
    try {
      if (selectedIdList.length) {
        const result = await batchInChunks((ids) => batchMoveModels(ids, target));
        succeeded += result.succeeded_count;
        failed += result.failed_count;
        movedModelIds.push(...result.succeeded_ids);
        failureDetails.push(
          ...result.failed.map((failure) => `Model #${failure.model_id}: ${failure.reason}`),
        );
      }
      for (const collection of selectedCollections) {
        try {
          await moveCollection(collection.id, parentId);
          succeeded += 1;
          movedCollections.push(collection);
        } catch {
          failed += 1;
          failureDetails.push(`Folder: ${collection.path}`);
        }
      }
      if (succeeded)
        toast.undo(`Moved ${succeeded}`, async () => {
          const groups = new Map<string, number[]>();
          for (const id of movedModelIds) {
            const original = originalModels.get(id)?.collection ?? "";
            groups.set(original, [...(groups.get(original) ?? []), id]);
          }
          for (const [collection, ids] of groups)
            for (let index = 0; index < ids.length; index += 500)
              await batchMoveModels(ids.slice(index, index + 500), collection);
          for (const collection of movedCollections)
            await moveCollection(collection.id, collection.parent_id);
          refresh();
          toast.success("Move undone");
        });
      if (failed) toast.warning(`${failed} skipped`, failureDetails.join(" · "));
      refresh();
      clearSelection();
    } catch (error) {
      toast.error(error);
    } finally {
      setBatchBusy(false);
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
          ...result.failed.map((failure) => `Model #${failure.model_id}: ${failure.reason}`),
        );
      }
      for (const collection of selectedCollections) {
        try {
          await deleteCollection(collection.id, true);
          succeeded += 1;
        } catch {
          failed += 1;
          failureDetails.push(`Folder: ${collection.path}`);
        }
      }
      if (deletedModelIds.length)
        toast.undo(
          `Moved ${deletedModelIds.length} model${deletedModelIds.length !== 1 ? "s" : ""} to trash`,
          async () => {
            await Promise.all(deletedModelIds.map(restoreModel));
            refresh();
            toast.success("Models restored");
          },
        );
      else if (succeeded) toast.success(`Deleted ${succeeded}`);
      if (failed) toast.warning(`${failed} skipped`, failureDetails.join(" · "));
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
    const originalModels = new Map(selectedModelSnapshot.current);
    try {
      const result = await batchInChunks((ids) => batchTagModels(ids, add, remove));
      if (result.succeeded_count)
        toast.undo(`Tagged ${result.succeeded_count}`, async () => {
          for (const id of result.succeeded_ids) {
            const original = originalModels.get(id);
            if (original) await updateModel(id, { tags: original.tags });
          }
          refresh();
          toast.success("Tags restored");
        });
      if (result.failed_count)
        toast.warning(
          `${result.failed_count} skipped`,
          result.failed.map((failure) => `#${failure.model_id}: ${failure.reason}`).join(" · "),
        );
      refresh();
      clearSelection();
    } catch (error) {
      toast.error(error);
    } finally {
      setBatchBusy(false);
    }
  }
  const hasActiveFilters =
    !!selectedCollection ||
    selectedTags.length > 0 ||
    selectedPrinterId !== null ||
    selectedPrinterPresence !== null ||
    !!query.trim() ||
    Object.values(structured).some((values) => values.length > 0) ||
    searchParams.has("uploaded_after") ||
    searchParams.has("uploaded_before");
  const displayCount = libraryItems.length;
  // Build the return target from the selected view as well as the router
  // snapshot. `router.replace()` is asynchronous, so a card clicked directly
  // after changing Library view must not capture the previous URL.
  const returnParams = new URLSearchParams(searchParams.toString());
  returnParams.delete("v");
  if (libraryView === "organized") returnParams.delete("type");
  else returnParams.set("type", libraryView);
  const currentLibraryHref = returnParams.size ? `/?${returnParams.toString()}` : "/";
  // While searching, the grid is a global result list, not a folder view: show
  // only collections whose name matches the query (anywhere in the tree), to
  // mirror the matching models. Without a query we fall back to the normal
  // folder explorer (immediate children of the selected collection).
  const visibleCollections = (() => {
    const needle = query.trim().toLowerCase();
    if (needle) {
      return collections
        .filter((c) => c.name.toLowerCase().includes(needle))
        .sort((a, b) => a.name.localeCompare(b.name));
    }
    return childCollections(collections, selectedCollection);
  })();
  const availableRecentFolders = recentFolders.filter(
    (path) =>
      path !== selectedCollection && collections.some((collection) => collection.path === path),
  );
  const breadcrumbs = useMemo(
    () => collectionBreadcrumbs(collections, selectedCollection),
    [collections, selectedCollection],
  );
  const selectedName = useMemo(
    () => selectedCollectionName(collections, selectedCollection),
    [collections, selectedCollection],
  );
  const selectedCollectionRow = useMemo(
    () => collections.find((c) => c.path === selectedCollection) ?? null,
    [collections, selectedCollection],
  );
  const canAdminSelectedCollection =
    user?.is_superuser || selectedCollectionRow?.effective_role === "admin";
  const hasWritableCollection = collections.some(canWriteCollection);
  const canUploadToVault =
    auth.isAuthenticated &&
    (user?.is_superuser || canWriteCollection(selectedCollectionRow) || hasWritableCollection);
  const uploadDefaultCollection =
    user?.is_superuser || canWriteCollection(selectedCollectionRow) ? selectedCollection : null;
  // Collections + tags are fetched by useCollections()/useTags() above; the
  // model grid + outliner come from useModelList()/useOutlinerModels() above.

  async function handleCreateCollection() {
    const name = newCollectionName.trim();
    if (!name) return;
    if (!auth.isAuthenticated) {
      auth.showAuthRequiredToast();
      return;
    }
    if (!canAdminSelectedCollection) {
      toast.warning("Admin access required");
      return;
    }
    try {
      const parentId = selectedCollection
        ? (collections.find((c) => c.path === selectedCollection)?.id ?? null)
        : null;
      await createCollection({ name, parent_id: parentId });
      setNewCollectionName("");
      setIsCreatingCollection(false);
      toast.success(`Collection "${name}" created`);
    } catch (e: any) {
      toast.error(e);
    }
  }

  async function handleMoveModel(modelId: number, targetCollection: string | null) {
    if (!auth.isAuthenticated) {
      auth.showAuthRequiredToast();
      return;
    }
    try {
      await updateModel(modelId, { collection: targetCollection ?? "" });
      toast.success("Moved");
      refresh();
    } catch (e: any) {
      toast.error(e);
    }
  }

  async function handleMoveCollection(collectionId: number, newParentId: number | null) {
    if (!auth.isAuthenticated) {
      auth.showAuthRequiredToast();
      return;
    }
    try {
      await moveCollection(collectionId, newParentId);
      toast.success("Moved");
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
      toast.success("Collection deleted");
      refresh();
    } catch (e: any) {
      toast.error(e);
    }
  }

  async function saveCollectionTags(collection: CollectionRead, nextTags: string[]) {
    try {
      await replaceCollectionTags(collection.id, nextTags);
      await collectionsQuery.refetch();
      toast.success(`Tags updated for ${collection.name}`);
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
    setFavoritesOnly(next);
    const params = new URLSearchParams(searchParams.toString());
    if (next) params.set("favorites", "true");
    else params.delete("favorites");
    router.replace(params.size ? `/?${params}` : "/", { scroll: false });
  }

  function toggleSelectMode() {
    if (selectMode) clearSelection();
    else setSelectMode(true);
  }

  function selectSort(value: SortKey) {
    setSortKey(value);
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
    router.replace(qs ? `/?${qs}` : "/", { scroll: false });
  }

  function clearAllFilters() {
    setSelectedTags([]);
    setSelectedPrinterId(null);
    setSelectedPrinterPresence(null);
    setFavoritesOnly(false);
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
      "uploaded_after",
      "uploaded_before",
    ])
      params.delete(key);
    const qs = params.toString();
    router.replace(qs ? `/?${qs}` : "/", { scroll: false });
  }

  const activeFilterItems: { label: string; onRemove: () => void }[] = (() => {
    const items: { label: string; onRemove: () => void }[] = [];
    if (query.trim()) {
      items.push({ label: `${ui("Search")}: ${query.trim()}`, onRemove: clearSearch });
    }
    for (const slug of selectedTags) {
      const tag = tags.find((item) => item.slug === slug);
      items.push({
        label: `${ui("Tag")}: ${tag?.name ?? slug}`,
        onRemove: () => setSelectedTags((current) => current.filter((item) => item !== slug)),
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
        label: selectedPrinterPresence === "none" ? "Vault only" : "On a printer",
        onRemove: () => setSelectedPrinterPresence(null),
      });
    }
    for (const key of STRUCTURED_FILTER_KEYS) {
      const values = structured[key];
      for (const value of values) {
        items.push({
          label: `${key.replaceAll("_", " ")}: ${value.replaceAll("_", " ")}`,
          onRemove: () =>
            setStructuredFilter(
              key,
              values.filter((item) => item !== value),
            ),
        });
      }
    }
    for (const key of ["uploaded_after", "uploaded_before"] as const) {
      const value = searchParams.get(key);
      if (value)
        items.push({
          label: `${ui(key === "uploaded_after" ? "Uploaded after" : "Uploaded before")}: ${value}`,
          onRemove: () => {
            const params = new URLSearchParams(searchParams.toString());
            params.delete(key);
            router.replace(params.size ? `/?${params}` : "/", { scroll: false });
          },
        });
    }
    return items;
  })();

  return (
    <Localized>
      <>
        <Modal
          open={saveViewOpen}
          onClose={() => {
            if (!saveViewBusy) {
              setSaveViewOpen(false);
              setSaveViewName("");
            }
          }}
          title="Save current view"
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
              <span className="text-sm font-medium text-foreground">View name</span>
              <Input
                autoFocus
                value={saveViewName}
                onChange={(event) => setSaveViewName(event.target.value)}
                maxLength={128}
                placeholder="Ready to print"
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
                Cancel
              </Button>
              <Button type="submit" loading={saveViewBusy} disabled={!saveViewName.trim()}>
                Save view
              </Button>
            </div>
          </form>
        </Modal>
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
        <NewMultipartModelModal
          key={selectedCollectionRow?.id ?? "vault"}
          open={multipartCreateOpen}
          onClose={() => setMultipartCreateOpen(false)}
          collectionId={selectedCollectionRow?.id ?? null}
          collections={collections}
          returnTo={currentLibraryHref}
        />
        <MobileFilterDrawer
          open={filterDrawerOpen}
          onClose={closeDrawer}
          collections={collections}
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
          loading={facetsLoading || facetQuery.isLoading}
          structuredFilters={
            <StructuredFilters
              facets={facetQuery.data}
              loading={facetQuery.isLoading}
              error={facetQuery.isError}
              active={structured}
              onChange={setStructuredFilter}
              uploadedAfter={searchParams.get("uploaded_after") ?? undefined}
              uploadedBefore={searchParams.get("uploaded_before") ?? undefined}
              onDateChange={(key, value) => {
                const params = new URLSearchParams(searchParams.toString());
                if (value) params.set(key, value);
                else params.delete(key);
                router.replace(params.size ? `/?${params}` : "/", { scroll: false });
              }}
              onClearAll={clearStructuredFilters}
            />
          }
          libraryView={libraryView}
          onLibraryViewChange={handleLibraryViewChange}
        />

        {/* Stitch layout: filter sidebar + main content */}
        <FilterSidebar
          collections={collections}
          models={outlinerModels}
          multipartModels={outlinerMultipartModels}
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
          onMoveModel={handleMoveModel}
          onMoveCollection={handleMoveCollection}
          onDeleteCollection={handleDeleteCollection}
          canViewPrinters={canViewPrinters}
          loading={facetsLoading || facetQuery.isLoading}
          structuredFilters={
            <StructuredFilters
              facets={facetQuery.data}
              loading={facetQuery.isLoading}
              error={facetQuery.isError}
              active={structured}
              onChange={setStructuredFilter}
              uploadedAfter={searchParams.get("uploaded_after") ?? undefined}
              uploadedBefore={searchParams.get("uploaded_before") ?? undefined}
              onDateChange={(key, value) => {
                const params = new URLSearchParams(searchParams.toString());
                if (value) params.set(key, value);
                else params.delete(key);
                router.replace(params.size ? `/?${params}` : "/", { scroll: false });
              }}
              onClearAll={clearStructuredFilters}
            />
          }
          libraryView={libraryView}
          onLibraryViewChange={handleLibraryViewChange}
        />

        <main
          className="flex-1 overflow-y-auto bg-background flex flex-col relative pb-24 md:pb-0"
          onDragEnter={onMainDragEnter}
          onDragOver={onMainDragOver}
          onDragLeave={onMainDragLeave}
          onDrop={onMainDrop}
        >
          {isDragging && canUploadToVault && (
            <div className="pointer-events-none absolute inset-0 z-40 flex items-center justify-center border-2 border-dashed border-primary bg-primary/5">
              <span className="bg-background border border-border rounded px-4 py-2 font-mono text-xs uppercase tracking-widest shadow">
                Drop to upload
              </span>
            </div>
          )}
          {/* Breadcrumb */}
          <nav className="px-4 sm:px-6 py-3 bg-background border-b border-border flex items-center space-x-2 text-sm tracking-tight">
            {selectedCollection && breadcrumbs.length > 0 ? (
              <>
                <button
                  onClick={() => handleCollectionChange(null)}
                  className="text-muted-foreground hover:text-foreground transition-colors"
                >
                  All Models
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
                All Models
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
                  Recent folders
                </p>
                {availableRecentFolders.map((path) => (
                  <button
                    key={path}
                    role="menuitem"
                    type="button"
                    onClick={() => {
                      handleCollectionChange(path);
                      setRecentFoldersOpen(false);
                    }}
                    className="flex w-full items-center gap-2 rounded px-2.5 py-2 text-left text-xs transition-colors hover:bg-popover-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <Folder className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                    <span className="truncate">{path}</span>
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
                  Clear recent folders
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
                    {selectedName ?? "All Models"}
                  </h1>
                  <p className="text-sm text-muted-foreground">
                    {loading
                      ? "Loading..."
                      : `${displayCount} item${displayCount !== 1 ? "s" : ""} shown${selectedName ? ` in this collection` : ""}`}
                    {refreshing && (
                      <span className="ml-2 font-mono text-xs text-muted-foreground">
                        Updating...
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
                    canUploadToVault ? "Upload artifacts" : "Sign in and get edit access to upload"
                  }
                  className="shrink-0 sm:hidden"
                >
                  Upload
                </Button>
              </div>

              <div className="flex w-full min-w-0 flex-col gap-3 sm:w-auto sm:flex-row sm:items-center sm:justify-end">
                <div className="grid w-full min-w-0 grid-cols-3 gap-2 sm:hidden">
                  <Button
                    type="button"
                    variant="outline"
                    onClick={openDrawer}
                    className="w-full min-w-0 px-2"
                  >
                    <SlidersHorizontal className="h-4 w-4 text-muted-foreground" />
                    <span className="min-w-0 truncate">Filters</span>
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
                          <span className="flex-1">Favorites</span>
                          {favoritesOnly && <Check className="h-3.5 w-3.5" />}
                        </button>
                        <SavedViewSelector
                          views={savedViews}
                          activeId={activeSavedViewId}
                          modified={savedViewModified}
                          onSelect={(view) => {
                            applySavedView(view);
                            setMoreOpen(false);
                          }}
                          onCreate={() => {
                            setMoreOpen(false);
                            setSaveViewOpen(true);
                          }}
                          onUpdate={(view) =>
                            manageSavedView(
                              () => updateSavedView(view.id, { filters: currentViewFilters() }),
                              "Saved view updated",
                            )
                          }
                          onRename={(view, name) =>
                            manageSavedView(
                              () => updateSavedView(view.id, { name }),
                              "Saved view renamed",
                            )
                          }
                          onDuplicate={(view) =>
                            manageSavedView(
                              () => createSavedView(duplicateViewName(view.name), view.filters),
                              "Saved view duplicated",
                            )
                          }
                          onDelete={(view) =>
                            manageSavedView(async () => {
                              await deleteSavedView(view.id);
                              if (activeSavedViewId === view.id) setActiveSavedViewId(null);
                            }, "Saved view deleted")
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
                          title="Select Models and folders (S)"
                          onClick={() => {
                            toggleSelectMode();
                            setMoreOpen(false);
                          }}
                          className={`flex w-full items-center gap-2 rounded px-2.5 py-2 text-left text-sm transition-colors hover:bg-popover-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${selectMode ? "bg-accent text-accent-foreground" : ""}`}
                        >
                          <CheckSquare className="h-4 w-4" />
                          <span className="flex-1">{selectMode ? "Done" : "Select"}</span>
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
                    onClick={openDrawer}
                    className="h-10 md:hidden sm:h-8"
                  >
                    <SlidersHorizontal className="h-4 w-4 text-muted-foreground" />
                    Filters
                  </Button>
                  <Button
                    variant="outline"
                    size="xs"
                    onClick={handleOpenCreateCollection}
                    disabled={!canAdminSelectedCollection}
                    title={
                      canAdminSelectedCollection
                        ? "Create a collection"
                        : "Admin access required for this collection"
                    }
                    className="hidden md:inline-flex"
                  >
                    <Plus className="w-4 h-4 text-muted-foreground" />
                    New collection
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
                        ? "Upload artifacts"
                        : "Sign in and get edit access to upload"
                    }
                    className="h-10 sm:h-8"
                  >
                    Upload
                  </Button>
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
                    <Plus className="h-4 w-4" /> {t("multipart.new")}
                  </Button>
                </div>
                {auth.isAuthenticated && (
                  <div className="hidden w-full flex-wrap items-center gap-2 border-t border-border pt-3 sm:flex sm:w-auto sm:border-t-0 sm:pt-0">
                    <Button
                      variant={favoritesOnly ? "secondary" : "outline"}
                      size="xs"
                      aria-pressed={favoritesOnly}
                      onClick={toggleFavorites}
                      className="h-10 sm:h-8"
                    >
                      <Star className={`h-4 w-4 ${favoritesOnly ? "fill-current" : ""}`} />{" "}
                      Favorites
                    </Button>
                    <SavedViewSelector
                      views={savedViews}
                      activeId={activeSavedViewId}
                      modified={savedViewModified}
                      onSelect={applySavedView}
                      onCreate={() => setSaveViewOpen(true)}
                      onUpdate={(view) =>
                        manageSavedView(
                          () => updateSavedView(view.id, { filters: currentViewFilters() }),
                          "Saved view updated",
                        )
                      }
                      onRename={(view, name) =>
                        manageSavedView(
                          () => updateSavedView(view.id, { name }),
                          "Saved view renamed",
                        )
                      }
                      onDuplicate={(view) =>
                        manageSavedView(
                          () => createSavedView(duplicateViewName(view.name), view.filters),
                          "Saved view duplicated",
                        )
                      }
                      onDelete={(view) =>
                        manageSavedView(async () => {
                          await deleteSavedView(view.id);
                          if (activeSavedViewId === view.id) setActiveSavedViewId(null);
                        }, "Saved view deleted")
                      }
                      triggerClassName="h-10 sm:h-8"
                    />
                    <Button
                      variant={selectMode ? "secondary" : "outline"}
                      size="xs"
                      aria-pressed={selectMode}
                      title="Select Models and folders (S)"
                      onClick={toggleSelectMode}
                      className="h-10 sm:h-8"
                    >
                      <CheckSquare className="w-4 h-4" />
                      {selectMode ? "Done" : "Select"}
                    </Button>
                  </div>
                )}
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

          {selectedCollectionRow && (
            <div className="space-y-3 border-b border-border px-4 py-3 sm:px-6">
              <EntityTagsDialog
                entityLabel={selectedCollectionRow.name}
                tags={selectedCollectionRow.tags}
                availableTags={tags}
                canEdit={!!user?.is_superuser || canWriteCollection(selectedCollectionRow)}
                help="Collection tags are inherited by Models in this collection and every descendant for search and filtering."
                onSave={(nextTags) => saveCollectionTags(selectedCollectionRow, nextTags)}
              />
              <CollectionReadme
                key={selectedCollectionRow.id}
                collectionId={selectedCollectionRow.id}
                canEdit={!!user?.is_superuser || canWriteCollection(selectedCollectionRow)}
              />
            </div>
          )}

          {activeFilterItems.length > 0 && (
            <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-3 sm:px-6">
              <span className="text-3xs font-mono uppercase tracking-wider text-muted-foreground">
                Filters
              </span>
              {activeFilterItems.map((item) => (
                <Button
                  key={item.label}
                  type="button"
                  variant="outline"
                  size="xs"
                  onClick={item.onRemove}
                  className="gap-1.5"
                  title={`Remove ${item.label}`}
                >
                  {item.label}
                  <X className="h-3 w-3" aria-hidden />
                </Button>
              ))}
              <Button type="button" variant="ghost" size="xs" onClick={clearAllFilters}>
                Clear all
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
              setDocView(view);
              const params = new URLSearchParams(searchParams.toString());
              if (view === "models") params.delete("v");
              else params.set("v", view);
              router.replace(params.size ? `/?${params}` : "/", { scroll: false });
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
                        ? `New subcollection in "${selectedName ?? selectedCollection}"...`
                        : "Collection name..."
                      : "Sign in to add"
                  }
                  disabled={!auth.isAuthenticated}
                  className="flex-1 max-w-xs bg-background text-foreground text-sm border border-border rounded px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent disabled:opacity-50"
                />
                <button
                  type="submit"
                  disabled={!newCollectionName.trim() || !auth.isAuthenticated}
                  className="px-3 py-1.5 text-xs font-medium text-primary-foreground bg-primary rounded hover:bg-primary-hover transition-colors disabled:opacity-50"
                >
                  Create
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setIsCreatingCollection(false);
                    setNewCollectionName("");
                  }}
                  className="px-3 py-1.5 text-xs font-medium text-foreground bg-background border border-border rounded hover:bg-muted transition-colors"
                >
                  Cancel
                </button>
              </form>
            </div>
          )}

          {docView === "models" && selectMode && (
            <div className="px-4 sm:px-6 py-2 bg-muted border-b border-border flex items-center gap-3 text-xs">
              <span className="font-mono text-muted-foreground">{selectionCount} selected</span>
              <button
                type="button"
                onClick={selectAllVisible}
                className="font-medium text-primary hover:underline"
              >
                Select all on screen ({sortedModels.length + visibleCollections.length})
              </button>
              <button
                type="button"
                onClick={() => void selectAllMatching()}
                disabled={selectingAll}
                className="font-medium text-primary hover:underline disabled:opacity-50"
              >
                {selectingAll ? "Selecting…" : "Select all matching models"}
              </button>
              {selectionCount > 0 && (
                <button
                  type="button"
                  onClick={() => {
                    setSelectedIds(new Set());
                    setSelectedCollectionIds(new Set());
                  }}
                  className="font-medium text-muted-foreground hover:text-foreground"
                >
                  Clear
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
              {error && (
                <div className="mx-6 mt-4 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
                  {error}
                </div>
              )}

              {loading ? (
                viewMode === "grid" ? (
                  <ModelGridSkeleton />
                ) : (
                  <ModelListSkeleton />
                )
              ) : sortedModels.length === 0 &&
                visibleMultipartModels.length === 0 &&
                visibleCollections.length === 0 ? (
                <EmptyState
                  title="No models found"
                  description={
                    query ||
                    selectedCollection ||
                    selectedTags.length ||
                    selectedPrinterId ||
                    selectedPrinterPresence
                      ? "Try clearing some filters."
                      : "Upload a model when you're ready, or skim the wiki first if this is a new install."
                  }
                  action={
                    hasActiveFilters ? (
                      <Button type="button" variant="outline" size="xs" onClick={clearAllFilters}>
                        Clear all filters
                      </Button>
                    ) : (
                      <div className="flex flex-wrap items-center justify-center gap-2">
                        {user?.is_superuser && (
                          <Button variant="outline" onClick={() => router.push("/getting-started")}>
                            {t("setup.resume")}
                          </Button>
                        )}
                        <Button
                          type="button"
                          size="sm"
                          disabled={!canUploadToVault}
                          onClick={() => {
                            setDropPreload({ files: [], mode: "files" });
                            setUploadOpen(true);
                          }}
                        >
                          Upload files
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
                          Import from URL
                        </Button>
                        {user?.is_superuser && (
                          <Button asChild variant="outline" size="sm">
                            <Link href="/settings">Connect folder or NAS</Link>
                          </Button>
                        )}
                      </div>
                    )
                  }
                  className="flex-1 py-20 animate-panel-in"
                />
              ) : viewMode === "grid" ? (
                <div key="grid" className={`${compact ? "p-3" : "p-4 sm:p-6"} animate-panel-in`}>
                  <div
                    className={`stagger-children grid grid-cols-1 ${compact ? "gap-2 sm:grid-cols-[repeat(auto-fill,minmax(260px,260px))]" : "gap-4 sm:grid-cols-[repeat(auto-fill,minmax(340px,340px))]"}`}
                  >
                    {visibleCollections.map((collection) => (
                      <CollectionFolderCard
                        key={collection.id}
                        collection={collection}
                        onSelect={handleCollectionChange}
                        onDropModel={canUploadToVault ? handleMoveModel : undefined}
                        selectable={selectMode}
                        selected={selectedCollectionIds.has(collection.id)}
                        onToggleSelect={toggleCollectionSelect}
                      />
                    ))}
                    {libraryItems.map((item) =>
                      item.kind === "multipart" ? (
                        <MultipartModelCard
                          key={`multipart-${item.value.id}`}
                          item={item.value}
                          returnTo={currentLibraryHref}
                          availableTags={tags}
                          onDataChange={refresh}
                        />
                      ) : (
                        <ModelCard
                          key={item.value.id}
                          model={item.value}
                          selectable={selectMode}
                          selected={selectedIds.has(item.value.id)}
                          onToggleSelect={toggleSelect}
                          draggable={canUploadToVault && !selectMode}
                          onEditTags={openTagEditor}
                        />
                      ),
                    )}
                  </div>
                  <LoadMore hasMore={hasMore} loading={loadingMore} onClick={loadMore} />
                </div>
              ) : (
                <div key="list" className="flex-1 overflow-y-auto animate-panel-in">
                  <div className="flex flex-col">
                    <div className="flex items-center gap-3 px-4 py-2 border-b border-border text-xs font-mono text-muted-foreground uppercase tracking-wider bg-muted/50">
                      <span className="w-10 flex-shrink-0">Thumb</span>
                      <span className="flex-1">Name</span>
                      <span className="w-24 text-right hidden sm:block">Collection</span>
                      <span className="w-20 text-right">Contents</span>
                      <span className="w-24 text-right hidden md:block">Updated</span>
                    </div>
                    {visibleCollections.map((collection) => (
                      <CollectionListRow
                        key={collection.id}
                        collection={collection}
                        onSelect={handleCollectionChange}
                        onDropModel={canUploadToVault ? handleMoveModel : undefined}
                        selectable={selectMode}
                        selected={selectedCollectionIds.has(collection.id)}
                        onToggleSelect={toggleCollectionSelect}
                      />
                    ))}
                    {libraryItems.map((item) =>
                      item.kind === "multipart" ? (
                        <MultipartModelListRow
                          key={`multipart-${item.value.id}`}
                          item={item.value}
                          returnTo={currentLibraryHref}
                        />
                      ) : (
                        <ModelListRow
                          key={item.value.id}
                          model={item.value}
                          selectable={selectMode}
                          selected={selectedIds.has(item.value.id)}
                          onToggleSelect={toggleSelect}
                          draggable={canUploadToVault && !selectMode}
                        />
                      ),
                    )}
                  </div>
                  <LoadMore hasMore={hasMore} loading={loadingMore} onClick={loadMore} />
                </div>
              )}
            </div>
          )}
        </main>

        {docView === "models" && (
          <BatchToolbar
            modelCount={selectedIds.size}
            selectedCollections={selectedCollections}
            collections={collections}
            tags={tags}
            busy={batchBusy}
            canMoveToRoot={!!user?.is_superuser}
            onMoveSelection={moveSelection}
            onRenameCollections={(names) =>
              runCollectionBatch("Renamed", (collection) =>
                renameCollection(collection.id, names[collection.id]),
              )
            }
            onApplyTags={tagSelection}
            onDeleteSelection={deleteSelection}
            onClear={clearSelection}
          />
        )}
        {tagTarget && (
          <ModelTagsDialog
            key={`${tagTarget.id}:${tagDialogSession}`}
            model={tagTarget}
            suggestions={tags}
            open={tagDialogOpen}
            onClose={() => setTagDialogOpen(false)}
            onSaved={(nextTags) => {
              setTagTarget((current) => (current ? { ...current, tags: nextTags } : current));
              refresh();
            }}
          />
        )}
      </>
    </Localized>
  );
}

// Makes a collection card accept a dragged model: highlights on hover and calls
// onDropModel(modelId, path) on drop. Ignores OS file drags (no MODEL_DND_MIME)
// so those still bubble to the main upload handler.
function useModelDropTarget(path: string, onDropModel?: (modelId: number, path: string) => void) {
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
      const id = Number(e.dataTransfer.getData(MODEL_DND_MIME));
      if (id) onDropModel(id, path);
    },
  };
  return { dragOver, handlers };
}

function CollectionFolderCard({
  collection,
  onSelect,
  onDropModel,
  selectable,
  selected,
  onToggleSelect,
}: {
  collection: CollectionRead;
  onSelect: (path: string) => void;
  onDropModel?: (modelId: number, path: string) => void;
  selectable?: boolean;
  selected?: boolean;
  onToggleSelect?: (id: number) => void;
}) {
  const { dragOver, handlers } = useModelDropTarget(collection.path, onDropModel);
  return (
    <Localized>
      <div
        role="button"
        tabIndex={0}
        data-collection-path={collection.path}
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
        className={`animate-card-in group flex flex-col text-left bg-muted border rounded-lg hover:shadow-sm transition-[border-color,box-shadow,transform] duration-fast active:scale-[0.99] relative overflow-hidden ${
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
                ariaLabel={`Select folder ${collection.name}`}
              />
            </span>
          )}
          <Folder className="w-12 h-12 sm:w-16 sm:h-16 text-primary/30" />
        </div>
        <div className="p-3 border-t border-border">
          <div className="flex items-center justify-end gap-2 mb-0.5">
            <span className="text-3xs text-muted-foreground font-mono">
              {collection.model_count} models
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
  onSelect,
  onDropModel,
  selectable,
  selected,
  onToggleSelect,
}: {
  collection: CollectionRead;
  onSelect: (path: string) => void;
  onDropModel?: (modelId: number, path: string) => void;
  selectable?: boolean;
  selected?: boolean;
  onToggleSelect?: (id: number) => void;
}) {
  const { dragOver, handlers } = useModelDropTarget(collection.path, onDropModel);
  return (
    <Localized>
      <div
        role="button"
        tabIndex={0}
        data-collection-path={collection.path}
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
            ariaLabel={`Select folder ${collection.name}`}
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
            {collection.path}
          </span>
        </span>
        <span className="w-24 text-right text-xs font-mono text-muted-foreground truncate hidden sm:block">
          Folder
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
  returnTo,
}: {
  item: MultipartModelListItem;
  returnTo: string;
}) {
  const { t } = useI18n();
  const thumb = useAuthenticatedAssetUrl(item.cover_thumbnail_url);
  return (
    <Link
      href={`/multipart-models/${item.id}?${new URLSearchParams({ return: returnTo }).toString()}`}
      aria-label={item.name}
      className="group flex items-center gap-2 border-b border-border px-4 py-3 transition-colors hover:bg-muted active:bg-muted md:gap-3"
    >
      <span className="flex h-8 w-8 shrink-0 items-center justify-center overflow-hidden rounded border border-primary/30 bg-muted md:h-10 md:w-10">
        {thumb ? (
          <img src={thumb} alt="" className="h-full w-full object-cover" loading="lazy" />
        ) : (
          <Boxes className="h-4 w-4 text-primary" aria-hidden />
        )}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium text-foreground">{item.name}</span>
        <span className="mt-0.5 inline-flex rounded border border-primary/30 bg-card px-1.5 py-px font-mono text-3xs font-semibold uppercase tracking-wider text-primary">
          {t("multipart.badge")}
        </span>
      </span>
      <span className="hidden w-24 truncate text-right font-mono text-xs text-muted-foreground sm:block">
        {item.collection || "—"}
      </span>
      <span className="w-20 text-right font-mono text-xs text-muted-foreground">
        {item.model_count}
      </span>
      <span className="hidden w-24 text-right font-mono text-xs text-muted-foreground md:block">
        {timeAgo(item.updated_at)}
      </span>
    </Link>
  );
}

function ModelListRow({
  model,
  selectable = false,
  selected = false,
  onToggleSelect,
  draggable = false,
}: {
  model: ModelListItem;
  selectable?: boolean;
  selected?: boolean;
  onToggleSelect?: (id: number, range?: boolean) => void;
  draggable?: boolean;
}) {
  const router = useRouter();
  const thumb = useAuthenticatedAssetUrl(model.thumbnail_url);
  const printerPresence = model.printer_presence ?? [];
  return (
    <Localized>
      <Link
        href={`/models/${model.id}`}
        draggable={draggable}
        onDragStart={
          draggable
            ? (e) => {
                e.dataTransfer.setData(MODEL_DND_MIME, String(model.id));
                e.dataTransfer.effectAllowed = "move";
              }
            : undefined
        }
        onMouseEnter={() => router.prefetch(`/models/${model.id}`)}
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
            ariaLabel={`Select ${model.name}`}
          />
        )}
        <div className="w-8 h-8 md:w-10 md:h-10 rounded bg-muted flex-shrink-0 overflow-hidden border border-border">
          {thumb ? (
            <img
              src={thumb}
              alt={model.name}
              className="h-full w-full object-cover"
              loading="lazy"
            />
          ) : (
            <div className="flex h-full w-full items-center justify-center">
              <FileText className="h-4 w-4 text-muted-foreground/50" />
            </div>
          )}
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-foreground truncate">{model.name}</p>
          {model.tags.length > 0 && (
            <div className="flex gap-1 mt-0.5">
              {model.tags.slice(0, 2).map((tag) => (
                <span
                  key={tag}
                  className="bg-accent text-accent-foreground px-1 py-px rounded font-mono text-3xs uppercase tracking-wider"
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
          {model.collection || "—"}
        </span>
        <span className="w-20 text-right text-xs font-mono text-muted-foreground">
          {model.file_count}
        </span>
        <span className="w-24 text-right text-xs font-mono text-muted-foreground hidden md:block">
          {timeAgo(model.updated_at)}
        </span>
      </Link>
    </Localized>
  );
}

function LoadMore({
  hasMore,
  loading,
  onClick,
}: {
  hasMore: boolean;
  loading: boolean;
  onClick: () => void;
}) {
  if (!hasMore) return null;
  return (
    <Localized>
      <div className="flex justify-center mt-6 pb-6">
        <button
          onClick={onClick}
          disabled={loading}
          className="px-4 py-2 rounded border border-border bg-background text-foreground hover:bg-muted disabled:opacity-50 font-mono text-[13px] uppercase tracking-wider transition-colors"
        >
          {loading ? "Loading..." : "Load more"}
        </button>
      </div>
    </Localized>
  );
}

export function ModelGridSkeleton() {
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
