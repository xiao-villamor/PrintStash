import { useEffect, useId, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { inputClasses } from "@/components/ui/input";
import { subjectCaptionOptions, useCaptionCommand } from "@/lib/queries/captions";
import { getSessionVersion } from "@/lib/session-transport";
import { parseApiError } from "@/lib/errors";
import { Modal } from "@/components/ui/modal";
import { useAuth } from "@/lib/auth-context";
import { useI18n } from "@/lib/i18n";
import type { CaptionPatch } from "@/types/captions";
import type { SearchSubjectType } from "@/types/search";

export function SubjectCaption({ type, id }: { type: SearchSubjectType; id: number }) {
  const { user } = useAuth();
  return user ? (
    <CaptionContent key={`${user.id}:${type}:${id}`} userId={user.id} type={type} id={id} />
  ) : null;
}

function CaptionContent({
  userId,
  type,
  id,
}: {
  userId: number;
  type: SearchSubjectType;
  id: number;
}) {
  const { t } = useI18n();
  const fieldId = useId();
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);
  const query = useQuery(subjectCaptionOptions(userId, type, id));
  // Draft/review versions are explicit user intent, not a second remote snapshot owner.
  const [draft, setDraft] = useState<{ text: string; baseVersion: string | null } | null>(null);
  const [review, setReview] = useState<{
    text: string;
    version: string | null;
    canEdit: boolean;
  } | null>(null);
  const [needsReview, setNeedsReview] = useState(false);
  const mutation = useCaptionCommand();
  const inaccessible = query.isError && [401, 403, 404].includes(parseApiError(query.error).status);
  const caption = inaccessible ? undefined : query.data;
  function send(body: CaptionPatch) {
    mutation.mutate(
      { userId, type, id, body, session: getSessionVersion() },
      {
        onSuccess: () => {
          setDraft(null);
          setNeedsReview(false);
        },
        onError: () => setNeedsReview(true),
      },
    );
  }
  async function reviewLatest() {
    const session = getSessionVersion();
    const result = await query.refetch();
    if (!live.current || session !== getSessionVersion() || result.isError || !result.data) return;
    setReview({
      text: result.data.text,
      version: result.data.version_token,
      canEdit: result.data.can_edit,
    });
  }
  const failure = query.isError ? (
    <div role="status" className="flex items-center gap-2 text-sm text-on-surface-variant">
      <p>{t("captions.loadFailed")}</p>
      <Button size="sm" variant="outline" onClick={() => void query.refetch()}>
        {t("Retry")}
      </Button>
    </div>
  ) : null;
  if (!caption) return failure;
  const readOnly = query.isError || !caption.can_edit || needsReview;
  const pending = caption.phase === "pending" || caption.phase === "running";
  const reason =
    caption.unavailable_reason === "caption_preview_unavailable"
      ? "captions.noPreview"
      : caption.unavailable_reason === "caption_disabled"
        ? "captions.disabled"
        : "aiSearch.captionRequirements";
  return (
    <section
      aria-label={t("captions.title")}
      className="space-y-2 border-t border-outline-variant pt-4"
    >
      {failure}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-medium text-on-surface">{t("captions.title")}</h3>
        {caption.state && (
          <span className="text-xs text-on-surface-variant">{t(`captions.${caption.state}`)}</span>
        )}
      </div>
      {draft !== null ? (
        <form
          className="space-y-2"
          onSubmit={(event) => {
            event.preventDefault();
            if (readOnly || mutation.isPending || !draft.text.trim()) return;
            send({
              action: "edit",
              text: draft.text.trim(),
              version_token: draft.baseVersion ?? undefined,
            });
          }}
        >
          <label htmlFor={fieldId} className="text-sm text-on-surface-variant">
            {t("captions.editLabel")}
          </label>
          <textarea
            id={fieldId}
            value={draft.text}
            readOnly={readOnly || mutation.isPending}
            maxLength={2048}
            rows={4}
            className={`${inputClasses} min-h-24`}
            onChange={(event) => setDraft({ ...draft, text: event.target.value })}
          />
          <div className="flex gap-2">
            <Button
              size="sm"
              type="submit"
              disabled={readOnly || mutation.isPending || !draft.text.trim()}
            >
              {t("captions.save")}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              type="button"
              disabled={mutation.isPending}
              onClick={() => setDraft(null)}
            >
              {t("captions.cancel")}
            </Button>
          </div>
        </form>
      ) : (
        <>
          {caption.text && (
            <p className="whitespace-pre-wrap break-words text-sm text-on-surface">
              {caption.text}
            </p>
          )}
          {pending && (
            <p role="status" className="text-sm text-on-surface-variant">
              {t("captions.pending")}
            </p>
          )}
          {caption.phase === "failed" && (
            <p role="status" className="text-sm text-on-surface-variant">
              {t("captions.failed")}
            </p>
          )}
          {!caption.can_generate && !caption.text && (
            <p className="text-sm text-on-surface-variant">{t(reason)}</p>
          )}
          {caption.state === "dismissed" && (
            <p className="text-sm text-on-surface-variant">{t("captions.dismissedHelp")}</p>
          )}
          {caption.can_edit && (
            <div className="flex flex-wrap gap-2">
              <Button
                size="sm"
                variant="ghost"
                disabled={readOnly || mutation.isPending}
                onClick={() => setDraft({ text: caption.text, baseVersion: caption.version_token })}
              >
                {t("captions.edit")}
              </Button>
              {caption.state !== "dismissed" && (caption.text || pending) && (
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={mutation.isPending || readOnly}
                  onClick={() =>
                    send({ action: "dismiss", version_token: caption.version_token ?? undefined })
                  }
                >
                  {t("captions.dismiss")}
                </Button>
              )}
              {caption.can_generate && !pending && !caption.text && (
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={mutation.isPending || readOnly}
                  onClick={() =>
                    send({
                      action: caption.state === "dismissed" ? "reset" : "generate",
                      version_token: caption.version_token ?? undefined,
                    })
                  }
                >
                  {t(caption.state === "dismissed" ? "captions.reset" : "captions.generate")}
                </Button>
              )}
            </div>
          )}
        </>
      )}
      {needsReview && (
        <Button
          size="sm"
          variant="outline"
          disabled={query.isFetching || mutation.isPending}
          onClick={() => void reviewLatest()}
        >
          {t("captions.reviewLatest")}
        </Button>
      )}
      <Modal
        open={review !== null && !query.isError}
        onClose={() => setReview(null)}
        title={t("captions.reviewLatest")}
      >
        {review && (
          <div className="space-y-4">
            <div>
              <h4 className="font-medium">{t("captions.latest")}</h4>
              <p className="whitespace-pre-wrap break-words text-sm">
                {review.text || t("captions.empty")}
              </p>
            </div>
            {draft && (
              <div>
                <h4 className="font-medium">{t("captions.draft")}</h4>
                <p className="whitespace-pre-wrap break-words text-sm">{draft.text}</p>
              </div>
            )}
            <div className="flex flex-wrap gap-2">
              <Button
                variant="outline"
                onClick={() => {
                  setDraft(null);
                  setReview(null);
                  setNeedsReview(false);
                  mutation.reset();
                }}
              >
                {t("captions.useLatest")}
              </Button>
              {draft && (
                <Button
                  disabled={!review.canEdit || !caption.can_edit || query.isError}
                  onClick={() => {
                    setDraft({ ...draft, baseVersion: review.version });
                    setReview(null);
                    setNeedsReview(false);
                    mutation.reset();
                  }}
                >
                  {t("captions.keepDraft")}
                </Button>
              )}
            </div>
          </div>
        )}
      </Modal>
      {mutation.isError && (
        <p role="alert" className="text-sm text-destructive">
          {t("captions.saveFailed")}
        </p>
      )}
    </section>
  );
}
