/** Shared review presentation for the two consumers of the connection editing contract. */
import { Button } from "@/components/ui/button";
import { uiText } from "@/lib/locale";
import type { StorageConnectionReview as Review } from "@/lib/queries/settings-storage";

export function StorageConnectionReview({
  review,
  busy,
  onReview,
  onAdopt,
  onSave,
}: {
  review: Review;
  busy: boolean;
  onReview: () => void;
  onAdopt: () => void;
  onSave: () => void;
}) {
  if (review.phase === "idle") return null;
  if (review.phase === "retired")
    return <p role="alert">{uiText("storage.connectionUnavailable")}</p>;
  return (
    <section role="alert" className="space-y-3 rounded border border-border p-4">
      <p>
        {uiText(review.problem === "conflict" ? "library.editConflict" : "library.saveUnconfirmed")}
      </p>
      {review.phase === "ready" && (
        <section aria-label={uiText("library.latestVersion")} className="space-y-2 text-sm">
          <p>{review.snapshot.name}</p>
          <p>
            {review.snapshot.kind} · {review.snapshot.purpose} ·{" "}
            {uiText(review.snapshot.enabled ? "Enabled" : "Paused")}
          </p>
          <dl className="grid grid-cols-2 gap-2">
            {Object.entries(review.snapshot.configuration).map(([key, value]) => (
              <div key={key}>
                <dt>{key}</dt>
                <dd>{String(value ?? "")}</dd>
              </div>
            ))}
            <div>
              <dt>{uiText("settings.backupLocalManual")}</dt>
              <dd>{uiText(review.snapshot.manual_backup_enabled ? "Enabled" : "Disabled")}</dd>
            </div>
            <div>
              <dt>{uiText("settings.backupLocalAutomatic")}</dt>
              <dd>{uiText(review.snapshot.automatic_backup_enabled ? "Enabled" : "Disabled")}</dd>
            </div>
          </dl>
          <p>{uiText("counts.credentials", { count: review.snapshot.secret_fields_set.length })}</p>
        </section>
      )}
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" disabled={busy || review.phase === "loading"} onClick={onReview}>
          {uiText("Review current values")}
        </Button>
        {review.phase === "ready" && (
          <>
            <Button variant="outline" disabled={busy} onClick={onAdopt}>
              {uiText("Use current values")}
            </Button>
            <Button disabled={busy || !review.sameIdentity} onClick={onSave}>
              {uiText("Save revised changes")}
            </Button>
          </>
        )}
      </div>
    </section>
  );
}
