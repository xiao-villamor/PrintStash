import { currentLocale } from "@/lib/locale";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { History } from "lucide-react";
import { Button } from "@/components/ui/button";
import { SettingsCard } from "@/components/settings-card";
import { backupRunsOptions, useBackupDestinationRetry } from "@/lib/queries/settings-backup-runs";
import { useAuth } from "@/lib/auth-context";
import { onAuthChange } from "@/lib/auth-store";
import { getSessionVersion } from "@/lib/session-transport";
import { parseApiError } from "@/lib/errors";
import { useI18n } from "@/lib/i18n";
import { toast } from "@/lib/toast";

export function BackupRunHistory({ onPublished }: { onPublished: () => void }) {
  const { t } = useI18n();
  const { user } = useAuth();
  const admin = useRef(!!user?.is_superuser);
  useLayoutEffect(() => {
    admin.current = !!user?.is_superuser;
  }, [user?.is_superuser]);
  const live = useRef(true);
  const [retired, setRetired] = useState(false);
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
    <SettingsCard
      icon={History}
      title={t("settings.backupRunsTitle")}
      description={t("settings.backupRunsDescription")}
      action={
        <Button
          variant="outline"
          size="sm"
          disabled={!user?.is_superuser || loading || retrying !== null}
          onClick={() => void history.refetch()}
        >
          {t("settings.backupRunRefresh")}
        </Button>
      }
    >
      <div className="space-y-4 p-4 sm:p-5" aria-busy={loading}>
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
            className="space-y-3 border-t border-border pt-4 first:border-t-0 first:pt-0"
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
      </div>
    </SettingsCard>
  );
}
