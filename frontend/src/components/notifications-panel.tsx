"use client";

import { currentLocale } from "@/lib/locale";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

import { useCallback, useEffect, useRef, useState } from "react";
import { Bell, Loader2, Plus, Send, Trash2, Pencil } from "lucide-react";
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
import { Button, buttonVariants } from "@/components/ui/button";
import { inputClasses } from "@/components/ui/input";
import { cn } from "@/lib/utils";
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
import { parseApiError } from "@/lib/errors";

const CARD = "overflow-hidden rounded-lg border border-border bg-card shadow-sm";
const INPUT = cn(inputClasses, "h-auto px-2.5 py-1.5 rounded placeholder:text-muted-foreground/40");
const BTN_PRIMARY = cn(buttonVariants({ size: "xs" }), "font-mono uppercase tracking-wider");
const BTN_SECONDARY = cn(
  buttonVariants({ variant: "outline", size: "xs" }),
  "font-mono uppercase tracking-wider text-muted-foreground",
);
const LABEL = "block text-2xs text-muted-foreground mb-1";

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

interface DraftState {
  id: number | null; // null => creating
  name: string;
  target: NotificationTarget;
  config: Record<string, string>;
  events: NotificationEvent[];
  printerIds: number[] | null; // null => all printers
  enabled: boolean;
}

function emptyDraft(): DraftState {
  return {
    id: null,
    name: "",
    target: "webhook",
    config: {},
    events: ["print_completed", "print_failed"],
    printerIds: null,
    enabled: true,
  };
}

/** Delivery-status chip: its wording plus the token classes that colour it. */
interface StatusBadge {
  text: string;
  cls: string;
}

