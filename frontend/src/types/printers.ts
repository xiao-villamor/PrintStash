export type PrinterStatus =
  | "unknown"
  | "offline"
  | "ready"
  | "printing"
  | "paused"
  | "error";

export type PrinterProvider =
  | "moonraker"
  | "bambu_lan"
  | "prusalink"
  | "elegoo_centauri"
  | "octoprint";
export type PrinterVariant =
  | "generic"
  | "elegoo_neptune4"
  | "elegoo_centauri_carbon"
  | "elegoo_centauri_carbon_2";
export type PrusaLinkAuthMode = "digest" | "api_key";

export type PrintJobState =
  | "queued"
  | "uploading"
  | "started"
  | "printing"
  | "paused"
  | "completed"
  | "cancelled"
  | "failed";
export type RoutingStrategy = "manual" | "default" | "least_busy";
export type PrinterRole = "view" | "print" | "control" | "admin";

export interface PrinterAccess {
  role: PrinterRole;
  can_view: boolean;
  can_print: boolean;
  can_control: boolean;
  can_admin: boolean;
}

export interface PrinterPermissionRead {
  id: number;
  user_id: number;
  username: string;
  printer_id: number;
  role: PrinterRole;
  created_at: string;
  updated_at: string;
}

export interface PrinterRead {
  id: number;
  name: string;
  provider: PrinterProvider;
  moonraker_url: string;
  has_api_key: boolean;
  provider_variant?: PrinterVariant | null;
  bambu_host?: string | null;
  bambu_serial?: string | null;
  has_bambu_access_code?: boolean;
  prusalink_url?: string | null;
  prusalink_auth_mode?: PrusaLinkAuthMode | null;
  prusalink_username?: string | null;
  has_prusalink_password?: boolean;
  has_prusalink_api_key?: boolean;
  elegoo_centauri_host?: string | null;
  elegoo_centauri_mainboard_id?: string | null;
  has_elegoo_centauri_access_code?: boolean;
  octoprint_url?: string | null;
  has_octoprint_api_key?: boolean;
  model_name?: string | null;
  detected_model?: string | null;
  capabilities: PrinterCapabilities;
  access: PrinterAccess;
  notes: string | null;
  group: string | null;
  is_default: boolean;
  drain_mode: boolean;
  drain_reason: string | null;
  drain_updated_at: string | null;
  status: PrinterStatus;
  last_seen_at: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
}

export interface PrinterCapabilities {
  can_start: boolean;
  can_pause: boolean;
  can_resume: boolean;
  can_cancel: boolean;
  can_live_status: boolean;
  can_upload: boolean;
  can_list_files: boolean;
  can_send_gcode: boolean;
  can_measure_consumption: boolean;
  support_level: "stable" | "beta" | string;
  support_notes: string[];
  unsupported_actions: string[];
}

export interface PrinterDiagnosticCheck {
  name: string;
  ok: boolean;
  code?: string;
  detail?: string;
}

export interface PrinterDiagnostics {
  printer_id: number;
  provider: PrinterProvider;
  support_level: "stable" | "beta" | string;
  capabilities: {
    can_upload: boolean;
    can_start: boolean;
    can_pause: boolean;
    can_resume: boolean;
    can_cancel: boolean;
    can_live_status: boolean;
    can_list_files: boolean;
    can_send_gcode: boolean;
    can_measure_consumption: boolean;
  };
  unsupported_actions: string[];
  notes: string[];
  checks: PrinterDiagnosticCheck[];
  ok: boolean;
}

export interface MoonrakerConfigRead {
  printer_id: number;
  server_info: Record<string, any>;
  printer_info: Record<string, any>;
  moonraker_config: Record<string, any>;
  klipper_config: Record<string, any>;
}

export interface PrinterFileRead {
  id: number;
  printer_id: number;
  printer_name: string | null;
  file_id: number | null;
  model_id: number | null;
  model_name: string | null;
  original_filename: string | null;
  remote_filename: string;
  size_bytes: number | null;
  sha256: string | null;
  matched_by: string;
  modified_at: string | null;
  last_seen_at: string;
  missing_since: string | null;
  created_at: string;
  updated_at: string;
}

