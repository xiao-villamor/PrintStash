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
} from "lucide-react";
import { listCollections, listModels, listPrinters, listTags } from "@/lib/api";
import Link from "next/link";
import { getAssetUrl } from "@/lib/api";

type SortKey = "date-desc" | "date-asc" | "name-asc" | "name-desc";
type ViewMode = "grid" | "list";

const SORT_LABELS: Record<SortKey, string> = {
  "date-desc": "Newest",
  "date-asc": "Oldest",
  "name-asc": "Name A-Z",
  "name-desc": "Name Z-A",
};

function sortModels(models: ModelListItem[], key: SortKey): ModelListItem[] {
  const sorted = [...models];
  switch (key) {
    case "date-desc":
      sorted.sort(
        (a, b) =>
          new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
      );
      break;
    case "date-asc":
      sorted.sort(
        (a, b) =>
          new Date(a.updated_at).getTime() - new Date(b.updated_at).getTime(),
      );
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

function timeAgo(dateStr: string): string {
  const now = Date.now();
  const then = new Date(dateStr).getTime();
  const diff = now - then;
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(dateStr).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
  });
}

const PAGE_SIZE = 60;

function childCollections(
  collections: CollectionRead[],
  selectedPath: string | null,
): CollectionRead[] {
  const selected = selectedPath
    ? collections.find((collection) => collection.path === selectedPath)
    : null;
  const parentId = selectedPath ? selected?.id ?? -1 : null;
  return collections
    .filter((collection) => collection.parent_id === parentId)
    .sort((a, b) => a.name.localeCompare(b.name));
}

function collectionBreadcrumbs(
  collections: CollectionRead[],
  selectedPath: string | null,
): CollectionRead[] {
  if (!selectedPath) return [];
  const byPath = new Map(collections.map((collection) => [collection.path, collection]));
  const parts = selectedPath.split("/");
  const crumbs: CollectionRead[] = [];
  for (let i = 1; i <= parts.length; i += 1) {
    const collection = byPath.get(parts.slice(0, i).join("/"));
    if (collection) crumbs.push(collection);
  }
  return crumbs;
}

