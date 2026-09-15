import type { ModelSort, SavedViewFilters } from "@/types";

export const historyKeys = [
  "printed_after",
  "printed_before",
  "print_duration_min_s",
  "print_duration_max_s",
] as const;
export const searchSorts: ModelSort[] = [
  "relevance",
  "date-desc",
  "date-asc",
  "name-asc",
  "name-desc",
  "printed-desc",
];
export function nonnegativeInteger(value: string | null): number | undefined {
  if (value === null || !/^\d+$/.test(value)) return undefined;
  const number = Number(value);
  return Number.isSafeInteger(number) && number <= 2147483647 ? number : undefined;
}
export function historyFilters(params: URLSearchParams) {
  return {
    printed_after: params.get("printed_after") || undefined,
    printed_before: params.get("printed_before") || undefined,
    print_duration_min_s: nonnegativeInteger(params.get("print_duration_min_s")),
    print_duration_max_s: nonnegativeInteger(params.get("print_duration_max_s")),
  };
}
function values<T extends string>(
  params: URLSearchParams,
  key: string,
  choices: readonly T[],
): T[] {
  return choices.filter((value) => params.getAll(key).includes(value));
}
export function readSearchFilters(params: URLSearchParams): SavedViewFilters {
  return {
    ...historyFilters(params),
    collection: params.get("c") || undefined,
    direct: params.get("direct") === "true",
    tag: params.getAll("tag"),
    favorites: params.get("favorites") === "true",
    printer_id: nonnegativeInteger(params.get("printer_id")) || undefined,
    printer_presence: values(params, "printer_presence", ["any", "none"] as const)[0],
    printed:
      params.get("printed") === "yes" ? true : params.get("printed") === "no" ? false : undefined,
    print_outcome: values(params, "print_outcome", [
      "queued",
      "uploading",
      "started",
      "printing",
      "paused",
      "completed",
      "cancelled",
      "failed",
    ] as const),
    material_type: params.getAll("material_type"),
    file_type: values(params, "file_type", ["stl", "3mf", "gcode", "obj", "step"] as const),
    revision_status: values(params, "revision_status", [
      "known_good",
      "needs_test",
      "failed",
      "archived",
    ] as const),
    storage: values(params, "storage", ["vault", "external"] as const),
    slicer_name: params.getAll("slicer_name"),
    printer_model: params.getAll("printer_model"),
    uploaded_after: params.get("uploaded_after") || undefined,
    uploaded_before: params.get("uploaded_before") || undefined,
    family_id: nonnegativeInteger(params.get("family_id")) || undefined,
    family_role: values(params, "family_role", [
      "canonical",
      "identical",
      "rescaled",
      "mirrored",
      "repaired",
      "print_variant",
    ] as const)[0],
    in_family: params.has("in_family") ? params.get("in_family") === "yes" : undefined,
    has_similar_candidates: params.has("has_similar_candidates")
      ? params.get("has_similar_candidates") === "yes"
      : undefined,
  };
}
export function writeSearchFilters(
  filters: SavedViewFilters,
  q = filters.q ?? "",
  sort: ModelSort = filters.sort ?? "relevance",
): URLSearchParams {
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (sort !== "relevance") params.set("sort", sort);
  for (const [key, value] of Object.entries(filters)) {
    if (key === "q" || key === "sort" || key === "browse" || value == null) continue;
    if (Array.isArray(value)) value.forEach((item) => params.append(key, item));
    else if (["printed", "in_family", "has_similar_candidates"].includes(key))
      params.set(key, value ? "yes" : "no");
    else if (value !== false && value !== "")
      params.set(key === "collection" ? "c" : key, String(value));
  }
  return params;
}
export function hasSearchFilters(filters: SavedViewFilters): boolean {
  return writeSearchFilters(filters).size > 0;
}
