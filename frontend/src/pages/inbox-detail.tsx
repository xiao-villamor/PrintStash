import { inboxFiles, inboxProviderLabel } from "./inbox-summary";
import { ProfileFacts } from "@/components/model-detail/profile-facts";
import { CaptureSourceSummary } from "@/components/model-detail/source-summary";
import { useTaxonomyCommands } from "@/features/library/taxonomy";
import { knownUiText } from "@/lib/locale";
import { uiText } from "@/lib/locale";
import { getErrorMessage, userMessage } from "@/lib/errors";
import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { Trash2 } from "lucide-react";
import { useParams } from "@tanstack/react-router";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { CollectionPicker } from "@/components/collection-picker";
import { Input, inputClasses } from "@/components/ui/input";
import { PageContainer } from "@/components/ui/page-container";
import { PageHeader } from "@/components/ui/page-header";
import {
  createCollection,
  dismissPendingImport,
  getPendingImport,
  importPendingImport,
  retryPendingImport,
  searchCollections,
  updatePendingImport,
} from "@/lib/api";
import { inboxApi, useInboxItem, useInboxCommands, inboxIsTerminal } from "@/lib/queries/inbox";
import { formatBytes, timeAgo } from "@/lib/format";
import { Link } from "@/lib/link";
import { useI18n } from "@/lib/i18n";
import { useRouter } from "@/lib/navigation";
import { useCollectionLookupById } from "@/lib/queries";
import { getSessionVersion, requireSessionVersion } from "@/lib/session-transport";
import { toast } from "@/lib/toast";
import type { CollectionNodeRead, InboxItem } from "@/types";
import { safeHttpUrl } from "@/components/model-detail/source-url";

export interface InboxDetailApi {
  createCollection: typeof createCollection;
  dismissPendingImport: typeof dismissPendingImport;
  getPendingImport: typeof getPendingImport;
  importPendingImport: typeof importPendingImport;
  retryPendingImport: typeof retryPendingImport;
  searchCollections: typeof searchCollections;
  updatePendingImport: typeof updatePendingImport;
}

const defaultInboxDetailApi: InboxDetailApi = {
  createCollection,
  dismissPendingImport,
  getPendingImport,
  importPendingImport,
  retryPendingImport,
  searchCollections,
  updatePendingImport,
};

const NEW_COLLECTION = "new";
const NO_COLLECTION = "none";
const PICK_COLLECTION = "pick";

function statusLabel(item: InboxItem, t: ReturnType<typeof useI18n>["t"]): string {
  if (item.completion === "partial") return t("inbox.partial");
  switch (item.state) {
    case "review":
      return t("inbox.needsReview");
    case "completed":
      return t("inbox.completed");
    case "captured":
      return t("inbox.state.captured");
    case "resolving":
      return t("inbox.state.resolving");
    case "importing":
      return t("inbox.state.importing");
    case "failed":
      return t("inbox.state.failed");
    default:
      return knownUiText(item.state);
  }
}

function resultLabel(result: InboxItem["results"][number], t: ReturnType<typeof useI18n>["t"]) {
  switch (result.state) {
    case "imported":
      return t("inbox.result.imported");
    case "deduplicated":
      return t("inbox.result.deduplicated");
    case "failed":
      return t("inbox.result.failed");
  }
}

function capturedTitle(item: InboxItem): string | null {
  if (item.manifest.schema_version === 2 || item.manifest.schema_version === 3) {
    const title = item.manifest.source.fields.title?.value.trim();
    if (title) return title;
  }
  return item.display_title?.trim() || null;
}