export function ModelBrowser() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [models, setModels] = useState<ModelListItem[]>([]);
  const [collections, setCollections] = useState<CollectionRead[]>([]);
  const [tags, setTags] = useState<TagRead[]>([]);
  const [printers, setPrinters] = useState<PrinterRead[]>([]);
  const [selectedCollection, setSelectedCollection] = useState<string | null>(null);
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [selectedPrinterId, setSelectedPrinterId] = useState<number | null>(null);
  const [selectedPrinterPresence, setSelectedPrinterPresence] = useState<"any" | "none" | null>(null);
  const [sortBy, setSortBy] = useState<SortKey>("date-desc");
  const [sortOpen, setSortOpen] = useState(false);
  const [viewMode, setViewMode] = useState<ViewMode>("grid");
  const [uploadOpen, setUploadOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [facetsLoading, setFacetsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const hasLoadedModels = useRef(false);
  const { open: filterDrawerOpen, openDrawer, closeDrawer } = useMobileFilterDrawer();

  // Allow navigation shortcuts to deep-link the modal open.
  useEffect(() => {
    if (searchParams.get("upload") === "1") {
      setUploadOpen(true);
      router.replace("/", { scroll: false });
    }
  }, [searchParams, router]);

  // Drive search from URL param.
  const query = searchParams.get("q") ?? "";

  const sortedModels = useMemo(
    () => sortModels(models, sortBy),
    [models, sortBy],
  );
  const visibleCollections = useMemo(
    () => childCollections(collections, selectedCollection),
    [collections, selectedCollection],
  );
  const breadcrumbs = useMemo(
    () => collectionBreadcrumbs(collections, selectedCollection),
    [collections, selectedCollection],
  );

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [c, t, p] = await Promise.all([
          listCollections(),
          listTags(),
          listPrinters(),
        ]);
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
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    let alive = true;
    const handle = setTimeout(async () => {
      if (hasLoadedModels.current) {
        setRefreshing(true);
      } else {
        setLoading(true);
      }
      try {
        const data = await listModels({
          limit: PAGE_SIZE,
          offset: 0,
          collection: selectedCollection ?? undefined,
          tag: selectedTags.length ? selectedTags : undefined,
          q: query.trim() || undefined,
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
        if (alive) {
          setLoading(false);
          setRefreshing(false);
        }
      }
    }, 200);
    return () => {
      alive = false;
      clearTimeout(handle);
    };
  }, [selectedCollection, selectedTags, selectedPrinterId, selectedPrinterPresence, query, reloadKey]);

  async function loadMore() {
    if (loadingMore || !hasMore) return;
    setLoadingMore(true);
    try {
      const data = await listModels({
        limit: PAGE_SIZE,
        offset: models.length,
        collection: selectedCollection ?? undefined,
        tag: selectedTags.length ? selectedTags : undefined,
        q: query.trim() || undefined,
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

  function refresh() {
    setReloadKey((k) => k + 1);
  }

  return (
    <>
      <UploadModal
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
        onUploaded={refresh}
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
        onCollectionChange={setSelectedCollection}
        onTagsChange={setSelectedTags}
        onPrinterChange={setSelectedPrinterId}
        onPrinterPresenceChange={setSelectedPrinterPresence}
        loading={facetsLoading}
      />

      <div className="flex flex-col h-full">
        {/* Filter sidebar + content split */}
        <div className="flex flex-1 min-h-0">
          <FilterSidebar
            collections={collections}
            tags={tags}
            printers={printers}
            selectedCollection={selectedCollection}
            selectedTags={selectedTags}
            selectedPrinterId={selectedPrinterId}
            selectedPrinterPresence={selectedPrinterPresence}
            onCollectionChange={setSelectedCollection}
            onTagsChange={setSelectedTags}
            onPrinterChange={setSelectedPrinterId}
            onPrinterPresenceChange={setSelectedPrinterPresence}
            loading={facetsLoading}
          />

          <section className="flex-1 overflow-y-auto p-4 md:p-6">
            {/* Header + controls — left aligned */}
            <div className="flex items-center justify-between mb-5 gap-3 flex-wrap">
              <div className="flex items-center gap-2">
                  <button
                    onClick={openDrawer}
                    className="md:hidden flex items-center gap-1.5 px-3 py-2 rounded border border-[var(--outline-variant)] text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-low)] transition-colors font-mono text-[11px] uppercase tracking-wider active:scale-95"
                >
                  <SlidersHorizontal className="h-4 w-4" />
                  Filters
                </button>
                <h2 className="text-lg font-semibold text-[var(--on-surface)]">
                  {selectedCollection ? "Collection" : "Vault"}
                  {!loading && (
                    <span className="ml-2 text-sm font-normal text-[var(--on-surface-variant)] font-mono">
                      ({models.length} models)
                    </span>
                  )}
                  {refreshing && (
                    <span className="ml-2 text-xs font-normal text-[var(--on-surface-variant)] font-mono">
                      Updating...
                    </span>
                  )}
                </h2>
              </div>

              <div className="flex items-center gap-2">
                <button
                  onClick={() => setUploadOpen(true)}
                  className="hidden md:flex items-center gap-1.5 px-3 py-2 rounded border border-[var(--outline-variant)] text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-low)] transition-colors font-mono text-[13px]"
                >
                  <Upload className="h-4 w-4" />
                  Upload
                </button>

                <div className="relative">
                  <button
                    onClick={() => setSortOpen(!sortOpen)}
                    className="flex items-center gap-1.5 px-3 py-2 rounded border border-[var(--outline-variant)] text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-low)] transition-colors font-mono text-[13px]"
                  >
                    <SlidersHorizontal className="h-4 w-4" />
                    {SORT_LABELS[sortBy]}
                  </button>
                  {sortOpen && (
                    <>
                      <div
                        className="fixed inset-0 z-10"
                        onClick={() => setSortOpen(false)}
                      />
                      <div className="absolute right-0 top-full mt-1 z-20 bg-[var(--surface-container-lowest)] border border-[var(--outline-variant)] rounded shadow-lg py-1 min-w-[140px]">
                        {(Object.entries(SORT_LABELS) as [SortKey, string][]).map(
                          ([key, label]) => (
                            <button
                              key={key}
                              onClick={() => {
                                setSortBy(key);
                                setSortOpen(false);
                              }}
                              className={`w-full text-left px-3 py-1.5 font-mono text-xs transition-colors ${
                                sortBy === key
                                  ? "text-[var(--primary)] bg-[var(--secondary-container)]"
                                  : "text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-low)]"
                              }`}
                            >
                              {label}
                            </button>
                          ),
                        )}
                      </div>
                    </>
                  )}
                </div>

                <div className="flex items-center gap-1 bg-[var(--surface-container)] rounded p-0.5">
                  <button
                    onClick={() => setViewMode("grid")}
                    className={`flex items-center justify-center w-8 h-8 rounded transition-colors ${
                      viewMode === "grid"
                        ? "bg-[var(--surface-container-lowest)] text-[var(--primary)] shadow-sm"
                        : "text-[var(--on-surface-variant)] hover:text-[var(--on-surface)]"
                    }`}
                  >
                    <Grid className="h-4 w-4" />
                  </button>
                  <button
                    onClick={() => setViewMode("list")}
                    className={`flex items-center justify-center w-8 h-8 rounded transition-colors ${
                      viewMode === "list"
                        ? "bg-[var(--surface-container-lowest)] text-[var(--primary)] shadow-sm"
                        : "text-[var(--on-surface-variant)] hover:text-[var(--on-surface)]"
                    }`}
                  >
                    <List className="h-4 w-4" />
                  </button>
                </div>
              </div>
            </div>

            {error && (
              <div className="rounded-md border border-[var(--error)]/50 bg-[var(--error-container)]/30 p-3 text-sm text-[var(--error)] mb-4">
                {error}
              </div>
            )}

            <CollectionBreadcrumbs
              breadcrumbs={breadcrumbs}
              onRoot={() => setSelectedCollection(null)}
              onSelect={(path) => setSelectedCollection(path)}
            />

            {loading ? (
              viewMode === "grid" ? (
                <ModelGridSkeleton />
              ) : (
                <ModelListSkeleton />
              )
            ) : sortedModels.length === 0 && visibleCollections.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-20 text-[var(--on-surface-variant)]">
                <p className="text-lg font-medium text-[var(--on-surface)]">
                  No models found
                </p>
                <p className="text-sm mt-1">
                  {query || selectedCollection || selectedTags.length
                    || selectedPrinterId
                    || selectedPrinterPresence
                    ? "Try clearing some filters."
                    : "Upload your first model to get started."}
                </p>
              </div>
            ) : viewMode === "grid" ? (
              <>
                {visibleCollections.length > 0 && (
                  <CollectionFolderGrid
                    collections={visibleCollections}
                    onSelect={(path) => setSelectedCollection(path)}
                  />
                )}
                <div className="grid grid-cols-1 sm:grid-cols-[repeat(auto-fill,minmax(280px,1fr))] gap-4">
                  {sortedModels.map((model) => (
                    <ModelCard key={model.id} model={model} />
                  ))}
                </div>
                <LoadMore hasMore={hasMore} loading={loadingMore} onClick={loadMore} />
              </>
            ) : (
              <>
                <div className="flex flex-col">
                  <div className="flex items-center gap-3 px-2 md:px-4 py-2 border-b border-[var(--outline-variant)] text-xs font-mono text-[var(--on-surface-variant)] uppercase tracking-wider">
                    <span className="w-8 md:w-10 flex-shrink-0">Thumb</span>
                    <span className="flex-1">Name</span>
                    <span className="w-24 text-right hidden sm:block">Collection</span>
                    <span className="w-12 md:w-20 text-right">Files</span>
                    <span className="w-20 md:w-24 text-right hidden md:block">Updated</span>
                    <span className="w-8" />
                  </div>
                  {visibleCollections.map((collection) => (
                    <CollectionListRow
                      key={collection.id}
                      collection={collection}
                      onSelect={(path) => setSelectedCollection(path)}
                    />
                  ))}
                  {sortedModels.map((model) => (
                    <ModelListRow key={model.id} model={model} />
                  ))}
                </div>
                <LoadMore hasMore={hasMore} loading={loadingMore} onClick={loadMore} />
              </>
            )}
          </section>
        </div>
      </div>
    </>
  );
}

function CollectionBreadcrumbs({
  breadcrumbs,
  onRoot,
  onSelect,
}: {
  breadcrumbs: CollectionRead[];
  onRoot: () => void;
  onSelect: (path: string) => void;
}) {
  return (
    <div className="mb-4 flex min-h-8 flex-wrap items-center gap-1 border-b border-[var(--outline-variant)] pb-3 font-mono text-xs text-[var(--on-surface-variant)]">
      <button
        type="button"
        onClick={onRoot}
        className="inline-flex items-center gap-1 rounded px-2 py-1 hover:bg-[var(--surface-container-low)] hover:text-[var(--on-surface)]"
      >
        <FolderOpen className="h-3.5 w-3.5 text-[var(--primary)]" />
        Vault
      </button>
      {breadcrumbs.map((collection) => (
        <span key={collection.id} className="inline-flex items-center gap-1">
          <ChevronRight className="h-3 w-3 opacity-50" />
          <button
            type="button"
            onClick={() => onSelect(collection.path)}
            className="rounded px-2 py-1 hover:bg-[var(--surface-container-low)] hover:text-[var(--on-surface)]"
          >
            {collection.name}
          </button>
        </span>
      ))}
    </div>
  );
}

function CollectionFolderGrid({
  collections,
  onSelect,
}: {
  collections: CollectionRead[];
  onSelect: (path: string) => void;
}) {
  return (
    <div className="mb-5 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
      {collections.map((collection) => (
        <button
          key={collection.id}
          type="button"
          onClick={() => onSelect(collection.path)}
          className="group flex h-20 items-center gap-3 rounded border border-[var(--outline-variant)] bg-[var(--surface-container-lowest)] px-3 text-left transition-colors hover:border-[var(--primary)] hover:bg-[var(--surface-container-low)]"
        >
          <span className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded bg-[var(--surface-container)] text-[var(--primary)] group-hover:bg-[var(--secondary-container)]">
            <Folder className="h-5 w-5" />
          </span>
          <span className="min-w-0">
            <span className="block truncate text-sm font-medium text-[var(--on-surface)]">
              {collection.name}
            </span>
            <span className="mt-1 block truncate font-mono text-[11px] text-[var(--on-surface-variant)]">
              {collection.model_count} models
            </span>
          </span>
        </button>
      ))}
    </div>
  );
}

function CollectionListRow({
  collection,
  onSelect,
}: {
  collection: CollectionRead;
  onSelect: (path: string) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onSelect(collection.path)}
      className="flex items-center gap-2 md:gap-3 px-2 md:px-4 py-3 border-b border-[var(--surface-variant)] text-left hover:bg-[var(--surface-container-low)] transition-colors group"
    >
      <span className="w-8 h-8 md:w-10 md:h-10 rounded bg-[var(--surface-container)] flex-shrink-0 border border-[var(--outline-variant)] flex items-center justify-center text-[var(--primary)]">
        <Folder className="h-4 w-4 md:h-5 md:w-5" />
      </span>
      <span className="flex-1 min-w-0">
        <span className="block text-sm font-medium text-[var(--on-surface)] truncate">
          {collection.name}
        </span>
        <span className="block font-mono text-[10px] text-[var(--on-surface-variant)] truncate">
          {collection.path}
        </span>
      </span>
      <span className="w-24 text-right text-xs font-mono text-[var(--on-surface-variant)] truncate hidden sm:block">
        Folder
      </span>
      <span className="w-20 text-right text-xs font-mono text-[var(--on-surface-variant)]">
        {collection.model_count}
      </span>
      <span className="w-20 md:w-24 hidden md:block" />
      <span className="w-8 flex justify-center">
        <ChevronRight className="h-4 w-4 text-[var(--on-surface-variant)] opacity-60 group-hover:opacity-100" />
      </span>
    </button>
  );
}

function ModelListRow({ model }: { model: ModelListItem }) {
  const thumb = model.thumbnail_url
    ? getAssetUrl(model.thumbnail_url)
    : null;
  const printerPresence = model.printer_presence ?? [];

  return (
    <Link
      href={`/models/${model.id}`}
      className="flex items-center gap-2 md:gap-3 px-2 md:px-4 py-3 border-b border-[var(--surface-variant)] hover:bg-[var(--surface-container-low)] transition-colors group active:bg-[var(--surface-container)]"
    >
      <div className="w-8 h-8 md:w-10 md:h-10 rounded bg-[var(--surface-container)] flex-shrink-0 overflow-hidden border border-[var(--outline-variant)]">
        {thumb ? (
          <img
            src={thumb}
            alt={model.name}
            className="h-full w-full object-cover"
            loading="lazy"
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center">
            <FileText className="h-4 w-4 text-[var(--on-surface-variant)] opacity-30" />
          </div>
        )}
      </div>

      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-[var(--on-surface)] truncate">
          {model.name}
        </p>
        {model.tags.length > 0 && (
          <div className="flex gap-1 mt-0.5">
            {model.tags.slice(0, 2).map((tag) => (
              <span
                key={tag}
                className="bg-[var(--surface-container)] text-[var(--on-surface)] px-1 py-px rounded font-mono text-[9px] uppercase tracking-wider"
              >
                {tag}
              </span>
            ))}
          </div>
        )}
        {printerPresence.length > 0 && (
          <div className="flex gap-1 mt-1">
            {printerPresence.slice(0, 2).map((presence) => (
              <span
                key={presence.printer_id}
                className="inline-flex items-center gap-1 rounded bg-emerald-500/10 px-1 py-px font-mono text-[9px] uppercase tracking-wider text-emerald-600"
              >
                <Printer className="h-3 w-3" />
                {presence.printer_name}
              </span>
            ))}
          </div>
        )}
      </div>

      <span className="w-24 text-right text-xs font-mono text-[var(--on-surface-variant)] truncate hidden sm:block">
        {model.collection || "—"}
      </span>

      <span className="w-20 text-right text-xs font-mono text-[var(--on-surface-variant)]">
        {model.file_count}
      </span>

      <span className="w-20 md:w-24 text-right text-xs font-mono text-[var(--on-surface-variant)] hidden md:block">
        {timeAgo(model.updated_at)}
      </span>

      <span className="w-8 flex justify-center">
        <MoreVertical className="h-4 w-4 text-[var(--on-surface-variant)] opacity-0 group-hover:opacity-100 transition-opacity" />
      </span>
    </Link>
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
    <div className="flex justify-center mt-6">
      <button
        onClick={onClick}
        disabled={loading}
        className="px-4 py-2 rounded border border-[var(--outline-variant)] bg-[var(--surface-container-lowest)] text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-low)] disabled:opacity-50 font-mono text-[13px] uppercase tracking-wider transition-colors"
      >
        {loading ? "Loading..." : "Load more"}
      </button>
    </div>
  );
}

export function ModelGridSkeleton() {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-[repeat(auto-fill,minmax(280px,1fr))] gap-4">
      {Array.from({ length: 8 }).map((_, i) => (
        <div
          key={i}
          className="space-y-2 rounded border border-[var(--outline-variant)] p-2"
        >
          <Skeleton className="aspect-[16/9] w-full rounded" />
          <Skeleton className="h-4 w-3/4" />
          <Skeleton className="h-3 w-1/2" />
        </div>
      ))}
    </div>
  );
}

function ModelListSkeleton() {
  return (
    <div className="flex flex-col">
      {Array.from({ length: 6 }).map((_, i) => (
        <div
          key={i}
          className="flex items-center gap-3 px-4 py-3 border-b border-[var(--surface-variant)]"
        >
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
