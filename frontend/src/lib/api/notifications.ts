import type { EditingBase } from "@/types/editing";
import { editHeaders, requireEditingBase, requireEditingReceipt } from "@/lib/api/editing";
import { getJson, requestApi, jsonHeaders, type GetJsonOptions } from "@/lib/api/request";
import type {
  NotificationChannel,
  NotificationChannelCreate,
  NotificationChannelUpdate,
  NotificationDelivery,
  NotificationsSettings,
  NotificationSwitch,
  NotificationTestResult,
} from "@/types";

export function getNotificationsSettings(
  options: GetJsonOptions = {},
): Promise<NotificationsSettings> {
  return getJson<NotificationsSettings>("/api/v1/notifications", { ...options });
}

export async function setNotificationsEnabled(
  enabled: boolean,
  options: { base: EditingBase; signal?: AbortSignal },
): Promise<NotificationSwitch> {
  requireEditingBase(options.base);
  const saved = await requestApi<NotificationSwitch>("/api/v1/notifications", {
    method: "PUT",
    headers: {
      ...jsonHeaders(),
      "If-Match": `"notification-settings-e${options.base.edit_epoch}-v${options.base.edit_version}"`,
      "X-PrintStash-Edit-Contract": "conditional-v1",
    },
    body: JSON.stringify({ enabled }),
    signal: options.signal,
  });
  requireEditingReceipt(saved, options.base);
  return saved;
}

export function createNotificationChannel(
  body: NotificationChannelCreate,
  options: GetJsonOptions = {},
): Promise<NotificationChannel> {
  return requestApi<NotificationChannel>("/api/v1/notifications/channels", {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify(body),
    signal: options.signal,
  });
}

export async function updateNotificationChannel(
  id: number,
  body: NotificationChannelUpdate,
  options: { base: EditingBase; signal?: AbortSignal },
): Promise<NotificationChannel> {
  const saved = await requestApi<NotificationChannel>(`/api/v1/notifications/channels/${id}`, {
    method: "PATCH",
    headers: { ...jsonHeaders(), ...editHeaders("notification-channel", id, options.base) },
    body: JSON.stringify(body),
    signal: options.signal,
  });
  requireEditingReceipt(saved, options.base);
  if (saved.id !== id) throw new Error("notification_identity_mismatch");
  return saved;
}

export function deleteNotificationChannel(id: number, options: GetJsonOptions = {}): Promise<void> {
  return requestApi<void>(`/api/v1/notifications/channels/${id}`, {
    method: "DELETE",
    signal: options.signal,
  });
}

export function testNotificationChannel(
  id: number,
  options: GetJsonOptions = {},
): Promise<NotificationTestResult> {
  return requestApi<NotificationTestResult>(`/api/v1/notifications/channels/${id}/test`, {
    method: "POST",
    headers: jsonHeaders(),
    body: "{}",
    signal: options.signal,
  });
}

export function listNotificationDeliveries(
  limit = 50,
  options: GetJsonOptions = {},
): Promise<NotificationDelivery[]> {
  return getJson<NotificationDelivery[]>(`/api/v1/notifications/deliveries?limit=${limit}`, {
    ...options,
  });
}
