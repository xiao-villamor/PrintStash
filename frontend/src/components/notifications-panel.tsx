"use client";

import { SettingsCard, SettingsEmptyRow, SettingsSwitch } from "@/components/settings-card";
import { SETTINGS_FIELD } from "@/components/settings-controls";
import { cn } from "@/lib/utils";
import { currentLocale } from "@/lib/locale";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { Loader2, Send, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import {
  createNotificationChannel,
  deleteNotificationChannel,
  getNotificationsSettings,
  listNotificationDeliveries,
  listPrinters,
  setNotificationsEnabled,
  testNotificationChannel,
  updateNotificationChannel,
} from "@/lib/api";
import type {
  NotificationChannel,
  NotificationEvent,
  NotificationTarget,
  PrinterRead,
} from "@/types";
import { toast } from "@/lib/toast";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Modal } from "@/components/ui/modal";
import { Localized } from "@/components/ui/localized";

import { useQuery } from "@tanstack/react-query";
import { printersOptions } from "@/lib/queries";
import {
  notificationSettingsOptions,
  notificationDeliveriesOptions,
  useNotificationCommands,
} from "@/lib/queries/settings-notifications";
import { getSessionVersion } from "@/lib/session-transport";
import { onAuthChange } from "@/lib/auth-store";
import { ApiError, parseApiError, userMessage } from "@/lib/errors";
import { captureEditingBase } from "@/lib/api/editing";
import type { EditingBase } from "@/types/editing";
import type { NotificationsSettings, NotificationChannelUpdate } from "@/types";

const INPUT = SETTINGS_FIELD;
const LABEL = "mb-1 block text-sm font-medium";

const TARGETS: { value: NotificationTarget; label: string }[] = [
  {
    value: "webhook",
    get label() {
      return uiText("Webhook");
    },
  },
  {
    value: "discord",
    get label() {
      return uiText("Discord");
    },
  },
  {
    value: "telegram",
    get label() {
      return uiText("Telegram");
    },
  },
  {
    value: "ntfy",
    get label() {
      return uiText("ntfy");
    },
  },
];

/** Decode a `<select>` value back into a target, ignoring anything not offered. */
function parseNotificationTarget(value: string): NotificationTarget | null {
  return TARGETS.find((target) => target.value === value)?.value ?? null;
}

const EVENTS: { value: NotificationEvent; label: string }[] = [
  {
    value: "print_completed",
    get label() {
      return uiText("Print completed");
    },
  },
  {
    value: "print_failed",
    get label() {
      return uiText("Print failed");
    },
  },
  {
    value: "print_cancelled",
    get label() {
      return uiText("Print cancelled");
    },
  },
  {
    value: "printer_offline",
    get label() {
      return uiText("Printer offline");
    },
  },
  {
    value: "storage_regression",
    get label() {
      return uiText("Vault audit regression");
    },
  },
  {
    value: "storage_recovery",
    get label() {
      return uiText("Vault audit recovery");
    },
  },
  {
    value: "storage_audit_failed",
    get label() {
      return uiText("Scheduled Vault audit failed");
    },
  },
  {
    value: "storage_audit_cancelled",
    get label() {
      return uiText("Scheduled Vault audit cancelled");
    },
  },
  {
    value: "storage_audit_overdue",
    get label() {
      return uiText("Scheduled Vault audit overdue");
    },
  },
  {
    value: "storage_repair_failed",
    get label() {
      return uiText("Vault repair failed");
    },
  },
  {
    value: "vault_migration",
    get label() {
      return uiText("Vault migration");
    },
  },
];

/** One editable entry of a channel's `config` map. */
interface TargetField {
  key: string;
  label: string;
  placeholder: string;
  secret?: boolean;
  optional?: boolean;
}

