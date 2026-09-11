import { useState } from "react";
import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import { Modal } from "@/components/ui/modal";
import { Button } from "@/components/ui/button";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { listFamilies, purgeFamily, restoreFamily } from "@/lib/api/families";
import { userMessage } from "@/lib/errors";
import { timeAgo } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import { toast } from "@/lib/toast";
import type { FamilyRead } from "@/types/families";

export function FamilyTrashDialog({ onClose }: { onClose: () => void }) {
  const { t } = useI18n();
  const cache = useQueryClient();
  const [busy, setBusy] = useState<number | null>(null);
  const [purging, setPurging] = useState<FamilyRead | null>(null);
  const [error, setError] = useState<string | null>(null);
  const query = useInfiniteQuery({
    queryKey: ["families", "trash"],
    initialPageParam: "",
    queryFn: ({ pageParam }) =>
      listFamilies({ trashed: true, limit: 30, cursor: pageParam || undefined }),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
  const families = query.data?.pages.flatMap((page) => page.items) ?? [];
  const refresh = async () => {
    await Promise.all([
      cache.invalidateQueries({ queryKey: ["families"] }),
      cache.invalidateQueries({ queryKey: ["models"] }),
    ]);
  };
  return (
    <Modal
      open
      onClose={() => {
        if (busy === null) onClose();
      }}
      title={t("families.trash")}
      className="max-w-xl"
    >
      <div className="space-y-3">
        <p className="text-sm text-muted-foreground">{t("families.archiveHelp")}</p>
        {query.isPending && <p role="status">{t("families.loading")}</p>}
        {(query.isError || error) && (
          <p role="alert" className="text-sm text-destructive">
            {error ?? userMessage(query.error)}
          </p>
        )}
        {query.isError && (
          <Button size="sm" variant="outline" onClick={() => void query.refetch()}>
            {t("families.retry")}
          </Button>
        )}
        {!query.isPending && !query.isError && families.length === 0 && (
          <p className="text-sm text-muted-foreground">{t("families.trashEmpty")}</p>
        )}
        <div className="max-h-80 overflow-y-auto divide-y divide-border">
          {families.map((family) => (
            <div key={family.id} className="flex flex-wrap items-center gap-2 py-3">
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{family.name}</p>
                {family.deleted_at && (
                  <p className="text-xs text-muted-foreground">{timeAgo(family.deleted_at)}</p>
                )}
              </div>
              <Button
                size="sm"
                variant="outline"
                loading={busy === family.id}
                disabled={busy !== null}
                onClick={async () => {
                  setBusy(family.id);
                  setError(null);
                  try {
                    const result = await restoreFamily(family.id, family.version);
                    await refresh();
                    if (result.omitted_member_ids.length)
                      toast.info(
                        t("families.restoreMissing", { count: result.omitted_member_ids.length }),
                      );
                  } catch (cause) {
                    setError(userMessage(cause));
                  } finally {
                    setBusy(null);
                  }
                }}
              >
                {t("families.restore")}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                disabled={busy !== null}
                onClick={() => setPurging(family)}
              >
                {t("families.purge")}
              </Button>
            </div>
          ))}
        </div>
        {query.hasNextPage && (
          <Button
            variant="outline"
            size="sm"
            loading={query.isFetchingNextPage}
            onClick={() => void query.fetchNextPage()}
          >
            {t("families.loadMore")}
          </Button>
        )}
        {purging && (
          <ConfirmModal
            open
            onClose={() => {
              if (busy === null) setPurging(null);
            }}
            busy={busy !== null}
            title={t("families.purge")}
            description={`${t("families.purgeHelp", { name: purging.name })}${error ? `\n\n${error}` : ""}`}
            confirmLabel={t("families.purge")}
            onConfirm={async () => {
              setBusy(purging.id);
              setError(null);
              try {
                await purgeFamily(purging.id, purging.version);
                setPurging(null);
                await refresh();
              } catch (cause) {
                setError(userMessage(cause));
              } finally {
                setBusy(null);
              }
            }}
          />
        )}
      </div>
    </Modal>
  );
}
