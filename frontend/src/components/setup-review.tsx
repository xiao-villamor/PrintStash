import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { formatBytes } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import { previewLookLabel, previewViewLabel } from "@/lib/preview-appearance";
import type { PreviewAppearance, PreviewMode } from "@/types/config";

function ReviewSection({
  title,
  editLabel,
  onEdit,
  disabled,
  children,
}: {
  title: string;
  editLabel: string;
  onEdit: () => void;
  disabled: boolean;
  children: ReactNode;
}) {
  return (
    <section className="space-y-2 rounded-md border border-border px-4 py-3">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-sm font-semibold text-foreground">{title}</h2>
        <Button type="button" variant="ghost" size="sm" disabled={disabled} onClick={onEdit}>
          {editLabel}
        </Button>
      </div>
      {children}
    </section>
  );
}

export function SetupReview({
  username,
  email,
  provider,
  files,
  previews,
  freeBytes,
  appearance,
  previewMode,
  busy,
  onEdit,
}: {
  username: string;
  email: string;
  provider: string;
  files?: string;
  previews?: string;
  freeBytes?: number | null;
  appearance: PreviewAppearance;
  previewMode: PreviewMode;
  busy: boolean;
  onEdit: (step: 1 | 2 | 3) => void;
}) {
  const { t } = useI18n();
  return (
    <div role="group" aria-label={t("setup.summary")} className="space-y-3 text-sm">
      <ReviewSection
        title={t("setup.stepAccount")}
        editLabel={t("setup.editAccount")}
        disabled={busy}
        onEdit={() => onEdit(1)}
      >
        <dl className="grid gap-3 sm:grid-cols-2">
          <div className="min-w-0 space-y-0.5">
            <dt className="text-muted-foreground">{t("setup.username")}</dt>
            <dd className="wrap-anywhere font-medium">{username}</dd>
          </div>
          <div className="min-w-0 space-y-0.5">
            <dt className="text-muted-foreground">{t("setup.reviewEmail")}</dt>
            <dd className="wrap-anywhere font-medium">{email || "—"}</dd>
          </div>
        </dl>
      </ReviewSection>
      <ReviewSection
        title={t("setup.stepStorage")}
        editLabel={t("setup.editStorage")}
        disabled={busy}
        onEdit={() => onEdit(2)}
      >
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
          <p className="font-medium">{provider}</p>
          {freeBytes != null && (
            <p className="text-xs text-muted-foreground">
              {`${formatBytes(freeBytes)} ${t("setup.availableSpace")}`.replace(/ /g, "\u00a0")}
            </p>
          )}
        </div>
        {(files || previews) && (
          <dl className="grid gap-2">
            {files && (
              <div className="space-y-0.5">
                <dt className="text-muted-foreground">{t("setup.files")}</dt>
                <dd className="wrap-anywhere font-mono text-xs">{files}</dd>
              </div>
            )}
            {previews && (
              <div className="space-y-0.5">
                <dt className="text-muted-foreground">{t("setup.previews")}</dt>
                <dd className="wrap-anywhere font-mono text-xs">{previews}</dd>
              </div>
            )}
          </dl>
        )}
      </ReviewSection>
      <ReviewSection
        title={t("setup.stepAppearance")}
        editLabel={t("setup.editAppearance")}
        disabled={busy}
        onEdit={() => onEdit(3)}
      >
        <dl className="grid gap-3 sm:grid-cols-3">
          <div className="space-y-0.5">
            <dt className="text-muted-foreground">{t("Colour")}</dt>
            <dd className="font-medium">{t(previewLookLabel[appearance.preview_look])}</dd>
          </div>
          <div className="space-y-0.5">
            <dt className="text-muted-foreground">{t("Floor grid")}</dt>
            <dd className="font-medium">{t(appearance.preview_floor_grid ? "On" : "Off")}</dd>
          </div>
          <div className="space-y-0.5">
            <dt className="text-muted-foreground">{t("setup.previewViews")}</dt>
            <dd className="font-medium">{t(previewViewLabel[previewMode])}</dd>
          </div>
        </dl>
      </ReviewSection>
    </div>
  );
}
