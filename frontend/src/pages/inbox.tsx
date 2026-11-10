import { useMemo, useState } from "react";
import {
  CheckCircle2,
  CircleAlert,
  Clock3,
  FileCheck2,
  Inbox,
  RefreshCw,
  Trash2,
  type LucideIcon,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ConfirmModal } from "@/components/ui/confirm-modal";
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
import { useI18n } from "@/lib/i18n";
import { getSessionVersion } from "@/lib/session-transport";
import { toast } from "@/lib/toast";
import type { InboxItem } from "@/types";

export interface InboxPageDeps {
  listPendingImports: typeof listPendingImports;
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
}: {
  label: string;
  items: InboxItem[];
  locale: string;
  t: ReturnType<typeof useI18n>["t"];
  retry: typeof retryPendingImport;
  deletingId: number | null;
  onDelete: (item: InboxItem) => void;
}) {
  return (
    <ul aria-label={label} className="divide-y divide-border">
      {items.map((item) => {
        const title = capturedTitle(item) || item.source_hostname || t("inbox.pendingImport");
        const provider = providerLabel(item) || t("inbox.sourcePreparing");
        const fileCount = manifestFiles(item).length;
        const StateIcon = stateIcon(item);
        return (
          <li
            key={item.id}
            className="flex min-w-0 flex-wrap items-center justify-between gap-3 px-5 py-4"
          >
            <div className="flex min-w-0 flex-1 basis-64 items-start gap-3">
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
                    <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
                    {t("inbox.retry")}
                  </Button>
                )}
                {item.state === "completed" && item.resulting_model_id ? (
                  <Button size="xs" variant="outline" asChild>
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
                    variant="outline"
                    aria-label={t("inbox.delete")}
                    title={t("inbox.delete")}
                    loading={deletingId === item.id}
                    onClick={() => onDelete(item)}
                    className="text-muted-foreground hover:text-destructive"
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

function manifestFiles(item: InboxItem) {
  if (item.manifest.kind === "archive") return item.manifest.entries ?? [];
  if (item.manifest.kind === "model_files") return item.manifest.files ?? [];
  return [];
}

function providerLabel(item: InboxItem): string | null {
  const provider =
    item.manifest.schema_version === 2
      ? item.manifest.source.provider
      : item.source_hostname?.replace(/^www\./, "").split(".")[0];
  if (!provider) return null;
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
    default:
      return provider.charAt(0).toUpperCase() + provider.slice(1);
  }
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
  const items = snapshot.data ?? EMPTY_INBOX;
  const loading = snapshot.isPending;
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
  const tabClass =
    "flex items-center justify-center gap-1 border-b-2 border-transparent pb-3 pt-4 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

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
              {uiText("Refresh")}
            </Button>
            <Button asChild size="sm">
              <Link href="/?upload=1">{t("inbox.openImport")}</Link>
            </Button>
          </>
        }
      />
      <Card className="overflow-hidden">
        <div className="flex flex-col gap-3 border-b border-border px-5 sm:flex-row sm:items-center sm:justify-between">
          <TabBar
            tabs={[
              {
                key: "queue",
                label: (
                  <>
                    {t("inbox.queue")} ·
                    <Badge
                      variant="secondary"
                      className="border-0 bg-transparent px-0 text-muted-foreground"
                    >
                      {groups.queue.length}
                    </Badge>
                  </>
                ),
              },
              {
                key: "completed",
                label: (
                  <>
                    {t("inbox.completed")} ·
                    <Badge
                      variant="secondary"
                      className="border-0 bg-transparent px-0 text-muted-foreground"
                    >
                      {groups.done.length}
                    </Badge>
                  </>
                ),
              },
            ]}
            active={activeTab}
            onChange={setActiveTab}
            showIndicator={false}
            className="gap-6"
            tabClassName={tabClass}
            activeTabClassName="border-foreground font-semibold"
          />
          {groups.done.length > 0 && activeTab === "completed" && (
            <Button
              type="button"
              size="xs"
              variant="outline"
              onClick={() => setClearCompletedOpen(true)}
            >
              <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
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
            <div role="alert" className="p-5 text-sm text-destructive">
              <p>{userMessage(snapshot.error)}</p>
              <Button variant="outline" onClick={() => void snapshot.refetch()}>
                {t("inbox.retry")}
              </Button>
            </div>
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
            />
          ) : (
            <EmptyState
              className="min-h-56 rounded-none border-0 shadow-none"
              icon={activeTab === "queue" ? Inbox : FileCheck2}
              title={
                activeTab === "queue" ? t("inbox.emptyQueueTitle") : t("inbox.emptyCompletedTitle")
              }
              description={
                activeTab === "queue"
                  ? t("inbox.emptyQueueDescription")
                  : t("inbox.emptyCompletedDescription")
              }
            />
          )}
        </>
        <p className="border-t border-border px-5 py-4 text-xs text-muted-foreground">
          {uiText(
            "Captures stay in the Inbox until you choose their destination. Removing a capture does not delete Models already imported into your Library.",
          )}
        </p>
      </Card>
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
