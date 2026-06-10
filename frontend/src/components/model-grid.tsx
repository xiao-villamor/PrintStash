"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { CollectionRead, ModelListItem, PrinterRead, TagRead } from "@/types";
import { ModelCard } from "@/components/model-card";
import { FilterSidebar } from "@/components/filter-sidebar";
import { MobileFilterDrawer } from "@/components/mobile-filter-drawer";
import { UploadModal } from "@/components/upload-modal";
import { Skeleton } from "@/components/ui/skeleton";
import { useMobileFilterDrawer } from "@/lib/mobile-filter-context";
import {
  SlidersHorizontal,
  Grid,
  List,
  Upload,
  FileText,
  MoreVertical,
  Printer,
  Folder,
  FolderOpen,
  ChevronRight,
  Plus,
} from "lucide-react";
import { listCollections, listModels, listPrinters, listTags, createCollection, updateModel, moveCollection, deleteCollection } from "@/lib/api";
import { toast } from "@/lib/toast";
import { useRequireAuth } from "@/lib/use-require-auth";
import Link from "next/link";
import { getAssetUrl } from "@/lib/api";
import { timeAgo } from "@/lib/format";

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
  const [models, setModels] = useState<ModelListItem[]>(initial?.models ?? []);
  const [collections, setCollections] = useState<CollectionRead[]>(initial?.collections ?? []);
  const [tags, setTags] = useState<TagRead[]>(initial?.tags ?? []);
  const [printers, setPrinters] = useState<PrinterRead[]>(initial?.printers ?? []);
  const [selectedCollection, setSelectedCollection] = useState<string | null>(null);
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [selectedPrinterId, setSelectedPrinterId] = useState<number | null>(null);
  const [selectedPrinterPresence, setSelectedPrinterPresence] = useState<"any" | "none" | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>("grid");
  const [uploadOpen, setUploadOpen] = useState(false);
  const [loading, setLoading] = useState(!initial);
  const [refreshing, setRefreshing] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(initial ? initial.models.length === PAGE_SIZE : false);
  const [facetsLoading, setFacetsLoading] = useState(!initial);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [isCreatingCollection, setIsCreatingCollection] = useState(false);
  const [newCollectionName, setNewCollectionName] = useState("");
  const hasLoadedModels = useRef(!!initial);
  // The server already rendered the first page with the current search query;
  // skip the redundant client fetch on hydration.
  const skipFirstFetch = useRef(!!initial);
  const { open: filterDrawerOpen, openDrawer, closeDrawer } = useMobileFilterDrawer();

  // Navigate into/out of a collection: clear stale models immediately so the
  // old cards don't flash for the 200 ms debounce window.
  function handleCollectionChange(path: string | null) {
    if (path !== selectedCollection) {
      setModels([]);
      setLoading(true);
      hasLoadedModels.current = false;
    }
    setSelectedCollection(path);
  }

  useEffect(() => {
    if (searchParams.get("upload") === "1") {
      setUploadOpen(true);
      router.replace("/", { scroll: false });
    }
  }, [searchParams, router]);

  const query = searchParams.get("q") ?? "";

  const sortedModels = useMemo(() => sortModels(models, "date-desc"), [models]);
  const visibleCollections = useMemo(
    () => childCollections(collections, selectedCollection),
    [collections, selectedCollection],
  );
  const breadcrumbs = useMemo(
    () => collectionBreadcrumbs(collections, selectedCollection),
    [collections, selectedCollection],
  );
  const selectedName = useMemo(
    () => selectedCollectionName(collections, selectedCollection),
    [collections, selectedCollection],
  );

  useEffect(() => {
    if (initial) return; // facets came down with the server render
    let alive = true;
    (async () => {
      try {
        const [c, t, p] = await Promise.all([listCollections(), listTags(), listPrinters()]);
        if (!alive) return;
        setCollections(c);
        setTags(t);
        setPrinters(p);
      } catch (e: any) {
        if (alive) setError(e.message);
      } finally {
        if (alive) setFacetsLoading(false);
      }
    })();
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (skipFirstFetch.current) {
      skipFirstFetch.current = false;
      return;
    }
    let alive = true;
    const handle = setTimeout(async () => {
      if (hasLoadedModels.current) setRefreshing(true);
      else setLoading(true);
      try {
        const searchQuery = query.trim() || undefined;
        const data = await listModels({
          limit: PAGE_SIZE,
          offset: 0,
          collection: selectedCollection ?? undefined,
          direct: !searchQuery,
          tag: selectedTags.length ? selectedTags : undefined,
          q: searchQuery,
          printer_id: selectedPrinterId ?? undefined,
          printer_presence: selectedPrinterId === null ? selectedPrinterPresence ?? undefined : undefined,
        });
        if (!alive) return;
        setModels(data);
        setHasMore(data.length === PAGE_SIZE);
        setError(null);
        hasLoadedModels.current = true;
      } catch (e: any) {
        if (alive) setError(e.message);
      } finally {
        if (alive) { setLoading(false); setRefreshing(false); }
      }
    }, 200);
    return () => { alive = false; clearTimeout(handle); };
  }, [selectedCollection, selectedTags, selectedPrinterId, selectedPrinterPresence, query, reloadKey]);

  async function loadMore() {
    if (loadingMore || !hasMore) return;
    setLoadingMore(true);
    try {
      const searchQuery = query.trim() || undefined;
      const data = await listModels({
        limit: PAGE_SIZE,
        offset: models.length,
        collection: selectedCollection ?? undefined,
        direct: !searchQuery,
        tag: selectedTags.length ? selectedTags : undefined,
        q: searchQuery,
        printer_id: selectedPrinterId ?? undefined,
        printer_presence: selectedPrinterId === null ? selectedPrinterPresence ?? undefined : undefined,
      });
      setModels((prev) => [...prev, ...data]);
      setHasMore(data.length === PAGE_SIZE);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoadingMore(false);
    }
  }

  function refresh() { setReloadKey((k) => k + 1); }

  async function refreshCollections() {
    try {
      const c = await listCollections();
      setCollections(c);
    } catch (e: any) {
      setError(e.message);
    }
  }

  async function handleCreateCollection() {
    const name = newCollectionName.trim();
    if (!name) return;
    if (!auth.isAuthenticated) { auth.showAuthRequiredToast(); return; }
    try {
      const parentId = selectedCollection
        ? collections.find((c) => c.path === selectedCollection)?.id ?? null
        : null;
      await createCollection({ name, parent_id: parentId });
      setNewCollectionName("");
      setIsCreatingCollection(false);
      toast.success(`Collection "${name}" created`);
      refreshCollections();
    } catch (e: any) {
      setError(e.message);
      toast.error(e);
    }
  }

  async function handleMoveModel(modelId: number, targetCollection: string | null) {
    if (!auth.isAuthenticated) { auth.showAuthRequiredToast(); return; }
    try {
      await updateModel(modelId, { collection: targetCollection ?? "" });
      toast.success("Moved");
      refresh();
      refreshCollections();
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
      refreshCollections();
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
      refreshCollections();
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
      <UploadModal open={uploadOpen} onClose={() => setUploadOpen(false)} onUploaded={refresh} defaultCollection={selectedCollection} />
      <MobileFilterDrawer
        open={filterDrawerOpen} onClose={closeDrawer}
        collections={collections} tags={tags} printers={printers}
        selectedCollection={selectedCollection} selectedTags={selectedTags}
        selectedPrinterId={selectedPrinterId} selectedPrinterPresence={selectedPrinterPresence}
        onCollectionChange={handleCollectionChange} onTagsChange={setSelectedTags}
        onPrinterChange={setSelectedPrinterId} onPrinterPresenceChange={setSelectedPrinterPresence}
        onCreateCollection={handleOpenCreateCollection}
        loading={facetsLoading}
      />

      {/* Stitch layout: filter sidebar + main content */}
      <FilterSidebar
        collections={collections} models={models} tags={tags} printers={printers}
        selectedCollection={selectedCollection} selectedTags={selectedTags}
        selectedPrinterId={selectedPrinterId} selectedPrinterPresence={selectedPrinterPresence}
        onCollectionChange={handleCollectionChange} onTagsChange={setSelectedTags}
        onPrinterChange={setSelectedPrinterId} onPrinterPresenceChange={setSelectedPrinterPresence}
        onCreateCollection={handleOpenCreateCollection}
        onMoveModel={handleMoveModel}
        onMoveCollection={handleMoveCollection}
        onDeleteCollection={handleDeleteCollection}
        loading={facetsLoading}
      />

      <main className="flex-1 overflow-y-auto bg-background flex flex-col">
        {/* Breadcrumb */}
        <nav className="px-6 py-3 bg-background border-b border-border flex items-center space-x-2 text-xs tracking-tight">
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
        <div className="px-6 py-8 bg-background border-b border-border">
          <div className="flex items-center justify-between">
            <div className="flex flex-col space-y-1">
              <h2 className="text-2xl font-bold text-foreground tracking-tight">
                {selectedName ?? "All Models"}
              </h2>
              <p className="text-sm text-muted-foreground">
                {loading ? "Loading..." : `${models.length} model${models.length !== 1 ? "s" : ""} total${selectedName ? ` in this collection` : ""}`}
                {refreshing && <span className="ml-2 font-mono text-xs text-muted-foreground">Updating...</span>}
              </p>
            </div>
            <div className="flex items-center space-x-3">
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
                  className="hidden md:flex items-center px-3 py-2 text-xs font-medium text-foreground bg-background border border-border rounded hover:bg-muted transition-all"
                >
                  <Plus className="w-4 h-4 mr-1.5 text-muted-foreground" />
                  New collection
                </button>
                <button
                  onClick={() => setUploadOpen(true)}
                  className="flex items-center px-3 py-2 text-xs font-medium text-white bg-blue-600 dark:bg-orange-600 rounded hover:bg-blue-700 dark:hover:bg-orange-700 transition-all shadow-sm"
                >
                  Upload
                </button>
              </div>
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

        {/* Content */}
        <div className="flex-1 flex flex-col bg-background">
          {error && (
            <div className="mx-6 mt-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>
          )}

          {loading ? (
            viewMode === "grid" ? <ModelGridSkeleton /> : <ModelListSkeleton />
          ) : sortedModels.length === 0 && visibleCollections.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 flex-1 text-muted-foreground">
              <p className="text-lg font-medium text-foreground">No models found</p>
              <p className="text-sm mt-1">
                {query || selectedCollection || selectedTags.length || selectedPrinterId || selectedPrinterPresence
                  ? "Try clearing some filters."
                  : "Upload your first model to get started."}
              </p>
            </div>
          ) : viewMode === "grid" ? (
            <div className="p-6">
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-[repeat(auto-fill,minmax(340px,340px))]">
                {visibleCollections.map((collection) => (
                  <CollectionFolderCard
                    key={collection.id}
                    collection={collection}
                    onSelect={handleCollectionChange}
                  />
                ))}
                {sortedModels.map((model) => (
                  <ModelCard key={model.id} model={model} />
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
                  />
                ))}
                {sortedModels.map((model) => (
                  <ModelListRow key={model.id} model={model} />
                ))}
              </div>
              <LoadMore hasMore={hasMore} loading={loadingMore} onClick={loadMore} />
            </div>
          )}
        </div>
      </main>
    </>
  );
}

