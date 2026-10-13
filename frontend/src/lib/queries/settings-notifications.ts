import type { EditingBase } from "@/types/editing";
import { captureEditingBase } from "@/lib/api/editing";
/** Notification settings own masked server receipts; secret drafts never enter mutation history. */
import { useEffect, useRef } from "react";
import { queryOptions, useQueryClient } from "@tanstack/react-query";
import * as notificationApi from "@/lib/api/notifications";
import { onAuthChange } from "@/lib/auth-store";
import { requireSessionVersion } from "@/lib/session-transport";
import { ApiError, parseApiError } from "@/lib/errors";
import type {
  NotificationChannelCreate,
  NotificationChannelUpdate,
  NotificationsSettings,
} from "@/types";

export type NotificationTransport = typeof notificationApi;
export const notificationKeys = {
  all: ["notification-settings"] as const,
  settings: ["notification-settings", "channels"] as const,
  deliveries: ["notification-settings", "deliveries", 25] as const,
};
export function notificationSettingsOptions(api: NotificationTransport = notificationApi) {
  return queryOptions({
    queryKey: notificationKeys.settings,
    queryFn: ({ signal }) => api.getNotificationsSettings({ signal }),
    retry: false,
  });
}
export function notificationDeliveriesOptions(api: NotificationTransport = notificationApi) {
  return queryOptions({
    queryKey: notificationKeys.deliveries,
    queryFn: ({ signal }) => api.listNotificationDeliveries(25, { signal }),
    retry: false,
  });
}

export function useNotificationCommands(
  canEdit: boolean,
  api: NotificationTransport = notificationApi,
) {
  const client = useQueryClient();
  const live = useRef(true);
  const active = useRef<AbortController | null>(null);
  useEffect(() => {
    live.current = true;
    if (!canEdit) active.current?.abort();
    const release = onAuthChange(() => {
      active.current?.abort();
      active.current = null;
    });
    return () => {
      live.current = false;
      active.current?.abort();
      release();
    };
  }, [canEdit]);
  // All these operations share the same panel lifetime and must not retain their
  // credential-bearing arguments in a MutationCache after returning.
  async function run<T>(
    session: number,
    operation: (signal: AbortSignal, current: () => void) => Promise<T>,
  ): Promise<T> {
    requireSessionVersion(session);
    if (!canEdit || !live.current)
      throw new DOMException("Notification editor retired", "AbortError");
    if (active.current) throw new Error("Notification command already pending");
    const request = new AbortController();
    active.current = request;
    const current = () => {
      requireSessionVersion(session);
      request.signal.throwIfAborted();
    };
    try {
      await client.cancelQueries({ queryKey: notificationKeys.settings });
      current();
      const result = await operation(request.signal, current);
      current();
      return result;
    } catch (error) {
      current();
      if ([401, 403, 404].includes(parseApiError(error).status))
        await client.invalidateQueries({ queryKey: notificationKeys.all });
      throw error;
    } finally {
      if (active.current === request) active.current = null;
    }
  }
  async function publish(
    session: number,
    operation: (signal: AbortSignal) => Promise<NotificationsSettings["channels"][number]>,
    base?: EditingBase,
  ) {
    return run(session, async (signal, current) => {
      const receipt = await operation(signal);
      current();
      await client.cancelQueries({ queryKey: notificationKeys.settings });
      current();
      client.setQueryData<NotificationsSettings>(
        notificationKeys.settings,
        (before) =>
          before && {
            ...before,
            channels: before.channels.some((channel) => channel.id === receipt.id)
              ? before.channels.map((channel) =>
                  channel.id === receipt.id &&
                  !(base && channel.edit_epoch !== base.edit_epoch) &&
                  !(
                    channel.edit_epoch === receipt.edit_epoch &&
                    channel.edit_version > receipt.edit_version
                  )
                    ? receipt
                    : channel,
                )
              : base
                ? before.channels
                : [...before.channels, receipt],
          },
      );
      return receipt;
    });
  }
  return {
    create: (body: NotificationChannelCreate, session: number) =>
      publish(session, (signal) => api.createNotificationChannel(body, { signal })),
    update: (id: number, body: NotificationChannelUpdate, base: EditingBase, session: number) =>
      publish(session, (signal) => api.updateNotificationChannel(id, body, { base, signal }), base),
    enable: (enabled: boolean, base: EditingBase, session: number) =>
      run(session, async (signal, current) => {
        const receipt = await api.setNotificationsEnabled(enabled, { base, signal });
        current();
        await client.cancelQueries({ queryKey: notificationKeys.settings });
        current();
        client.setQueryData<NotificationsSettings>(notificationKeys.settings, (before) =>
          before &&
          before.edit_epoch === base.edit_epoch &&
          before.edit_version <= receipt.edit_version
            ? { ...before, ...captureEditingBase(receipt), enabled: receipt.enabled }
            : before,
        );
        return receipt;
      }),
    review: (session: number, channelId?: number) =>
      run(session, async (signal, current) => {
        const snapshot = await api.getNotificationsSettings({ signal });
        current();
        if (
          channelId !== undefined &&
          !snapshot.channels.some((channel) => channel.id === channelId)
        )
          throw new ApiError(
            404,
            "notification_channel_not_found",
            "notification_channel_not_found",
          );
        return snapshot;
      }),
    adopt: (session: number, channelId?: number) =>
      run(session, async (_signal, current) => {
        // The preview may have aged while the operator compared it. Adoption
        // reads through the canonical Query owner instead of installing that
        // preview over a newer observation (including a restored history).
        const snapshot = await client.fetchQuery({
          ...notificationSettingsOptions(api),
          staleTime: 0,
        });
        current();
        if (
          channelId !== undefined &&
          !snapshot.channels.some((channel) => channel.id === channelId)
        )
          throw new ApiError(
            404,
            "notification_channel_not_found",
            "notification_channel_not_found",
          );
        return snapshot;
      }),
    remove: (id: number, session: number) =>
      run(session, async (signal, current) => {
        await api.deleteNotificationChannel(id, { signal });
        current();
        await client.cancelQueries({ queryKey: notificationKeys.settings });
        current();
        client.setQueryData<NotificationsSettings>(
          notificationKeys.settings,
          (before) =>
            before && { ...before, channels: before.channels.filter((row) => row.id !== id) },
        );
        await client.invalidateQueries({ queryKey: notificationKeys.deliveries });
      }),
    test: (id: number, session: number) =>
      run(session, async (signal, current) => {
        const receipt = await api.testNotificationChannel(id, { signal });
        current();
        await client.invalidateQueries({ queryKey: notificationKeys.all });
        return receipt;
      }),
  };
}
