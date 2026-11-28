import { FieldErrors } from "@/components/ui/field-errors";
import { useFormValidation } from "@/lib/form-validation";
import { inboxFiles, inboxProviderLabel, inboxCanImport } from "./inbox-summary";
import { ProtectedThumbnail } from "@/components/protected-thumbnail";
import { Checkbox } from "@/components/ui/checkbox";
import { useId, useMemo, useRef, useState, type FormEvent } from "react";
import {
  CheckCircle2,
  CircleAlert,
  Clock3,
  FileCheck2,
  Inbox,
  Link2,
  RefreshCw,
  type LucideIcon,
} from "lucide-react";

import { ErrorState } from "@/components/ui/error-state";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { FormField } from "@/components/ui/form-field";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { EmptyState } from "@/components/ui/empty-state";
import { PageContainer } from "@/components/ui/page-container";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { TabBar } from "@/components/ui/tabs";
import {
  batchPendingImports,
  dismissPendingImport,
  listPendingImports,
  retryPendingImport,
} from "@/lib/api";
import { inboxApi, useInboxSnapshot, useInboxCommands } from "@/lib/queries/inbox";
import { timeAgo } from "@/lib/format";
import { uiText } from "@/lib/locale";
import { userMessage } from "@/lib/errors";
import { Link } from "@/lib/link";
import { useRouter } from "@/lib/navigation";
import { useI18n } from "@/lib/i18n";
import { getSessionVersion } from "@/lib/session-transport";
import { toast } from "@/lib/toast";
import type { InboxItem } from "@/types";

export interface InboxPageDeps {
  listPendingImports: typeof listPendingImports;
  capturePendingImport?: typeof inboxApi.capturePendingImport;
  retryPendingImport: typeof retryPendingImport;
  dismissPendingImport: typeof dismissPendingImport;
  batchPendingImports: typeof batchPendingImports;
}

const inboxPageDeps: InboxPageDeps = {
  listPendingImports,
  retryPendingImport,
  dismissPendingImport,
  batchPendingImports,
};

const EMPTY_INBOX: InboxItem[] = [];

type InboxTab = "queue" | "completed";

