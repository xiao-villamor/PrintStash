import { FilterGroup } from "@/components/filter-group";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { PrintHistoryFields } from "@/components/print-history-fields";
import { historyKeys } from "@/lib/search-filters";
import type { SavedViewFilters } from "@/types";
import { FILTER_INPUT } from "@/lib/filter-input";
import { filterValueText } from "@/lib/filter-labels";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import { X } from "lucide-react";
import { useState } from "react";

import { Checkbox } from "@/components/ui/checkbox";
import { Localized } from "@/components/ui/localized";
import type { FacetValueRead, ModelFacetsRead } from "@/types";

type FacetFilterKey =
  | "file_type"
  | "material_type"
  | "slicer_name"
  | "printer_model"
  | "revision_status"
  | "print_outcome"
  | "storage"
  | "printed";

type FilterKey = FacetFilterKey | "has_similar_candidates";

const GROUPS: Array<{ key: FacetFilterKey; label: string }> = [
  {
    key: "file_type",
    get label() {
      return uiText("File type");
    },
  },
  {
    key: "material_type",
    get label() {
      return uiText("Material");
    },
  },
  {
    key: "slicer_name",
    get label() {
      return uiText("Slicer");
    },
  },
  {
    key: "printer_model",
    get label() {
      return uiText("Printer model");
    },
  },
  {
    key: "revision_status",
    get label() {
      return uiText("Revision");
    },
  },
  {
    key: "printed",
    get label() {
      return uiText("Printed");
    },
  },
  {
    key: "print_outcome",
    get label() {
      return uiText("Print outcome");
    },
  },
  {
    key: "storage",
    get label() {
      return uiText("Storage");
    },
  },
];

