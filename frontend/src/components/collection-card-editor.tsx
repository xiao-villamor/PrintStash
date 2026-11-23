import { useDeferredValue, useId, useState, type FormEvent } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { Box } from "lucide-react";
import { ProtectedThumbnail } from "@/components/protected-thumbnail";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { TabBar } from "@/components/ui/tabs";
import { useTaxonomyCommands } from "@/features/library/taxonomy";
import { listLibraryPage } from "@/lib/api/library-browse";
import { getCollectionCover } from "@/lib/api/taxonomy";
import { useUiLocale } from "@/lib/i18n";
import { uiText } from "@/lib/locale";
import { queryKeys } from "@/lib/query-client";
import { getSessionVersion } from "@/lib/session-transport";
import { toast } from "@/lib/toast";
import type { CollectionCover, CollectionRead } from "@/types";

export type CollectionCardAction = "rename" | "cover";

interface EditorProps {
  collection: CollectionRead;
  onClose: () => void;
  onSaved: () => void;
}

export function CollectionCardEditor({
  action,
  ...props
}: EditorProps & { action: CollectionCardAction }) {
  useUiLocale();
  return action === "rename" ? <RenameDialog {...props} /> : <CoverDialog {...props} />;
}

function RenameDialog({ collection, onClose, onSaved }: EditorProps) {
  const { renameCollection } = useTaxonomyCommands();
  const formId = useId();
  const [name, setName] = useState(collection.name);
  const [busy, setBusy] = useState(false);
  const changed = name.trim().length > 0 && name.trim() !== collection.name;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!changed || busy) return;
    setBusy(true);
    try {
      await renameCollection(collection.id, name.trim());
      toast.success(uiText("collection.renameSuccess", { count: 1 }));
      onSaved();
    } catch (error) {
      toast.error(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open
      onClose={() => !busy && onClose()}
      title={uiText("collection.renameTitle")}
      className="max-w-sm"
      footer={
        <>
          <Button type="button" variant="outline" disabled={busy} onClick={onClose}>
            {uiText("Cancel")}
          </Button>
          <Button type="submit" form={formId} loading={busy} disabled={!changed}>
            {uiText("Rename")}
          </Button>
        </>
      }
    >
      <form id={formId} className="space-y-2" onSubmit={submit}>
        <label className="text-sm font-medium" htmlFor={`${formId}-name`}>
          {uiText("inbox.collectionName")}
        </label>
        <Input
          id={`${formId}-name`}
          data-autofocus
          value={name}
          onFocus={(event) => event.currentTarget.select()}
          onChange={(event) => setName(event.target.value)}
          maxLength={100}
          required
          disabled={busy}
        />
      </form>
    </Modal>
  );
}

function CoverDialog({ collection, ...props }: EditorProps) {
  const selection = useQuery({
    queryKey: [...queryKeys.collections, "cover", collection.id, getSessionVersion()],
    queryFn: ({ signal }) => getCollectionCover(collection.id, { signal }),
  });
  return selection.data ? (
    <CoverForm {...props} collection={collection} initial={selection.data} />
  ) : (
    <Modal
      open
      onClose={props.onClose}
      title={uiText("collection.coverTitle")}
      className="max-w-lg"
    >
      {selection.isError ? (
        <div className="space-y-3">
          <p role="alert" className="text-sm">
            {uiText("collection.coverLoadFailed")}
          </p>
          <Button variant="outline" size="sm" onClick={() => void selection.refetch()}>
            {uiText("Retry")}
          </Button>
        </div>
      ) : (
        <p role="status" className="text-sm text-muted-foreground">
          {uiText("Loading")}
        </p>
      )}
    </Modal>
  );
}