function statusBadge(status: string | null): StatusBadge {
  if (status === "sent")
    return { text: "Delivered", cls: "text-green-600 dark:text-green-400 border-green-600/40" };
  if (status === "failed")
    return { text: uiText("Failed"), cls: "text-red-600 dark:text-red-400 border-red-600/40" };
  if (status === "pending")
    return { text: "Pending", cls: "text-amber-600 dark:text-amber-400 border-amber-600/40" };
  return { text: "—", cls: "text-muted-foreground border-border" };
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
  const [draft, setDraft] = useState<DraftState | null>(null);
  const [busy, setBusy] = useState<number | "save" | "switch" | null>(null);
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
    });
    return () => {
      live.current = false;
      release();
    };
  }, []);
  const current = (session: number) => live.current && session === getSessionVersion();

  async function toggleEnabled(next: boolean) {
    const session = getSessionVersion();
    setBusy("switch");
    try {
      await commands.enable(next, session);
    } catch (error) {
      if (current(session)) deps.toast.error(error);
    } finally {
      if (current(session)) setBusy(null);
    }
  }

  const startEdit = useCallback((ch: NotificationChannel) => {
    setDraft({
      id: ch.id,
      name: ch.name,
      target: ch.target,
      // Secret values come back masked ("********"); start blank so an
      // untouched field is sent blank and the backend keeps the stored value.
      config: Object.fromEntries(Object.entries(ch.config).filter(([, v]) => v !== "********")),
      events: ch.events,
      printerIds: ch.printer_ids,
      enabled: ch.enabled,
    });
  }, []);

  async function saveDraft() {
    if (!draft) return;
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
        await commands.update(draft.id, body, session);
        if (current(session)) deps.toast.success(uiText("Channel updated."));
      }
      if (current(session)) setDraft(null);
    } catch (error) {
      if (current(session)) deps.toast.error(error);
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
      <Button onClick={() => void settingsQuery.refetch()}>
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
      <div className="space-y-4">
        {settingsFailure}
        <fieldset
          className="min-w-0 space-y-4"
          disabled={busy !== null || Boolean(settingsQuery.error)}
        >
          {/* Master switch */}
          <div className={`${CARD} px-4 sm:px-6 py-4 flex items-center justify-between gap-3`}>
            <div className="min-w-0 flex items-start gap-2">
              <Bell className="h-4 w-4 mt-0.5 text-muted-foreground flex-shrink-0" />
              <div>
                <h3 className="text-sm font-semibold text-foreground">{uiText("Notifications")}</h3>
                <p className="text-xs text-muted-foreground mt-0.5">
                  {uiText(
                    "Send webhook, Discord, Telegram, or ntfy alerts on print and printer events.",
                  )}
                </p>
              </div>
            </div>
            <label className="inline-flex items-center gap-2 flex-shrink-0">
              <input
                type="checkbox"
                checked={enabled}
                disabled={!canEdit || busy === "switch"}
                onChange={(e) => toggleEnabled(e.target.checked)}
                className="h-4 w-4 accent-primary"
              />
              <span className="text-xs font-mono uppercase tracking-wider text-muted-foreground">
                {enabled ? uiText("On") : uiText("Off")}
              </span>
            </label>
          </div>

          {!canEdit && (
            <p className="text-xs text-muted-foreground italic">
              {uiText("Only an administrator can manage notification channels.")}
            </p>
          )}

          {/* Channel list */}
          {canEdit && (
            <div className="space-y-2">
              {channels.length === 0 && !draft && (
                <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-border bg-muted/20 px-6 py-8 text-center">
                  <Bell className="h-7 w-7 text-muted-foreground/50" />
                  <p className="text-sm font-medium text-foreground">
                    {uiText("No notification channels yet")}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {uiText("Add a channel to start receiving print and printer alerts.")}
                  </p>
                </div>
              )}
              {channels.map((ch) => {
                const badge = statusBadge(ch.last_status);
                return (
                  <div key={ch.id} className={`${CARD} px-4 py-3`}>
                    <div className="flex items-center justify-between gap-3 flex-wrap">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-medium text-foreground truncate">
                            {ch.name}
                          </span>
                          <span className="font-mono text-3xs uppercase tracking-wider px-1.5 py-0.5 rounded border border-border text-muted-foreground">
                            {ch.target}
                          </span>
                          {!ch.enabled &&
                            (ch.consecutive_failures > 0 ? (
                              <span
                                className="font-mono text-3xs uppercase tracking-wider px-1.5 py-0.5 rounded border text-amber-600 dark:text-amber-400 border-amber-600/40"
                                title={ch.last_error ?? undefined}
                              >
                                {uiText("Auto-disabled")}
                              </span>
                            ) : (
                              <span className="font-mono text-3xs uppercase tracking-wider text-muted-foreground">
                                {uiText("disabled")}
                              </span>
                            ))}
                        </div>
                        <p className="text-2xs text-muted-foreground mt-0.5 truncate">
                          {ch.events
                            .map((e) => EVENTS.find((x) => x.value === e)?.label ?? e)
                            .join(", ")}
                          {ch.printer_ids
                            ? uiText(" · {value1} printer(s)", {
                                value1: String(ch.printer_ids.length),
                              })
                            : uiText(" · all printers")}
                        </p>
                      </div>
                      <div className="flex items-center gap-1.5 flex-shrink-0">
                        <span
                          className={`font-mono text-3xs uppercase tracking-wider px-2 py-1 rounded border ${badge.cls}`}
                          title={ch.last_error ?? undefined}
                        >
                          {badge.text}
                        </span>
                        <button
                          type="button"
                          onClick={() => sendTest(ch.id)}
                          disabled={busy === ch.id}
                          className={BTN_SECONDARY}
                          title={uiText("Send a test notification")}
                        >
                          {busy === ch.id ? (
                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          ) : (
                            <Send className="h-3.5 w-3.5" />
                          )}
                        </button>
                        <button
                          type="button"
                          onClick={() => startEdit(ch)}
                          className={BTN_SECONDARY}
                          title={uiText("Edit channel")}
                        >
                          <Pencil className="h-3.5 w-3.5" />
                        </button>
                        <button
                          type="button"
                          onClick={() => removeChannel(ch.id)}
                          disabled={busy === ch.id}
                          className={BTN_SECONDARY}
                          title={uiText("Delete channel")}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    </div>
                  </div>
                );
              })}

              {/* Draft form */}
              {draft ? (
                <ChannelForm
                  draft={draft}
                  setDraft={setDraft}
                  printers={printersQuery.data}
                  printersError={printersQuery.error}
                  retryPrinters={() => void printersQuery.refetch()}
                  onSave={saveDraft}
                  onCancel={() => setDraft(null)}
                  saving={busy === "save"}
                />
              ) : (
                <button
                  type="button"
                  onClick={() => setDraft(emptyDraft())}
                  className={BTN_PRIMARY}
                >
                  <Plus className="h-3.5 w-3.5" />
                  {uiText("Add channel")}
                </button>
              )}
            </div>
          )}

          {/* Recent deliveries */}
        </fieldset>
        {deliveriesQuery.error && (
          <div role="alert">
            <p>{uiText("Notification delivery history could not be loaded.")}</p>
            <Button onClick={() => void deliveriesQuery.refetch()}>
              {uiText("Retry delivery history")}
            </Button>
          </div>
        )}
        {deliveriesQuery.isPending && <p role="status">{uiText("Loading delivery history…")}</p>}
        {canEdit && !deliveriesQuery.error && deliveries && deliveries.length > 0 && (
          <div className={CARD}>
            <div className="px-4 py-3 border-b border-border">
              <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                {uiText("Recent deliveries")}
              </h4>
            </div>
            <div className="divide-y divide-border">
              {deliveries.map((d) => {
                const badge = statusBadge(d.status);
                return (
                  <div
                    key={d.id}
                    className="px-4 py-2 flex items-center justify-between gap-3 text-xs"
                  >
                    <span className="font-mono text-muted-foreground truncate">
                      {EVENTS.find((e) => e.value === d.event_type)?.label ?? d.event_type}
                    </span>
                    <div className="flex items-center gap-2 flex-shrink-0">
                      {d.attempts > 1 && (
                        <span className="text-muted-foreground">×{d.attempts}</span>
                      )}
                      <span className="text-muted-foreground">
                        {d.created_at ? new Date(d.created_at).toLocaleString(currentLocale()) : ""}
                      </span>
                      <span
                        className={`font-mono text-3xs uppercase tracking-wider px-1.5 py-0.5 rounded border ${badge.cls}`}
                        title={d.last_error ?? undefined}
                      >
                        {badge.text}
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </Localized>
  );
}

function ChannelForm({
  draft,
  setDraft,
  printers,
  onSave,
  onCancel,
  saving,
  printersError,
  retryPrinters,
}: {
  draft: DraftState;
  setDraft: (d: DraftState) => void;
  printers: PrinterRead[] | undefined;
  printersError: Error | null;
  retryPrinters: () => void;
  onSave: () => void;
  onCancel: () => void;
  saving: boolean;
}) {
  useUiLocale();
  const fields: TargetField[] = TARGET_FIELDS[draft.target];
  const scoped = draft.printerIds !== null;

  return (
    <Localized>
      <form
        className={`${CARD} p-4 space-y-3`}
        onSubmit={(e) => {
          e.preventDefault();
          onSave();
        }}
      >
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <label className={LABEL}>{uiText("Name")}</label>
            <input
              value={draft.name}
              onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              placeholder={uiText("Living-room printer alerts")}
              className={INPUT}
            />
          </div>
          <div>
            <label className={LABEL}>{uiText("Type")}</label>
            <select
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
            <label className={LABEL}>{f.label}</label>
            <input
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

        <div>
          <label className={LABEL}>{uiText("Events")}</label>
          <div className="flex flex-wrap gap-3">
            {EVENTS.map((ev) => (
              <label
                key={ev.value}
                className="inline-flex items-center gap-1.5 text-xs text-foreground"
              >
                <input
                  type="checkbox"
                  checked={draft.events.includes(ev.value)}
                  onChange={(e) =>
                    setDraft({
                      ...draft,
                      events: e.target.checked
                        ? [...draft.events, ev.value]
                        : draft.events.filter((x) => x !== ev.value),
                    })
                  }
                  className="h-3.5 w-3.5 accent-primary"
                />
                {ev.label}
              </label>
            ))}
          </div>
        </div>

        <div>
          <label className={LABEL}>{uiText("Printers")}</label>
          <label className="inline-flex items-center gap-1.5 text-xs text-foreground mb-2">
            <input
              type="checkbox"
              checked={!scoped}
              onChange={(e) => setDraft({ ...draft, printerIds: e.target.checked ? null : [] })}
              className="h-3.5 w-3.5 accent-primary"
            />
            {uiText("All printers")}
          </label>
          {scoped && (
            <div className="flex flex-wrap gap-3">
              {printersError ? (
                <div role="alert">
                  <p>{uiText("Printer choices could not be loaded.")}</p>
                  <Button type="button" onClick={retryPrinters}>
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
                    className="inline-flex items-center gap-1.5 text-xs text-foreground"
                  >
                    <input
                      type="checkbox"
                      checked={(draft.printerIds ?? []).includes(p.id)}
                      onChange={(e) =>
                        setDraft({
                          ...draft,
                          printerIds: e.target.checked
                            ? [...(draft.printerIds ?? []), p.id]
                            : (draft.printerIds ?? []).filter((x) => x !== p.id),
                        })
                      }
                      className="h-3.5 w-3.5 accent-primary"
                    />
                    {p.name}
                  </label>
                ))}
            </div>
          )}
        </div>

        <label className="inline-flex items-center gap-1.5 text-xs text-foreground">
          <input
            type="checkbox"
            checked={draft.enabled}
            onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })}
            className="h-3.5 w-3.5 accent-primary"
          />
          {uiText("Enabled")}
        </label>

        <div className="flex items-center gap-2 pt-1">
          <button
            type="submit"
            disabled={saving || (scoped && (!printers || Boolean(printersError)))}
            className={BTN_PRIMARY}
          >
            {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
            {draft.id === null ? uiText("Create channel") : uiText("Save changes")}
          </button>
          <button type="button" onClick={onCancel} disabled={saving} className={BTN_SECONDARY}>
            {uiText("Cancel")}
          </button>
        </div>
      </form>
    </Localized>
  );
}
