"use client";

import { SETTINGS_FIELD } from "@/components/settings-controls";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AlertTriangle, CheckCircle2 } from "lucide-react";

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
import { Button } from "@/components/ui/button";

const INPUT_CLASS = SETTINGS_FIELD;

const SECRET_MASK = "********";

export function SpoolmanConnectCard({
  canEdit,
  actionHost,
}: {
  canEdit: boolean;
  actionHost?: HTMLElement | null;
}) {
  useUiLocale();
  const formId = useId();
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
  const connectionForm = useRef<HTMLFormElement>(null);
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
  const historyChanged =
    review.phase === "ready" && review.snapshot.edit_epoch !== (draftBase ?? status)?.edit_epoch;

  const beginDraft = () => {
    if (status && !draftBase) setDraftBase(captureEditingBase(status));
  };
  const current = (session: number) => live.current && session === getSessionVersion();
  async function mutate(
    body: Parameters<typeof commands.save>[0],
    ok?: string,
    revised?: SpoolmanStatus,
  ) {
    if (
      !canEdit ||
      !status ||
      busy ||
      (review.phase !== "idle" && !revised) ||
      (revised && historyChanged)
    )
      return;
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
        setDraftBase((base) => base ?? captureEditingBase(status));
        setFailedToggle(ok ? null : body);
        setReview({ phase: "required" });
      }
    } finally {
      if (current(session)) setBusy(false);
    }
  }
  const saveConnection = (revised?: SpoolmanStatus) => {
    if (!baseUrl.trim() || !connectionForm.current?.reportValidity()) return;
    return mutate(
      { base_url: baseUrl.trim(), api_key: apiKey === SECRET_MASK ? undefined : apiKey },
      uiText("Saved."),
      revised,
    );
  };
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
  async function adoptLatest() {
    const session = getSessionVersion();
    setBusy(true);
    setError("");
    try {
      const snapshot = await commands.adopt(session);
      if (!current(session)) return;
      setBaseUrl(snapshot.base_url ?? "");
      setApiKey(snapshot.has_api_key ? SECRET_MASK : "");
      setDraftBase(null);
      setHydratedFrom(snapshot);
      setReview({ phase: "idle" });
      setFailedToggle(null);
    } catch (error) {
      if (current(session)) setError(userMessage(error));
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

  const actions =
    canEdit && status ? (
      <>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={runTest}
          disabled={busy || !baseUrl.trim() || review.phase !== "idle"}
        >
          {uiText("Test connection")}
        </Button>
        <Button
          type="submit"
          form={formId + "-connection"}
          size="sm"
          loading={busy}
          disabled={busy || !baseUrl.trim() || review.phase !== "idle"}
        >
          {uiText("Save")}
        </Button>
      </>
    ) : null;

  return (
    <Localized>
      <div className="overflow-hidden border border-border bg-card">
        {actionHost && actions ? createPortal(actions, actionHost) : null}
        <div className="space-y-1 p-4 sm:p-5">
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
                      {historyChanged && (
                        <p>
                          {uiText(
                            "The database history changed. Use current values before trying again.",
                          )}
                        </p>
                      )}

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
                        disabled={busy || historyChanged}
                        onClick={() =>
                          void (failedToggle
                            ? mutate(failedToggle, undefined, review.snapshot)
                            : saveConnection(review.snapshot))
                        }
                      >
                        {uiText("library.retryDraft")}
                      </button>
                      <button type="button" disabled={busy} onClick={() => void adoptLatest()}>
                        {uiText("library.useLatest")}
                      </button>
                    </>
                  )}
                </div>
              )}
              {/* Master switch */}
              <label className="settings-preference-row">
                <span className="font-medium text-foreground">
                  {uiText("Enable Spoolman integration")}
                </span>
                <span>
                  <select
                    value={enabled ? "enabled" : "disabled"}
                    disabled={busy || review.phase !== "idle"}
                    onChange={(e) => toggleEnabled(e.target.value === "enabled")}
                    className={INPUT_CLASS}
                  >
                    <option value="enabled">{uiText("Enabled")}</option>
                    <option value="disabled">{uiText("Disabled")}</option>
                  </select>
                  {enabled && (
                    <span className="mt-1 block text-xs text-muted-foreground">
                      {uiText(connected ? "Connected" : "Not connected")}
                    </span>
                  )}
                </span>
              </label>

              {/* Connection */}
              <form
                ref={connectionForm}
                id={formId + "-connection"}
                onSubmit={(e) => {
                  e.preventDefault();
                  saveConnection();
                }}
              >
                <div className="settings-preference-row">
                  <label className="text-sm font-medium" htmlFor={formId + "-base-url"}>
                    {uiText("Base URL")}
                  </label>
                  <input
                    id={formId + "-base-url"}
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
                <div className="settings-preference-row">
                  <label className="text-sm font-medium" htmlFor={formId + "-api-key"}>
                    {uiText("API key ")}
                    <span className="settings-row-hint">{uiText("(optional)")}</span>
                  </label>
                  <input
                    id={formId + "-api-key"}
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
                {!actionHost && (
                  <div className="flex flex-wrap justify-end gap-2 pt-3">{actions}</div>
                )}
              </form>

              {/* Write-back + double-count warning */}
              {enabled && (
                <div className="space-y-2">
                  <label className="settings-preference-row">
                    <span className="text-sm text-foreground">
                      {uiText("Write consumption back to Spoolman")}
                      <span className="block text-2xs text-muted-foreground">
                        {uiText(
                          "Decrements the selected spool by measured filament when a print completes (Moonraker-measured prints only).",
                        )}
                      </span>
                    </span>
                    <select
                      value={status?.write_enabled ? "enabled" : "disabled"}
                      disabled={busy || review.phase !== "idle"}
                      onChange={(e) => toggleWrite(e.target.value === "enabled")}
                      className={INPUT_CLASS}
                    >
                      <option value="enabled">{uiText("Enabled")}</option>
                      <option value="disabled">{uiText("Disabled")}</option>
                    </select>
                  </label>
                  {status?.native_hook_detected && (
                    <div className="space-y-2 border border-border p-3 text-xs text-foreground">
                      <div className="flex items-start gap-2">
                        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 flex-shrink-0 text-warning" />
                        <span>
                          {uiText(
                            "Moonraker's native Spoolman integration is already decrementing the active spool, so PrintStash automatically skips its own write-back to avoid double-counting. Only override this if you have disabled Moonraker's hook and want PrintStash to count consumption.",
                          )}
                        </span>
                      </div>
                      <label className="flex items-center gap-2 cursor-action pl-5">
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
                  <h4 className="pb-2 pt-3 text-[13px] font-medium text-foreground">
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

              {error && <p className="text-xs text-destructive">{error}</p>}
              {notice && !error && (
                <p className="flex items-center gap-1.5 text-xs text-foreground">
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
