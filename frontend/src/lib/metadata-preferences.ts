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

export type MetadataPreferences = Record<MetadataFieldId, boolean>;

export const DEFAULT_METADATA_PREFERENCES: MetadataPreferences = Object.fromEntries(
  METADATA_FIELDS.map((field) => [field.id, true]),
) as MetadataPreferences;

export function readMetadataPreferences(): MetadataPreferences {
  if (typeof window === "undefined") return DEFAULT_METADATA_PREFERENCES;
  const raw = window.localStorage.getItem(METADATA_PREFERENCE_STORAGE_KEY);
  if (!raw) return DEFAULT_METADATA_PREFERENCES;
  try {
    const parsed = JSON.parse(raw) as Partial<Record<MetadataFieldId, boolean>>;
    return {
      ...DEFAULT_METADATA_PREFERENCES,
      ...Object.fromEntries(
        METADATA_FIELDS.map((field) => [field.id, parsed[field.id] !== false]),
      ),
    } as MetadataPreferences;
  } catch {
    return DEFAULT_METADATA_PREFERENCES;
  }
}

export function writeMetadataPreferences(preferences: MetadataPreferences): void {
  window.localStorage.setItem(
    METADATA_PREFERENCE_STORAGE_KEY,
    JSON.stringify(preferences),
  );
}
