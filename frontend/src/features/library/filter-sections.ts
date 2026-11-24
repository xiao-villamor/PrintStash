/** The category list reads as three short, familiar groups instead of one long column. */
export const FILTER_SECTIONS = [
  {
    label: "library.filters.sectionFiles",
    ids: ["file_type", "storage", "tags", "uploaded"],
  },
  {
    label: "library.filters.sectionPrinting",
    ids: [
      "material_type",
      "slicer_name",
      "printer_model",
      "printer",
      "revision_status",
      "print_outcome",
      "history",
    ],
  },
] as const;
/** Two-state filters toggle in place: opening a page for one checkbox was a detour. */
export const QUICK_FILTERS = ["favorites", "printed", "has_similar_candidates"] as const;