function CollectionFolderCard({ collection, onSelect }: { collection: CollectionRead; onSelect: (path: string) => void }) {
  return (
    <button
      type="button"
      onClick={() => onSelect(collection.path)}
      className="group flex flex-col text-left bg-muted border border-border rounded-lg hover:border-orange-500 dark:hover:border-orange-500 hover:shadow-sm transition-all relative overflow-hidden"
    >
      <div className="flex-1 flex items-center justify-center bg-muted/60 dark:bg-[var(--surface-container-high)] min-h-[140px]">
        <Folder className="w-16 h-16 text-blue-600/30 dark:text-orange-500/25" />
      </div>
      <div className="p-3 border-t border-border">
        <div className="flex items-center justify-between gap-2 mb-0.5">
          <div className="flex items-center gap-1.5 min-w-0">
            <Folder className="w-3.5 h-3.5 text-blue-600 dark:text-orange-500 shrink-0" />
            <span className="text-[10px] font-bold text-muted-foreground uppercase tracking-widest">Folder</span>
          </div>
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

function CollectionListRow({ collection, onSelect }: { collection: CollectionRead; onSelect: (path: string) => void }) {
  return (
    <button
      type="button"
      onClick={() => onSelect(collection.path)}
      className="flex items-center gap-2 md:gap-3 px-4 py-3 border-b border-border text-left hover:bg-muted transition-colors group"
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

function ModelListRow({ model }: { model: ModelListItem }) {
  const router = useRouter();
  const thumb = model.thumbnail_url ? getAssetUrl(model.thumbnail_url) : null;
  const printerPresence = model.printer_presence ?? [];
  return (
    <Link
      href={`/models/${model.id}`}
      onMouseEnter={() => router.prefetch(`/models/${model.id}`)}
      className="flex items-center gap-2 md:gap-3 px-4 py-3 border-b border-border hover:bg-muted transition-colors group active:bg-muted"
    >
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