export default function InboxDetailPage({ api = defaultInboxDetailApi }: { api?: InboxDetailApi }) {
  const { locale, t } = useI18n();
  const { id } = useParams({ strict: false });
  const inboxId = Number(id);
  const routeRef = useRef(inboxId);
  useLayoutEffect(() => {
    routeRef.current = inboxId;
  }, [inboxId]);
  const router = useRouter();
  const [submittedId, setSubmittedId] = useState<number | null>(null);
  const remote = useInboxItem(
    Number.isSafeInteger(inboxId) && inboxId > 0 ? inboxId : null,
    api,
    submittedId === inboxId,
  );
  const commands = useInboxCommands({ ...inboxApi, ...api });
  const taxonomy = useTaxonomyCommands({ createCollection: api.createCollection });
  const item = remote.data ?? null;
  type ReviewDraft = {
    id: number;
    base: InboxItem;
    selected: string[];
    tags: string;
    destination: string;
    pickedCollection: CollectionNodeRead | null;
    collectionName: string;
  };
  const [editingDestinationId, setEditingDestinationId] = useState<number | null>(null);
  const [draft, setDraft] = useState<ReviewDraft | null>(null);
  const review =
    draft?.id === inboxId
      ? draft
      : item
        ? {
            id: item.id,
            base: item,
            selected:
              item.manifest.kind === "direct" || item.manifest.kind === "browser_file"
                ? inboxFiles(item).map((file) => file.id)
                : (item.manifest.selected_ids ??
                  (inboxFiles(item).length === 1 ? inboxFiles(item).map((file) => file.id) : [])),
            tags: item.requested_tags.join(", "),
            destination:
              item.target_collection_id === null
                ? NEW_COLLECTION
                : String(item.target_collection_id),
            pickedCollection: null,
            collectionName:
              capturedTitle(item) || t("inbox.defaultCollectionName", { id: String(item.id) }),
          }
        : null;
  const selected = review?.selected ?? [];
  const tags = review?.tags ?? "";
  const destination = review?.destination ?? NEW_COLLECTION;
  const pickedCollection = review?.pickedCollection ?? null;
  const collectionName = review?.collectionName ?? "";
  function editReview(patch: Partial<ReviewDraft>) {
    if (review) setDraft({ ...review, ...patch });
  }
  const setSelected = (next: string[] | ((current: string[]) => string[])) =>
    editReview({ selected: next instanceof Function ? next(selected) : next });
  const setTags = (tags: string) => editReview({ tags });
  const setDestination = (destination: string) => editReview({ destination });
  const setCollectionName = (collectionName: string) => editReview({ collectionName });
  const destinationId =
    destination !== NEW_COLLECTION &&
    destination !== NO_COLLECTION &&
    destination !== PICK_COLLECTION
      ? Number(destination)
      : null;
  const savedCollection = useCollectionLookupById(destinationId);
  const destinationCollection =
    pickedCollection?.id === destinationId ? pickedCollection : savedCollection.data?.collection;
  const [busyId, setBusyId] = useState<number | null>(null);
  const busy = busyId === inboxId;
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null);
  const confirmDelete = confirmDeleteId === inboxId;
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const deleting = deletingId === inboxId;
  const choices = useMemo(() => (item ? inboxFiles(item) : []), [item]);
  if (remote.isError && !item)
    return (
      <PageContainer>
        <PageHeader title={t("inbox.detailTitle")} />
        <div role="alert">
          <p>{userMessage(remote.error)}</p>
          <Button variant="outline" onClick={() => void remote.refetch()}>
            {t("inbox.retry")}
          </Button>
        </div>
      </PageContainer>
    );
  if (!item)
    return (
      <PageContainer>
        <PageHeader title={t("inbox.detailTitle")} />
        <p className="text-sm text-muted-foreground">{t("inbox.loading")}</p>
      </PageContainer>
    );
  const toggle = (fileId: string) =>
    setSelected((current) =>
      current.includes(fileId) ? current.filter((value) => value !== fileId) : [...current, fileId],
    );
  const saveReview = async (collectionId: number | null, session: number) => {
    await commands.update.mutateAsync({
      session,
      id: item.id,
      payload: {
        collection_id: collectionId,
        tags: tags
          .split(",")
          .map((tag) => tag.trim())
          .filter(Boolean),
        selected_ids: selected,
      },
    });
  };
  const resolveDestination = async (session: number): Promise<number | null> => {
    requireSessionVersion(session);
    if (destination === NO_COLLECTION) return null;
    if (destination === PICK_COLLECTION) throw new Error("Choose a collection before importing");
    if (destination !== NEW_COLLECTION) return Number(destination);

    const name = collectionName.trim();
    // Search is paged, so a same-named root cannot be missed behind nested matches.
    let cursor: string | null = null;
    do {
      requireSessionVersion(session);
      const page = await api.searchCollections(name, "edit", cursor);
      requireSessionVersion(session);
      if (routeRef.current !== inboxId) throw new DOMException("Inbox route changed", "AbortError");
      const existing = page.items.find(
        (collection) => collection.parent_id === null && collection.name === name,
      );
      if (existing) {
        setDestination(String(existing.id));
        return existing.id;
      }
      cursor = page.next_cursor;
    } while (cursor !== null);
    requireSessionVersion(session);
    const created = await taxonomy.createCollection({ name, parent_id: null });
    requireSessionVersion(session);
    if (routeRef.current !== inboxId) throw new DOMException("Inbox route changed", "AbortError");
    setDestination(String(created.id));
    return created.id;
  };
  const importSelected = async () => {
    setBusyId(inboxId);
    const session = getSessionVersion();
    try {
      const collectionId = await resolveDestination(session);
      requireSessionVersion(session);
      if (routeRef.current !== inboxId) return;
      await saveReview(collectionId, session);
      requireSessionVersion(session);
      if (routeRef.current !== inboxId) return;
      const next = await commands.import.mutateAsync({
        id: item.id,
        selectedIds: selected,
        session,
      });
      if (getSessionVersion() !== session || routeRef.current !== inboxId) return;
      setSubmittedId(inboxIsTerminal(next) ? null : item.id);
    } catch (error) {
      if (getSessionVersion() === session && routeRef.current === inboxId) toast.error(error);
    } finally {
      if (getSessionVersion() === session && routeRef.current === inboxId) setBusyId(null);
    }
  };
  const deleteItem = async () => {
    setDeletingId(inboxId);
    const session = getSessionVersion();
    try {
      await commands.dismiss.mutateAsync({ id: item.id, session });
      if (getSessionVersion() !== session || routeRef.current !== inboxId) return;
      setConfirmDeleteId(null);
      router.push("/inbox");
    } catch (error) {
      if (getSessionVersion() === session && routeRef.current === inboxId) toast.error(error);
    } finally {
      if (getSessionVersion() === session && routeRef.current === inboxId) setDeletingId(null);
    }
  };
  const retry = async () => {
    const session = getSessionVersion();
    try {
      const next = await commands.retry.mutateAsync({ id: item.id, session });
      if (getSessionVersion() !== session || routeRef.current !== inboxId) return;
      setSubmittedId(inboxIsTerminal(next) ? null : item.id);
    } catch (error) {
      if (getSessionVersion() === session && routeRef.current === inboxId) toast.error(error);
    }
  };
  const title = capturedTitle(item) || t("inbox.detailTitle");
  const sourceUrl = item.source_url ? safeHttpUrl(item.source_url) : null;
  const selectedFiles = choices.filter((file) => selected.includes(file.id));
  const selectedBytes = selectedFiles.reduce((total, file) => total + (file.size ?? 0), 0);
  const allSelected = choices.length > 0 && selected.length === choices.length;
  const collectionNameMissing =
    destination === NEW_COLLECTION && collectionName.trim().length === 0;
  const collectionNameId = `inbox-collection-name-${item.id}`;
  const collectionNameHintId = `${collectionNameId}-hint`;
  return (
    <PageContainer>
      {remote.isError && (
        <div role="alert">
          <p>{userMessage(remote.error)}</p>
          <Button variant="outline" onClick={() => void remote.refetch()}>
            {t("inbox.retry")}
          </Button>
        </div>
      )}
      <PageHeader
        title={t("inbox.title")}
        description={t("inbox.detailDescription")}
        actions={
          <>
            <Button variant="outline" size="sm" asChild>
              <Link href="/inbox">{t("inbox.back")}</Link>
            </Button>
            {item.state === "completed" && item.resulting_model_id && (
              <Button size="sm" asChild>
                <Link href={`/models/${item.resulting_model_id}`}>{t("inbox.openModel")}</Link>
              </Button>
            )}
            {sourceUrl && (
              <Button variant="outline" size="sm" asChild>
                <a href={sourceUrl} target="_blank" rel="noreferrer">
                  {uiText("Open source")}
                </a>
              </Button>
            )}
          </>
        }
      />
      <div className="overflow-hidden border border-border bg-card">
        <div className="flex flex-wrap items-start justify-between gap-3 px-5 py-5">
          <div className="min-w-0">
            <h2 className="break-words text-sm font-semibold">{title}</h2>
            <p className="mt-2 text-xs text-muted-foreground">
              {inboxProviderLabel(item) || t("inbox.sourcePreparing")} ·{" "}
              <time
                dateTime={item.created_at}
                title={new Date(item.created_at).toLocaleString(locale)}
              >
                {timeAgo(item.created_at)}
              </time>
            </p>
            <CaptureSourceSummary manifest={item.manifest} />
            {item.source_url && !sourceUrl && (
              <p className="mt-2 truncate text-xs text-muted-foreground">{item.source_url}</p>
            )}
          </div>
          <div className="flex items-center gap-2">
            <span
              className={
                item.state === "failed"
                  ? "text-xs text-destructive"
                  : "text-xs text-muted-foreground"
              }
            >
              {statusLabel(item, t)}
            </span>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label={t("inbox.delete")}
              title={t("inbox.delete")}
              onClick={() => setConfirmDeleteId(inboxId)}
            >
              <Trash2 className="h-3.5 w-3.5 text-muted-foreground" />
            </Button>
          </div>
        </div>
        {item.error_code && (
          <div
            role="alert"
            className="rounded-md border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive"
          >
            {getErrorMessage(item.error_code)}
            {item.error_code === "staging_expired" && sourceUrl && (
              <a className="ml-2 underline" href={sourceUrl} target="_blank" rel="noreferrer">
                {uiText("Return to the source and capture the files again")}
              </a>
            )}
          </div>
        )}
        {item.review_reason && item.state === "review" && (
          <p role="status" className="px-5 text-sm text-muted-foreground">
            {getErrorMessage(item.review_reason)}
          </p>
        )}
        {item.state === "importing" && (
          <p role="status" aria-live="polite" className="text-sm text-muted-foreground">
            {t("inbox.importing")}
          </p>
        )}
        {item.state === "review" && (
          <div className="grid gap-4 px-5 pb-5">
            <Card>
              <CardHeader className="flex flex-row items-center justify-between gap-4 space-y-0 p-5 pb-3">
                <div>
                  <h2 className="text-sm font-semibold">{t("inbox.filesToImport")}</h2>
                  <p className="mt-1 text-sm text-muted-foreground">
                    {t("inbox.selectedFiles", {
                      selected: String(selected.length),
                      total: String(choices.length),
                    })}
                  </p>
                </div>
                <Button
                  size="xs"
                  variant="ghost"
                  onClick={() => setSelected(allSelected ? [] : choices.map((file) => file.id))}
                >
                  {allSelected ? t("inbox.clearAll") : t("inbox.selectAll")}
                </Button>
              </CardHeader>
              <CardContent className="p-0">
                <fieldset aria-label={t("inbox.filesToImport")}>
                  <div className="divide-y divide-border/70">
                    {choices.map((file) => (
                      <label
                        key={file.id}
                        className="flex cursor-action items-center gap-3 px-5 py-4 hover:bg-muted/50"
                      >
                        <Checkbox
                          checked={selected.includes(file.id)}
                          onChange={() => toggle(file.id)}
                          ariaLabel={t("inbox.selectFile", { name: file.name })}
                        />

                        <div className="min-w-0 flex-1 text-sm font-medium">
                          <span className="block truncate">{file.name}</span>
                          {item.manifest.schema_version === 3 &&
                            item.manifest.profiles[file.id] && (
                              <ProfileFacts profile={item.manifest.profiles[file.id]} />
                            )}
                        </div>
                        <span className="shrink-0 text-xs uppercase text-muted-foreground">
                          {[file.file_type, file.size === null ? null : formatBytes(file.size)]
                            .filter(Boolean)
                            .join(" · ")}
                        </span>
                      </label>
                    ))}
                  </div>
                </fieldset>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="flex flex-row items-center justify-between space-y-0 p-5 pb-3">
                <h2 className="text-sm font-semibold">{t("inbox.destination")}</h2>
                <Button
                  size="xs"
                  variant="outline"
                  onClick={() =>
                    setEditingDestinationId(editingDestinationId === inboxId ? null : inboxId)
                  }
                >
                  {editingDestinationId === inboxId
                    ? t("inbox.doneEditing")
                    : t("inbox.editDestination")}
                </Button>
              </CardHeader>
              <CardContent className="space-y-4 p-5 pt-0">
                {editingDestinationId !== inboxId ? (
                  <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2 text-sm">
                    <dt className="text-muted-foreground">{uiText("Collection")}</dt>
                    <dd className="break-words">
                      {destination === NEW_COLLECTION
                        ? collectionName
                        : destination === NO_COLLECTION
                          ? t("inbox.noCollection")
                          : (destinationCollection?.display_path ?? t("inbox.destination"))}
                    </dd>
                    <dt className="text-muted-foreground">{t("inbox.tags")}</dt>
                    <dd className="break-words">{tags || t("inbox.noTags")}</dd>
                  </dl>
                ) : (
                  <>
                    <label className="block text-sm font-medium">
                      {uiText("Collection")}
                      <select
                        value={destination}
                        onChange={(event) => setDestination(event.target.value)}
                        className={inputClasses}
                      >
                        <option value={NEW_COLLECTION}>{t("inbox.newCollectionFromTitle")}</option>
                        <option value={NO_COLLECTION}>{t("inbox.noCollection")}</option>
                        <option value={PICK_COLLECTION}>
                          {uiText("Choose existing collection")}
                        </option>
                        {destinationId !== null && (
                          <option value={destinationId}>
                            {destinationCollection?.display_path ??
                              uiText("Collection #{value1}", { value1: String(destinationId) })}
                          </option>
                        )}
                      </select>
                    </label>
                    {destination === PICK_COLLECTION && (
                      <CollectionPicker
                        minRole="edit"
                        selectedPath={null}
                        emptyLabel={uiText("No editable collections.")}
                        onSelect={(collection) => {
                          if (collection === null) throw new Error("A collection is required");
                          editReview({
                            pickedCollection: collection,
                            destination: String(collection.id),
                          });
                        }}
                      />
                    )}
                    {destinationId !== null && savedCollection.isError && (
                      <p role="alert" className="text-sm text-destructive">
                        {uiText("Folders could not be loaded.")}
                      </p>
                    )}
                    {destination === NEW_COLLECTION && (
                      <div>
                        <label htmlFor={collectionNameId} className="block text-sm font-medium">
                          {t("inbox.collectionName")}
                        </label>
                        <Input
                          id={collectionNameId}
                          aria-describedby={collectionNameHintId}
                          className="mt-1.5"
                          value={collectionName}
                          onChange={(event) => setCollectionName(event.target.value)}
                          maxLength={255}
                        />
                        <p
                          id={collectionNameHintId}
                          className="mt-1.5 text-xs font-normal text-muted-foreground"
                        >
                          {t("inbox.collectionNameHint")}
                        </p>
                      </div>
                    )}
                    <label className="block text-sm font-medium">
                      <span>{t("inbox.tags")}</span>
                      <Input
                        className="mt-1.5"
                        value={tags}
                        onChange={(event) => setTags(event.target.value)}
                        placeholder={t("inbox.commaSeparated")}
                      />
                    </label>
                  </>
                )}
                <div className="border-t border-border/70 pt-4">
                  <div className="mb-4 flex items-center justify-between text-sm">
                    <span className="text-muted-foreground">{t("inbox.selected")}</span>
                    <span className="font-medium">
                      {selected.length}
                      {selectedBytes > 0 ? ` · ${formatBytes(selectedBytes)}` : ""}
                    </span>
                  </div>
                  <Button
                    size="sm"
                    onClick={() => void importSelected()}
                    disabled={
                      selected.length === 0 ||
                      collectionNameMissing ||
                      destination === PICK_COLLECTION
                    }
                    loading={busy}
                  >
                    {t("inbox.import")}
                  </Button>
                </div>
              </CardContent>
            </Card>
          </div>
        )}
        {item.results.length > 0 && (
          <Card>
            <CardContent className="space-y-3 p-5">
              <h2 className="text-sm font-medium">{t("inbox.results")}</h2>
              {item.results.map((result) => (
                <div key={result.id} className="flex flex-wrap items-center gap-2 text-sm">
                  <Badge variant={result.state === "failed" ? "destructive" : "success"}>
                    {resultLabel(result, t)}
                  </Badge>
                  <span>{result.original_filename}</span>
                  {result.error_code && (
                    <p className="w-full text-sm text-destructive">
                      {getErrorMessage(result.error_code)}
                      {" · "}
                      {result.retryable && item.error_code !== "staging_expired"
                        ? uiText("Retry failed files to try this file again.")
                        : uiText("Return to the source and capture the files again")}
                    </p>
                  )}
                  {result.model_id &&
                    (item.results.length > 1 || result.model_id !== item.resulting_model_id) && (
                      <Link
                        className="text-primary hover:underline"
                        href={`/models/${result.model_id}`}
                      >
                        {t("inbox.openModel")}
                      </Link>
                    )}
                </div>
              ))}
              {item.completion === "partial" &&
                item.retryable &&
                item.error_code !== "staging_expired" &&
                item.results.some((result) => result.state === "failed" && result.retryable) && (
                  <Button size="sm" variant="outline" onClick={() => void retry()}>
                    {t("inbox.retryFailedFiles")}
                  </Button>
                )}
            </CardContent>
          </Card>
        )}
        {item.state === "failed" && item.retryable && item.error_code !== "staging_expired" && (
          <Button variant="outline" onClick={() => void retry()}>
            {t("inbox.retry")}
          </Button>
        )}
      </div>
      <ConfirmModal
        open={confirmDelete}
        onClose={() => setConfirmDeleteId(null)}
        title={t("inbox.deleteTitle")}
        description={t("inbox.deleteDescription")}
        confirmLabel={t("inbox.deleteConfirm")}
        busy={deleting}
        onConfirm={() => void deleteItem()}
      />
    </PageContainer>
  );
}
