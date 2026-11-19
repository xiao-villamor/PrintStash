import { useEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { ArrowRight, Folder } from "lucide-react";
import { Link } from "@/lib/link";
import { Button } from "@/components/ui/button";
import { LibraryItems } from "@/components/library-items";
import { useLibraryBrowse } from "@/features/library/browse";
import type { LibraryReadingRenderer } from "@/features/library/reading-position";
import { useViewportAdmission } from "@/lib/use-viewport-admission";
import { uiText } from "@/lib/locale";
import type { CollectionNodeRead, ModelListItem } from "@/types";
import type { LibraryBrowseParams } from "@/types/library-browse";

export function LibraryCollectionGroup({
  collection,
  userId,
  sort,
  compact,
  scrollRef,
  href,
  onOpen,
  onLoaded,
  renderModel,
}: {
  collection: CollectionNodeRead;
  userId: number;
  sort: LibraryBrowseParams["sort"];
  compact: boolean;
  scrollRef: RefObject<HTMLElement | null>;
  href: string;
  onOpen: (collection: CollectionNodeRead) => void;
  onLoaded: (id: number, models: ModelListItem[] | null) => void;
  renderModel: (model: ModelListItem) => ReactNode;
}) {
  const key = `printstash.collection-group.${userId}.${collection.id}`;
  const [expanded, setExpanded] = useState(() => {
    try {
      return localStorage.getItem(key) === "open";
    } catch {
      return false;
    }
  });
  const [columns, setColumns] = useState(1);
  const renderers = useRef(new Map<string, LibraryReadingRenderer>());
  const { ref, admitted } = useViewportAdmission();
  const browse = useLibraryBrowse(
    { collection: collection.path, direct: false, view: "all", sort, limit: 24 },
    admitted,
  );
  // Browse returns both entity kinds. This section is a preview of child Models.
  const pages = browse.isPlaceholderData ? undefined : browse.data?.pages;
  const loaded = pages?.flatMap((page) =>
    page.items.flatMap((item) => (item.kind === "model" ? [item.model] : [])),
  );
  useEffect(() => {
    onLoaded(
      collection.id,
      pages?.flatMap((page) =>
        page.items.flatMap((item) => (item.kind === "model" ? [item.model] : [])),
      ) ?? null,
    );
    return () => onLoaded(collection.id, null);
  }, [collection.id, onLoaded, pages]);
  useEffect(() => {
    if (expanded && browse.hasNextPage && !browse.isFetching && !browse.isFetchNextPageError) {
      void browse.loadMore();
    }
  }, [browse, expanded]);
  const models = loaded ?? [];
  return (
    <section ref={ref} className="min-w-0 border border-border bg-card p-3">
      <div className="mb-2 flex min-w-0 flex-wrap items-center gap-2">
        <Folder className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
        <h3 className="min-w-0 truncate text-sm font-semibold">{collection.name}</h3>
        <span className="font-mono text-xs text-muted-foreground">{collection.model_count}</span>
        <Button className="ml-auto rounded-none" size="xs" variant="ghost" asChild>
          <Link
            href={href}
            onClick={(event) => {
              if (event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return;
              event.preventDefault();
              onOpen(collection);
            }}
          >
            {uiText("Open collection")} <ArrowRight className="h-3.5 w-3.5" aria-hidden />
          </Link>
        </Button>
      </div>
      <LibraryItems
        items={expanded ? models : models.slice(0, columns)}
        itemKey={(model) => `/models/${model.id}`}
        renderItem={renderModel}
        scrollRef={scrollRef}
        readingRenderers={renderers}
        readingId="child-models"
        layout="grid"
        compact={compact}
        onColumnsChange={setColumns}
      />
      {browse.isError && (
        <Button
          size="xs"
          variant="ghost"
          onClick={() => {
            if (browse.isFetchNextPageError) void browse.loadMore();
            else void browse.refetch();
          }}
        >
          {uiText("Retry")}
        </Button>
      )}
      {(expanded || models.length > columns || browse.hasNextPage) && (
        <Button
          className="mt-2 rounded-none"
          size="xs"
          variant="ghost"
          aria-expanded={expanded}
          onClick={() => {
            const next = !expanded;
            setExpanded(next);
            try {
              if (next) localStorage.setItem(key, "open");
              else localStorage.removeItem(key);
            } catch {
              // Keep the current view usable when storage is unavailable.
            }
          }}
        >
          {expanded
            ? uiText("Show fewer")
            : uiText("Show all {value1}", { value1: String(collection.model_count) })}
        </Button>
      )}
    </section>
  );
}
