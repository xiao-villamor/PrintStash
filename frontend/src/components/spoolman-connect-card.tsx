"use client";

import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

import { useEffect, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, Loader2, PlugZap, Save } from "lucide-react";

import { useSpoolmanCommands } from "@/lib/queries/settings-spoolman";
import { captureEditingBase } from "@/lib/api/editing";
import { onAuthChange } from "@/lib/auth-store";
import type { EditingBase } from "@/types/editing";
import { useSpoolmanStatus, useSpools } from "@/lib/queries";
import { getSessionVersion } from "@/lib/session-transport";
import { formatGrams } from "@/lib/format";
import { parseApiError, userMessage } from "@/lib/errors";
import type { SpoolmanStatus } from "@/types";
import { Localized } from "@/components/ui/localized";

const INPUT_CLASS =
  "w-full px-2.5 py-1.5 text-sm rounded border border-border bg-background text-foreground placeholder:text-muted-foreground/40 disabled:opacity-50";

const SECRET_MASK = "********";

export function SpoolmanConnectCard({ canEdit }: { canEdit: boolean }) {
  useUiLocale();
  const commands = useSpoolmanCommands(canEdit);
  const query = useSpoolmanStatus({ enabled: canEdit });
  const denied = [401, 403].includes(parseApiError(query.error).status);
  const status = denied ? undefined : query.data;
  const isLoading = query.isLoading;
  const enabled = !!status?.enabled;
  const { data: spools } = useSpools({ enabled: enabled && canEdit });

  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const [draftBase, setDraftBase] = useState<EditingBase | null>(null);
  const [hydratedFrom, setHydratedFrom] = useState<SpoolmanStatus | undefined>(undefined);
  const [review, setReview] = useState<
    { phase: "idle" } | { phase: "required" } | { phase: "ready"; snapshot: SpoolmanStatus }
  >({ phase: "idle" });
  const [failedToggle, setFailedToggle] = useState<Parameters<typeof commands.save>[0] | null>(
    null,
  );
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    const retire = () => {
      setBaseUrl("");
      setApiKey("");
      setDraftBase(null);
      setHydratedFrom(undefined);
      setReview({ phase: "idle" });
      setFailedToggle(null);
      setError("");
      setNotice("");
      setBusy(false);
    };
    const release = onAuthChange(retire);
    if (!canEdit || denied) retire();
    return () => {
      live.current = false;
      release();
    };
  }, [canEdit, denied]);
  if (status && status !== hydratedFrom) {
    setHydratedFrom(status);
    if (!draftBase) {
      setBaseUrl(status.base_url ?? "");
      setApiKey(status.has_api_key ? SECRET_MASK : "");
    }
  }
  const connected = !!status?.connected;
  const beginDraft = () => {
    if (status && !draftBase) setDraftBase(captureEditingBase(status));
  };
  const current = (session: number) => live.current && session === getSessionVersion();
  async function mutate(
    body: Parameters<typeof commands.save>[0],
    ok?: string,
    revised?: SpoolmanStatus,
  ) {
    if (!canEdit || !status || busy || (review.phase !== "idle" && !revised)) return;
    const session = getSessionVersion();
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const updated = await commands.save(body, revised ?? draftBase ?? status, session);
      if (!current(session)) return;
      if (ok) {
        setDraftBase(null);
        setBaseUrl(updated.base_url ?? "");
        setApiKey(updated.has_api_key ? SECRET_MASK : "");
        setNotice(ok);
      }
      setReview({ phase: "idle" });
    } catch (e) {
      if (!current(session)) return;
      setError(userMessage(e));
      const code = parseApiError(e).status;
      if ([412, 428].includes(code) || code === 0 || code >= 500) {
        setFailedToggle(ok ? null : body);
        setReview({ phase: "required" });
      }
    } finally {
      if (current(session)) setBusy(false);
    }
  }
  const saveConnection = (revised?: SpoolmanStatus) =>
    mutate(
      { base_url: baseUrl.trim(), api_key: apiKey === SECRET_MASK ? undefined : apiKey },
      uiText("Saved."),
      revised,
    );
  const toggleEnabled = (next: boolean) => mutate({ enabled: next });
  const toggleWrite = (next: boolean) => mutate({ write_enabled: next });
  const toggleWriteForce = (next: boolean) => mutate({ write_force: next });
  async function reviewLatest() {
    const session = getSessionVersion();
    setBusy(true);
    setError("");
    try {
      const snapshot = await commands.review(session);
      if (current(session)) setReview({ phase: "ready", snapshot });
    } catch (e) {
      if (current(session)) setError(userMessage(e));
    } finally {
      if (current(session)) setBusy(false);
    }
  }
  async function runTest() {
    if (!canEdit || !status || busy) return;
    const session = getSessionVersion();
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const res = await commands.test(
        { base_url: baseUrl.trim(), api_key: apiKey === SECRET_MASK ? undefined : apiKey },
        session,
      );
      if (!current(session)) return;
      if (res.connected)
        setNotice(
          uiText("Connected{value1}.", {
            value1: String(res.version ? ` — Spoolman v${res.version}` : ""),
          }),
        );
      else setError(res.error || uiText("Spoolman did not respond."));
    } catch (e) {
      if (current(session)) setError(userMessage(e));
    } finally {
      if (current(session)) setBusy(false);
    }
  }

  return (
    <Localized>
      <div className="bg-card border border-border rounded overflow-hidden">
        <div className="px-4 sm:px-6 lg:px-8 py-4 sm:py-5 border-b border-border flex items-center justify-between gap-2">
          <div className="min-w-0">
            <h3 className="text-sm font-semibold text-foreground">Spoolman</h3>
            <p className="text-xs text-muted-foreground mt-0.5">
              {uiText(
                "Track filament inventory and per-print consumption with a self-hosted Spoolman instance. Off by default.",
              )}
            </p>
          </div>
          <span
            className={`font-mono text-3xs uppercase tracking-wider px-2 py-1 rounded border flex-shrink-0 ${
              enabled && connected
                ? "text-green-600 dark:text-green-400 border-green-600/40"
                : "text-muted-foreground border-border"
            }`}
          >
            {!enabled
              ? uiText("Disabled")
              : connected
                ? uiText("Connected")
                : uiText("Not connected")}
          </span>
        </div>

        <div className="p-3 sm:p-4 lg:p-6 space-y-4">
          {!canEdit ? (
            <p className="text-xs text-muted-foreground italic">
              {uiText("Only an administrator can configure Spoolman.")}
            </p>
          ) : isLoading ? (
            <p className="text-sm text-muted-foreground">{uiText("Loading…")}</p>
          ) : !status ? (
            <div role="alert">
              <p>{userMessage(query.error)}</p>
              <button type="button" onClick={() => void query.refetch()}>
                {uiText("Retry")}
              </button>
            </div>
          ) : (
            <>
              {review.phase !== "idle" && (
                <div role="alert" className="space-y-2">
                  <p>{uiText("library.editConflict")}</p>
                  <button type="button" disabled={busy} onClick={() => void reviewLatest()}>
                    {uiText("library.reviewLatest")}
                  </button>
                  {review.phase === "ready" && (
                    <>
                      <p>{uiText("library.latestVersion")}</p>
                      <p>{review.snapshot.base_url}</p>
                      <dl>
                        <dt>{uiText("Enable Spoolman integration")}</dt>
                        <dd>{uiText(review.snapshot.enabled ? "Enabled" : "Disabled")}</dd>
                        <dt>{uiText("Write consumption back to Spoolman")}</dt>
                        <dd>{uiText(review.snapshot.write_enabled ? "Enabled" : "Disabled")}</dd>
                        <dt>{uiText("Write back anyway (I disabled Moonraker's hook)")}</dt>
                        <dd>{uiText(review.snapshot.write_force ? "Enabled" : "Disabled")}</dd>
                      </dl>
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() =>
                          void (failedToggle
                            ? mutate(failedToggle, undefined, review.snapshot)
                            : saveConnection(review.snapshot))
                        }
                      >
                        {uiText("library.retryDraft")}
                      </button>
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => {
                          setBaseUrl(review.snapshot.base_url ?? "");
                          setApiKey(review.snapshot.has_api_key ? SECRET_MASK : "");
                          setDraftBase(captureEditingBase(review.snapshot));
                          setReview({ phase: "idle" });
                          setError("");
                        }}
                      >
                        {uiText("library.useLatest")}
                      </button>
                    </>
                  )}
                </div>
              )}
              {/* Master switch */}
              <label className="flex items-center justify-between gap-3 cursor-pointer">
                <span className="text-sm text-foreground">
                  {uiText("Enable Spoolman integration")}
                </span>
                <input
                  type="checkbox"
                  checked={enabled}
                  disabled={busy || review.phase !== "idle"}
                  onChange={(e) => toggleEnabled(e.target.checked)}
                  className="h-4 w-4"
                />
              </label>

              {/* Connection */}
              <form
                className="space-y-3"
                onSubmit={(e) => {
                  e.preventDefault();
                  saveConnection();
                }}
              >
                <div>
                  <label className="block text-2xs text-muted-foreground mb-1">
                    {uiText("Base URL")}
                  </label>
                  <input
                    type="url"
                    value={baseUrl}
                    disabled={busy}
                    onChange={(e) => {
                      beginDraft();
                      setBaseUrl(e.target.value);
                    }}
                    placeholder="http://spoolman.local:7912"
                    className={INPUT_CLASS}
                  />
                </div>
                <div>
                  <label className="block text-2xs text-muted-foreground mb-1">
                    {uiText("API key ")}
                    <span className="opacity-60">{uiText("(optional)")}</span>
                  </label>
                  <input
                    type="password"
                    autoComplete="off"
                    value={apiKey}
                    disabled={busy}
                    onChange={(e) => {
                      beginDraft();
                      setApiKey(e.target.value);
                    }}
                    placeholder={uiText("Only if Spoolman sits behind an authenticating proxy")}
                    className={INPUT_CLASS}
                  />
                </div>
                <div className="flex items-center gap-2">
                  <button
                    type="submit"
                    disabled={busy || !baseUrl.trim() || review.phase !== "idle"}
                    className="inline-flex items-center gap-1.5 px-4 py-2 rounded bg-primary text-primary-foreground font-mono text-xs uppercase tracking-wider hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed transition-opacity"
                  >
                    {busy ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Save className="h-3.5 w-3.5" />
                    )}
                    {uiText("Save")}
                  </button>
                  <button
                    type="button"
                    onClick={runTest}
                    disabled={busy || !baseUrl.trim() || review.phase !== "idle"}
                    className="inline-flex items-center gap-1.5 px-3 py-2 rounded border border-border text-muted-foreground font-mono text-xs uppercase tracking-wider hover:bg-muted disabled:opacity-50 transition-colors"
                  >
                    <PlugZap className="h-3.5 w-3.5" />
                    {uiText("Test connection")}
                  </button>
                </div>
              </form>

              {/* Write-back + double-count warning */}
              {enabled && (
                <div className="space-y-2 pt-1 border-t border-border">
                  <label className="flex items-center justify-between gap-3 cursor-pointer pt-3">
                    <span className="text-sm text-foreground">
                      {uiText("Write consumption back to Spoolman")}
                      <span className="block text-2xs text-muted-foreground">
                        {uiText(
                          "Decrements the selected spool by measured filament when a print completes (Moonraker-measured prints only).",
                        )}
                      </span>
                    </span>
                    <input
                      type="checkbox"
                      checked={!!status?.write_enabled}
                      disabled={busy || review.phase !== "idle"}
                      onChange={(e) => toggleWrite(e.target.checked)}
                      className="h-4 w-4 flex-shrink-0"
                    />
                  </label>
                  {status?.native_hook_detected && (
                    <div className="space-y-2 text-2xs text-amber-600 dark:text-amber-400 bg-amber-500/10 border border-amber-500/30 rounded p-2">
                      <div className="flex items-start gap-2">
                        <AlertTriangle className="h-3.5 w-3.5 mt-0.5 flex-shrink-0" />
                        <span>
                          {uiText(
                            "Moonraker's native Spoolman integration is already decrementing the active spool, so PrintStash automatically skips its own write-back to avoid double-counting. Only override this if you have disabled Moonraker's hook and want PrintStash to count consumption.",
                          )}
                        </span>
                      </div>
                      <label className="flex items-center gap-2 cursor-pointer pl-5">
                        <input
                          type="checkbox"
                          checked={!!status?.write_force}
                          disabled={busy || review.phase !== "idle"}
                          onChange={(e) => toggleWriteForce(e.target.checked)}
                          className="h-3.5 w-3.5 flex-shrink-0"
                        />
                        <span>{uiText("Write back anyway (I disabled Moonraker's hook)")}</span>
                      </label>
                    </div>
                  )}
                </div>
              )}

              {/* Inventory */}
              {enabled && connected && spools && spools.length > 0 && (
                <div className="pt-1 border-t border-border">
                  <h4 className="text-2xs uppercase tracking-wider text-muted-foreground pt-3 pb-2">
                    {uiText("Inventory")}
                  </h4>
                  <ul className="space-y-1">
                    {spools.map((s) => (
                      <li
                        key={s.id}
                        className="flex items-center justify-between gap-3 text-sm py-1"
                      >
                        <span className="flex items-center gap-2 min-w-0">
                          <span
                            className="h-2.5 w-2.5 rounded-full flex-shrink-0 border border-border"
                            style={{
                              backgroundColor: s.color_hex
                                ? `#${s.color_hex.replace(/^#/, "")}`
                                : "transparent",
                            }}
                          />
                          <span className="truncate text-foreground">
                            {s.filament_name ||
                              s.name ||
                              uiText("Spool {value1}", { value1: String(s.id) })}
                            {s.vendor_name ? (
                              <span className="text-muted-foreground"> · {s.vendor_name}</span>
                            ) : null}
                          </span>
                        </span>
                        <span className="font-mono text-xs text-muted-foreground flex-shrink-0">
                          {uiText("{value1} left", {
                            value1: String(formatGrams(s.remaining_weight) ?? ""),
                          })}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {error && <p className="text-xs text-red-600 dark:text-red-400">{error}</p>}
              {notice && !error && (
                <p className="flex items-center gap-1.5 text-xs text-green-600 dark:text-green-400">
                  <CheckCircle2 className="h-3.5 w-3.5" />
                  {notice}
                </p>
              )}
            </>
          )}
        </div>
      </div>
    </Localized>
  );
}
