export type PrinterStatus =
  | "unknown"
  | "offline"
  | "ready"
  | "printing"
  | "paused"
  | "error";

export type PrintJobState =
  | "queued"
  | "uploading"
  | "started"
  | "printing"
  | "paused"
  | "completed"
  | "cancelled"
  | "failed";

export interface PrinterRead {
  id: number;
  name: string;
  moonraker_url: string;
  has_api_key: boolean;
  notes: string | null;
  group: string | null;
  status: PrinterStatus;
  last_seen_at: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
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
  moonraker_url: string;
  api_key?: string;
  notes?: string;
  group?: string;
}

export interface PrinterUpdate {
  name?: string;
  moonraker_url?: string;
  api_key?: string;
  notes?: string;
  group?: string;
}

export interface PrintJobRead {
  id: number;
  printer_id: number;
  file_id: number;
  model_id: number;
  remote_filename: string;
  state: PrintJobState;
  progress: number;
  source: string;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface SendToPrinter {
  file_id: number;
  start_print?: boolean;
  remote_filename?: string;
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
