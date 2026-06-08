export interface MetadataRead {
  slicer_name: string | null;
  slicer_version: string | null;
  printer_model: string | null;
  nozzle_diameter_mm: number | null;
  layer_height_mm: number | null;
  first_layer_height_mm: number | null;
  infill_percent: number | null;
  wall_loops: number | null;
  top_shell_layers: number | null;
  bottom_shell_layers: number | null;
  support_material: boolean | null;
  nozzle_temperature_c: number | null;
  bed_temperature_c: number | null;
  estimated_time_s: number | null;
  filament_weight_g: number | null;
  filament_length_mm: number | null;
  filament_cost: number | null;
  material_type: string | null;
  material_brand: string | null;
  bbox_x_mm: number | null;
  bbox_y_mm: number | null;
  bbox_z_mm: number | null;
  volume_mm3: number | null;
  triangle_count: number | null;
}

export type FileRevisionStatus =
  | "known_good"
  | "needs_test"
  | "failed"
  | "archived";

export interface FileRead {
  id: number;
  model_id: number;
  original_filename: string;
  file_type: "stl" | "3mf" | "gcode" | "obj";
  version: number;
  gcode_revision_number?: number | null;
  size_bytes: number;
  sha256: string;
  revision_label?: string | null;
  revision_status: FileRevisionStatus | null;
  revision_notes: string | null;
  is_recommended: boolean;
  uploaded_at: string;
  metadata: MetadataRead | null;
}

export interface FileRevisionUpdate {
  revision_label?: string | null;
  revision_status?: FileRevisionStatus | null;
  revision_notes?: string | null;
  is_recommended?: boolean;
}

export interface ModelRead {
  id: number;
  name: string;
  slug: string;
  hash: string;
  category: string | null;
  category_id: number | null;
  description: string | null;
  tags: string[];
  thumbnail_url: string | null;
  created_at: string;
  updated_at: string;
  files: FileRead[];
}

export interface ModelPrinterFileRead {
  file_id: number;
  printer_id: number;
  printer_name: string;
  remote_filename: string;
  matched_by: string;
  last_seen_at: string;
  missing_since: string | null;
}

export interface ModelPrinterPresenceRead {
  printer_id: number;
  printer_name: string;
  file_count: number;
}

export interface ModelListItem {
  id: number;
  name: string;
  slug: string;
  category: string | null;
  category_id: number | null;
  tags: string[];
  thumbnail_url: string | null;
  file_count: number;
  printer_presence: ModelPrinterPresenceRead[];
  updated_at: string;
}

export interface TrashedModelRead {
  id: number;
  name: string;
  slug: string;
  category: string | null;
  tags: string[];
  thumbnail_url: string | null;
  file_count: number;
  size_bytes: number;
  deleted_at: string;
  expires_at: string | null;
}

export interface TrashPurgeRead {
  purged_model_ids: number[];
  purged_count: number;
}

export interface StorageUsageRead {
  backend: string;
  prefix: string | null;
  bucket: string | null;
  object_count: number;
  total_size_bytes: number;
  ok: boolean;
  error: string | null;
}

export interface VaultStatsRead {
  model_count: number;
  file_count: number;
  source_file_count: number;
  gcode_file_count: number;
  category_count: number;
  tag_count: number;
  printer_count: number;
  indexed_size_bytes: number;
  storage: StorageUsageRead;
}

export interface ModelUpdate {
  name?: string;
  description?: string;
  category?: string;
  tags?: string[];
}

export interface IngestResponse {
  job_id: string;
  state: "pending" | "running" | "completed" | "failed";
  message: string;
}

export interface IngestJobStatus {
  job_id: string;
  state: "pending" | "running" | "completed" | "failed";
  model_id: number | null;
  file_id: number | null;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface ListModelsParams {
  category?: string;
  tag?: string[];
  q?: string;
  printer_id?: number;
  printer_presence?: "any" | "none";
  limit?: number;
  offset?: number;
}

export interface CategoryCreate {
  name: string;
  parent_id?: number | null;
}

export interface TagCreate {
  name: string;
}

export interface CategoryRead {
  id: number;
  name: string;
  slug: string;
  path: string;
  parent_id: number | null;
  model_count: number;
}

export interface FilamentProfileRead {
  id: number;
  name: string;
  material_type: string | null;
  material_brand: string | null;
  cost_per_kg: number | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

export interface FilamentProfileCreate {
  name: string;
  material_type?: string | null;
  material_brand?: string | null;
  cost_per_kg?: number | null;
  notes?: string | null;
}

export interface FilamentProfileUpdate {
  name?: string;
  material_type?: string | null;
  material_brand?: string | null;
  cost_per_kg?: number | null;
  notes?: string | null;
}

export interface PrinterProfileRead {
  id: number;
  name: string;
  printer_model: string | null;
  slicer_name: string | null;
  nozzle_diameter_mm: number | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

export interface PrinterProfileCreate {
  name: string;
  printer_model?: string | null;
  slicer_name?: string | null;
  nozzle_diameter_mm?: number | null;
  notes?: string | null;
}

export interface PrinterProfileUpdate {
  name?: string;
  printer_model?: string | null;
  slicer_name?: string | null;
  nozzle_diameter_mm?: number | null;
  notes?: string | null;
}

export interface TagRead {
  id: number;
  name: string;
  slug: string;
  model_count: number;
}