export interface PrinterCreate {
  name: string;
  provider?: PrinterProvider;
  moonraker_url?: string;
  api_key?: string;
  provider_variant?: PrinterVariant;
  bambu_host?: string;
  bambu_serial?: string;
  bambu_access_code?: string;
  prusalink_url?: string;
  prusalink_auth_mode?: PrusaLinkAuthMode;
  prusalink_username?: string;
  prusalink_password?: string;
  prusalink_api_key?: string;
  elegoo_centauri_host?: string;
  elegoo_centauri_access_code?: string;
  elegoo_centauri_mainboard_id?: string;
  octoprint_url?: string;
  octoprint_api_key?: string;
  model_name?: string;
  notes?: string;
  group?: string;
}

export interface PrinterUpdate {
  provider?: PrinterProvider;
  name?: string;
  moonraker_url?: string;
  api_key?: string;
  provider_variant?: PrinterVariant;
  bambu_host?: string;
  bambu_serial?: string;
  bambu_access_code?: string;
  prusalink_url?: string;
  prusalink_auth_mode?: PrusaLinkAuthMode;
  prusalink_username?: string;
  prusalink_password?: string;
  prusalink_api_key?: string;
  elegoo_centauri_host?: string;
  elegoo_centauri_access_code?: string;
  elegoo_centauri_mainboard_id?: string;
  octoprint_url?: string;
  octoprint_api_key?: string;
  model_name?: string;
  notes?: string;
  group?: string;
}

export interface PrintJobRead {
  id: number;
  printer_id: number | null;
  file_id: number;
  model_id: number;
  remote_filename: string;
  state: PrintJobState;
  progress: number;
  source: string;
  error: string | null;
  routing_strategy: RoutingStrategy;
  queue_position: number;
  provider_job_id: string | null;
  blocked_reason: string | null;
  dispatch_claimed_at: string | null;
  dispatch_attempts: number;
  retryable: boolean;
  requested_by: number | null;
  spool_id: number | null;
  spool_name: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface SendToPrinter {
  file_id: number;
  start_print?: boolean;
  remote_filename?: string;
  spool_id?: number | null;
  spool_name?: string | null;
  spool_filament_id?: number | null;
}

export interface StartPrinterFile {
  remote_filename: string;
  file_id?: number | null;
}

export interface Dashboard {
  total_printers: number;
  status_counts: Record<string, number>;
  active_jobs: number;
  groups: DashboardGroup[];
}

export interface DashboardGroup {
  name: string;
  count: number;
  status_counts: Record<string, number>;
}

export interface PrinterSnapshot {
  print_stats?: {
    state?: string;
    filename?: string;
    print_duration?: number;
    total_duration?: number;
    message?: string;
  };
  virtual_sdcard?: {
    progress?: number;
    file_position?: number;
    file_size?: number;
  };
  extruder?: { temperature?: number; target?: number };
  heater_bed?: { temperature?: number; target?: number };
  toolhead?: { position?: number[]; homed_axes?: string };
  webhooks?: { state?: string; state_message?: string };
  [k: string]: any;
}

export interface PrinterStatusResponse {
  printer: PrinterRead;
  snapshot: PrinterSnapshot;
}

export interface FleetSummary {
  total_printers: number;
  queued_jobs: number;
  active_jobs: number;
  draining_printers: number;
  maintenance_printers: number;
  attention_jobs: number;
}

export interface QueueJobCreate {
  file_id: number;
  strategy: RoutingStrategy;
  printer_id?: number | null;
  spool_id?: number | null;
  spool_name?: string | null;
  spool_filament_id?: number | null;
}

export interface QueueJobUpdate {
  strategy?: RoutingStrategy;
  printer_id?: number | null;
  queue_position?: number;
  expected_updated_at?: string;
}

export interface PrinterRoutingUpdate {
  is_default?: boolean;
  drain_mode?: boolean;
  drain_reason?: string | null;
}

export interface MaintenanceWindow {
  id: number;
  printer_id: number;
  starts_at: string;
  ends_at: string;
  reason: string | null;
  created_at: string;
  updated_at: string;
}

export interface MaintenanceLog {
  id: number;
  printer_id: number;
  performed_at: string;
  category: string;
  note: string;
  counter_value: number | null;
  counter_unit: string | null;
  created_at: string;
  updated_at: string;
}
