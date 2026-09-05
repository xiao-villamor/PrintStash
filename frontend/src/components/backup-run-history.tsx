import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { listBackupRuns, retryBackupDestination } from "@/lib/api/backup";
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
  const {
    data: runs = [],
    isPending: loading,
    isError: failed,
    refetch,
  } = useQuery({
    queryKey: ["backup-runs", refreshKey],
    queryFn: listBackupRuns,
  });
  const [retrying, setRetrying] = useState<string | null>(null);

  async function retry(id: string) {
    setRetrying(id);
    try {
      await retryBackupDestination(id);
      toast.success(t("settings.backupRetryDone"));
      onPublished();
    } catch (error) {
      toast.error(error);
    } finally {
      await refetch();
      setRetrying(null);
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
          disabled={loading || retrying !== null}
          aria-label={t("settings.backupRunRefresh")}
          onClick={() => void refetch()}
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
                {new Date(run.created_at).toLocaleString()}
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
                        ? `${t("settings.backupLastVerified")}: ${new Date(destination.verified_at).toLocaleString()}`
                        : t("settings.backupNeverVerified")}
                    </p>
                    {destination.error_code && (
                      <p className="max-w-prose text-sm text-destructive">
                        {reason(destination.error_code)}
                      </p>
                    )}
                  </div>
                  {destination.outcome === "failed" && (
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={retrying !== null || run.outcome === "running"}
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