function ImportList({
  label,
  items,
  locale,
  t,
  retry,
  deletingId,
  onDelete,
  selected,
  onSelect,
  onImport,
}: {
  label: string;
  items: InboxItem[];
  locale: string;
  t: ReturnType<typeof useI18n>["t"];
  retry: typeof retryPendingImport;
  deletingId: number | null;
  onDelete: (item: InboxItem) => void;
  selected: number[];
  onSelect: (id: number) => void;
  onImport: (ids: number[]) => void;
}) {
  return (
    <ul aria-label={label} className="divide-y divide-border">
      {items.map((item) => {
        const title = capturedTitle(item) || item.source_hostname || t("inbox.pendingImport");
        const provider = inboxProviderLabel(item) || t("inbox.sourcePreparing");
        const fileCount = inboxFiles(item).length;
        const StateIcon = stateIcon(item);
        return (
          <li
            key={item.id}
            className="flex min-w-0 flex-wrap items-center justify-between gap-3 px-5 py-4"
          >
            <div className="flex min-w-0 flex-1 basis-64 items-start gap-3">
              {inboxCanImport(item) && (
                <div className="pt-1">
                  <Checkbox
                    checked={selected.includes(item.id)}
                    onChange={() => onSelect(item.id)}
                    ariaLabel={t("inbox.selectFile", { name: title })}
                  />
                </div>
              )}
              {item.thumbnail_url && (
                <ProtectedThumbnail
                  path={item.thumbnail_url}
                  alt=""
                  placeholder={null}
                  className="size-12 shrink-0 overflow-hidden rounded-md bg-muted"
                  imageClassName="size-full object-cover"
                />
              )}
              <span className="sr-only">
                <StateIcon className={stateIconClass(item)} aria-hidden="true" />
              </span>
              <div className="min-w-0 flex-1">
                <h3 className="line-clamp-2 break-words text-sm font-medium leading-5 text-foreground">
                  {title}
                </h3>
                <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                  <span className="text-muted-foreground">{provider}</span>
                  <span className="inline-flex items-center gap-1">
                    ·{" "}
                    <time
                      dateTime={item.completed_at || item.created_at}
                      title={new Date(item.completed_at || item.created_at).toLocaleString(locale)}
                    >
                      {timeAgo(item.completed_at || item.created_at)}
                    </time>
                  </span>
                  {fileCount > 0 && (
                    <span className="inline-flex items-center gap-1">
                      · {t("inbox.fileSummary", { count: String(fileCount) })}
                    </span>
                  )}
                  {item.results.length > 0 && (
                    <span>{t("inbox.resultSummary", { count: String(item.results.length) })}</span>
                  )}
                  <span className="text-xs text-muted-foreground">· {statusLabel(item, t)}</span>
                </div>
              </div>
            </div>
            <div className="flex min-w-0 shrink-0 items-center justify-between gap-3 border-t border-border pt-3 sm:justify-end sm:border-0 sm:pt-0">
              <div className="flex flex-wrap justify-end gap-2">
                {inboxCanImport(item) && (
                  <Button size="xs" onClick={() => onImport([item.id])}>
                    {t("inbox.import")}
                  </Button>
                )}
                {item.state === "failed" && item.retryable && (
                  <Button
                    size="xs"
                    variant="outline"
                    onClick={() => {
                      const session = getSessionVersion();
                      void retry(item.id)
                        .then(() => {
                          if (getSessionVersion() === session)
                            toast.success(t("inbox.retryQueued"));
                        })
                        .catch((error) => {
                          if (getSessionVersion() === session) toast.error(error);
                        });
                    }}
                  >
                    {t("inbox.retry")}
                  </Button>
                )}
                {item.state === "completed" &&
                item.completion !== "partial" &&
                item.results.length <= 1 &&
                item.resulting_model_id ? (
                  <Button size="xs" asChild>
                    <Link href={`/models/${item.resulting_model_id}`}>{t("inbox.openModel")}</Link>
                  </Button>
                ) : (
                  <Button size="xs" variant="outline" asChild>
                    <Link href={`/inbox/${item.id}`}>
                      {item.state === "review" ? t("inbox.review") : t("inbox.view")}
                    </Link>
                  </Button>
                )}
                {item.state !== "importing" && item.state !== "resolving" && (
                  <Button
                    type="button"
                    size="xs"
                    variant="danger"
                    aria-label={t("inbox.delete")}
                    title={t("inbox.delete")}
                    loading={deletingId === item.id}
                    onClick={() => onDelete(item)}
                  >
                    {uiText("Dismiss…")}
                  </Button>
                )}
              </div>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

function capturedTitle(item: InboxItem): string | null {
  if (item.manifest.schema_version === 2) {
    const title = item.manifest.source.fields.title?.value.trim();
    if (title) return title;
  }
  return item.display_title?.trim() || null;
}

function stateIcon(item: InboxItem): LucideIcon {
  if (item.state === "completed") return CheckCircle2;
  if (item.state === "failed") return CircleAlert;
  if (item.state === "review") return FileCheck2;
  return Clock3;
}

function stateIconClass(item: InboxItem): string {
  if (item.state === "completed") return "h-4 w-4 text-success";
  if (item.state === "failed") return "h-4 w-4 text-destructive";
  if (item.state === "review") return "h-4 w-4 text-primary";
  return "h-4 w-4 text-muted-foreground";
}

function statusLabel(item: InboxItem, t: ReturnType<typeof useI18n>["t"]): string {
  if (item.completion === "partial") return t("inbox.partial");
  if (item.state === "review") return t("inbox.needsReview");
  if (item.state === "completed") return t("inbox.completed");
  switch (item.state) {
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

export default function InboxPage({ deps = inboxPageDeps }: { deps?: InboxPageDeps }) {
  const { locale, t } = useI18n();
  const snapshot = useInboxSnapshot(deps);
  const commands = useInboxCommands({ ...inboxApi, ...deps });
  const router = useRouter();
  const sourceUrlId = useId();
  const captureFormId = useId();
  const captureValidation = useFormValidation();
  const captureInFlight = useRef(false);
  const [addUrlOpen, setAddUrlOpen] = useState(false);
  const [sourceUrl, setSourceUrl] = useState("");
  const items = snapshot.data ?? EMPTY_INBOX;
  const loading = snapshot.isPending;
  const [selected, setSelected] = useState<number[]>([]);
  const [activeTab, setActiveTab] = useState<InboxTab>("queue");
  const [deleteTarget, setDeleteTarget] = useState<InboxItem | null>(null);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [clearCompletedOpen, setClearCompletedOpen] = useState(false);
  const [clearingCompleted, setClearingCompleted] = useState(false);
  const groups = useMemo(
    () => ({
      queue: items.filter((item) => item.state !== "completed" && item.state !== "dismissed"),
      done: items.filter((item) => item.state === "completed"),
    }),
    [items],
  );

  const selectedReady = groups.queue.filter(
    (item) => selected.includes(item.id) && inboxCanImport(item),
  );
  async function importItems(ids: number[]) {
    const session = getSessionVersion();
    try {
      const results = await commands.batch.mutateAsync({
        session,
        payload: {
          item_ids: ids,
          action: "import",
          use_default_destination: true,
        },
      });
      if (getSessionVersion() === session) {
        const started = new Set(
          results
            .filter((item) => item.state === "importing" || item.state === "completed")
            .map((item) => item.id),
        );
        setSelected((current) => current.filter((id) => !started.has(id)));
        if (results.some((item) => item.state === "review")) toast.error(t("inbox.needsReview"));
      }
    } catch (error) {
      if (getSessionVersion() === session) toast.error(error);
    }
  }

  function closeAddUrl() {
    if (captureInFlight.current) return;
    setAddUrlOpen(false);
    setSourceUrl("");
  }

  async function submitCapture(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const url = sourceUrl.trim();
    if (!url || captureInFlight.current) return;
    captureInFlight.current = true;
    const session = getSessionVersion();
    try {
      const item = await commands.capture.mutateAsync({ url, session });
      if (getSessionVersion() !== session) return;
      toast.success(uiText("Captured URL. Review it before importing."));
      captureInFlight.current = false;
      closeAddUrl();
      router.push(`/inbox/${item.id}`);
    } catch (error) {
      if (getSessionVersion() === session) toast.error(error);
    } finally {
      captureInFlight.current = false;
    }
  }

  async function deleteImport() {
    if (!deleteTarget) return;
    setDeletingId(deleteTarget.id);
    const session = getSessionVersion();
    try {
      await commands.dismiss.mutateAsync({ id: deleteTarget.id, session });
      if (getSessionVersion() !== session) return;
      setDeleteTarget(null);
      toast.success(t("inbox.deleteSuccess"));
    } catch (error) {
      if (getSessionVersion() === session) toast.error(error);
    } finally {
      if (getSessionVersion() === session) setDeletingId(null);
    }
  }

  async function clearCompleted() {
    const itemIds = groups.done.map((item) => item.id);
    if (!itemIds.length) {
      setClearCompletedOpen(false);
      return;
    }
    setClearingCompleted(true);
    const session = getSessionVersion();
    try {
      await commands.batch.mutateAsync({
        payload: { item_ids: itemIds, action: "dismiss" },
        session,
      });
      if (getSessionVersion() !== session) return;
      setClearCompletedOpen(false);
      toast.success(t("inbox.clearCompletedSuccess"));
    } catch (error) {
      if (getSessionVersion() === session) toast.error(error);
    } finally {
      if (getSessionVersion() === session) setClearingCompleted(false);
    }
  }

  const visibleItems = activeTab === "queue" ? groups.queue : groups.done;

  return (
    <PageContainer>
      <PageHeader
        title={t("inbox.title")}
        description={t("inbox.description")}
        actions={
          <>
            <Button
              variant="outline"
              size="sm"
              loading={snapshot.isFetching}
              onClick={() => void snapshot.refetch()}
            >
              <RefreshCw className="h-4 w-4" aria-hidden />
              {uiText("Refresh")}
            </Button>
            <Button
              size="sm"
              variant={selectedReady.length > 0 ? "outline" : "default"}
              onClick={() => setAddUrlOpen(true)}
            >
              <Link2 className="h-4 w-4" aria-hidden />
              {t("inbox.addUrl")}
            </Button>
          </>
        }
      />
      <Card className="overflow-hidden">
        <div className="flex flex-col gap-3 border-b border-border px-5 py-3 sm:flex-row sm:items-center sm:justify-between">
          <TabBar
            variant="framed"
            tabs={[
              {
                key: "queue",
                label: (
                  <>
                    {t("inbox.queue")} ·
                    <span className="font-normal text-muted-foreground">{groups.queue.length}</span>
                  </>
                ),
              },
              {
                key: "completed",
                label: (
                  <>
                    {t("inbox.completed")} ·
                    <span className="font-normal text-muted-foreground">{groups.done.length}</span>
                  </>
                ),
              },
            ]}
            active={activeTab}
            onChange={setActiveTab}
          />
          {activeTab === "queue" && selectedReady.length > 0 && (
            <Button
              size="xs"
              onClick={() => void importItems(selectedReady.map((item) => item.id))}
            >
              {t("inbox.importSelected")}
            </Button>
          )}
          {groups.done.length > 0 && activeTab === "completed" && (
            <Button
              type="button"
              size="xs"
              variant="danger"
              onClick={() => setClearCompletedOpen(true)}
            >
              {t("inbox.clearCompleted")}
            </Button>
          )}
        </div>
        <>
          <div className="px-5 pb-2 pt-5">
            <div className="min-w-0">
              <h2 className="text-sm font-semibold text-foreground">
                {activeTab === "queue" ? t("inbox.queueTitle") : t("inbox.completed")}
              </h2>
            </div>
          </div>
          {snapshot.isError ? (
            <ErrorState
              layout="block"
              message={userMessage(snapshot.error)}
              retryLabel={t("inbox.retry")}
              onRetry={() => void snapshot.refetch()}
            />
          ) : loading ? (
            <div aria-label={t("inbox.loading")} className="space-y-3 p-5">
              <Skeleton className="h-14 w-full" />
              <Skeleton className="h-14 w-full" />
            </div>
          ) : visibleItems.length ? (
            <ImportList
              label={activeTab === "queue" ? t("inbox.queueTitle") : t("inbox.completed")}
              items={visibleItems}
              locale={locale}
              t={t}
              retry={(id) => commands.retry.mutateAsync({ id, session: getSessionVersion() })}
              deletingId={deletingId}
              onDelete={setDeleteTarget}
              selected={selected}
              onSelect={(id) =>
                setSelected((current) =>
                  current.includes(id) ? current.filter((item) => item !== id) : [...current, id],
                )
              }
              onImport={(ids) => void importItems(ids)}
            />
          ) : (
            <EmptyState
              className="min-h-56 rounded-none border-0 shadow-none"
              icon={activeTab === "queue" ? Inbox : FileCheck2}
              title={
                activeTab === "queue" ? t("inbox.emptyQueueTitle") : t("inbox.emptyCompletedTitle")
              }
              description={
                activeTab === "queue" ? t("inbox.startHelp") : t("inbox.emptyCompletedDescription")
              }
            />
          )}
        </>
      </Card>
      <Modal
        open={addUrlOpen}
        onClose={closeAddUrl}
        title={t("inbox.addUrl")}
        className="max-w-md"
        footer={
          <>
            <Button
              type="button"
              variant="outline"
              disabled={commands.capture.isPending}
              onClick={closeAddUrl}
            >
              {uiText("Cancel")}
            </Button>
            <Button
              type="submit"
              form={captureFormId}
              disabled={commands.capture.isPending}
              loading={commands.capture.isPending}
            >
              {uiText("Capture for review")}
            </Button>
          </>
        }
      >
        <form
          id={captureFormId}
          {...captureValidation.formProps}
          onSubmit={(event) => captureValidation.handle(event, submitCapture)}
        >
          <FieldErrors errors={captureValidation.errors}>
            <FormField
              label={uiText("Source URL")}
              htmlFor={sourceUrlId}
              hintId={`${sourceUrlId}-hint`}
              hint={uiText(
                "Create a durable capture, then review its files and confirm or change the collection and tags.",
              )}
            >
              <Input
                id={sourceUrlId}
                type="url"
                inputMode="url"
                required
                value={sourceUrl}
                readOnly={commands.capture.isPending}
                onChange={(event) => setSourceUrl(event.target.value)}
                placeholder={uiText("Model page, collection, or direct .stl/.zip link")}
                aria-describedby={`${sourceUrlId}-hint`}
              />
            </FormField>
          </FieldErrors>
        </form>
      </Modal>
      <ConfirmModal
        open={deleteTarget !== null}
        onClose={() => setDeleteTarget(null)}
        onConfirm={() => void deleteImport()}
        title={
          deleteTarget?.state === "completed"
            ? t("inbox.deleteCompletedTitle")
            : t("inbox.deleteTitle")
        }
        description={
          deleteTarget?.state === "completed"
            ? t("inbox.deleteCompletedDescription")
            : t("inbox.deleteDescription")
        }
        confirmLabel={t("inbox.deleteConfirm")}
        busy={deletingId !== null}
      />
      <ConfirmModal
        open={clearCompletedOpen}
        onClose={() => setClearCompletedOpen(false)}
        onConfirm={() => void clearCompleted()}
        title={t("inbox.clearCompletedTitle")}
        description={t("inbox.clearCompletedDescription", {
          count: String(groups.done.length),
        })}
        confirmLabel={t("inbox.clearCompleted")}
        busy={clearingCompleted}
      />
    </PageContainer>
  );
}
