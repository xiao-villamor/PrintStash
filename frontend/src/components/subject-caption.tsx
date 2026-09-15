import { useId, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { inputClasses } from "@/components/ui/input";
import { getCaption, patchCaption } from "@/lib/api/captions";
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
  const client = useQueryClient();
  const fieldId = useId();
  const key = ["subject-caption", userId, type, id];
  const query = useQuery({
    queryKey: key,
    queryFn: () => getCaption(type, id),
    gcTime: 0,
    refetchInterval: (current) =>
      current.state.data?.phase === "pending" || current.state.data?.phase === "running"
        ? 5000
        : false,
  });
  const [draft, setDraft] = useState<string | null>(null);
  const mutation = useMutation({
    mutationFn: (body: CaptionPatch) =>
      patchCaption(type, id, {
        ...body,
        version_token: query.data?.version_token ?? undefined,
      }),
    onSuccess: (value) => {
      client.setQueryData(key, value);
      void client.invalidateQueries({ queryKey: ["library-search"] });
      setDraft(null);
    },
  });
  const caption = query.data;
  if (!caption) {
    return query.isError ? (
      <p role="status" className="text-sm text-on-surface-variant">
        {t("captions.loadFailed")}
      </p>
    ) : null;
  }
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
            mutation.mutate({ action: "edit", text: draft.trim() });
          }}
        >
          <label htmlFor={fieldId} className="text-sm text-on-surface-variant">
            {t("captions.editLabel")}
          </label>
          <textarea
            id={fieldId}
            value={draft}
            maxLength={2048}
            rows={4}
            className={`${inputClasses} min-h-24`}
            onChange={(event) => setDraft(event.target.value)}
          />
          <div className="flex gap-2">
            <Button size="sm" type="submit" disabled={mutation.isPending || !draft.trim()}>
              {t("captions.save")}
            </Button>
            <Button size="sm" variant="ghost" type="button" onClick={() => setDraft(null)}>
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
              <Button size="sm" variant="ghost" onClick={() => setDraft(caption.text)}>
                {t("captions.edit")}
              </Button>
              {caption.state !== "dismissed" && (caption.text || pending) && (
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={mutation.isPending}
                  onClick={() => mutation.mutate({ action: "dismiss" })}
                >
                  {t("captions.dismiss")}
                </Button>
              )}
              {caption.can_generate && !pending && !caption.text && (
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={mutation.isPending}
                  onClick={() =>
                    mutation.mutate({
                      action: caption.state === "dismissed" ? "reset" : "generate",
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
      {mutation.isError && (
        <p role="alert" className="text-sm text-destructive">
          {t("captions.saveFailed")}
        </p>
      )}
    </section>
  );
}
