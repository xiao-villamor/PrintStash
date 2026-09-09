export type NotificationTarget = "webhook" | "discord" | "telegram" | "ntfy";

export type NotificationEvent =
  | "print_completed"
  | "print_failed"
  | "print_cancelled"
  | "printer_offline"
  | "storage_regression"
  | "storage_recovery"
  | "storage_audit_failed"
  | "storage_audit_cancelled"
  | "storage_audit_overdue"
  | "storage_repair_failed"
  | "vault_migration";

export interface NotificationChannel {
  id: number;
  name: string;
  target: NotificationTarget;
  enabled: boolean;
  /** Non-secret config returned as-is; secret values appear as "********". */
  config: Record<string, string>;
  /** has_<secretKey> booleans, e.g. { has_url: true }. */
  config_flags: Record<string, boolean>;
  events: NotificationEvent[];
  /** null = all printers. */
  printer_ids: number[] | null;
  last_status: string | null;
  last_error: string | null;
  last_delivered_at: string | null;
  /** Consecutive permanently-failed deliveries; channel auto-disables past a threshold. */
  consecutive_failures: number;
}

export interface NotificationsSettings {
  enabled: boolean;
  channels: NotificationChannel[];
}

export interface NotificationChannelCreate {
  name: string;
  target: NotificationTarget;
  config: Record<string, string>;
  events: NotificationEvent[];
  printer_ids?: number[] | null;
  enabled?: boolean;
}

export interface NotificationChannelUpdate {
  name?: string;
  config?: Record<string, string>;
  events?: NotificationEvent[];
  printer_ids?: number[] | null;
  enabled?: boolean;
}

export interface NotificationTestResult {
  ok: boolean;
  error: string | null;
}

export interface NotificationDelivery {
  id: number;
  channel_id: number;
  event_type: NotificationEvent;
  printer_id: number | null;
  status: "pending" | "sent" | "failed";
  attempts: number;
  last_error: string | null;
  created_at: string | null;
  delivered_at: string | null;
}
