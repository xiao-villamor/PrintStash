"use client";

import { useCallback, useEffect, useMemo, useState, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "@/lib/navigation";
import { CollectionRead, ModelListItem, PrinterRead, TagRead } from "@/types";
import { ModelCard, MODEL_DND_MIME } from "@/components/model-card";
import { BatchToolbar } from "@/components/batch-toolbar";
import { Checkbox } from "@/components/ui/checkbox";
import { CollectionReadme } from "@/components/collection-readme";
import { DocumentBrowser } from "@/components/document-browser";
import { FilterSidebar } from "@/components/filter-sidebar";
import { MobileFilterDrawer } from "@/components/mobile-filter-drawer";
import { UploadModal, UploadMode } from "@/components/upload-modal";
import { Skeleton } from "@/components/ui/skeleton";
import { useMobileFilterDrawer } from "@/lib/mobile-filter-context";
import {
  SlidersHorizontal,
  BookOpen,
  Grid,
  List,
  FileText,
  MoreVertical,
  Printer,
  Folder,
  ChevronRight,
  Plus,
  CheckSquare,
} from "lucide-react";
import { createCollection, updateModel, moveCollection, deleteCollection, batchMoveModels, batchTagModels, batchDeleteModels, } from "@/lib/api";
import { isMeshFile, isGcodeFile, extensionOf, walkEntries, entriesFromDataTransfer, BulkItem } from "@/lib/bulk-upload";
import { useCollections, useModelList, useOutlinerModels, usePrinters, useTags, useVaultStats, type ModelListFilters, } from "@/lib/queries";
import { queryKeys } from "@/lib/query-client";
import { toast } from "@/lib/toast";
import { useRequireAuth } from "@/lib/use-require-auth";
import { useAuth } from "@/lib/auth-context";
import { Link } from "@/lib/navigation";
import { timeAgo } from "@/lib/format";
import { rememberLastCollection, readLastView, rememberLastView } from "@/lib/last-collection";
import { useAuthenticatedAssetUrl } from "@/lib/use-authenticated-asset-url";

type SortKey = "date-desc" | "date-asc" | "name-asc" | "name-desc";
type ViewMode = "grid" | "list";

const PAGE_SIZE = 60;

function sortModels(models: ModelListItem[], key: SortKey): ModelListItem[] {
  const sorted = [...models];
  switch (key) {
    case "date-desc":
      sorted.sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime());
      break;
    case "date-asc":
      sorted.sort((a, b) => new Date(a.updated_at).getTime() - new Date(b.updated_at).getTime());
      break;
    case "name-asc":
      sorted.sort((a, b) => a.name.localeCompare(b.name));
      break;
    case "name-desc":
      sorted.sort((a, b) => b.name.localeCompare(a.name));
      break;
  }
  return sorted;
}


