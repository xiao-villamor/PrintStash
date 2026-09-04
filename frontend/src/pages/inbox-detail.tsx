import { useEffect, useMemo, useState } from "react";
import { CalendarDays, ExternalLink, FileBox, FolderPlus, Link2, Tags, Trash2 } from "lucide-react";
import { useParams } from "react-router-dom";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { Input } from "@/components/ui/input";
import { PageContainer } from "@/components/ui/page-container";
import { PageHeader } from "@/components/ui/page-header";
import {
  createCollection,
  dismissPendingImport,
  getPendingImport,
  importPendingImport,
  retryPendingImport,
  updatePendingImport,
} from "@/lib/api";
import { createCompletionChainedPoller } from "@/lib/completion-chained-polling";
import { formatBytes } from "@/lib/format";
import { Link } from "@/lib/link";
import { useI18n } from "@/lib/i18n";
import { useRouter } from "@/lib/navigation";
import { useCollections } from "@/lib/queries";
import { toast } from "@/lib/toast";
import type { InboxManifestFile, InboxItem } from "@/types";
import { safeHttpUrl } from "@/components/model-detail/source-url";

export interface InboxDetailApi {
  createCollection: typeof createCollection;
  dismissPendingImport: typeof dismissPendingImport;
  getPendingImport: typeof getPendingImport;
  importPendingImport: typeof importPendingImport;
  retryPendingImport: typeof retryPendingImport;
  updatePendingImport: typeof updatePendingImport;
}

const defaultInboxDetailApi: InboxDetailApi = {
  createCollection,
  dismissPendingImport,
  getPendingImport,
  importPendingImport,
  retryPendingImport,
  updatePendingImport,
};

const ACTIVE_STATES = new Set<InboxItem["state"]>(["captured", "resolving", "importing"]);
const NEW_COLLECTION = "new";
const NO_COLLECTION = "none";

function files(item: InboxItem): InboxManifestFile[] {
  return item.manifest.kind === "archive"
    ? (item.manifest.entries ?? [])
    : item.manifest.kind === "model_files"
      ? (item.manifest.files ?? [])
      : [];
}

function isTerminalState(state: InboxItem["state"]): boolean {
  return state === "completed" || state === "failed" || state === "dismissed";
}

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
      return item.state;
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
  if (item.manifest.schema_version === 2) {
    const title = item.manifest.source.fields.title?.value.trim();
    if (title) return title;
  }
  return item.display_title?.trim() || null;
}

function providerLabel(item: InboxItem): string {
  const provider =
    item.manifest.schema_version === 2
      ? item.manifest.source.provider
      : item.source_hostname?.split(".").at(-2);
  if (!provider) return "Web";
  switch (provider.toLowerCase()) {
    case "cults3d":
      return "Cults3D";
    case "makerworld":
      return "MakerWorld";
    case "myminifactory":
      return "MyMiniFactory";
    case "printables":
      return "Printables";
    case "thingiverse":
      return "Thingiverse";
  }
  return provider.charAt(0).toUpperCase() + provider.slice(1);
}

function statusVariant(item: InboxItem): "destructive" | "secondary" | "success" | "warning" {
  if (item.state === "failed") return "destructive";
  if (item.state === "completed") return "success";
  if (item.state === "review") return "warning";
  return "secondary";
}