function CoverForm({
  collection,
  initial,
  onClose,
  onSaved,
}: EditorProps & { initial: CollectionCover }) {
  const { setCollectionCover, uploadCollectionCover } = useTaxonomyCommands();
  const [mode, setMode] = useState(initial.kind);
  const [selected, setSelected] = useState(initial.kind === "models" ? initial.model_ids : []);
  const [image, setImage] = useState<File | null>(null);
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState(false);
  const formId = useId();
  const term = useDeferredValue(search.trim());
  const tabs = [
    { key: "automatic", label: uiText("Automatic") },
    { key: "models", label: uiText("collection.coverModels") },
    { key: "image", label: uiText("modelCover.upload") },
  ] as const;
  const models = useInfiniteQuery({
    queryKey: [
      ...queryKeys.models,
      "collection-cover-candidates",
      collection.id,
      collection.path,
      term,
    ],
    queryFn: ({ pageParam, signal }) =>
      listLibraryPage(
        {
          collection: collection.path,
          q: term || undefined,
          view: "all",
          sort: "date-desc",
          limit: 24,
          cursor: pageParam || undefined,
        },
        { signal },
      ),
    initialPageParam: "",
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    enabled: mode === "models",
  });
  const candidates =
    models.data?.pages.flatMap((page) =>
      page.items.flatMap((entry) => (entry.kind === "model" ? [entry.model] : [])),
    ) ?? [];
  const canSave =
    mode === "image"
      ? !!image
      : mode === "models"
        ? selected.length > 0 &&
          (initial.kind !== "models" || selected.join(",") !== initial.model_ids.join(","))
        : initial.kind !== "automatic";

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || !canSave) return;
    setBusy(true);
    try {
      if (mode === "image" && image) {
        await uploadCollectionCover(collection.id, image);
      } else if (mode === "models") {
        await setCollectionCover(collection.id, { kind: "models", model_ids: selected });
      } else if (mode === "automatic") {
        await setCollectionCover(collection.id, { kind: "automatic" });
      }
      onSaved();
    } catch (error) {
      toast.error(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open
      onClose={() => !busy && onClose()}
      title={uiText("collection.coverTitle")}
      description={<span className="wrap-anywhere">{collection.name}</span>}
      className="max-w-lg"
      footer={
        <>
          <Button type="button" variant="outline" disabled={busy} onClick={onClose}>
            {uiText("Cancel")}
          </Button>
          <Button type="submit" form={formId} disabled={!canSave} loading={busy}>
            {uiText("collection.coverSave")}
          </Button>
        </>
      }
    >
      <form id={formId} onSubmit={submit} className="space-y-4">
        <TabBar
          tabs={[...tabs]}
          active={mode}
          onChange={(value) => !busy && setMode(value)}
          className="flex-wrap border-b border-border"
          tabClassName="px-2 py-2 text-sm text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          activeTabClassName="text-foreground"
        />
        <div role="tabpanel" aria-label={tabs.find((tab) => tab.key === mode)?.label}>
          {mode === "automatic" && (
            <p className="text-sm text-muted-foreground">
              {uiText("collection.coverAutomaticHelp")}
            </p>
          )}
          {mode === "image" && (
            <div className="space-y-3">
              {initial.kind === "image" && (
                <ProtectedThumbnail
                  path={`/api/v1/collections/${collection.id}/images/${initial.filename}`}
                  alt={uiText("collection.coverCurrent")}
                  placeholder={null}
                  className="flex h-28 items-center justify-center bg-muted"
                  imageClassName="h-full w-full object-contain"
                />
              )}
              <Input
                aria-label={uiText("multipart.coverImage")}
                type="file"
                accept="image/png,image/jpeg,image/gif,image/webp"
                disabled={busy}
                onChange={(event) => setImage(event.target.files?.[0] ?? null)}
              />
            </div>
          )}
          {mode === "models" && (
            <div className="space-y-3">
              <Input
                type="search"
                aria-label={uiText("Search models")}
                placeholder={uiText("Search models")}
                value={search}
                disabled={busy}
                onChange={(event) => setSearch(event.target.value)}
              />
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-xs text-muted-foreground" role="status">
                  {uiText("collection.coverSelectionCount", { count: selected.length })}
                </p>
                {selected.length > 0 && (
                  <Button
                    type="button"
                    variant="ghost"
                    size="xs"
                    disabled={busy}
                    onClick={() => setSelected([])}
                  >
                    {uiText("Clear selection")}
                  </Button>
                )}
              </div>
              {models.isError && (
                <div className="flex items-center gap-2">
                  <p role="alert" className="text-sm">
                    {uiText("collection.coverCandidatesFailed")}
                  </p>
                  <Button
                    type="button"
                    size="xs"
                    variant="outline"
                    onClick={() => void models.refetch()}
                  >
                    {uiText("Retry")}
                  </Button>
                </div>
              )}
              {models.isPending ? (
                <p role="status" className="text-sm text-muted-foreground">
                  {uiText("Loading")}
                </p>
              ) : !models.isError && candidates.length === 0 && !models.hasNextPage ? (
                <p className="text-sm text-muted-foreground">{uiText("No models found")}</p>
              ) : null}
              {candidates.length > 0 && (
                <ul className="max-h-64 divide-y divide-border overflow-y-auto border border-border">
                  {candidates.map((model) => {
                    const checked = selected.includes(model.id);
                    return (
                      <li key={model.id}>
                        <label className="flex min-w-0 cursor-action items-center gap-3 px-3 py-2 text-sm hover:bg-muted">
                          <input
                            type="checkbox"
                            checked={checked}
                            disabled={busy || (!checked && selected.length === 4)}
                            onChange={() =>
                              setSelected((current) =>
                                checked
                                  ? current.filter((id) => id !== model.id)
                                  : [...current, model.id],
                              )
                            }
                          />
                          <ProtectedThumbnail
                            path={model.thumbnail_url}
                            alt=""
                            placeholder={
                              <Box className="size-4 text-muted-foreground" aria-hidden />
                            }
                            priority="background"
                            className="grid size-9 shrink-0 place-items-center overflow-hidden bg-muted"
                            imageClassName="h-full w-full object-contain"
                          />
                          <span className="wrap-anywhere">{model.name}</span>
                        </label>
                      </li>
                    );
                  })}
                </ul>
              )}
              {models.hasNextPage && (
                <Button
                  type="button"
                  size="xs"
                  variant="outline"
                  loading={models.isFetchingNextPage}
                  onClick={() => void models.fetchNextPage()}
                >
                  {uiText("Load more")}
                </Button>
              )}
            </div>
          )}
        </div>
      </form>
    </Modal>
  );
}
