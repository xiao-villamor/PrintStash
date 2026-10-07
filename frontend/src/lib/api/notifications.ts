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
  return getJson<NotificationsSettings>("/api/v1/notifications", { ...options, fresh: true });
}

export function setNotificationsEnabled(
  enabled: boolean,
  options: GetJsonOptions = {},
): Promise<NotificationSwitch> {
  return requestApi<NotificationSwitch>("/api/v1/notifications", {
    method: "PUT",
    headers: jsonHeaders(),
    body: JSON.stringify({ enabled }),
    signal: options.signal,
  });
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

export function updateNotificationChannel(
  id: number,
  body: NotificationChannelUpdate,
  options: GetJsonOptions = {},
): Promise<NotificationChannel> {
  return requestApi<NotificationChannel>(`/api/v1/notifications/channels/${id}`, {
    method: "PATCH",
    headers: jsonHeaders(),
    body: JSON.stringify(body),
    signal: options.signal,
  });
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
    fresh: true,
  });
}
