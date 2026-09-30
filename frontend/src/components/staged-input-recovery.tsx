import { useState } from "react";

import { Button } from "@/components/ui/button";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { discardJobStaging } from "@/lib/api/jobs";
import { userMessage } from "@/lib/errors";
import { formatBytes } from "@/lib/format";
import { currentLocale, uiText } from "@/lib/locale";
import type { JobStatus } from "@/types";

export function StagedInputRecovery({
  jobId,
  staging,
  onDiscard,
}: {
  jobId: string;
  staging: NonNullable<JobStatus["staging"]>;
  onDiscard: () => void;
}) {
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function discard() {
    setBusy(true);
    setError(null);
    try {
      await discardJobStaging(jobId);
      setConfirm(false);
      onDiscard();
    } catch (cause) {
      setError(userMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-2 space-y-2 text-xs text-muted-foreground">
      <p>
        {uiText("Retained input: {size} · {count} staged files", {
          size: formatBytes(staging.retained_bytes),
          count: staging.lease_count,
        })}
      </p>
      <p>
        {uiText("Earliest expiry: {date}", {
          date: new Intl.DateTimeFormat(currentLocale(), {
            dateStyle: "medium",
            timeStyle: "short",
          }).format(new Date(staging.earliest_expiry)),
        })}
      </p>
      {staging.discard_available && (
        <Button size="sm" variant="outline" disabled={busy} onClick={() => setConfirm(true)}>
          {uiText("Discard staged input")}
        </Button>
      )}
      {error && (
        <p role="alert" className="text-destructive">
          {error}
        </p>
      )}
      <ConfirmModal
        open={confirm}
        onClose={() => {
          if (!busy) setConfirm(false);
        }}
        onConfirm={() => void discard()}
        title={uiText("Discard staged input?")}
        description={uiText(
          "This frees retained upload capacity. Retrying this import will require uploading its input again.",
        )}
        confirmLabel={uiText("Discard staged input")}
        busy={busy}
      />
    </div>
  );
}
