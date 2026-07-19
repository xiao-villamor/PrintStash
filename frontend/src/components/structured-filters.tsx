import { ChevronDown, X } from "lucide-react";
import { useState } from "react";

import { Checkbox } from "@/components/ui/checkbox";
import type { FacetValueRead, ModelFacetsRead } from "@/types";

type FilterKey = "file_type" | "material_type" | "slicer_name" | "printer_model" | "revision_status" | "print_outcome" | "storage" | "printed";

const GROUPS: Array<{ key: FilterKey; label: string }> = [
  { key: "file_type", label: "Artifact" },
  { key: "material_type", label: "Material" },
  { key: "slicer_name", label: "Slicer" },
  { key: "printer_model", label: "Printer model" },
  { key: "revision_status", label: "Revision" },
  { key: "printed", label: "Printed" },
  { key: "print_outcome", label: "Print outcome" },
  { key: "storage", label: "Storage" },
];

export function StructuredFilters({
  facets,
  active,
  onChange,
  uploadedAfter,
  uploadedBefore,
  onDateChange,
  loading = false,
  error = false,
}: {
  facets?: ModelFacetsRead;
  active: Partial<Record<FilterKey, string[]>>;
  onChange: (key: FilterKey, values: string[]) => void;
  uploadedAfter?: string;
  uploadedBefore?: string;
  onDateChange?: (key: "uploaded_after" | "uploaded_before", value: string) => void;
  loading?: boolean;
  error?: boolean;
}) {
  const [open, setOpen] = useState<Set<FilterKey>>(new Set(["file_type", "material_type", "revision_status"]));

  function toggleGroup(key: FilterKey) {
    setOpen((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }

  function toggleValue(key: FilterKey, value: string) {
    const selected = active[key] ?? [];
    onChange(key, selected.includes(value) ? selected.filter((item) => item !== value) : [...selected, value]);
  }

  const count = Object.values(active).reduce((total, values) => total + (values?.length ?? 0), 0)
    + (uploadedAfter ? 1 : 0) + (uploadedBefore ? 1 : 0);
  function clearAll() {
    GROUPS.forEach(({ key }) => onChange(key, []));
    onDateChange?.("uploaded_after", "");
    onDateChange?.("uploaded_before", "");
  }
  return (
    <section>
      <div className="mb-2 flex items-center justify-between px-2">
        <h3 className="text-xs font-bold uppercase tracking-wider text-muted-foreground">Model filters</h3>
        {count > 0 && <button type="button" onClick={clearAll} className="flex items-center gap-1 text-3xs text-muted-foreground hover:text-foreground"><X className="h-3 w-3" /> Clear {count}</button>}
      </div>
      {loading && <p className="px-2 py-2 text-xs text-muted-foreground">Loading filter values…</p>}
      {error && <p className="mx-2 mb-2 rounded-md border border-destructive/30 px-2 py-2 text-xs text-destructive">Filter values could not be loaded.</p>}
      <div className="space-y-1">
        {GROUPS.map(({ key, label }) => {
          const values: FacetValueRead[] = facets?.[key] ?? [];
          const selected = active[key] ?? [];
          if (values.length === 0 && selected.length === 0) return null;
          return (
            <div key={key} className="rounded-md border border-border bg-card/40">
              <button type="button" onClick={() => toggleGroup(key)} className="flex w-full items-center gap-2 px-2 py-1.5 text-left text-xs font-medium text-foreground">
                <ChevronDown className={`h-3.5 w-3.5 text-muted-foreground transition-transform duration-press ${open.has(key) ? "" : "-rotate-90"}`} />
                <span className="flex-1">{label}</span>
                {selected.length > 0 && <span className="rounded-full bg-accent px-1.5 text-3xs text-accent-foreground">{selected.length}</span>}
              </button>
              {open.has(key) && (
                <div className="space-y-1 border-t border-border px-2 py-2">
                  {values.map((item) => (
                    <label key={item.value} className="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-xs hover:bg-muted">
                      <Checkbox checked={selected.includes(item.value)} onChange={() => toggleValue(key, item.value)} />
                      <span className="min-w-0 flex-1 truncate capitalize">{item.value.replaceAll("_", " ")}</span>
                      <span className="font-mono text-3xs text-muted-foreground">{item.count}</span>
                    </label>
                  ))}
                </div>
              )}
            </div>
          );
        })}
        <div className="rounded-md border border-border bg-card/40 p-2">
          <p className="mb-2 text-xs font-medium text-foreground">Uploaded</p>
          <div className="grid grid-cols-2 gap-2">
            <label className="text-3xs text-muted-foreground">After<input type="date" value={uploadedAfter ?? ""} onChange={(event) => onDateChange?.("uploaded_after", event.target.value)} className="mt-1 w-full rounded border border-input bg-background px-1 py-1 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-ring" /></label>
            <label className="text-3xs text-muted-foreground">Before<input type="date" value={uploadedBefore ?? ""} onChange={(event) => onDateChange?.("uploaded_before", event.target.value)} className="mt-1 w-full rounded border border-input bg-background px-1 py-1 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-ring" /></label>
          </div>
        </div>
      </div>
    </section>
  );
}
