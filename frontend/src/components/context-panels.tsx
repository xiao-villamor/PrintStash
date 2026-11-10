import { useState } from "react";
import { ListFilter, MoreHorizontal } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { Link } from "@/lib/link";
import { uiText } from "@/lib/locale";
import { timeAgoShort } from "@/lib/format";
import { useI18n, useUiLocale } from "@/lib/i18n";
import { useAuth } from "@/lib/auth-context";
import { usePathname, useRouter } from "@/lib/navigation";
import { useNavigationShell } from "@/lib/navigation-shell";
import { useSavedViews } from "@/features/library/saved-views";
import { writeLibraryFilters } from "@/features/library/filters";
import { useInboxCommands, useInboxSnapshot } from "@/lib/queries/inbox";
import { getSessionVersion } from "@/lib/session-transport";
import { toast } from "@/lib/toast";
import type { InboxItem } from "@/types";

export function PendingSidebar() {
  const { t } = useI18n();
  const query = useInboxSnapshot();
  const commands = useInboxCommands();
  const [target, setTarget] = useState<InboxItem | null>(null);
  const pending =
    query.data?.filter((item) => item.state !== "completed" && item.state !== "dismissed") ?? [];
  async function dismiss() {
    if (!target || commands.dismiss.isPending) return;
    try {
      await commands.dismiss.mutateAsync({ id: target.id, session: getSessionVersion() });
      setTarget(null);
    } catch (error) {
      toast.error(error);
    }
  }
  return (
    <aside
      aria-label={t("inbox.title")}
      className="flex h-full w-[321px] shrink-0 flex-col border-r border-border bg-sidebar"
    >
      <h2 className="flex h-12 shrink-0 items-center px-4 font-mono text-2xs font-medium uppercase tracking-[.06em] text-muted-foreground">
        {t("inbox.title")}
      </h2>
      <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-3">
        {query.isPending && (
          <p role="status" className="py-2 text-xs text-muted-foreground">
            {t("inbox.loading")}
          </p>
        )}
        {query.isError && (
          <div role="alert" className="space-y-2 text-xs">
            <p>{uiText("Could not load this list.")}</p>
            <Button variant="outline" size="xs" onClick={() => void query.refetch()}>
              {uiText("Retry")}
            </Button>
          </div>
        )}
        {!query.isPending && !query.isError && pending.length === 0 && (
          <p className="py-2 text-xs text-muted-foreground">{t("inbox.emptyQueueTitle")}</p>
        )}
        {pending.map((item) => (
          <article key={item.id} className="border-b border-border py-3 first:pt-0">
            <h3 className="truncate text-[13px] font-semibold">
              {item.display_title ?? item.source_url ?? t("inbox.pendingImport")}
            </h3>
            <p className="mt-1 truncate text-xs text-muted-foreground">
              {item.source_hostname ?? t("inbox.source")} · {timeAgoShort(item.created_at)}
            </p>
            <div className="mt-2 flex gap-2">
              <Button asChild size="xs" variant="outline" className="h-7 px-2.5">
                <Link href={`/inbox/${item.id}`}>{t("inbox.review")}</Link>
              </Button>
              <Button
                size="xs"
                variant="outline"
                className="h-7 px-2.5"
                disabled={commands.dismiss.isPending || item.state === "importing"}
                onClick={() => setTarget(item)}
              >
                {t("inbox.dismissConfirm")}
              </Button>
            </div>
          </article>
        ))}
      </div>
      <ConfirmModal
        open={target !== null}
        onClose={() => {
          if (!commands.dismiss.isPending) setTarget(null);
        }}
        onConfirm={() => void dismiss()}
        title={t("inbox.dismissTitle")}
        description={t("inbox.dismissDescription")}
        confirmLabel={t("inbox.dismissConfirm")}
        busy={commands.dismiss.isPending}
      />
    </aside>
  );
}

export function SavedViewsSidebar() {
  useUiLocale();
  const owner = useSavedViews(true);
  const { user } = useAuth();
  const pathname = usePathname();
  const router = useRouter();
  const shell = useNavigationShell();
  return (
    <aside
      aria-label={uiText("Saved views")}
      className="flex h-full w-[321px] shrink-0 flex-col border-r border-border bg-sidebar"
    >
      <div className="flex h-12 shrink-0 items-center justify-between pl-4 pr-2">
        <h2 className="font-mono text-2xs font-medium uppercase tracking-[.06em] text-muted-foreground">
          {uiText("Saved views")}
        </h2>
        <button
          type="button"
          aria-label={uiText("Manage saved views")}
          title={uiText("Manage saved views")}
          className="flex h-7 w-7 items-center justify-center text-muted-foreground hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          onClick={() => {
            shell.requestSavedViews();
            if (pathname !== "/") router.push("/");
          }}
        >
          <MoreHorizontal className="h-4 w-4" aria-hidden />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-1.5 pb-3">
        {owner.query.isPending && (
          <p role="status" className="px-2.5 py-2 text-xs text-muted-foreground">
            {uiText("savedView.loading")}
          </p>
        )}
        {owner.query.isError && (
          <div role="alert" className="space-y-2 px-2.5 text-xs">
            <p>{uiText("Could not load this list.")}</p>
            <Button variant="outline" size="xs" onClick={() => void owner.query.refetch()}>
              {uiText("Retry")}
            </Button>
          </div>
        )}
        {owner.query.isSuccess && owner.views.length === 0 && (
          <p className="px-2.5 py-2 text-xs text-muted-foreground">
            {uiText("No saved views yet")}
          </p>
        )}
        {owner.views.map((view) => (
          <button
            key={view.id}
            type="button"
            className="flex h-[30px] w-full items-center gap-2 px-2.5 text-left text-[13px] hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
            onClick={() => {
              if (pathname === "/")
                window.dispatchEvent(
                  new CustomEvent("printstash:select-saved-view", { detail: view.id }),
                );
              else
                router.push(`/?${writeLibraryFilters(view.filters, user?.is_superuser === true)}`);
            }}
          >
            <ListFilter
              className="h-3.5 w-3.5 shrink-0 text-muted-foreground"
              strokeWidth={1.6}
              aria-hidden
            />
            <span className="truncate">{view.name}</span>
          </button>
        ))}
      </div>
    </aside>
  );
}