function childCollections(
  collections: CollectionRead[],
  selectedPath: string | null,
): CollectionRead[] {
  const selected = selectedPath
    ? collections.find((c) => c.path === selectedPath)
    : null;
  const parentId = selectedPath ? selected?.id ?? -1 : null;
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
  const router = useRouter();
  const searchParams = useSearchParams();
  const auth = useRequireAuth();
  const { user } = useAuth();
  // Shared taxonomy facets from the TanStack Query cache: one cache entry shared
  // with the detail/upload views, revalidated on focus, and refetched after any
  // collection/tag mutation (the api layer invalidates the query cache).
  const collectionsQuery = useCollections();
  const tagsQuery = useTags();
  // Library-wide totals (access-scoped, excludes trashed + sentinel models).
  // Used to label the "All Models" root, where the grid only fetches the models
  // sitting directly at the root (see #30).
  const vaultStatsQuery = useVaultStats();
  const collections = collectionsQuery.data ?? [];
  const tags = tagsQuery.data ?? [];
  // Printers (superuser-only filter) share the same cache as the printers page
  // and send-to dialog; gated so non-admins don't fetch a list they can't use.
  const printers =
    usePrinters({ enabled: !!user?.is_superuser }).data ?? initial?.printers ?? [];
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [selectedPrinterId, setSelectedPrinterId] = useState<number | null>(null);
  const [selectedPrinterPresence, setSelectedPrinterPresence] = useState<"any" | "none" | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>("grid");
  // Seed from the URL (`?v=docs`), falling back to the remembered tab, so
  // returning from a document (Back or the logo) lands on the Documents tab
  // instead of resetting to Models.
  const [docView, setDocView] = useState<"models" | "docs">(
    searchParams.get("v") === "docs" ? "docs" : readLastView(),
  );
  const [uploadOpen, setUploadOpen] = useState(false);
  const [dropPreload, setDropPreload] = useState<{ files: File[]; items?: BulkItem[]; mode: UploadMode } | null>(null);
const [dropCollection, setDropCollection] = useState<string | null>(null);
const [isDragging, setIsDragging] = useState(false);
const dragEnterCount = useRef(0);

function classifyDrop(files: File[]): { files: File[]; mode: UploadMode } | null {
  const meshes = files.filter((f) => isMeshFile(f.name));
  const gcodes = files.filter((f) => isGcodeFile(f.name));
  const zips   = files.filter((f) => extensionOf(f.name) === ".zip");
  if (meshes.length >= 2) return { mode: "bulk", files: meshes };
  if (meshes.length === 1) return { mode: "files", files: [...meshes, ...gcodes.slice(0, 1)] };
  if (gcodes.length > 0)  return { mode: "files", files: [gcodes[0]] };
  if (zips.length > 0)    return { mode: "zip",   files: [zips[0]] };
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
  if (--dragEnterCount.current <= 0) { dragEnterCount.current = 0; setIsDragging(false); }
}
async function onMainDrop(e: React.DragEvent) {
  if (!isFileDrag(e)) return; // a model dropped on empty space is a no-op
  e.preventDefault();
  dragEnterCount.current = 0;
  setIsDragging(false);
  if (!canUploadToVault) return;
  const collPath = (e.target as Element).closest("[data-collection-path]")
    ?.getAttribute("data-collection-path") ?? null;
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

  // Collection selection lives in the URL (`?c=<path>`) so it resets when the
  // user navigates away (e.g. to Settings) and clicks "Vault" again — that link
  // points at "/" with no param. Deriving it straight from the param (instead of
  // mirroring into state) means a folder switch just re-keys the model query;
  // `keepPreviousData` holds the old cards on screen until the new page lands, so
  // there's no manual clearing or loading flash.
  const selectedCollection = searchParams.get("c") || null;
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

  function handleCollectionChange(path: string | null) {
    setSelectedIds(new Set());
    const params = new URLSearchParams(searchParams.toString());
    if (path) params.set("c", path);
    else params.delete("c");
    const qs = params.toString();
    router.replace(qs ? `/?${qs}` : "/", { scroll: false });
  }

  useEffect(() => {
    if (searchParams.get("upload") === "1") {
      setUploadOpen(true);
      const params = new URLSearchParams(searchParams.toString());
      params.delete("upload");
      const qs = params.toString();
      router.replace(qs ? `/?${qs}` : "/", { scroll: false });
    }
  }, [searchParams, router]);

  const query = searchParams.get("q") ?? "";
  const searchQuery = query.trim() || undefined;
  const canViewPrinters = !!user?.is_superuser;
  const queryClient = useQueryClient();

  // Filters shared by the grid + outliner queries; only the search query and
  // pagination differ between them.
  const baseFilters: ModelListFilters = {
    tag: selectedTags.length ? selectedTags : undefined,
    printer_id: canViewPrinters ? selectedPrinterId ?? undefined : undefined,
    printer_presence:
      canViewPrinters && selectedPrinterId === null
        ? selectedPrinterPresence ?? undefined
        : undefined,
  };
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
  );
  const outlinerQuery = useOutlinerModels(baseFilters, 500);

  const models = useMemo(
    () => modelQuery.data?.pages.flat() ?? [],
    [modelQuery.data],
  );
  const outlinerModels = outlinerQuery.data ?? [];
  // First load shows skeletons; a filter change keeps the previous page visible
  // and just flags `refreshing` for the subtle "Updating…" hint.
  const loading = modelQuery.isLoading;
  const refreshing = modelQuery.isFetching && !modelQuery.isFetchingNextPage && !loading;
  const loadingMore = modelQuery.isFetchingNextPage;
  const hasMore = modelQuery.hasNextPage ?? false;
  const error = modelQuery.error ? (modelQuery.error as Error).message : null;
  function loadMore() {
    if (hasMore && !loadingMore) modelQuery.fetchNextPage();
  }
  function refresh() {
    queryClient.invalidateQueries({ queryKey: queryKeys.models });
  }

  // Multi-select for batch actions. The selected set is view-independent so it
  // survives load-more and search; backend per-model RBAC makes cross-collection
  // selections safe. We clear it when navigating folders (see below) so a hidden
  // off-screen selection doesn't linger.
  const [selectMode, setSelectMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [batchBusy, setBatchBusy] = useState(false);

  const toggleSelect = useCallback((id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const clearSelection = useCallback(() => {
    setSelectedIds(new Set());
    setSelectMode(false);
  }, []);

  const sortedModels = useMemo(() => sortModels(models, "date-desc"), [models]);

  function selectAllVisible() {
    setSelectedIds(new Set(sortedModels.map((m) => m.id)));
  }

  function summarizeBatch(verb: string, result: { succeeded_count: number; failed_count: number }) {
    if (result.succeeded_count > 0) toast.success(`${verb} ${result.succeeded_count}`);
    if (result.failed_count > 0) {
      toast.warning(`${result.failed_count} skipped (no permission)`);
    }
    refresh();
    clearSelection();
  }

  async function runBatch<T extends { succeeded_count: number; failed_count: number }>(
    verb: string,
    fn: () => Promise<T>,
  ) {
    if (!auth.isAuthenticated) { auth.showAuthRequiredToast(); return; }
    setBatchBusy(true);
    try {
      summarizeBatch(verb, await fn());
    } catch (e: any) {
      toast.error(e);
    } finally {
      setBatchBusy(false);
    }
  }

  const selectedIdList = useMemo(() => Array.from(selectedIds), [selectedIds]);
  // "All Models" is a folder explorer: at the root the grid shows collection
  // cards plus only the models sitting directly at the root, so models.length is
  // the uncollected handful — 0 for a fully-foldered NAS library. When we're at
  // the root with no filter narrowing the view, label it with the real library
  // total instead of that root-only count (#30).
  const hasActiveFilters =
    selectedTags.length > 0 ||
    selectedPrinterId !== null ||
    selectedPrinterPresence !== null ||
    !!query.trim();
  const totalLibraryCount = vaultStatsQuery.data?.model_count ?? null;
  const showLibraryTotal =
    !selectedCollection && !hasActiveFilters && totalLibraryCount !== null;
  const displayCount = showLibraryTotal ? totalLibraryCount : models.length;
  // While searching, the grid is a global result list, not a folder view: show
  // only collections whose name matches the query (anywhere in the tree), to
  // mirror the matching models. Without a query we fall back to the normal
  // folder explorer (immediate children of the selected collection).
  const visibleCollections = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (needle) {
      return collections
        .filter((c) => c.name.toLowerCase().includes(needle))
        .sort((a, b) => a.name.localeCompare(b.name));
    }
    return childCollections(collections, selectedCollection);
  }, [collections, selectedCollection, query]);
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
    user?.is_superuser || canWriteCollection(selectedCollectionRow)
      ? selectedCollection
      : null;
  // Collections + tags are fetched by useCollections()/useTags() above; the
  // model grid + outliner come from useModelList()/useOutlinerModels() above.

  async function handleCreateCollection() {
    const name = newCollectionName.trim();
    if (!name) return;
    if (!auth.isAuthenticated) { auth.showAuthRequiredToast(); return; }
    if (!canAdminSelectedCollection) {
      toast.warning("Admin access required");
      return;
    }
    try {
      const parentId = selectedCollection
        ? collections.find((c) => c.path === selectedCollection)?.id ?? null
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
    if (!auth.isAuthenticated) { auth.showAuthRequiredToast(); return; }
    try {
      await updateModel(modelId, { collection: targetCollection ?? "" });
      toast.success("Moved");
      refresh();
    } catch (e: any) {
      toast.error(e);
    }
  }

  async function handleMoveCollection(collectionId: number, newParentId: number | null) {
    if (!auth.isAuthenticated) { auth.showAuthRequiredToast(); return; }
    try {
      await moveCollection(collectionId, newParentId);
      toast.success("Moved");
      refresh();
    } catch (e: any) {
      toast.error(e);
    }
  }

  async function handleDeleteCollection(id: number, recursive: boolean) {
    if (!auth.isAuthenticated) { auth.showAuthRequiredToast(); return; }
    try {
      await deleteCollection(id, recursive);
      toast.success("Collection deleted");
      refresh();
    } catch (e: any) {
      toast.error(e);
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

  return (
    <>
      <UploadModal
        open={uploadOpen}
        onClose={() => { setUploadOpen(false); setDropPreload(null); setDropCollection(null); }}
        onUploaded={refresh}
        defaultCollection={dropCollection ?? uploadDefaultCollection}
        preloadFiles={dropPreload?.files ?? null}
        preloadItems={dropPreload?.items ?? null}
        initialMode={dropPreload?.mode}
      />
      <MobileFilterDrawer
        open={filterDrawerOpen} onClose={closeDrawer}
        collections={collections} tags={tags} printers={printers}
        selectedCollection={selectedCollection} selectedTags={selectedTags}
        selectedPrinterId={selectedPrinterId} selectedPrinterPresence={selectedPrinterPresence}
        onCollectionChange={handleCollectionChange} onTagsChange={setSelectedTags}
        onPrinterChange={setSelectedPrinterId} onPrinterPresenceChange={setSelectedPrinterPresence}
        onCreateCollection={handleOpenCreateCollection}
        canViewPrinters={canViewPrinters}
        loading={facetsLoading}
      />

      {/* Stitch layout: filter sidebar + main content */}
      <FilterSidebar
        collections={collections} models={outlinerModels} tags={tags} printers={printers}
        selectedCollection={selectedCollection} selectedTags={selectedTags}
        selectedPrinterId={selectedPrinterId} selectedPrinterPresence={selectedPrinterPresence}
        onCollectionChange={handleCollectionChange} onTagsChange={setSelectedTags}
        onPrinterChange={setSelectedPrinterId} onPrinterPresenceChange={setSelectedPrinterPresence}
        onCreateCollection={handleOpenCreateCollection}
        onMoveModel={handleMoveModel}
        onMoveCollection={handleMoveCollection}
        onDeleteCollection={handleDeleteCollection}
        canViewPrinters={canViewPrinters}
        loading={facetsLoading}
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
        </nav>

        {/* Content Top Bar */}
        <div className="px-4 sm:px-6 py-5 sm:py-8 bg-background border-b border-border">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex min-w-0 flex-col space-y-1">
              <h2 className="text-xl sm:text-2xl font-bold text-foreground tracking-tight truncate">
                {selectedName ?? "All Models"}
              </h2>
              <p className="text-sm text-muted-foreground">
                {loading ? "Loading..." : `${displayCount} model${displayCount !== 1 ? "s" : ""} total${selectedName ? ` in this collection` : ""}`}
                {refreshing && <span className="ml-2 font-mono text-xs text-muted-foreground">Updating...</span>}
              </p>
            </div>
            <div className="flex items-center justify-between gap-3 sm:justify-end">
              <div className="flex items-center space-x-2">
                <button
                  onClick={openDrawer}
                  className="md:hidden flex items-center px-3 py-2 text-xs font-medium text-foreground bg-background border border-border rounded hover:bg-muted transition-all"
                >
                  <SlidersHorizontal className="w-4 h-4 mr-1.5 text-muted-foreground" />
                  Filters
                </button>
                <button
                  onClick={handleOpenCreateCollection}
                  disabled={!canAdminSelectedCollection}
                  className="hidden md:flex items-center px-3 py-2 text-xs font-medium text-foreground bg-background border border-border rounded hover:bg-muted transition-all"
                >
                  <Plus className="w-4 h-4 mr-1.5 text-muted-foreground" />
                  New collection
                </button>
                <button
                  onClick={() => { setDropPreload(null); setDropCollection(null); setUploadOpen(true); }}
                  disabled={!canUploadToVault}
                  className="flex items-center px-3 py-2 text-xs font-medium text-white bg-blue-600 dark:bg-orange-600 rounded hover:bg-blue-700 dark:hover:bg-orange-700 transition-all shadow-sm disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  Upload
                </button>
              </div>
              {auth.isAuthenticated && (
                <button
                  onClick={() => {
                    if (selectMode) clearSelection();
                    else setSelectMode(true);
                  }}
                  className={`hidden md:flex items-center px-3 py-2 text-xs font-medium rounded border transition-all ${
                    selectMode
                      ? "text-white bg-blue-600 dark:bg-orange-600 border-transparent hover:bg-blue-700 dark:hover:bg-orange-700"
                      : "text-foreground bg-background border-border hover:bg-muted"
                  }`}
                >
                  <CheckSquare className="w-4 h-4 mr-1.5" />
                  {selectMode ? "Done" : "Select"}
                </button>
              )}
              <div className="h-6 w-px bg-muted mx-1 hidden md:block" />
              <div className="flex items-center bg-muted p-1 rounded">
                <button
                  onClick={() => setViewMode("grid")}
                  className={`p-1.5 rounded transition-all ${viewMode === "grid" ? "bg-background text-blue-600 dark:text-orange-500 shadow-sm" : "text-muted-foreground hover:text-foreground"}`}
                  title="Grid View"
                >
                  <Grid className="w-4 h-4" />
                </button>
                <button
                  onClick={() => setViewMode("list")}
                  className={`p-1.5 rounded transition-all ${viewMode === "list" ? "bg-background text-blue-600 dark:text-orange-500 shadow-sm" : "text-muted-foreground hover:text-foreground"}`}
                  title="List View"
                >
                  <List className="w-4 h-4" />
                </button>
              </div>
            </div>
          </div>
        </div>

        {selectedCollectionRow && (
          <CollectionReadme
            key={selectedCollectionRow.id}
            collectionId={selectedCollectionRow.id}
            canEdit={!!user?.is_superuser || canWriteCollection(selectedCollectionRow)}
          />
        )}

        {/* Models / Documents tabs */}
        <div className="flex items-center gap-1 px-4 sm:px-6 pt-3 border-b border-border">
          {(["models", "docs"] as const).map((v) => (
            <button
              key={v}
              onClick={() => setDocView(v)}
              className={`px-3 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
                docView === v
                  ? "border-blue-600 dark:border-orange-500 text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              {v === "models" ? "Models" : "Documents"}
            </button>
          ))}
        </div>

        {isCreatingCollection && (
          <div className="px-6 py-3 bg-muted border-b border-border">
            <form
              onSubmit={(e) => { e.preventDefault(); handleCreateCollection(); }}
              className="flex items-center gap-2"
            >
              <input
                autoFocus
                value={newCollectionName}
                onChange={(e) => setNewCollectionName(e.target.value)}
                placeholder={auth.isAuthenticated ? (selectedCollection ? `New subcollection in "${selectedName ?? selectedCollection}"...` : "Collection name...") : "Sign in to add"}
                disabled={!auth.isAuthenticated}
                className="flex-1 max-w-xs bg-background text-foreground text-sm border border-border rounded px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-blue-600 dark:focus:ring-orange-500 focus:border-transparent disabled:opacity-50"
              />
              <button
                type="submit"
                disabled={!newCollectionName.trim() || !auth.isAuthenticated}
                className="px-3 py-1.5 text-xs font-medium text-white bg-blue-600 dark:bg-orange-600 rounded hover:bg-blue-700 dark:hover:bg-orange-700 transition-colors disabled:opacity-50"
              >
                Create
              </button>
              <button
                type="button"
                onClick={() => { setIsCreatingCollection(false); setNewCollectionName(""); }}
                className="px-3 py-1.5 text-xs font-medium text-foreground bg-background border border-border rounded hover:bg-muted transition-colors"
              >
                Cancel
              </button>
            </form>
          </div>
        )}

        {selectMode && (
          <div className="px-4 sm:px-6 py-2 bg-muted border-b border-border flex items-center gap-3 text-xs">
            <span className="font-mono text-muted-foreground">
              {selectedIds.size} selected
            </span>
            <button
              type="button"
              onClick={selectAllVisible}
              className="font-medium text-blue-600 dark:text-orange-500 hover:underline"
            >
              Select all on screen ({sortedModels.length})
            </button>
            {selectedIds.size > 0 && (
              <button
                type="button"
                onClick={() => setSelectedIds(new Set())}
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
            <div className="mx-6 mt-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>
          )}

          {loading ? (
            viewMode === "grid" ? <ModelGridSkeleton /> : <ModelListSkeleton />
          ) : sortedModels.length === 0 && visibleCollections.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 px-6 flex-1 text-center text-muted-foreground">
              <p className="text-lg font-medium text-foreground">No models found</p>
              <p className="text-sm mt-1">
                {query || selectedCollection || selectedTags.length || selectedPrinterId || selectedPrinterPresence
                  ? "Try clearing some filters."
                  : "Upload a model when you're ready, or skim the wiki first if this is a new install."}
              </p>
              {!query && !selectedCollection && selectedTags.length === 0 && !selectedPrinterId && !selectedPrinterPresence && (
                <a
                  href="https://xiao-villamor.github.io/PrintStash/"
                  className="mt-4 inline-flex items-center gap-2 rounded border border-border bg-background px-3 py-2 text-sm font-medium text-foreground transition-colors hover:bg-muted"
                >
                  <BookOpen className="h-4 w-4 text-muted-foreground" />
                  Open wiki
                </a>
              )}
            </div>
          ) : viewMode === "grid" ? (
            <div className="p-4 sm:p-6">
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-[repeat(auto-fill,minmax(340px,340px))]">
                {visibleCollections.map((collection) => (
                  <CollectionFolderCard
                    key={collection.id}
                    collection={collection}
                    onSelect={handleCollectionChange}
                    onDropModel={canUploadToVault ? handleMoveModel : undefined}
                  />
                ))}
                {sortedModels.map((model) => (
                  <ModelCard
                    key={model.id}
                    model={model}
                    selectable={selectMode}
                    selected={selectedIds.has(model.id)}
                    onToggleSelect={toggleSelect}
                    draggable={canUploadToVault && !selectMode}
                  />
                ))}
              </div>
              <LoadMore hasMore={hasMore} loading={loadingMore} onClick={loadMore} />
            </div>
          ) : (
            <div className="flex-1 overflow-y-auto">
              <div className="flex flex-col">
                <div className="flex items-center gap-3 px-4 py-2 border-b border-border text-xs font-mono text-muted-foreground uppercase tracking-wider bg-muted/50">
                  <span className="w-10 flex-shrink-0">Thumb</span>
                  <span className="flex-1">Name</span>
                  <span className="w-24 text-right hidden sm:block">Collection</span>
                  <span className="w-20 text-right">Files</span>
                  <span className="w-24 text-right hidden md:block">Updated</span>
                  <span className="w-8" />
                </div>
                {visibleCollections.map((collection) => (
                  <CollectionListRow
                    key={collection.id}
                    collection={collection}
                    onSelect={handleCollectionChange}
                    onDropModel={canUploadToVault ? handleMoveModel : undefined}
                  />
                ))}
                {sortedModels.map((model) => (
                  <ModelListRow
                    key={model.id}
                    model={model}
                    selectable={selectMode}
                    selected={selectedIds.has(model.id)}
                    onToggleSelect={toggleSelect}
                    draggable={canUploadToVault && !selectMode}
                  />
                ))}
              </div>
              <LoadMore hasMore={hasMore} loading={loadingMore} onClick={loadMore} />
            </div>
          )}
        </div>
        )}
      </main>

      <BatchToolbar
        count={selectedIds.size}
        collections={collections}
        tags={tags}
        busy={batchBusy}
        onMove={(target) => runBatch("Moved", () => batchMoveModels(selectedIdList, target))}
        onApplyTags={(add, remove) =>
          runBatch("Tagged", () => batchTagModels(selectedIdList, add, remove))
        }
        onDelete={() => runBatch("Deleted", () => batchDeleteModels(selectedIdList))}
        onClear={clearSelection}
      />
    </>
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

function CollectionFolderCard({ collection, onSelect, onDropModel }: { collection: CollectionRead; onSelect: (path: string) => void; onDropModel?: (modelId: number, path: string) => void }) {
  const { dragOver, handlers } = useModelDropTarget(collection.path, onDropModel);
  return (
    <button
      type="button"
      data-collection-path={collection.path}
      onClick={() => onSelect(collection.path)}
      {...handlers}
      className={`animate-card-in group flex flex-col text-left bg-muted border rounded-lg hover:shadow-sm transition-all relative overflow-hidden ${
        dragOver
          ? "border-blue-500 dark:border-orange-500 ring-2 ring-blue-500/40 dark:ring-orange-500/40"
          : "border-border hover:border-orange-500 dark:hover:border-orange-500"
      }`}
    >
      <div className="flex-1 flex items-center justify-center bg-muted/60 dark:bg-[var(--surface-container-high)] min-h-[100px] sm:min-h-[140px]">
        <Folder className="w-12 h-12 sm:w-16 sm:h-16 text-blue-600/30 dark:text-orange-500/25" />
      </div>
      <div className="p-3 border-t border-border">
        <div className="flex items-center justify-end gap-2 mb-0.5">
          <span className="text-[10px] text-muted-foreground font-mono">{collection.model_count} models</span>
        </div>
        <p className="text-sm font-bold text-foreground truncate tracking-tight">{collection.name}</p>
      </div>
    </button>
  );
}

function CollectionFolderGrid({ collections, onSelect }: { collections: CollectionRead[]; onSelect: (path: string) => void }) {
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-[repeat(auto-fill,minmax(220px,220px))]">
      {collections.map((collection) => (
        <button
          key={collection.id}
          type="button"
          onClick={() => onSelect(collection.path)}
          className="group flex flex-col p-3 bg-muted border border-border rounded-lg hover:border-blue-600 dark:hover:border-orange-500 hover:bg-blue-50/30 dark:hover:bg-orange-950/20 hover:shadow-sm transition-all text-left relative overflow-hidden"
        >
          <div className="flex items-center justify-between w-full mb-2">
            <div className="flex items-center gap-2">
              <Folder className="w-5 h-5 text-blue-600 dark:text-orange-500" />
              <span className="text-[10px] font-bold text-muted-foreground uppercase tracking-widest">Folder</span>
            </div>
            <span className="text-[10px] text-foreground font-mono font-bold">{collection.model_count} models</span>
          </div>
          <p className="text-base font-bold text-foreground truncate tracking-tight">{collection.name}</p>
          <p className="text-[9px] text-muted-foreground mt-1 font-mono uppercase">{collection.path}</p>
        </button>
      ))}
    </div>
  );
}

function CollectionListRow({ collection, onSelect, onDropModel }: { collection: CollectionRead; onSelect: (path: string) => void; onDropModel?: (modelId: number, path: string) => void }) {
  const { dragOver, handlers } = useModelDropTarget(collection.path, onDropModel);
  return (
    <button
      type="button"
      data-collection-path={collection.path}
      onClick={() => onSelect(collection.path)}
      {...handlers}
      className={`flex items-center gap-2 md:gap-3 px-4 py-3 border-b text-left transition-colors group ${
        dragOver
          ? "border-blue-500 dark:border-orange-500 ring-2 ring-inset ring-blue-500/40 dark:ring-orange-500/40 bg-muted"
          : "border-border hover:bg-muted"
      }`}
    >
      <span className="w-8 h-8 md:w-10 md:h-10 rounded bg-blue-50 flex-shrink-0 border border-blue-100 dark:border-orange-900 flex items-center justify-center text-blue-600 dark:text-orange-500">
        <Folder className="h-4 w-4 md:h-5 md:w-5" />
      </span>
      <span className="flex-1 min-w-0">
        <span className="block text-sm font-medium text-foreground truncate">{collection.name}</span>
        <span className="block font-mono text-[10px] text-muted-foreground truncate">{collection.path}</span>
      </span>
      <span className="w-24 text-right text-xs font-mono text-muted-foreground truncate hidden sm:block">Folder</span>
      <span className="w-20 text-right text-xs font-mono text-muted-foreground">{collection.model_count}</span>
      <span className="w-24 hidden md:block" />
      <span className="w-8 flex justify-center">
        <ChevronRight className="h-4 w-4 text-muted-foreground/50 opacity-60 group-hover:opacity-100" />
      </span>
    </button>
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
  onToggleSelect?: (id: number) => void;
  draggable?: boolean;
}) {
  const router = useRouter();
  const thumb = useAuthenticatedAssetUrl(model.thumbnail_url);
  const printerPresence = model.printer_presence ?? [];
  return (
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
          onToggleSelect?.(model.id);
        }
      }}
      className={`flex items-center gap-2 md:gap-3 px-4 py-3 border-b border-border transition-colors group active:bg-muted ${
        draggable ? "cursor-grab active:cursor-grabbing" : ""
      } ${selected ? "bg-blue-50 dark:bg-orange-950/30" : "hover:bg-muted"}`}
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
          <img src={thumb} alt={model.name} className="h-full w-full object-cover" loading="lazy" />
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
              <span key={tag} className="bg-blue-50 text-blue-700 dark:text-orange-400 px-1 py-px rounded font-mono text-[9px] uppercase tracking-wider">{tag}</span>
            ))}
          </div>
        )}
        {printerPresence.length > 0 && (
          <div className="flex gap-1 mt-1">
            {printerPresence.slice(0, 2).map((p) => (
              <span key={p.printer_id} className="inline-flex items-center gap-1 rounded bg-emerald-50 px-1 py-px font-mono text-[9px] uppercase tracking-wider text-emerald-600">
                <Printer className="h-3 w-3" />{p.printer_name}
              </span>
            ))}
          </div>
        )}
      </div>
      <span className="w-24 text-right text-xs font-mono text-muted-foreground truncate hidden sm:block">{model.collection || "—"}</span>
      <span className="w-20 text-right text-xs font-mono text-muted-foreground">{model.file_count}</span>
      <span className="w-24 text-right text-xs font-mono text-muted-foreground hidden md:block">{timeAgo(model.updated_at)}</span>
      <span className="w-8 flex justify-center">
        <MoreVertical className="h-4 w-4 text-muted-foreground/50 opacity-0 group-hover:opacity-100 transition-opacity" />
      </span>
    </Link>
  );
}

function LoadMore({ hasMore, loading, onClick }: { hasMore: boolean; loading: boolean; onClick: () => void }) {
  if (!hasMore) return null;
  return (
    <div className="flex justify-center mt-6 pb-6">
      <button onClick={onClick} disabled={loading} className="px-4 py-2 rounded border border-border bg-background text-foreground hover:bg-muted disabled:opacity-50 font-mono text-[13px] uppercase tracking-wider transition-colors">
        {loading ? "Loading..." : "Load more"}
      </button>
    </div>
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