// Config fields rendered per target. `secret` fields are masked on read; an
// existing value survives an edit when left blank. `satisfies` keeps the check
// that every target has a form while leaving each entry's own shape intact.
const TARGET_FIELDS = {
  webhook: [
    {
      key: "url",
      get label() {
        return uiText("Webhook URL");
      },
      placeholder: "https://example.com/hook",
      secret: true,
    },
    {
      key: "secret",
      get label() {
        return uiText("Signing secret (optional)");
      },
      get placeholder() {
        return uiText("notifications.signingSecretPlaceholder");
      },
      secret: true,
      optional: true,
    },
  ],
  discord: [
    {
      key: "url",
      get label() {
        return uiText("Discord webhook URL");
      },
      placeholder: "https://discord.com/api/webhooks/…",
      secret: true,
    },
  ],
  telegram: [
    {
      key: "bot_token",
      get label() {
        return uiText("Bot token");
      },
      placeholder: "123456:ABC-DEF…",
      secret: true,
    },
    {
      key: "chat_id",
      get label() {
        return uiText("Chat ID");
      },
      placeholder: "-1001234567890",
    },
  ],
  ntfy: [
    {
      key: "server_url",
      get label() {
        return uiText("Server URL");
      },
      placeholder: "https://ntfy.sh",
    },
    {
      key: "topic",
      get label() {
        return uiText("Topic");
      },
      placeholder: "my-printer-alerts",
    },
    {
      key: "token",
      get label() {
        return uiText("Access token (optional)");
      },
      placeholder: "tk_…",
      secret: true,
      optional: true,
    },
  ],
} satisfies Record<NotificationTarget, TargetField[]>;

interface DraftFields {
  name: string;
  target: NotificationTarget;
  config: Record<string, string>;
  events: NotificationEvent[];
  printerIds: number[] | null; // null => all printers
  enabled: boolean;
}

type DraftState = DraftFields &
  ({ id: null; base: null } | { id: number; base: NotificationChannel });
type ChannelReview =
  | { phase: "idle" }
  | { phase: "required" }
  | { phase: "ready"; snapshot: NotificationsSettings };
type SwitchReview = {
  intent: { base: EditingBase; enabled: boolean };
  snapshot: NotificationsSettings | null;
};

function draftFromChannel(channel: NotificationChannel): DraftState {
  return {
    id: channel.id,
    base: channel,
    name: channel.name,
    target: channel.target,
    config: Object.fromEntries(
      Object.entries(channel.config).filter(([, value]) => value !== "********"),
    ),
    events: channel.events,
    printerIds: channel.printer_ids?.length ? channel.printer_ids : null,
    enabled: channel.enabled,
  };
}

function channelChanges(draft: DraftFields, base: NotificationChannel): NotificationChannelUpdate {
  const changes: NotificationChannelUpdate = {};
  if (draft.name.trim() !== base.name) changes.name = draft.name.trim();
  if (draft.enabled !== base.enabled) changes.enabled = draft.enabled;
  if (JSON.stringify(draft.events) !== JSON.stringify(base.events)) changes.events = draft.events;
  const originalScope = base.printer_ids?.length ? base.printer_ids : null;
  if (JSON.stringify(draft.printerIds) !== JSON.stringify(originalScope))
    changes.printer_ids = draft.printerIds;
  const fields: TargetField[] = TARGET_FIELDS[draft.target];
  const config = Object.fromEntries(
    Object.entries(draft.config).filter(
      ([key, value]) =>
        value !== base.config[key] &&
        value !== "********" &&
        !(fields.some((field) => field.key === key && field.secret) && value === ""),
    ),
  );
  if (Object.keys(config).length) changes.config = config;
  return changes;
}

function emptyDraft(): DraftState {
  return {
    id: null,
    base: null,
    name: "",
    target: "webhook",
    config: {},
    events: ["print_completed", "print_failed"],
    printerIds: null,
    enabled: true,
  };
}

/** Delivery status as aligned text: its wording plus the tone that marks attention. */
interface StatusBadge {
  text: string;
  tone: "danger" | "warning" | undefined;
}

function statusBadge(status: string | null): StatusBadge {
  if (status === "sent") return { text: "Delivered", tone: undefined };
  if (status === "failed") return { text: uiText("Failed"), tone: "danger" };
  if (status === "pending") return { text: "Pending", tone: "warning" };
  return { text: "—", tone: undefined };
}

/**
 * Everything the panel reaches outside itself: the notification endpoints and the
 * toast surface. Injectable so a test can drive the panel with fakes instead of
 * replacing the modules underneath it.
 */
