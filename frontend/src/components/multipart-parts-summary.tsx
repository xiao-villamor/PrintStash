/** The models picked for a new multipart set, shown above the browser that picks them. */
import { X } from "lucide-react";
import { useI18n } from "@/lib/i18n";
import { Button } from "@/components/ui/button";
import { Cover } from "@/components/multipart-model-presentation";
import type { ModelListItem } from "@/types";

export function MultipartPartsSummary({
  models,
  limit,
  disabled,
  onRemove,
}: {
  models: ModelListItem[];
  limit: number;
  disabled: boolean;
  onRemove: (modelId: number) => void;
}) {
  const { t } = useI18n();
  return (
    <section aria-labelledby="multipart-parts-summary" className="shrink-0 border border-border">
      <div className="flex items-baseline justify-between gap-3 border-b border-border px-3 py-2">
        <h3 id="multipart-parts-summary" className="text-sm font-semibold">
          {t("multipart.partsInSet")}
        </h3>
        <p className="text-xs tabular-nums text-muted-foreground">
          {t("multipart.partsOfLimit", { count: String(models.length), limit: String(limit) })}
        </p>
      </div>
      {models.length === 0 ? (
        <p className="px-3 py-4 text-sm text-muted-foreground">{t("multipart.noPartsPicked")}</p>
      ) : (
        <ul className="grid max-h-44 gap-2 overflow-y-auto p-2 sm:grid-cols-2 lg:grid-cols-3">
          {models.map((model) => (
            <li
              key={model.id}
              className="flex min-w-0 items-center gap-2 border border-border bg-card p-1.5 pr-1"
            >
              <span className="library-preview h-10 w-10 shrink-0 overflow-hidden border border-border">
                <Cover src={model.thumbnail_url} alt="" />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium">{model.name}</span>
                <span className="block truncate text-xs text-muted-foreground">
                  {model.collection_label ?? t("multipart.vaultOnly")}
                </span>
              </span>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="w-7 shrink-0"
                disabled={disabled}
                aria-label={t("multipart.removePickedModel", { name: model.name })}
                onClick={() => onRemove(model.id)}
              >
                <X className="h-3.5 w-3.5" aria-hidden />
              </Button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