export default function InboxDetailPage({ api = defaultInboxDetailApi }: { api?: InboxDetailApi }) {
  const { locale, t } = useI18n();
  const { id } = useParams();
  const inboxId = Number(id);
  const router = useRouter();
  const collections = useCollections().data ?? [];
  const [item, setItem] = useState<InboxItem | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [tags, setTags] = useState("");
  const [destination, setDestination] = useState<string>(NEW_COLLECTION);
  const [collectionName, setCollectionName] = useState("");
  const [busy, setBusy] = useState(false);
  // The import/retry endpoint returns the pre-worker row, which can still be
  // `review` while its background work has already been queued.
  const [pollingAfterSubmit, setPollingAfterSubmit] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const poller = useMemo(
    () =>
      createCompletionChainedPoller<InboxItem>({
        request: () => api.getPendingImport(inboxId),
        intervalMs: 1_500,
        shouldContinue: (next, forceContinue) =>
          !isTerminalState(next.state) && (ACTIVE_STATES.has(next.state) || forceContinue),
        onResult: (next) => {
          setItem(next);
          if (isTerminalState(next.state)) setPollingAfterSubmit(false);
          setSelected(next.manifest.selected_ids ?? []);
          setTags(next.requested_tags.join(", "));
          setDestination(
            next.target_collection_id === null ? NEW_COLLECTION : String(next.target_collection_id),
          );
          setCollectionName(
            capturedTitle(next) || t("inbox.defaultCollectionName", { id: String(next.id) }),
          );
        },
        onError: toast.error,
      }),
    [api, inboxId, t],
  );
  useEffect(() => {
    if (!Number.isFinite(inboxId)) return;
    poller.refresh();
    return () => poller.stop();
  }, [inboxId, poller]);
  useEffect(() => {
    if (!item) return;
    if (!isTerminalState(item.state) && (pollingAfterSubmit || ACTIVE_STATES.has(item.state))) {
      poller.start();
    } else {
      poller.stop();
    }
  }, [item, pollingAfterSubmit, poller]);
  const choices = useMemo(() => (item ? files(item) : []), [item]);
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
  const saveReview = async (collectionId: number | null) => {
    const next = await api.updatePendingImport(item.id, {
      collection_id: collectionId,
      tags: tags
        .split(",")
        .map((tag) => tag.trim())
        .filter(Boolean),
      selected_ids: selected,
    });
    setItem(next);
  };
  const resolveDestination = async (): Promise<number | null> => {
    if (destination === NO_COLLECTION) return null;
    if (destination !== NEW_COLLECTION) return Number(destination);

    const name = collectionName.trim();
    const existing = collections.find(
      (collection) => collection.parent_id === null && collection.name === name,
    );
    if (existing) {
      setDestination(String(existing.id));
      return existing.id;
    }
    const created = await api.createCollection({ name, parent_id: null });
    setDestination(String(created.id));
    return created.id;
  };
  const importSelected = async () => {
    poller.stop();
    setBusy(true);
    try {
      const collectionId = await resolveDestination();
      await saveReview(collectionId);
      const next = await api.importPendingImport(item.id, selected);
      setItem(next);
      const continuePolling = !isTerminalState(next.state);
      setPollingAfterSubmit(continuePolling);
      if (continuePolling) poller.start(true);
    } catch (error) {
      toast.error(error);
      if (item && !isTerminalState(item.state)) poller.start();
    } finally {
      setBusy(false);
    }
  };
  const deleteItem = async () => {
    setDeleting(true);
    try {
      await api.dismissPendingImport(item.id);
      setConfirmDelete(false);
      router.push("/inbox");
    } catch (error) {
      toast.error(error);
    } finally {
      setDeleting(false);
    }
  };
  const retry = async () => {
    poller.stop();
    try {
      const next = await api.retryPendingImport(item.id);
      setItem(next);
      const continuePolling = !isTerminalState(next.state);
      setPollingAfterSubmit(continuePolling);
      if (continuePolling) poller.start(true);
    } catch (error) {
      toast.error(error);
      if (item && !isTerminalState(item.state)) poller.start();
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
      <PageHeader
        title={title}
        description={t("inbox.detailDescription")}
        actions={
          <>
            <Button variant="outline" asChild>
              <Link href="/inbox">{t("inbox.back")}</Link>
            </Button>
            <Button variant="destructive" onClick={() => setConfirmDelete(true)}>
              <Trash2 className="h-4 w-4" aria-hidden="true" />
              {t("inbox.delete")}
            </Button>
          </>
        }
      />
      <Card>
        <CardContent className="grid gap-5 p-5 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center">
          <div className="min-w-0 space-y-3">
            <div className="flex items-center gap-2">
              <Link2 className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
              <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                {t("inbox.source")}
              </p>
            </div>
            {sourceUrl ? (
              <a
                href={sourceUrl}
                target="_blank"
                rel="noreferrer"
                className="inline-flex max-w-full items-center gap-1.5 text-sm font-medium text-primary hover:underline"
              >
                <span className="truncate">{item.source_hostname || sourceUrl}</span>
                <ExternalLink className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              </a>
            ) : item.source_url ? (
              <p className="truncate text-sm text-muted-foreground">{item.source_url}</p>
            ) : (
              <p className="text-sm text-muted-foreground">{t("inbox.sourcePreparing")}</p>
            )}
            <div className="flex flex-wrap gap-x-5 gap-y-2 text-xs text-muted-foreground">
              <span className="inline-flex items-center gap-1.5">
                <FileBox className="h-3.5 w-3.5" aria-hidden="true" />
                {providerLabel(item)}
              </span>
              <span className="inline-flex items-center gap-1.5">
                <CalendarDays className="h-3.5 w-3.5" aria-hidden="true" />
                {new Date(item.created_at).toLocaleDateString(locale, { dateStyle: "medium" })}
              </span>
            </div>
          </div>
          <Badge variant={statusVariant(item)}>{statusLabel(item, t)}</Badge>
        </CardContent>
      </Card>
      {item.error_code && (
        <div
          role="alert"
          className="rounded-md border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive"
        >
          {item.error_code}
        </div>
      )}
      {item.state === "importing" && (
        <p role="status" aria-live="polite" className="text-sm text-muted-foreground">
          {t("inbox.importing")}
        </p>
      )}
      {item.state === "review" && (
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_22rem] lg:items-start">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between gap-4 space-y-0 border-b border-border/70 p-5">
              <div>
                <h2 className="text-base font-semibold">{t("inbox.filesToImport")}</h2>
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
                      className="flex cursor-pointer items-center gap-3 px-5 py-4 hover:bg-muted/50"
                    >
                      <Checkbox
                        checked={selected.includes(file.id)}
                        onChange={() => toggle(file.id)}
                        ariaLabel={t("inbox.selectFile", { name: file.name })}
                      />
                      <FileBox
                        className="h-4 w-4 shrink-0 text-muted-foreground"
                        aria-hidden="true"
                      />
                      <span className="min-w-0 flex-1 truncate text-sm font-medium">
                        {file.name}
                      </span>
                      <span className="shrink-0 text-xs uppercase text-muted-foreground">
                        {file.file_type} · {formatBytes(file.size)}
                      </span>
                    </label>
                  ))}
                </div>
              </fieldset>
            </CardContent>
          </Card>

          <Card className="lg:sticky lg:top-6">
            <CardHeader className="border-b border-border/70 p-5">
              <div className="flex items-center gap-2">
                <FolderPlus className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
                <h2 className="text-base font-semibold">{t("inbox.importSettings")}</h2>
              </div>
              <p className="text-sm text-muted-foreground">
                {t("inbox.importSettingsDescription")}
              </p>
            </CardHeader>
            <CardContent className="space-y-5 p-5">
              <label className="block text-sm font-medium">
                {t("inbox.destination")}
                <select
                  value={destination}
                  onChange={(event) => setDestination(event.target.value)}
                  className="mt-1.5 block h-10 w-full rounded-md border border-input bg-background px-3 text-sm text-foreground ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                >
                  <option value={NEW_COLLECTION}>{t("inbox.newCollectionFromTitle")}</option>
                  <option value={NO_COLLECTION}>{t("inbox.noCollection")}</option>
                  {collections.map((collection) => (
                    <option key={collection.id} value={collection.id}>
                      {collection.path}
                    </option>
                  ))}
                </select>
              </label>
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
                <span className="inline-flex items-center gap-1.5">
                  <Tags className="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
                  {t("inbox.tags")}
                </span>
                <Input
                  className="mt-1.5"
                  value={tags}
                  onChange={(event) => setTags(event.target.value)}
                  placeholder={t("inbox.commaSeparated")}
                />
              </label>
              <div className="border-t border-border/70 pt-4">
                <div className="mb-4 flex items-center justify-between text-sm">
                  <span className="text-muted-foreground">{t("inbox.selected")}</span>
                  <span className="font-medium">
                    {selected.length}
                    {selectedBytes > 0 ? ` · ${formatBytes(selectedBytes)}` : ""}
                  </span>
                </div>
                <Button
                  className="w-full"
                  onClick={() => void importSelected()}
                  disabled={selected.length === 0 || collectionNameMissing}
                  loading={busy}
                >
                  {t("inbox.importSelected")}
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
              <div key={result.id} className="flex items-center gap-2 text-sm">
                <Badge variant={result.state === "failed" ? "destructive" : "success"}>
                  {resultLabel(result, t)}
                </Badge>
                <span>{result.original_filename}</span>
                {result.model_id && (
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
              item.results.some((result) => result.state === "failed" && result.retryable) && (
                <Button size="sm" variant="outline" onClick={() => void retry()}>
                  {t("inbox.retryFailedFiles")}
                </Button>
              )}
          </CardContent>
        </Card>
      )}
      {item.state === "failed" && item.retryable && (
        <Button variant="outline" onClick={() => void retry()}>
          {t("inbox.retry")}
        </Button>
      )}
      <ConfirmModal
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        title={t("inbox.deleteTitle")}
        description={t("inbox.deleteDescription")}
        confirmLabel={t("inbox.deleteConfirm")}
        busy={deleting}
        onConfirm={() => void deleteItem()}
      />
    </PageContainer>
  );
}
