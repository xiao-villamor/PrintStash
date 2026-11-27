/** A bounded library browser for choosing the first pieces of a multipart set. */
import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Box } from "lucide-react";
import { useCollectionChildren, useCollectionLookup, useQueryApi } from "@/lib/queries";
import { useI18n } from "@/lib/i18n";
import { uiText } from "@/lib/locale";
import { getSessionVersion, requireSessionVersion } from "@/lib/session-transport";
import { Button } from "@/components/ui/button";
import {
  SelectionBrowserToolbar,
  SelectionBrowserViewport,
  SelectionBrowserBreadcrumbs,
  SelectionFolderCard,
  SelectionItemCard,
} from "@/components/selection-browser";
import { MULTIPART_INITIAL_LIMIT } from "@/lib/multipart-model-presentation";
import type { CollectionNodeRead, ModelListItem } from "@/types";

const PAGE_SIZE = 24;

export function MultipartCreationBrowser({
  selected,
  onSelectionChange,
  initialCollection,
  disabled,
  active,
  onBusyChange,
}: {
  selected: Map<number, ModelListItem>;
  onSelectionChange: (models: Map<number, ModelListItem>) => void;
  initialCollection: CollectionNodeRead | null;
  disabled: boolean;
  active: boolean;
  onBusyChange: (busy: boolean) => void;
}) {
  const { t } = useI18n();
  const api = useQueryApi();
  const [folderBusy, setFolderBusy] = useState<string | null>(null);
  const [folderError, setFolderError] = useState<string | null>(null);
  const folderRequest = useRef<AbortController | null>(null);
  const selectionDisabled = disabled || folderBusy !== null;
  const [wasActive, setWasActive] = useState(active);
  if (wasActive !== active) {
    setWasActive(active);
    setFolderBusy(null);
    setFolderError(null);
  }
  useEffect(() => {
    return () => {
      const pending = folderRequest.current;
      if (pending) {
        pending.abort();
        folderRequest.current = null;
        onBusyChange(false);
      }
    };
  }, [active, onBusyChange]);
  const [collection, setCollection] = useState(initialCollection);
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [cursor, setCursor] = useState<string | null>(null);
  const [previous, setPrevious] = useState<Array<string | null>>([]);
  useEffect(() => {
    if (query.trim() === search) return;
    const timer = setTimeout(() => {
      setSearch(query.trim());
      setCursor(null);
      setPrevious([]);
    }, 200);
    return () => clearTimeout(timer);
  }, [query, search]);
  const children = useCollectionChildren(collection?.id ?? null, { enabled: !search });
  const lookup = useCollectionLookup(collection?.path ?? null);
  const models = useQuery({
    queryKey: [
      "models",
      "multipart-creation",
      getSessionVersion(),
      search,
      collection?.path ?? null,
      cursor,
    ],
    queryFn: ({ signal }) =>
      api.listModelPage(
        {
          q: search || undefined,
          collection: search ? undefined : collection?.path,
          direct: !search,
          limit: PAGE_SIZE,
          sort: "name-asc",
          cursor: cursor ?? undefined,
        },
        { signal },
      ),
  });
  const folders = children.isError
    ? []
    : (children.data?.pages.flatMap((page) => page.items) ?? []);
  const items = models.isError ? [] : (models.data?.items ?? []);
  const ancestors =
    lookup.data && !lookup.isPlaceholderData && lookup.data.collection.id === collection?.id
      ? [...lookup.data.ancestors, lookup.data.collection]
      : collection
        ? [collection]
        : [];
  function navigate(next: CollectionNodeRead | null) {
    setCollection(next);
    setQuery("");
    setSearch("");
    setCursor(null);
    setPrevious([]);
  }
  function belongsTo(model: ModelListItem, folder: CollectionNodeRead): boolean {
    return (
      model.collection === folder.path || Boolean(model.collection?.startsWith(folder.path + "/"))
    );
  }
  async function toggleFolder(folder: CollectionNodeRead) {
    if (!active || selectionDisabled || folderRequest.current) return;
    setFolderError(null);
    const chosen = [...selected.values()].filter((model) => belongsTo(model, folder));
    if (folder.model_count > 0 && chosen.length >= folder.model_count) {
      const next = new Map(selected);
      for (const model of chosen) next.delete(model.id);
      onSelectionChange(next);
      return;
    }
    const controller = new AbortController();
    const session = getSessionVersion();
    folderRequest.current = controller;
    setFolderBusy(folder.path);
    onBusyChange(true);
    try {
      const incoming = new Map<number, ModelListItem>();
      let nextCursor: string | null = null;
      do {
        const page = await api.listModelPage(
          {
            collection: folder.path,
            limit: MULTIPART_INITIAL_LIMIT,
            sort: "name-asc",
            cursor: nextCursor ?? undefined,
          },
          { signal: controller.signal },
        );
        if (controller.signal.aborted) return;
        requireSessionVersion(session);
        if (page.total + selected.size - chosen.length > MULTIPART_INITIAL_LIMIT) {
          setFolderError(
            t("multipart.folderSelectionLimit", { limit: String(MULTIPART_INITIAL_LIMIT) }),
          );
          return;
        }
        for (const model of page.items) incoming.set(model.id, model);
        if (incoming.size > MULTIPART_INITIAL_LIMIT) {
          setFolderError(
            t("multipart.folderSelectionLimit", { limit: String(MULTIPART_INITIAL_LIMIT) }),
          );
          return;
        }
        nextCursor = page.next_cursor;
      } while (nextCursor);
      const next = new Map([...selected, ...incoming]);
      if (next.size > MULTIPART_INITIAL_LIMIT) {
        setFolderError(
          t("multipart.folderSelectionLimit", { limit: String(MULTIPART_INITIAL_LIMIT) }),
        );
        return;
      }
      onSelectionChange(next);
    } catch {
      if (!controller.signal.aborted) setFolderError(t("multipart.folderSelectionError"));
    } finally {
      if (folderRequest.current === controller) {
        folderRequest.current = null;
        setFolderBusy(null);
        onBusyChange(false);
      }
    }
  }
  function toggle(model: ModelListItem) {
    setFolderError(null);
    const next = new Map(selected);
    if (next.has(model.id)) next.delete(model.id);
    else if (next.size < MULTIPART_INITIAL_LIMIT) next.set(model.id, model);
    onSelectionChange(next);
  }
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      <SelectionBrowserToolbar
        query={query}
        onQueryChange={setQuery}
        searchLabel={t("multipart.searchModels")}
        summary={t("multipart.creationLimitHelp", { limit: String(MULTIPART_INITIAL_LIMIT) })}
        selectLabel={t("multipart.selectPage")}
        clearLabel={uiText("Clear selection")}
        selectDisabled={
          selectionDisabled ||
          models.isFetching ||
          !items.length ||
          items.every((item) => selected.has(item.id)) ||
          selected.size + items.filter((item) => !selected.has(item.id)).length >
            MULTIPART_INITIAL_LIMIT
        }
        clearDisabled={selectionDisabled || selected.size === 0}
        onSelectAll={() =>
          onSelectionChange(
            new Map([...selected, ...items.map((item) => [item.id, item] as const)]),
          )
        }
        onClear={() => onSelectionChange(new Map())}
      />
      {folderError && (
        <p role="alert" className="text-sm text-destructive">
          {folderError}
        </p>
      )}
      <SelectionBrowserBreadcrumbs
        label={t("multipart.browseCollections")}
        items={[
          {
            key: "root",
            label: t("multipart.allModels"),
            current: !collection,
            onClick: () => navigate(null),
          },
          ...ancestors.map((item) => ({
            key: String(item.id),
            label: item.name,
            current: item.id === collection?.id,
            onClick: () => navigate(item),
          })),
        ]}
      />
      <SelectionBrowserViewport
        path={collection?.path ?? ""}
        pageKey={JSON.stringify([collection?.id, search, cursor])}
        loading={models.isPending || (!search && children.isPending)}
        loadingLabel={t("multipart.loadingModels")}
      >
        {!search && children.isError && (
          <div role="alert" className="flex flex-wrap items-center gap-2 py-3 text-sm">
            {t("multipart.collectionsError")}
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => void children.refetch()}
            >
              {t("multipart.retry")}
            </Button>
          </div>
        )}
        {models.isError && (
          <div role="alert" className="flex flex-wrap items-center gap-2 py-3 text-sm">
            {t("multipart.candidatesError")}
            <Button type="button" variant="outline" size="sm" onClick={() => void models.refetch()}>
              {t("multipart.retry")}
            </Button>
          </div>
        )}
        <ul
          aria-label={t("multipart.modelPicker")}
          className="grid grid-cols-1 gap-2 sm:grid-cols-2 md:grid-cols-3"
        >
          {!search &&
            folders.map((folder) => {
              const selectedCount = [...selected.values()].filter((model) =>
                belongsTo(model, folder),
              ).length;
              const allSelected = folder.model_count > 0 && selectedCount >= folder.model_count;
              return (
                <li key={"folder/" + folder.id} className="min-w-0">
                  <SelectionFolderCard
                    name={folder.name}
                    path={folder.display_path}
                    openLabel={uiText("Open folder {value1}", { value1: folder.name })}
                    onOpen={() => navigate(folder)}
                    summary={
                      folderBusy === folder.path
                        ? t("multipart.loadingModels")
                        : t("multipart.folderSelectionCount", {
                            count: String(selectedCount),
                            total: String(folder.model_count),
                          })
                    }
                    selection={{
                      selected: allSelected,
                      partial: selectedCount > 0,
                      disabled: selectionDisabled || folder.model_count === 0,
                      pending: folderBusy === folder.path,
                      label: uiText(
                        allSelected ? "Deselect folder {value1}" : "Select folder {value1}",
                        { value1: folder.name },
                      ),
                      onToggle: () => {
                        void toggleFolder(folder);
                      },
                    }}
                  />
                </li>
              );
            })}
          {items.map((item) => (
            <li key={item.id} className="min-w-0">
              <SelectionItemCard
                name={item.name}
                label={item.name}
                title={item.name}
                icon={<Box className="h-5 w-5 shrink-0 text-muted-foreground" aria-hidden />}
                summary={item.collection_label ?? t("multipart.vaultOnly")}
                selected={selected.has(item.id)}
                disabled={
                  selectionDisabled ||
                  (!selected.has(item.id) && selected.size >= MULTIPART_INITIAL_LIMIT)
                }
                onToggle={() => toggle(item)}
              />
            </li>
          ))}
        </ul>
        {!search && children.hasNextPage && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            loading={children.isFetchingNextPage}
            onClick={() => void children.fetchNextPage()}
          >
            {uiText("Show more folders")}
          </Button>
        )}
        {models.isSuccess &&
          !items.length &&
          (search || (!children.isPending && !children.isError && !folders.length)) && (
            <p className="py-5 text-sm text-muted-foreground">{t("multipart.noCandidates")}</p>
          )}
      </SelectionBrowserViewport>
      <div className="flex h-9 shrink-0 items-center justify-end gap-2">
        {(previous.length > 0 || models.data?.next_cursor) && (
          <>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={models.isFetching || !previous.length}
              onClick={() => {
                setCursor(previous.at(-1) ?? null);
                setPrevious(previous.slice(0, -1));
              }}
            >
              {t("multipart.previousPage")}
            </Button>
            <span className="text-sm tabular-nums">
              {t("multipart.pageNumber", { number: String(previous.length + 1) })}
            </span>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={models.isFetching || !models.data?.next_cursor}
              onClick={() => {
                setPrevious([...previous, cursor]);
                setCursor(models.data?.next_cursor ?? null);
              }}
            >
              {t("multipart.nextPage")}
            </Button>
          </>
        )}
      </div>
    </div>
  );
}
