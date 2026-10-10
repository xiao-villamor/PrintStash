import { PrintHistoryFields } from "@/components/print-history-fields";
import { historyKeys } from "@/lib/search-filters";
import type { SavedViewFilters } from "@/types";
import { filterValueText } from "@/lib/filter-labels";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import { ChevronDown, X } from "lucide-react";
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

/** The printable source formats; sliced G-code and 2D DXF stay as their own rows. */
const SOURCE_MESH_TYPES = new Set(["stl", "3mf", "obj", "step"]);

const GROUPS: Array<{ key: FacetFilterKey; label: string }> = [
  {
    key: "file_type",
    get label() {
      return uiText("Artifact");
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
          <h3 className="text-xs font-bold uppercase tracking-wider text-muted-foreground">
            {uiText("Model filters")}
          </h3>
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
        {loading && (
          <p className="px-2 py-2 text-xs text-muted-foreground">
            {uiText("Loading filter values…")}
          </p>
        )}
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
              <div key={key}>
                <button
                  type="button"
                  aria-expanded={isOpen}
                  aria-controls={contentId}
                  onClick={() => toggleGroup(key)}
                  className="flex w-full items-center gap-1 rounded px-2 py-1.5 text-left text-sm font-medium text-foreground transition-[background-color,color,transform] duration-press hover:bg-muted active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <ChevronDown
                    className={`h-4 w-4 text-muted-foreground transition-transform duration-press ${isOpen ? "" : "-rotate-90"}`}
                  />
                  <span className="flex-1">{label}</span>
                  {selected.length > 0 && (
                    <span className="rounded-full bg-accent px-1.5 text-3xs text-accent-foreground">
                      {selected.length}
                    </span>
                  )}
                </button>
                {isOpen && (
                  <div
                    id={contentId}
                    className="ml-4 space-y-0.5 border-l border-border py-0.5 pl-3"
                  >
                    {key === "file_type" ? (
                      <FileTypeOptions
                        values={values}
                        selected={selected}
                        onToggle={(value) => toggleValue(key, value)}
                        onChange={(next) => onChange(key, next)}
                      />
                    ) : (
                      values.map((item) => (
                        <FacetOption
                          key={item.value}
                          label={filterValueText(key, item.value)}
                          count={item.count}
                          checked={selected.includes(item.value)}
                          onChange={() => toggleValue(key, item.value)}
                        />
                      ))
                    )}
                  </div>
                )}
              </div>
            );
          })}
          <details
            className="px-2 py-3"
            open={historyKeys.some((key) => history[key] != null && history[key] !== "")}
          >
            <summary className="cursor-pointer text-sm font-medium">
              {uiText("Print history")}
            </summary>
            <PrintHistoryFields
              value={history}
              onChange={(key, value) => onDateChange?.(key, value)}
            />
          </details>
          <details className="pt-1" open={!!uploadedAfter || !!uploadedBefore}>
            <summary className="cursor-pointer px-2 py-1.5 text-sm font-medium text-foreground">
              {uiText("Uploaded")}
            </summary>
            <div className="grid grid-cols-2 gap-2 px-2 pb-1">
              <label className="text-3xs text-muted-foreground">
                {uiText("After")}
                <input
                  type="date"
                  value={uploadedAfter ?? ""}
                  onChange={(event) => onDateChange?.("uploaded_after", event.target.value)}
                  className="mt-1 w-full rounded border border-input bg-background px-1 py-1 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
                />
              </label>
              <label className="text-3xs text-muted-foreground">
                {uiText("Before")}
                <input
                  type="date"
                  value={uploadedBefore ?? ""}
                  onChange={(event) => onDateChange?.("uploaded_before", event.target.value)}
                  className="mt-1 w-full rounded border border-input bg-background px-1 py-1 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
                />
              </label>
            </div>
          </details>
        </div>
      </section>
    </Localized>
  );
}

function FacetOption({
  label,
  count,
  checked,
  onChange,
  className = "",
}: {
  label: string;
  count: number;
  checked: boolean;
  onChange: () => void;
  className?: string;
}) {
  return (
    <label
      className={`flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-xs transition-colors duration-press hover:bg-muted ${className}`}
    >
      <Checkbox className="h-4 w-4" checked={checked} onChange={onChange} />
      <span className="min-w-0 flex-1 truncate">{label}</span>
      <span className="min-w-[18px] rounded bg-muted px-1 py-0.5 text-center text-2xs font-medium text-muted-foreground">
        {count}
      </span>
    </label>
  );
}

/**
 * Source meshes nest under one row that selects or clears them together; G-code,
 * DXF and any format the server adds later stay flat beside it. Each value is still
 * its own `file_type`, so saved views and the API see exactly what they did before.
 */
function FileTypeOptions({
  values,
  selected,
  onToggle,
  onChange,
}: {
  values: FacetValueRead[];
  selected: string[];
  onToggle: (value: string) => void;
  onChange: (values: string[]) => void;
}) {
  const meshes = values.filter((item) => SOURCE_MESH_TYPES.has(item.value));
  const others = values.filter((item) => !SOURCE_MESH_TYPES.has(item.value));
  const meshValues = meshes.map((item) => item.value);
  const allMeshes = meshValues.length > 0 && meshValues.every((value) => selected.includes(value));

  function toggleMeshes() {
    const rest = selected.filter((value) => !SOURCE_MESH_TYPES.has(value));
    onChange(allMeshes ? rest : [...rest, ...meshValues]);
  }

  return (
    <>
      {meshes.length > 0 && (
        <div>
          <FacetOption
            label={uiText("Source meshes")}
            count={meshes.reduce((total, item) => total + item.count, 0)}
            checked={allMeshes}
            onChange={toggleMeshes}
            className="font-medium"
          />
          <div className="ml-4 space-y-0.5 border-l border-border pl-2">
            {meshes.map((item) => (
              <FacetOption
                key={item.value}
                label={filterValueText("file_type", item.value)}
                count={item.count}
                checked={selected.includes(item.value)}
                onChange={() => onToggle(item.value)}
              />
            ))}
          </div>
        </div>
      )}
      {others.map((item) => (
        <FacetOption
          key={item.value}
          label={filterValueText("file_type", item.value)}
          count={item.count}
          checked={selected.includes(item.value)}
          onChange={() => onToggle(item.value)}
        />
      ))}
    </>
  );
}
