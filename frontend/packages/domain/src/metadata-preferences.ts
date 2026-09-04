export const METADATA_PREFERENCE_STORAGE_KEY = "printstash.metadata.visible";

export const METADATA_FIELDS = [
  { id: "printer_profile", label: "Printer profile" },
  { id: "material", label: "Material" },
  { id: "filament_profile", label: "Filament profile" },
  { id: "layer_height", label: "Layer height" },
  { id: "first_layer", label: "First layer" },
  { id: "nozzle", label: "Nozzle" },
  { id: "infill", label: "Infill" },
  { id: "walls", label: "Walls" },
  { id: "top_bottom", label: "Top / bottom" },
  { id: "supports", label: "Supports" },
  { id: "nozzle_temp", label: "Nozzle temperature" },
  { id: "bed_temp", label: "Bed temperature" },
  { id: "estimated_time", label: "Estimated time" },
  { id: "filament_weight", label: "Filament weight" },
  { id: "filament_cost", label: "Filament cost" },
  { id: "mesh_volume", label: "Mesh volume" },
  { id: "mesh_triangles", label: "Mesh triangles" },
  { id: "slicer_info", label: "Slicer info" },
] as const;

export type MetadataFieldId = (typeof METADATA_FIELDS)[number]["id"];

/** One visibility flag per known metadata field — a closed record, never an open bag. */
export type MetadataPreferences = { [Field in MetadataFieldId]: boolean };

/** Build a full preference record from a per-field visibility decision. */
function preferencesFrom(isVisible: (field: MetadataFieldId) => boolean): MetadataPreferences {
  const entries = METADATA_FIELDS.map((field) => [field.id, isVisible(field.id)]);
  // SAFETY: MetadataFieldId is derived from METADATA_FIELDS, so mapping that list
  // yields exactly one entry per key of MetadataPreferences. Object.fromEntries can
  // only promise a string-keyed bag, which cannot carry that completeness.
  return Object.fromEntries(entries) as MetadataPreferences;
}

export const DEFAULT_METADATA_PREFERENCES = preferencesFrom(() => true);

/**
 * Decode the persisted payload into the set of fields the user has explicitly hidden.
 * Returns null when there is nothing readable there — malformed JSON, or a value that
 * is not an object, both of which mean "no stored preference".
 */
function parseHiddenFields(raw: string): ReadonlySet<MetadataFieldId> | null {
  let payload: unknown;
  try {
    payload = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!(payload instanceof Object)) return null;
  const stored = new Map<string, unknown>(Object.entries(payload));
  // Only a literal `false` hides a field; every other stored value stays visible.
  return new Set(METADATA_FIELDS.map((field) => field.id).filter((id) => stored.get(id) === false));
}

export function readMetadataPreferences(): MetadataPreferences {
  if (!("localStorage" in globalThis)) return DEFAULT_METADATA_PREFERENCES;
  const raw = globalThis.localStorage.getItem(METADATA_PREFERENCE_STORAGE_KEY);
  if (!raw) return DEFAULT_METADATA_PREFERENCES;
  const hidden = parseHiddenFields(raw);
  if (hidden === null) return DEFAULT_METADATA_PREFERENCES;
  return preferencesFrom((field) => !hidden.has(field));
}

export function writeMetadataPreferences(preferences: MetadataPreferences): void {
  window.localStorage.setItem(METADATA_PREFERENCE_STORAGE_KEY, JSON.stringify(preferences));
}
