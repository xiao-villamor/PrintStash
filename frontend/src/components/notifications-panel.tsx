"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Bell,
  Loader2,
  Plus,
  Send,
  Trash2,
  Pencil,
} from "lucide-react";
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
  NotificationDelivery,
  NotificationEvent,
  NotificationTarget,
  PrinterRead,
} from "@/types";
import { toast } from "@/lib/toast";
import { buttonVariants } from "@/components/ui/button";
import { inputClasses } from "@/components/ui/input";
import { cn } from "@/lib/utils";

const CARD =
  "overflow-hidden rounded-lg border border-border bg-card shadow-sm";
const INPUT = cn(inputClasses, "h-auto px-2.5 py-1.5 rounded placeholder:text-muted-foreground/40");
const BTN_PRIMARY = cn(buttonVariants({ size: "xs" }), "font-mono uppercase tracking-wider");
const BTN_SECONDARY = cn(
  buttonVariants({ variant: "outline", size: "xs" }),
  "font-mono uppercase tracking-wider text-muted-foreground",
);
const LABEL = "block text-2xs text-muted-foreground mb-1";

const TARGETS: { value: NotificationTarget; label: string }[] = [
  { value: "webhook", label: "Webhook" },
  { value: "discord", label: "Discord" },
  { value: "telegram", label: "Telegram" },
  { value: "ntfy", label: "ntfy" },
];

const EVENTS: { value: NotificationEvent; label: string }[] = [
  { value: "print_completed", label: "Print completed" },
  { value: "print_failed", label: "Print failed" },
  { value: "print_cancelled", label: "Print cancelled" },
  { value: "printer_offline", label: "Printer offline" },
];

// Config fields rendered per target. `secret` fields are masked on read; an
// existing value survives an edit when left blank.
const TARGET_FIELDS: Record<
  NotificationTarget,
  { key: string; label: string; placeholder: string; secret?: boolean; optional?: boolean }[]
> = {
  webhook: [
    { key: "url", label: "Webhook URL", placeholder: "https://example.com/hook", secret: true },
    {
      key: "secret",
      label: "Signing secret (optional)",
      placeholder: "used to HMAC-sign the payload",
      secret: true,
      optional: true,
    },
  ],
  discord: [
    {
      key: "url",
      label: "Discord webhook URL",
      placeholder: "https://discord.com/api/webhooks/…",
      secret: true,
    },
  ],
  telegram: [
    { key: "bot_token", label: "Bot token", placeholder: "123456:ABC-DEF…", secret: true },
    { key: "chat_id", label: "Chat ID", placeholder: "-1001234567890" },
  ],
  ntfy: [
    { key: "server_url", label: "Server URL", placeholder: "https://ntfy.sh" },
    { key: "topic", label: "Topic", placeholder: "my-printer-alerts" },
    { key: "token", label: "Access token (optional)", placeholder: "tk_…", secret: true, optional: true },
  ],
};

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

function statusBadge(status: string | null): { text: string; cls: string } {
  if (status === "sent")
    return { text: "Delivered", cls: "text-green-600 dark:text-green-400 border-green-600/40" };
  if (status === "failed")
    return { text: "Failed", cls: "text-red-600 dark:text-red-400 border-red-600/40" };
  if (status === "pending")
    return { text: "Pending", cls: "text-amber-600 dark:text-amber-400 border-amber-600/40" };
  return { text: "—", cls: "text-muted-foreground border-border" };
}

