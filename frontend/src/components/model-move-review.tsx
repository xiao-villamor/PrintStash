import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { useUiLocale } from "@/lib/i18n";
import { uiText } from "@/lib/locale";
import type { useModelMoves } from "@/features/library/moves";

/** Presentation only; the move owner retains the original destination and reviewed base. */
export function ModelMoveReview({ moves }: { moves: ReturnType<typeof useModelMoves> }) {
  useUiLocale();
  const issue = moves.issues[0];
  if (!issue) return null;
  const { source, destination, review } = issue;
  const busy = review.phase === "saving" || review.phase === "reviewing";
  return (
    <Modal open title={uiText("library.moveReview")} onClose={() => moves.dismiss(source.id)}>
      <div className="space-y-4">
        {review.phase === "denied" ? (
          <p role="alert">{uiText("library.moveUnavailable")}</p>
        ) : (
          <>
            <p role="alert">
              {uiText(
                issue.problem === "conflict" ? "library.editConflict" : "library.saveUnconfirmed",
              )}
            </p>
            <p>
              {uiText("library.moveIntent", {
                name: source.name,
                destination: destination ?? uiText("All Models"),
              })}
            </p>
            {review.phase === "review-failed" && (
              <p role="alert">{uiText("library.moveReviewFailed")}</p>
            )}
            {review.phase === "reviewed" && (
              <section aria-label={uiText("library.latestVersion")} className="space-y-2">
                <p>{review.model.name}</p>
                <p>
                  {uiText("library.moveLocation", {
                    location: review.model.collection ?? uiText("All Models"),
                  })}
                </p>
                <div className="flex gap-2">
                  <Button variant="outline" onClick={() => void moves.adopt(source.id)}>
                    {uiText("library.useLatest")}
                  </Button>
                  <Button
                    disabled={
                      review.model.effective_role !== "edit" &&
                      review.model.effective_role !== "admin"
                    }
                    onClick={() => void moves.retry(source.id)}
                  >
                    {uiText("library.retryDraft")}
                  </Button>
                </div>
              </section>
            )}
          </>
        )}
        <Button
          variant="outline"
          loading={review.phase === "reviewing"}
          disabled={busy}
          onClick={() => void moves.review(source.id)}
        >
          {uiText("library.reviewLatest")}
        </Button>
      </div>
    </Modal>
  );
}
