import { Input } from "@/components/ui/input";
import { useUiLocale } from "@/lib/i18n";
import { uiText } from "@/lib/locale";
import { historyKeys } from "@/lib/search-filters";
import type { SavedViewFilters } from "@/types";

export function PrintHistoryFields({
  value,
  onChange,
}: {
  value: Pick<SavedViewFilters, (typeof historyKeys)[number]>;
  onChange: (key: (typeof historyKeys)[number], value: string) => void;
}) {
  useUiLocale();
  const t = uiText;
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {historyKeys.map((key) => (
        <label key={key} className="min-w-0 space-y-1 text-xs text-muted-foreground">
          {t(`aiSearch.filter.${key}`)}
          <Input
            type={key.includes("duration") ? "number" : "text"}
            min={key === "print_duration_max_s" ? 1 : 0}
            max={2147483647}
            step={1}
            placeholder={
              key.includes("duration") ? t("aiSearch.seconds") : t("aiSearch.dateExample")
            }
            value={value[key] ?? ""}
            onChange={(event) => onChange(key, event.target.value)}
          />
        </label>
      ))}
    </div>
  );
}
