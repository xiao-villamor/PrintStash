import { currentLocale } from "@/lib/locale";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  backupRunKeys,
  backupRunsOptions,
  useBackupDestinationRetry,
} from "@/lib/queries/settings-backup-runs";
import { useAuth } from "@/lib/auth-context";
import { onAuthChange } from "@/lib/auth-store";
import { getSessionVersion } from "@/lib/session-transport";
import { parseApiError } from "@/lib/errors";
import { useI18n } from "@/lib/i18n";
import { toast } from "@/lib/toast";

export function BackupRunHistory({
  refreshKey,
  onPublished,
}: {
  refreshKey: number;
  onPublished: () => void;
}) {
  const { t } = useI18n();
  const { user } = useAuth();
  const admin = useRef(!!user?.is_superuser);
  useLayoutEffect(() => {
    admin.current = !!user?.is_superuser;
  }, [user?.is_superuser]);
  const live = useRef(true);
  const [retired, setRetired] = useState(false);
  const previousRefresh = useRef(refreshKey);
  const client = useQueryClient();
  const history = useQuery({ ...backupRunsOptions(), enabled: !!user?.is_superuser && !retired });
  const command = useBackupDestinationRetry();
  const retrying = command.retrying;
  const denied = history.isError && [401, 403, 404].includes(parseApiError(history.error).status);
  const runs = user?.is_superuser && !denied && !retired ? (history.data ?? []) : [];
  const loading = !!user?.is_superuser && history.isPending;
  const failed = history.isError || !user?.is_superuser;
  useEffect(() => {
    live.current = true;
    const release = onAuthChange(() => setRetired(true));
    return () => {
      live.current = false;
      release();
    };
  }, []);
  useEffect(() => {
    if (previousRefresh.current === refreshKey) return;
    previousRefresh.current = refreshKey;
    if (user?.is_superuser && !retired)
      void client.invalidateQueries({ queryKey: backupRunKeys.all, exact: true });
  }, [client, refreshKey, retired, user?.is_superuser]);
  async function retry(id: string) {
    const session = getSessionVersion();
    function current() {
      return live.current && admin.current && !retired && session === getSessionVersion();
    }
    try {
      await command.retry({ session, destinationId: id, taskTitle: t("settings.backupRetryTask") });
      if (!current()) return;
      toast.success(t("settings.backupRetryDone"));
      onPublished();
    } catch (error) {
      if (current()) toast.error(error);
    }
  }

  const runLabels = {
    running: t("settings.backupRunRunning"),
    completed: t("settings.backupRunCompleted"),
    partial: t("settings.backupRunPartial"),
    failed: t("settings.backupRunFailed"),
  };
  const replicaLabels = {
    pending: t("settings.backupReplicaPending"),
    publishing: t("settings.backupReplicaPublishing"),
    completed: t("settings.backupReplicaCompleted"),
    failed: t("settings.backupReplicaFailed"),
  };
  function reason(code: string) {
    if (code === "backup_retry_new_backup_required") return t("settings.backupRetryNewRequired");
    if (code === "backup_retry_target_changed") return t("settings.backupRetryTargetChanged");
    if (code === "backup_retry_target_unverified") return t("settings.backupRetryTargetUnverified");
    if (code === "backup_publication_interrupted") return t("settings.backupReplicaInterrupted");
    return t("settings.backupReplicaUnavailable");
  }

  if (retired) return null;
  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between gap-3">
        <div className="space-y-1">
          <CardTitle>{t("settings.backupRunsTitle")}</CardTitle>
          <CardDescription>{t("settings.backupRunsDescription")}</CardDescription>
        </div>
        <Button
          variant="ghost"
          size="icon"
          disabled={!user?.is_superuser || loading || retrying !== null}
          aria-label={t("settings.backupRunRefresh")}
          onClick={() => void history.refetch()}
        >
          <RefreshCw className="h-4 w-4" />
        </Button>
      </CardHeader>
      <CardContent className="space-y-4" aria-busy={loading}>
        {failed && (
          <p role="alert" className="text-sm text-destructive">
            {t("settings.backupRunsLoadFailed")}
          </p>
        )}
        {!loading && !failed && runs.length === 0 && (
          <p className="text-sm text-muted-foreground">{t("settings.backupRunsEmpty")}</p>
        )}
        {runs.map((run) => (
          <article
            key={run.id}
            aria-label={`${run.backup_id}: ${runLabels[run.outcome]}`}
            className="space-y-3 border-t border-border pt-4"
          >
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <p className="text-sm font-medium">{runLabels[run.outcome]}</p>
              <time className="text-xs text-muted-foreground" dateTime={run.created_at}>
                {new Date(run.created_at).toLocaleString(currentLocale())}
              </time>
            </div>
            <ul className="space-y-3">
              {run.destinations.map((destination) => (
                <li
                  key={destination.id}
                  className="flex flex-wrap items-start justify-between gap-3"
                >
                  <div className="min-w-0 space-y-1">
                    <p className="text-sm font-medium">
                      {destination.name} · {replicaLabels[destination.outcome]}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      {destination.verified_at
                        ? `${t("settings.backupLastVerified")}: ${new Date(destination.verified_at).toLocaleString(currentLocale())}`
                        : t("settings.backupNeverVerified")}
                    </p>
                    {run.outcome === "failed" && run.archive_sha256 === null ? (
                      <p className="max-w-prose text-sm text-destructive">
                        {t("settings.backupRetryNewRequired")}
                      </p>
                    ) : destination.error_code ? (
                      <p className="max-w-prose text-sm text-destructive">
                        {reason(destination.error_code)}
                      </p>
                    ) : null}
                  </div>
                  {destination.outcome === "failed" && (
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={
                        retrying !== null ||
                        failed ||
                        run.outcome === "running" ||
                        run.archive_sha256 === null
                      }
                      onClick={() => void retry(destination.id)}
                    >
                      {retrying === destination.id
                        ? t("settings.backupRetrying")
                        : t("settings.backupRetry")}
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          </article>
        ))}
      </CardContent>
    </Card>
  );
}