export function StructuredFilters({
  facets,
  active,
  onChange,
  uploadedAfter,
  uploadedBefore,
  history = {},
  onDateChange,
  onClearAll,
  loading = false,
  error = false,
}: {
  facets?: ModelFacetsRead;
  active: Partial<Record<FilterKey, string[]>>;
  onChange: (key: FilterKey, values: string[]) => void;
  uploadedAfter?: string;
  uploadedBefore?: string;
  history?: Pick<SavedViewFilters, (typeof historyKeys)[number]>;
  onDateChange?: (
    key: "uploaded_after" | "uploaded_before" | (typeof historyKeys)[number],
    value: string,
  ) => void;
  onClearAll?: () => void;
  loading?: boolean;
  error?: boolean;
}) {
  useUiLocale();
  const [open, setOpen] = useState<Partial<Record<FilterKey, boolean>>>({});
  const [extraOpen, setExtraOpen] = useState<{ history?: boolean; uploaded?: boolean }>({});
  const historyActive = historyKeys.some((key) => history[key] != null && history[key] !== "");
  const uploadedActive = !!uploadedAfter || !!uploadedBefore;

  function toggleGroup(key: FilterKey) {
    setOpen((current) => ({ ...current, [key]: !(current[key] ?? !!active[key]?.length) }));
  }

  function toggleValue(key: FilterKey, value: string) {
    const selected = active[key] ?? [];
    onChange(
      key,
      selected.includes(value) ? selected.filter((item) => item !== value) : [...selected, value],
    );
  }

  const count =
    Object.values(active).reduce((total, values) => total + (values?.length ?? 0), 0) +
    (uploadedAfter ? 1 : 0) +
    (uploadedBefore ? 1 : 0) +
    historyKeys.filter((key) => history[key] != null && history[key] !== "").length;
  function clearAll() {
    if (onClearAll) {
      onClearAll();
      return;
    }
    GROUPS.forEach(({ key }) => onChange(key, []));
    onChange("has_similar_candidates", []);
    onDateChange?.("uploaded_after", "");
    onDateChange?.("uploaded_before", "");
    historyKeys.forEach((key) => onDateChange?.(key, ""));
  }
  return (
    <Localized>
      <section>
        <div className="mb-2 flex items-center justify-between pl-2 pr-1">
          <div className="flex items-center gap-2">
            <h3 className="text-xs font-bold uppercase tracking-wider text-muted-foreground">
              {uiText("Model filters")}
            </h3>
            <span className="h-3.5 w-3.5 shrink-0">
              {loading && <Spinner size="sm" label={uiText("Loading filter values…")} />}
            </span>
          </div>
          {count > 0 && (
            <button
              type="button"
              onClick={clearAll}
              className="flex items-center gap-1 text-3xs text-muted-foreground hover:text-foreground"
            >
              <X className="h-3 w-3" />
              {uiText(" Clear ")}
              {count}
            </button>
          )}
        </div>
        {error && (
          <p className="mx-2 mb-2 rounded-md border border-destructive/30 px-2 py-2 text-xs text-destructive">
            {uiText("Filter values could not be loaded.")}
          </p>
        )}
        <div className="space-y-0.5">
          <label className="flex items-center gap-2 px-2 py-2 text-sm">
            <Checkbox
              ariaLabel={uiText("similarity.hasCandidates")}
              checked={active.has_similar_candidates?.includes("yes") ?? false}
              onChange={(checked) => onChange("has_similar_candidates", checked ? ["yes"] : [])}
            />
            {uiText("similarity.hasCandidates")}
          </label>
          {GROUPS.map(({ key, label }) => {
            const values: FacetValueRead[] = facets?.[key] ?? [];
            const selected = active[key] ?? [];
            const isOpen = open[key] ?? selected.length > 0;
            const contentId = `model-filter-${key}`;
            if (values.length === 0 && selected.length === 0) return null;
            return (
              <FilterGroup
                key={key}
                id={contentId}
                label={label}
                count={selected.length}
                open={isOpen}
                onToggle={() => toggleGroup(key)}
              >
                {values.map((item) => (
                  <label
                    key={item.value}
                    className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-xs transition-colors duration-press hover:bg-muted"
                  >
                    <Checkbox
                      className="h-4 w-4"
                      checked={selected.includes(item.value)}
                      onChange={() => toggleValue(key, item.value)}
                    />
                    <span className="min-w-0 flex-1 truncate capitalize">
                      {filterValueText(key, item.value)}
                    </span>
                    <span className="min-w-[18px] rounded bg-muted px-1 py-0.5 text-center text-2xs font-medium text-muted-foreground">
                      {item.count}
                    </span>
                  </label>
                ))}
              </FilterGroup>
            );
          })}
          <FilterGroup
            id="model-filter-history"
            label={uiText("Print history")}
            count={historyKeys.filter((key) => history[key] != null && history[key] !== "").length}
            open={extraOpen.history ?? historyActive}
            onToggle={() =>
              setExtraOpen((current) => ({
                ...current,
                history: !(current.history ?? historyActive),
              }))
            }
          >
            <PrintHistoryFields
              compact
              value={history}
              onChange={(key, value) => onDateChange?.(key, value)}
            />
          </FilterGroup>
          <FilterGroup
            id="model-filter-uploaded"
            label={uiText("Uploaded")}
            count={(uploadedAfter ? 1 : 0) + (uploadedBefore ? 1 : 0)}
            open={extraOpen.uploaded ?? uploadedActive}
            onToggle={() =>
              setExtraOpen((current) => ({
                ...current,
                uploaded: !(current.uploaded ?? uploadedActive),
              }))
            }
          >
            <div className="grid gap-2 py-1">
              <label className="space-y-1 text-xs text-muted-foreground">
                {uiText("After")}
                <Input
                  type="date"
                  value={uploadedAfter ?? ""}
                  onChange={(event) => onDateChange?.("uploaded_after", event.target.value)}
                  className={FILTER_INPUT}
                />
              </label>
              <label className="space-y-1 text-xs text-muted-foreground">
                {uiText("Before")}
                <Input
                  type="date"
                  value={uploadedBefore ?? ""}
                  onChange={(event) => onDateChange?.("uploaded_before", event.target.value)}
                  className={FILTER_INPUT}
                />
              </label>
            </div>
          </FilterGroup>
        </div>
      </section>
    </Localized>
  );
}
