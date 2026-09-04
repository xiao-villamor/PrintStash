import { getJson, sendAction, sendJson } from "@/lib/api/request";
import type {
  NotificationChannel,
  NotificationChannelCreate,
  NotificationChannelUpdate,
  NotificationDelivery,
  NotificationsSettings,
  NotificationTestResult,
} from "@/types";

export function getNotificationsSettings(): Promise<NotificationsSettings> {
  return getJson<NotificationsSettings>("/api/v1/notifications");
}

export function setNotificationsEnabled(enabled: boolean): Promise<{ enabled: boolean }> {
  return sendJson<{ enabled: boolean }>("/api/v1/notifications", "PUT", {
    enabled,
  });
}

export function createNotificationChannel(
  body: NotificationChannelCreate,
): Promise<NotificationChannel> {
  return sendJson<NotificationChannel>("/api/v1/notifications/channels", "POST", body);
}

export function updateNotificationChannel(
  id: number,
  body: NotificationChannelUpdate,
): Promise<NotificationChannel> {
  return sendJson<NotificationChannel>(`/api/v1/notifications/channels/${id}`, "PATCH", body);
}

export function deleteNotificationChannel(id: number): Promise<void> {
  return sendAction(`/api/v1/notifications/channels/${id}`, "DELETE");
}

export function testNotificationChannel(id: number): Promise<NotificationTestResult> {
  return sendJson<NotificationTestResult>(`/api/v1/notifications/channels/${id}/test`, "POST", {});
}

export function listNotificationDeliveries(limit = 50): Promise<NotificationDelivery[]> {
  return getJson<NotificationDelivery[]>(`/api/v1/notifications/deliveries?limit=${limit}`);
}