export interface NotificationsPanelDeps {
  getNotificationsSettings: typeof getNotificationsSettings;
  setNotificationsEnabled: typeof setNotificationsEnabled;
  createNotificationChannel: typeof createNotificationChannel;
  updateNotificationChannel: typeof updateNotificationChannel;
  deleteNotificationChannel: typeof deleteNotificationChannel;
  testNotificationChannel: typeof testNotificationChannel;
  listNotificationDeliveries: typeof listNotificationDeliveries;
  listPrinters: typeof listPrinters;
  toast: Pick<typeof toast, "error" | "success" | "warning">;
}

const LIVE_DEPS: NotificationsPanelDeps = {
  getNotificationsSettings,
  setNotificationsEnabled,
  createNotificationChannel,
  updateNotificationChannel,
  deleteNotificationChannel,
  testNotificationChannel,
  listNotificationDeliveries,
  listPrinters,
  toast,
};

export function NotificationsPanel({
  canEdit,
  deps = LIVE_DEPS,
}: {
  canEdit: boolean;
  deps?: NotificationsPanelDeps;
}) {
  useUiLocale();
  const channelFormId = useId();
  const masterDescriptionId = useId();
  const [draft, setDraft] = useState<DraftState | null>(null);
  const [busy, setBusy] = useState<number | "save" | "switch" | "review" | null>(null);
  const [channelReview, setChannelReview] = useState<ChannelReview>({ phase: "idle" });
  const [switchReview, setSwitchReview] = useState<SwitchReview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const settingsQuery = useQuery({ ...notificationSettingsOptions(deps), enabled: canEdit });
  const deliveriesQuery = useQuery({ ...notificationDeliveriesOptions(deps), enabled: canEdit });
  const printersQuery = useQuery({
    ...printersOptions(deps),
    enabled: canEdit && draft !== null,
    retry: false,
  });
  const commands = useNotificationCommands(canEdit, deps);
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    const release = onAuthChange(() => {
      setDraft(null);
      setBusy(null);
      setChannelReview({ phase: "idle" });
      setSwitchReview(null);
      setError(null);
    });
    return () => {
      live.current = false;
      release();
    };
  }, []);
  const current = (session: number) => live.current && session === getSessionVersion();

  const reviewedChannel =
    channelReview.phase === "ready"
      ? channelReview.snapshot.channels.find((channel) => channel.id === draft?.id)
      : null;
  const channelReplaced = Boolean(
    reviewedChannel && draft?.base && reviewedChannel.edit_epoch !== draft.base.edit_epoch,
  );
  const switchReplaced = Boolean(
    switchReview?.snapshot &&
    switchReview.snapshot.edit_epoch !== switchReview.intent.base.edit_epoch,
  );

  function isDenied(cause: unknown): boolean {
    return [401, 403, 404].includes(parseApiError(cause).status);
  }
  function needsReview(cause: unknown): boolean {
    const status = parseApiError(cause).status;
    return status === 0 || status === 409 || status === 412 || status === 428 || status >= 500;
  }
  function retireEditor() {
    setDraft(null);
    setChannelReview({ phase: "idle" });
    setSwitchReview(null);
  }
  async function toggleEnabled(next: boolean, revised = false) {
    const snapshot = revised ? switchReview?.snapshot : settingsQuery.data;
    if (!snapshot || busy !== null || (revised ? switchReplaced : switchReview !== null)) return;
    const intent =
      revised && switchReview
        ? switchReview.intent
        : { enabled: next, base: captureEditingBase(snapshot) };
    const session = getSessionVersion();
    setBusy("switch");
    setError(null);
    try {
      await commands.enable(intent.enabled, captureEditingBase(snapshot), session);
      if (current(session)) setSwitchReview(null);
    } catch (cause) {
      if (current(session)) {
        setError(userMessage(cause));
        if (isDenied(cause)) retireEditor();
        else if (needsReview(cause)) setSwitchReview({ intent, snapshot: null });
      }
    } finally {
      if (current(session)) setBusy(null);
    }
  }
  const startEdit = useCallback((channel: NotificationChannel) => {
    setDraft(draftFromChannel(channel));
    setChannelReview({ phase: "idle" });
    setError(null);
  }, []);

  async function reviewChannel() {
    if (!draft || draft.id === null || busy !== null) return;
    const session = getSessionVersion();
    setBusy("review");
    setError(null);
    setChannelReview({ phase: "required" });
    try {
      const snapshot = await commands.review(session, draft.id);
      if (current(session)) setChannelReview({ phase: "ready", snapshot });
    } catch (cause) {
      if (current(session)) {
        setError(userMessage(cause));
        if (isDenied(cause)) retireEditor();
      }
    } finally {
      if (current(session)) setBusy(null);
    }
  }
  async function reviewSwitch() {
    if (!switchReview || busy !== null) return;
    const session = getSessionVersion();
    setBusy("review");
    setError(null);
    setSwitchReview({ ...switchReview, snapshot: null });
    try {
      const snapshot = await commands.review(session);
      if (current(session)) setSwitchReview({ intent: switchReview.intent, snapshot });
    } catch (cause) {
      if (current(session)) {
        setError(userMessage(cause));
        if (isDenied(cause)) retireEditor();
      }
    } finally {
      if (current(session)) setBusy(null);
    }
  }

  async function adoptCurrent(target: number | "switch") {
    if (busy !== null) return;
    const session = getSessionVersion();
    setBusy("review");
    setError(null);
    try {
      const snapshot = await commands.adopt(session, target === "switch" ? undefined : target);
      if (!current(session)) return;
      if (target === "switch") setSwitchReview(null);
      else {
        const channel = snapshot.channels.find((row) => row.id === target);
        if (!channel)
          throw new ApiError(
            404,
            "notification_channel_not_found",
            "notification_channel_not_found",
          );
        startEdit(channel);
      }
    } catch (cause) {
      if (current(session)) {
        setError(userMessage(cause));
        if (isDenied(cause)) retireEditor();
      }
    } finally {
      if (current(session)) setBusy(null);
    }
  }

  const channelFormDisabled = busy !== null || Boolean(settingsQuery.error);
  const invalidChannelScope =
    draft?.printerIds != null &&
    (!printersQuery.data || printersQuery.isError || draft.printerIds.length === 0);

  function closeChannelForm() {
    if (busy === "save") return;
    setDraft(null);
    setChannelReview({ phase: "idle" });
    setError(null);
  }

  async function saveDraft(revised = false) {
    if (!draft || busy !== null || (draft.printerIds !== null && draft.printerIds.length === 0))
      return;
    if (
      draft.id !== null &&
      (revised ? !reviewedChannel || channelReplaced : channelReview.phase !== "idle")
    )
      return;
    if (!draft.name.trim()) {
      deps.toast.error(uiText("Channel name is required."));
      return;
    }
    if (draft.events.length === 0) {
      deps.toast.error(uiText("Select at least one event."));
      return;
    }
    const session = getSessionVersion();
    setBusy("save");
    setError(null);
    try {
      const body = {
        name: draft.name.trim(),
        config: draft.config,
        events: draft.events,
        printer_ids: draft.printerIds,
        enabled: draft.enabled,
      };
      if (draft.id === null) {
        await commands.create({ ...body, target: draft.target }, session);
        if (current(session)) deps.toast.success(uiText("Channel created."));
      } else {
        await commands.update(
          draft.id,
          channelChanges(draft, draft.base),
          captureEditingBase(revised && reviewedChannel ? reviewedChannel : draft.base),
          session,
        );
        if (current(session)) deps.toast.success(uiText("Channel updated."));
      }
      if (current(session)) {
        setDraft(null);
        setChannelReview({ phase: "idle" });
      }
    } catch (cause) {
      if (current(session)) {
        if (draft.id === null) deps.toast.error(cause);
        else {
          setError(userMessage(cause));
          if (isDenied(cause)) retireEditor();
          else if (needsReview(cause)) setChannelReview({ phase: "required" });
        }
      }
    } finally {
      if (current(session)) setBusy(null);
    }
  }

  async function removeChannel(id: number) {
    const session = getSessionVersion();
    setBusy(id);
    try {
      await commands.remove(id, session);
      if (current(session) && draft?.id === id) setDraft(null);
    } catch (error) {
      if (current(session)) deps.toast.error(error);
    } finally {
      if (current(session)) setBusy(null);
    }
  }

  async function sendTest(id: number) {
    const session = getSessionVersion();
    setBusy(id);
    try {
      const result = await commands.test(id, session);
      if (current(session)) {
        if (result.ok) deps.toast.success(uiText("Test notification sent."));
        else deps.toast.warning(uiText("Test failed"), result.error ?? undefined);
      }
    } catch (error) {
      if (current(session)) deps.toast.error(error);
    } finally {
      if (current(session)) setBusy(null);
    }
  }

  if (!canEdit) return <p>{uiText("Only an administrator can manage notification channels.")}</p>;
  const settingsFailure = settingsQuery.error && (
    <div role="alert">
      <p>{uiText("Notification settings could not be loaded.")}</p>
      <Button size="sm" onClick={() => void settingsQuery.refetch()}>
        {uiText("Retry notification settings")}
      </Button>
    </div>
  );
  const denied =
    settingsQuery.error && [401, 403, 404].includes(parseApiError(settingsQuery.error).status);
  if (denied || !settingsQuery.data)
    return settingsFailure || <p role="status">{uiText("Loading…")}</p>;
  const { enabled, channels } = settingsQuery.data;
  const deliveries = deliveriesQuery.data;

  return (
    <Localized>
      <div className="settings-notifications">
        {settingsFailure}
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        <fieldset
          className="min-w-0 space-y-6"
          disabled={busy !== null || Boolean(settingsQuery.error)}
        >
          <SettingsCard hideHeader title={uiText("Notifications")}>
            <div className="settings-preference-row">
              <span>
                <span className="block">{uiText("Notifications")}</span>
                <span
                  id={masterDescriptionId}
                  className="mt-1 block text-sm font-normal text-muted-foreground"
                >
                  {uiText("Turn every channel on or off at once. Channels keep their settings.")}
                </span>
              </span>
              <SettingsSwitch
                checked={enabled}
                onChange={toggleEnabled}
                label={uiText("Notifications")}
                descriptionId={masterDescriptionId}
                disabled={!canEdit || busy === "switch" || switchReview !== null}
              />
            </div>
          </SettingsCard>

          {switchReview && (
            <div role="status" className="space-y-2 text-sm">
              <p>{uiText("Review the current notification setting before trying again.")}</p>
              <p>
                {uiText("Requested")}: {switchReview.intent.enabled ? uiText("On") : uiText("Off")}
              </p>
              {switchReview.snapshot && (
                <p>
                  {uiText("Current")}:{" "}
                  {switchReview.snapshot.enabled ? uiText("On") : uiText("Off")}
                </p>
              )}
              {switchReplaced && (
                <p>
                  {uiText("The database history changed. Use current values before trying again.")}
                </p>
              )}
              <div className="flex flex-wrap gap-2">
                <Button variant="outline" size="sm" onClick={() => void reviewSwitch()}>
                  {uiText("Review current values")}
                </Button>
                {switchReview.snapshot && (
                  <>
                    <Button variant="outline" size="sm" onClick={() => void adoptCurrent("switch")}>
                      {uiText("Use current values")}
                    </Button>
                    <Button
                      size="sm"
                      disabled={switchReplaced}
                      onClick={() => void toggleEnabled(switchReview.intent.enabled, true)}
                    >
                      {uiText("Save revised changes")}
                    </Button>
                  </>
                )}
              </div>
            </div>
          )}

          {!canEdit && (
            <p className="text-xs text-muted-foreground italic">
              {uiText("Only an administrator can manage notification channels.")}
            </p>
          )}

          {/* Channel list */}
          {canEdit && (
            <SettingsCard
              title={uiText("Channels")}
              description={uiText(
                "Send webhook, Discord, Telegram, or ntfy alerts on print and printer events.",
              )}
              action={
                canEdit ? (
                  <Button size="sm" onClick={() => setDraft(emptyDraft())}>
                    {uiText("Add channel")}
                  </Button>
                ) : undefined
              }
            >
              {channels.length === 0 && (
                <div className={cn(!enabled && "opacity-50")}>
                  <SettingsEmptyRow>
                    {uiText("No notification channels yet")}
                    {". "}
                    {uiText("Add a channel to start receiving print and printer alerts.")}
                  </SettingsEmptyRow>
                </div>
              )}
              {channels.map((ch) => {
                const badge = statusBadge(ch.last_status);
                return (
                  <div key={ch.id} className="settings-list-row">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="settings-list-title wrap-anywhere">{ch.name}</span>
                        <Badge variant="secondary">{ch.target}</Badge>
                        {!ch.enabled &&
                          (ch.consecutive_failures > 0 ? (
                            <span
                              className="settings-status"
                              data-tone="warning"
                              title={ch.last_error ?? undefined}
                            >
                              {uiText("Auto-disabled")}
                            </span>
                          ) : (
                            <span className="settings-status">{uiText("disabled")}</span>
                          ))}
                      </div>
                      <p className="settings-list-meta">
                        {ch.events
                          .map((e) => EVENTS.find((x) => x.value === e)?.label ?? e)
                          .join(", ")}
                        {ch.printer_ids?.length
                          ? uiText(" · {value1} printer(s)", {
                              value1: String(ch.printer_ids.length),
                            })
                          : uiText(" · all printers")}
                      </p>
                    </div>
                    <div>
                      <span
                        className="settings-status"
                        data-tone={badge.tone}
                        title={ch.last_error ?? undefined}
                      >
                        {badge.text}
                      </span>
                      <Button
                        size="sm"
                        variant="outline"
                        title={uiText("Edit channel")}
                        onClick={() => startEdit(ch)}
                      >
                        {uiText("Edit channel")}
                      </Button>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon-sm"
                        disabled={busy === ch.id}
                        onClick={() => void sendTest(ch.id)}
                        title={uiText("Send a test notification")}
                        aria-label={uiText("Send a test notification")}
                      >
                        {busy === ch.id ? (
                          <Loader2 className="size-4 animate-spin" aria-hidden />
                        ) : (
                          <Send className="size-4" aria-hidden />
                        )}
                      </Button>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon-sm"
                        className="text-destructive"
                        disabled={busy === ch.id}
                        onClick={() => void removeChannel(ch.id)}
                        title={uiText("Delete channel")}
                        aria-label={uiText("Delete channel")}
                      >
                        <Trash2 className="size-4" aria-hidden />
                      </Button>
                    </div>
                  </div>
                );
              })}

              {/* Draft form */}
              {draft ? (
                <Modal
                  open
                  title={uiText(draft.id ? "Edit channel" : "Add channel")}
                  onClose={closeChannelForm}
                  dismissible={busy !== "save"}
                  className="settings-dialog max-w-3xl"
                  footer={
                    <>
                      <Button
                        type="button"
                        variant="outline"
                        disabled={channelFormDisabled}
                        onClick={closeChannelForm}
                      >
                        {uiText("Cancel")}
                      </Button>
                      <Button
                        type="submit"
                        form={channelFormId}
                        loading={busy === "save"}
                        disabled={
                          channelFormDisabled ||
                          channelReview.phase !== "idle" ||
                          invalidChannelScope
                        }
                      >
                        {uiText(draft.id === null ? "Create channel" : "Save changes")}
                      </Button>
                    </>
                  }
                >
                  <fieldset disabled={channelFormDisabled} className="space-y-4">
                    {error && (
                      <p role="alert" className="text-sm text-destructive">
                        {error}
                      </p>
                    )}
                    <ChannelForm
                      formId={channelFormId}
                      draft={draft}
                      setDraft={setDraft}
                      printers={printersQuery.data}
                      printersError={printersQuery.error}
                      retryPrinters={() => void printersQuery.refetch()}
                      onSave={() => {
                        if (!channelFormDisabled && !invalidChannelScope) void saveDraft();
                      }}
                    />
                    {channelReview.phase !== "idle" && (
                      <div role="status" className="space-y-2 text-sm">
                        <p>{uiText("Review the current channel before saving again.")}</p>
                        {reviewedChannel && <ChannelSnapshot channel={reviewedChannel} />}
                        {channelReplaced && (
                          <p>
                            {uiText(
                              "The channel was replaced. Use current values before editing it.",
                            )}
                          </p>
                        )}
                        <div className="flex flex-wrap gap-2">
                          <Button variant="outline" size="sm" onClick={() => void reviewChannel()}>
                            {uiText("Review current values")}
                          </Button>
                          {reviewedChannel && channelReview.phase === "ready" && (
                            <>
                              <Button
                                variant="outline"
                                size="sm"
                                onClick={() => void adoptCurrent(reviewedChannel.id)}
                              >
                                {uiText("Use current values")}
                              </Button>
                              <Button
                                size="sm"
                                disabled={
                                  channelReplaced ||
                                  (draft.printerIds !== null && draft.printerIds.length === 0)
                                }
                                onClick={() => void saveDraft(true)}
                              >
                                {uiText("Save revised changes")}
                              </Button>
                            </>
                          )}
                        </div>
                      </div>
                    )}
                  </fieldset>
                </Modal>
              ) : null}
            </SettingsCard>
          )}

          {/* Recent deliveries */}
        </fieldset>
        {deliveriesQuery.error && (
          <div role="alert">
            <p>{uiText("Notification delivery history could not be loaded.")}</p>
            <Button size="sm" onClick={() => void deliveriesQuery.refetch()}>
              {uiText("Retry delivery history")}
            </Button>
          </div>
        )}
        {deliveriesQuery.isPending && <p role="status">{uiText("Loading delivery history…")}</p>}
        {canEdit && !deliveriesQuery.error && deliveries && (
          <SettingsCard
            title={uiText("Recent deliveries")}
            description={uiText("The last delivery attempts and the response from each channel.")}
          >
            {deliveries.length === 0 && (
              <SettingsEmptyRow>{uiText("No notification deliveries yet.")}</SettingsEmptyRow>
            )}
            <div className="divide-y divide-border">
              {deliveries.map((d) => {
                const badge = statusBadge(d.status);
                return (
                  <div key={d.id} className="settings-list-row text-sm">
                    <span className="text-muted-foreground wrap-anywhere">
                      {EVENTS.find((e) => e.value === d.event_type)?.label ?? d.event_type}
                    </span>
                    <div>
                      {d.attempts > 1 && (
                        <span className="text-muted-foreground">×{d.attempts}</span>
                      )}
                      <span className="text-muted-foreground">
                        {d.created_at ? new Date(d.created_at).toLocaleString(currentLocale()) : ""}
                      </span>
                      <span
                        className="settings-status"
                        data-tone={badge.tone}
                        title={d.last_error ?? undefined}
                      >
                        {badge.text}
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          </SettingsCard>
        )}
      </div>
    </Localized>
  );
}

function ChannelForm({
  formId,
  draft,
  setDraft,
  printers,
  onSave,
  printersError,
  retryPrinters,
}: {
  formId: string;
  draft: DraftState;
  setDraft: (d: DraftState) => void;
  printers: PrinterRead[] | undefined;
  printersError: Error | null;
  retryPrinters: () => void;
  onSave: () => void;
}) {
  useUiLocale();
  const fields: TargetField[] = TARGET_FIELDS[draft.target];
  const scoped = draft.printerIds !== null;

  return (
    <Localized>
      <form
        id={formId}
        className="space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          onSave();
        }}
      >
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <label className={LABEL} htmlFor={formId + "-name"}>
              {uiText("Name")}
            </label>
            <input
              id={formId + "-name"}
              value={draft.name}
              onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              placeholder={uiText("Living-room printer alerts")}
              className={INPUT}
            />
          </div>
          <div>
            <label className={LABEL} htmlFor={formId + "-type"}>
              {uiText("Type")}
            </label>
            <select
              id={formId + "-type"}
              value={draft.target}
              disabled={draft.id !== null}
              onChange={(e) => {
                const target = parseNotificationTarget(e.target.value);
                if (target) setDraft({ ...draft, target, config: {} });
              }}
              className={`${INPUT} disabled:opacity-60`}
            >
              {TARGETS.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
          </div>
        </div>

        {fields.map((f) => (
          <div key={f.key}>
            <label className={LABEL} htmlFor={formId + "-config-" + f.key}>
              {f.label}
            </label>
            <input
              id={formId + "-config-" + f.key}
              type={f.secret ? "password" : "text"}
              value={draft.config[f.key] ?? ""}
              onChange={(e) =>
                setDraft({ ...draft, config: { ...draft.config, [f.key]: e.target.value } })
              }
              placeholder={
                draft.id !== null && f.secret ? uiText("•••••••• (unchanged)") : f.placeholder
              }
              className={INPUT}
              autoComplete="off"
            />
          </div>
        ))}

        <fieldset>
          <legend className={LABEL}>{uiText("Events")}</legend>
          <div className="flex flex-wrap gap-3">
            {EVENTS.map((ev) => (
              <label
                key={ev.value}
                className="inline-flex items-center gap-1.5 text-sm text-foreground"
              >
                <Checkbox
                  checked={draft.events.includes(ev.value)}
                  onChange={(checked) =>
                    setDraft({
                      ...draft,
                      events: checked
                        ? [...draft.events, ev.value]
                        : draft.events.filter((x) => x !== ev.value),
                    })
                  }
                  className="shrink-0"
                />
                {ev.label}
              </label>
            ))}
          </div>
        </fieldset>

        <fieldset>
          <legend className={LABEL}>{uiText("Printers")}</legend>
          <label className="inline-flex items-center gap-1.5 text-sm text-foreground mb-2">
            <Checkbox
              checked={!scoped}
              onChange={(checked) => setDraft({ ...draft, printerIds: checked ? null : [] })}
              className="shrink-0"
            />
            {uiText("All printers")}
          </label>
          {scoped && draft.printerIds?.length === 0 && (
            <p className="text-sm text-muted-foreground">
              {uiText("Select at least one printer, or choose All printers.")}
            </p>
          )}
          {scoped && (
            <div className="flex flex-wrap gap-3">
              {printersError ? (
                <div role="alert">
                  <p>{uiText("Printer choices could not be loaded.")}</p>
                  <Button size="sm" type="button" onClick={retryPrinters}>
                    {uiText("Retry printer choices")}
                  </Button>
                </div>
              ) : !printers ? (
                <p role="status">{uiText("Loading printer choices…")}</p>
              ) : null}
              {!printersError && printers?.length === 0 && (
                <span className="text-2xs text-muted-foreground italic">
                  {uiText("No printers configured.")}
                </span>
              )}
              {!printersError &&
                printers?.map((p) => (
                  <label
                    key={p.id}
                    className="inline-flex items-center gap-1.5 text-sm text-foreground"
                  >
                    <Checkbox
                      checked={(draft.printerIds ?? []).includes(p.id)}
                      onChange={(checked) =>
                        setDraft({
                          ...draft,
                          printerIds: checked
                            ? [...(draft.printerIds ?? []), p.id]
                            : (draft.printerIds ?? []).filter((x) => x !== p.id),
                        })
                      }
                      className="shrink-0"
                    />
                    {p.name}
                  </label>
                ))}
            </div>
          )}
        </fieldset>

        <div className="flex items-center justify-between gap-3 text-sm text-foreground">
          <span>{uiText("Enabled")}</span>
          <SettingsSwitch
            label={uiText("Enabled")}
            checked={draft.enabled}
            onChange={(checked) => setDraft({ ...draft, enabled: checked })}
          />
        </div>
      </form>
    </Localized>
  );
}

function ChannelSnapshot({ channel }: { channel: NotificationChannel }) {
  const fields: TargetField[] = TARGET_FIELDS[channel.target];
  return (
    <div className="space-y-1 rounded border border-border p-3">
      <p>
        {uiText("Current channel")}: {channel.name}
      </p>
      <p>
        {channel.target} · {channel.enabled ? uiText("On") : uiText("Off")}
      </p>
      <p>
        {channel.events
          .map((event) => EVENTS.find((entry) => entry.value === event)?.label ?? event)
          .join(", ")}
      </p>
      <p>
        {uiText("Printers")}:{" "}
        {channel.printer_ids?.length ? channel.printer_ids.join(", ") : uiText("All printers")}
      </p>
      {fields.map((field) => (
        <p key={field.key}>
          {field.label}: {channel.config[field.key] || "—"}
        </p>
      ))}
    </div>
  );
}