export function NotificationsPanel({ canEdit }: { canEdit: boolean }) {
  const [enabled, setEnabled] = useState(false);
  const [channels, setChannels] = useState<NotificationChannel[]>([]);
  const [printers, setPrinters] = useState<PrinterRead[]>([]);
  const [deliveries, setDeliveries] = useState<NotificationDelivery[]>([]);
  const [loading, setLoading] = useState(true);
  const [draft, setDraft] = useState<DraftState | null>(null);
  const [busy, setBusy] = useState<number | "save" | "switch" | null>(null);

  const load = useCallback(async () => {
    try {
      const [settings, printerList, deliveryList] = await Promise.all([
        getNotificationsSettings(),
        listPrinters().catch(() => []),
        listNotificationDeliveries(25).catch(() => []),
      ]);
      setEnabled(settings.enabled);
      setChannels(settings.channels);
      setPrinters(printerList);
      setDeliveries(deliveryList);
    } catch (e) {
      toast.error(e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const toggleEnabled = useCallback(
    async (next: boolean) => {
      setBusy("switch");
      try {
        await setNotificationsEnabled(next);
        setEnabled(next);
      } catch (e) {
        toast.error(e);
      } finally {
        setBusy(null);
      }
    },
    [],
  );

  const startEdit = useCallback((ch: NotificationChannel) => {
    setDraft({
      id: ch.id,
      name: ch.name,
      target: ch.target,
      // Secret values come back masked ("********"); start blank so an
      // untouched field is sent blank and the backend keeps the stored value.
      config: Object.fromEntries(
        Object.entries(ch.config).filter(([, v]) => v !== "********"),
      ),
      events: ch.events,
      printerIds: ch.printer_ids,
      enabled: ch.enabled,
    });
  }, []);

  const saveDraft = useCallback(async () => {
    if (!draft) return;
    if (!draft.name.trim()) {
      toast.error("Channel name is required.");
      return;
    }
    if (draft.events.length === 0) {
      toast.error("Select at least one event.");
      return;
    }
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
        await createNotificationChannel({ ...body, target: draft.target });
        toast.success("Channel created.");
      } else {
        await updateNotificationChannel(draft.id, body);
        toast.success("Channel updated.");
      }
      setDraft(null);
      await load();
    } catch (e) {
      toast.error(e);
    } finally {
      setBusy(null);
    }
  }, [draft, load]);

  const removeChannel = useCallback(
    async (id: number) => {
      setBusy(id);
      try {
        await deleteNotificationChannel(id);
        await load();
      } catch (e) {
        toast.error(e);
      } finally {
        setBusy(null);
      }
    },
    [load],
  );

  const sendTest = useCallback(
    async (id: number) => {
      setBusy(id);
      try {
        const res = await testNotificationChannel(id);
        if (res.ok) toast.success("Test notification sent.");
        else toast.warning("Test failed", res.error ?? undefined);
        await load();
      } catch (e) {
        toast.error(e);
      } finally {
        setBusy(null);
      }
    },
    [load],
  );

  if (loading) {
    return <p className="text-sm text-muted-foreground">Loading…</p>;
  }

  return (
    <div className="space-y-4">
      {/* Master switch */}
      <div className={`${CARD} px-4 sm:px-6 py-4 flex items-center justify-between gap-3`}>
        <div className="min-w-0 flex items-start gap-2">
          <Bell className="h-4 w-4 mt-0.5 text-muted-foreground flex-shrink-0" />
          <div>
            <h3 className="text-sm font-semibold text-foreground">Notifications</h3>
            <p className="text-xs text-muted-foreground mt-0.5">
              Send webhook, Discord, Telegram, or ntfy alerts on print and printer events.
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
            {enabled ? "On" : "Off"}
          </span>
        </label>
      </div>

      {!canEdit && (
        <p className="text-xs text-muted-foreground italic">
          Only an administrator can manage notification channels.
        </p>
      )}

      {/* Channel list */}
      {canEdit && (
        <div className="space-y-2">
          {channels.length === 0 && !draft && (
            <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-border bg-muted/20 px-6 py-8 text-center">
              <Bell className="h-7 w-7 text-muted-foreground/50" />
              <p className="text-sm font-medium text-foreground">No notification channels yet</p>
              <p className="text-xs text-muted-foreground">Add a channel to start receiving print and printer alerts.</p>
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
                            Auto-disabled
                          </span>
                        ) : (
                          <span className="font-mono text-3xs uppercase tracking-wider text-muted-foreground">
                            disabled
                          </span>
                        ))}
                    </div>
                    <p className="text-2xs text-muted-foreground mt-0.5 truncate">
                      {ch.events.map((e) => EVENTS.find((x) => x.value === e)?.label ?? e).join(", ")}
                      {ch.printer_ids
                        ? ` · ${ch.printer_ids.length} printer(s)`
                        : " · all printers"}
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
                      title="Send a test notification"
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
                      title="Edit channel"
                    >
                      <Pencil className="h-3.5 w-3.5" />
                    </button>
                    <button
                      type="button"
                      onClick={() => removeChannel(ch.id)}
                      disabled={busy === ch.id}
                      className={BTN_SECONDARY}
                      title="Delete channel"
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
              printers={printers}
              onSave={saveDraft}
              onCancel={() => setDraft(null)}
              saving={busy === "save"}
            />
          ) : (
            <button type="button" onClick={() => setDraft(emptyDraft())} className={BTN_PRIMARY}>
              <Plus className="h-3.5 w-3.5" />
              Add channel
            </button>
          )}
        </div>
      )}

      {/* Recent deliveries */}
      {canEdit && deliveries.length > 0 && (
        <div className={CARD}>
          <div className="px-4 py-3 border-b border-border">
            <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Recent deliveries
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
                      {d.created_at ? new Date(d.created_at).toLocaleString() : ""}
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
  );
}

function ChannelForm({
  draft,
  setDraft,
  printers,
  onSave,
  onCancel,
  saving,
}: {
  draft: DraftState;
  setDraft: (d: DraftState) => void;
  printers: PrinterRead[];
  onSave: () => void;
  onCancel: () => void;
  saving: boolean;
}) {
  const fields = TARGET_FIELDS[draft.target];
  const scoped = draft.printerIds !== null;

  return (
    <form
      className={`${CARD} p-4 space-y-3`}
      onSubmit={(e) => {
        e.preventDefault();
        onSave();
      }}
    >
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div>
          <label className={LABEL}>Name</label>
          <input
            value={draft.name}
            onChange={(e) => setDraft({ ...draft, name: e.target.value })}
            placeholder="Living-room printer alerts"
            className={INPUT}
          />
        </div>
        <div>
          <label className={LABEL}>Type</label>
          <select
            value={draft.target}
            disabled={draft.id !== null}
            onChange={(e) =>
              setDraft({ ...draft, target: e.target.value as NotificationTarget, config: {} })
            }
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
              draft.id !== null && f.secret ? "•••••••• (unchanged)" : f.placeholder
            }
            className={INPUT}
            autoComplete="off"
          />
        </div>
      ))}

      <div>
        <label className={LABEL}>Events</label>
        <div className="flex flex-wrap gap-3">
          {EVENTS.map((ev) => (
            <label key={ev.value} className="inline-flex items-center gap-1.5 text-xs text-foreground">
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
        <label className={LABEL}>Printers</label>
        <label className="inline-flex items-center gap-1.5 text-xs text-foreground mb-2">
          <input
            type="checkbox"
            checked={!scoped}
            onChange={(e) => setDraft({ ...draft, printerIds: e.target.checked ? null : [] })}
            className="h-3.5 w-3.5 accent-primary"
          />
          All printers
        </label>
        {scoped && (
          <div className="flex flex-wrap gap-3">
            {printers.length === 0 && (
              <span className="text-2xs text-muted-foreground italic">
                No printers configured.
              </span>
            )}
            {printers.map((p) => (
              <label key={p.id} className="inline-flex items-center gap-1.5 text-xs text-foreground">
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
        Enabled
      </label>

      <div className="flex items-center gap-2 pt-1">
        <button type="submit" disabled={saving} className={BTN_PRIMARY}>
          {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
          {draft.id === null ? "Create channel" : "Save changes"}
        </button>
        <button type="button" onClick={onCancel} disabled={saving} className={BTN_SECONDARY}>
          Cancel
        </button>
      </div>
    </form>
  );
}
