export interface SpoolmanStatus {
  enabled: boolean;
  base_url: string | null;
  has_api_key: boolean;
  write_enabled: boolean;
  /** Write back even when Spoolman reports an active spool (native-hook override). */
  write_force: boolean;
  connected: boolean;
  version: string | null;
  error: string | null;
  /** Moonraker's native Spoolman hook is already decrementing the active spool. */
  native_hook_detected: boolean;
}

export interface SpoolmanUpdate {
  enabled?: boolean;
  base_url?: string | null;
  api_key?: string | null;
  write_enabled?: boolean;
  write_force?: boolean;
}

export interface SpoolmanTestResult {
  connected: boolean;
  version: string | null;
  error: string | null;
  native_hook_detected: boolean;
}

export interface SpoolRead {
  id: number;
  filament_id: number | null;
  name: string | null;
  filament_name: string | null;
  vendor_name: string | null;
  material: string | null;
  color_hex: string | null;
  remaining_weight: number | null;
  used_weight: number | null;
  archived: boolean;
  location: string | null;
}
