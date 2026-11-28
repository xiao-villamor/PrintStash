import {
  useCallback,
  useDeferredValue,
  useEffect,
  useId,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { Box, Check, Folder, ImagePlus, X } from "lucide-react";
import { CollectionCoverArtwork } from "@/components/collection-cover-artwork";
import { ProtectedThumbnail } from "@/components/protected-thumbnail";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { TabBar } from "@/components/ui/tabs";
import { useTaxonomyCommands } from "@/features/library/taxonomy";
import { getLibraryThumbnails, listLibraryPage } from "@/lib/api/library-browse";
import { getCollectionCover } from "@/lib/api/taxonomy";
import { formatBytes } from "@/lib/format";
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

function CoverPreviewCard({ name, children }: { name: string; children: ReactNode }) {
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-card">
      {children}
      <div className="flex min-w-0 items-center gap-2 border-t border-border px-3 py-2">
        <Folder className="size-4 shrink-0 text-muted-foreground" aria-hidden />
        <span className="min-w-0 wrap-anywhere text-sm font-medium">{name}</span>
      </div>
    </div>
  );
}

function CoverImagePicker({
  collection,
  savedImage,
  image,
  busy,
  onChoose,
  onReady,
}: {
  collection: CollectionRead;
  savedImage: string | null;
  image: CoverImageDraft | null;
  busy: boolean;
  onChoose: (file: File | null) => void;
  onReady: (url: string) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const chooseButton = useRef<HTMLButtonElement>(null);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<
    "aiSearch.oneImage" | "collection.coverImageInvalid" | "collection.coverImageUnreadable" | null
  >(null);

  function select(files: FileList) {
    if (busy || files.length === 0) return;
    if (files.length !== 1) {
      setError("aiSearch.oneImage");
      return;
    }
    const file = files[0];
    if (
      !/\.(?:gif|jpe?g|png|webp)$/i.test(file.name) ||
      !file.size ||
      file.size > 10 * 1024 * 1024
    ) {
      setError("collection.coverImageInvalid");
      return;
    }
    setError(null);
    onChoose(file);
  }

  return (
    <section
      aria-label={uiText("collection.coverPreview")}
      className={`min-w-0 space-y-3 ${dragging && !busy ? "outline-2 outline-primary outline-offset-4" : ""}`}
      onDragEnter={(event) => {
        if (!event.dataTransfer.types.includes("Files")) return;
        event.preventDefault();
        if (!busy) setDragging(true);
      }}
      onDragOver={(event) => {
        if (!event.dataTransfer.types.includes("Files")) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = busy ? "none" : "copy";
      }}
      onDragLeave={(event) => {
        if (
          !(event.relatedTarget instanceof Node) ||
          !event.currentTarget.contains(event.relatedTarget)
        )
          setDragging(false);
      }}
      onDrop={(event) => {
        if (!event.dataTransfer.types.includes("Files")) return;
        event.preventDefault();
        setDragging(false);
        select(event.dataTransfer.files);
      }}
    >
      <CoverPreviewCard name={collection.name}>
        {image ? (
          <div className="collection-card-collage overflow-hidden bg-muted">
            <img
              key={image.url}
              src={image.url}
              alt={uiText("collection.coverImageDraft")}
              className="size-full object-cover"
              onLoad={() => onReady(image.url)}
              onError={() => {
                setError("collection.coverImageUnreadable");
                onChoose(null);
              }}
            />
          </div>
        ) : savedImage ? (
          <CollectionCoverArtwork image={savedImage} className="collection-card-collage bg-muted" />
        ) : (
          <div className="collection-card-collage grid place-content-center justify-items-center gap-2 bg-muted px-3 text-center text-muted-foreground">
            <ImagePlus className="size-7" aria-hidden />
            <span className="text-sm font-medium">{uiText("collection.coverDropImage")}</span>
          </div>
        )}
      </CoverPreviewCard>
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <div className="min-w-0 flex-1 basis-40">
          {image ? (
            <div role="status" className="min-w-0">
              <p className="wrap-anywhere text-sm font-medium">{image.file.name}</p>
              <p className="text-xs text-muted-foreground">{formatBytes(image.file.size)}</p>
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">
              {uiText("collection.coverImageFormats")}
            </p>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <input
            ref={input}
            type="file"
            hidden
            accept="image/png,image/jpeg,image/gif,image/webp"
            aria-label={uiText("multipart.coverImage")}
            disabled={busy}
            onChange={(event) => {
              if (event.currentTarget.files) select(event.currentTarget.files);
              event.currentTarget.value = "";
            }}
          />
          <Button
            ref={chooseButton}
            type="button"
            variant="outline"
            size="sm"
            disabled={busy}
            onClick={() => input.current?.click()}
          >
            {uiText(image || savedImage ? "multipart.replaceCover" : "aiSearch.chooseImage")}
          </Button>
          {image && (
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              aria-label={uiText("aiSearch.clearImage")}
              disabled={busy}
              onClick={() => {
                setError(null);
                onChoose(null);
                chooseButton.current?.focus();
              }}
            >
              <X className="size-4" aria-hidden />
            </Button>
          )}
        </div>
      </div>
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {uiText(error)}
        </p>
      )}
    </section>
  );
}

interface CoverImageDraft {
  file: File;
  url: string;
  ready: boolean;
}

export function CollectionCardRename({
  collection,
  onEnd,
}: {
  collection: CollectionRead;
  onEnd: (saved: boolean) => void;
}) {
  const { renameCollection } = useTaxonomyCommands();
  const [name, setName] = useState(collection.name);
  const [busy, setBusy] = useState(false);
  const changed = name.trim().length > 0 && name.trim() !== collection.name;
  const focusName = useCallback((input: HTMLInputElement | null) => {
    input?.focus();
    input?.select();
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!changed || busy) return;
    setBusy(true);
    try {
      await renameCollection(collection.id, name.trim());
      toast.success(uiText("collection.renameSuccess", { count: 1 }));
      onEnd(true);
    } catch (error) {
      toast.error(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      noValidate
      className="relative z-20 flex min-w-0 flex-1 items-center gap-1"
      onSubmit={submit}
      onKeyDown={(event) => {
        if (event.key === "Escape" && !busy) {
          event.stopPropagation();
          onEnd(false);
        }
      }}
    >
      <Input
        ref={focusName}
        aria-label={uiText("inbox.collectionName")}
        className="h-7 min-w-0 px-1.5 text-sm"
        value={name}
        onChange={(event) => setName(event.target.value)}
        maxLength={100}
        required
        readOnly={busy}
        aria-busy={busy}
      />
      <Button
        type="submit"
        variant="ghost"
        size="icon-sm"
        className="size-7 shrink-0"
        loading={busy}
        disabled={!changed}
        aria-label={uiText("Save")}
      >
        <Check className="size-4" aria-hidden />
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        className="size-7 shrink-0"
        disabled={busy}
        onClick={() => onEnd(false)}
        aria-label={uiText("Cancel")}
      >
        <X className="size-4" aria-hidden />
      </Button>
    </form>
  );
}

export function CollectionCoverEditor({ collection, ...props }: EditorProps) {
  useUiLocale();
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
  const initialModels = initial.kind === "models" ? initial.model_ids : [];
  const [selected, setSelected] = useState(initialModels);
  const [pickedThumbnails, setPickedThumbnails] = useState<Record<number, string | null>>({});
  const [image, setImage] = useState<CoverImageDraft | null>(null);
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState(false);
  const formId = useId();
  const previewUrl = image?.url;
  useEffect(
    () => () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    },
    [previewUrl],
  );
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
  const candidateThumbnails = new Map(candidates.map((model) => [model.id, model.thumbnail_url]));
  const selectedThumbnails = useQuery({
    queryKey: [
      ...queryKeys.models,
      "collection-cover-selected",
      collection.id,
      getSessionVersion(),
      initialModels,
    ],
    queryFn: ({ signal }) => getLibraryThumbnails(initialModels, { signal }),
    enabled: mode === "models" && initialModels.length > 0,
  });
  const savedThumbnails = new Map(
    selectedThumbnails.data?.items.map((item) => [item.model_id, item.thumbnail_url]) ?? [],
  );
  const coverModels = selected.map((id) => ({
    key: String(id),
    thumbnail: candidateThumbnails.get(id) ?? pickedThumbnails[id] ?? savedThumbnails.get(id),
  }));
  const canSave =
    mode === "image"
      ? !!image?.ready
      : mode === "models"
        ? selected.length > 0 &&
          (initial.kind !== "models" || selected.join(",") !== initial.model_ids.join(","))
        : initial.kind !== "automatic";

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || !canSave) return;
    setBusy(true);
    try {
      if (mode === "image" && image?.ready) {
        await uploadCollectionCover(collection.id, image.file);
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
      className={mode === "models" ? "max-w-4xl" : "max-w-lg"}
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
          variant="framed"
          tabs={[...tabs]}
          active={mode}
          onChange={(value) => !busy && setMode(value)}
        />
        <div role="tabpanel" aria-label={tabs.find((tab) => tab.key === mode)?.label}>
          {mode === "automatic" && (
            <p className="text-sm text-muted-foreground">
              {uiText("collection.coverAutomaticHelp")}
            </p>
          )}
          {mode === "image" && (
            <CoverImagePicker
              collection={collection}
              savedImage={
                initial.kind === "image"
                  ? `/api/v1/collections/${collection.id}/images/${initial.filename}`
                  : null
              }
              image={image}
              busy={busy}
              onChoose={(file) =>
                setImage(file ? { file, url: URL.createObjectURL(file), ready: false } : null)
              }
              onReady={(url) =>
                setImage((draft) => (draft?.url === url ? { ...draft, ready: true } : draft))
              }
            />
          )}
          {mode === "models" && (
            <div className="@container/cover-picker">
              <div className="grid min-w-0 gap-5 @2xl/cover-picker:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
                <section
                  aria-label={uiText("collection.coverPreview")}
                  className="min-w-0 space-y-3"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <h3 className="text-sm font-semibold">{uiText("collection.coverPreview")}</h3>
                    <span className="text-xs text-muted-foreground" role="status">
                      {uiText("collection.coverSelectionCount", { count: selected.length })}
                    </span>
                  </div>
                  <CoverPreviewCard name={collection.name}>
                    <CollectionCoverArtwork
                      models={coverModels}
                      className="collection-card-collage bg-muted"
                    />
                  </CoverPreviewCard>
                  {selected.length > 0 ? (
                    <Button
                      type="button"
                      variant="ghost"
                      size="xs"
                      disabled={busy}
                      onClick={() => setSelected([])}
                    >
                      {uiText("Clear selection")}
                    </Button>
                  ) : (
                    <p className="text-xs text-muted-foreground">
                      {uiText("collection.coverChooseHint")}
                    </p>
                  )}
                </section>
                <div className="min-w-0 space-y-3">
                  <Input
                    type="search"
                    aria-label={uiText("Search models")}
                    placeholder={uiText("Search models")}
                    value={search}
                    disabled={busy}
                    onChange={(event) => setSearch(event.target.value)}
                  />
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
                    <ul className="grid max-h-80 grid-cols-[repeat(auto-fill,minmax(min(100%,7rem),1fr))] gap-2 overflow-y-auto p-1">
                      {candidates.map((model) => {
                        const checked = selected.includes(model.id);
                        return (
                          <li key={model.id}>
                            <label
                              className={`relative flex h-full min-w-0 cursor-action flex-col border text-left focus-within:ring-2 focus-within:ring-ring ${checked ? "border-primary bg-accent" : "border-border bg-card hover:border-border-strong"}`}
                            >
                              <input
                                type="checkbox"
                                className="absolute right-2 top-2 z-10 size-4"
                                checked={checked}
                                disabled={busy || (!checked && selected.length === 4)}
                                onChange={() => {
                                  if (!checked) {
                                    setPickedThumbnails((current) => ({
                                      ...current,
                                      [model.id]: model.thumbnail_url,
                                    }));
                                  }
                                  setSelected((current) =>
                                    checked
                                      ? current.filter((id) => id !== model.id)
                                      : [...current, model.id],
                                  );
                                }}
                              />
                              <ProtectedThumbnail
                                path={model.thumbnail_url}
                                alt=""
                                placeholder={
                                  <Box className="size-4 text-muted-foreground" aria-hidden />
                                }
                                priority="background"
                                className="grid aspect-[4/3] min-w-0 shrink-0 place-items-center overflow-hidden bg-muted"
                                imageClassName="h-full w-full object-contain"
                              />
                              <span className="px-2 py-1.5">
                                <span
                                  className="line-clamp-2 min-h-8 wrap-anywhere text-xs leading-4"
                                  title={model.name}
                                >
                                  {model.name}
                                </span>
                              </span>
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
              </div>
            </div>
          )}
        </div>
      </form>
    </Modal>
  );
}
