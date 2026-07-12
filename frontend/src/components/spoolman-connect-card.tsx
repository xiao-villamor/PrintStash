"use client";

import { useCallback, useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Loader2, PlugZap, Save } from "lucide-react";

import { testSpoolman, updateSpoolman } from "@/lib/api";
import { useSpoolmanStatus, useSpools } from "@/lib/queries";
import { queryKeys } from "@/lib/query-client";
import { formatGrams } from "@/lib/format";
import { userMessage } from "@/lib/errors";
import type { SpoolmanStatus } from "@/types";

const INPUT_CLASS =
  "w-full px-2.5 py-1.5 text-sm rounded border border-border bg-background text-foreground placeholder:text-muted-foreground/40 disabled:opacity-50";

const SECRET_MASK = "********";

export function SpoolmanConnectCard({ canEdit }: { canEdit: boolean }) {
  const qc = useQueryClient();
  const { data: status, isLoading } = useSpoolmanStatus();
  const enabled = !!status?.enabled;
  const { data: spools } = useSpools({ enabled });

  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  // Hydrate the form from server state once it loads (and after saves).
  useEffect(() => {
    if (status) {
      setBaseUrl(status.base_url ?? "");
      setApiKey(status.has_api_key ? SECRET_MASK : "");
    }
  }, [status]);

  const connected = !!status?.connected;

  // Run a mutation, push the fresh status into the cache so the badge + form
  // reflect it immediately, and surface a one-line outcome. `ok` is the success
  // message (omit for silent toggles).
  const mutate = useCallback(
    async (body: Parameters<typeof updateSpoolman>[0], ok?: string) => {
      setBusy(true);
      setError("");
      setNotice("");
      try {
        const updated = await updateSpoolman(body);
        qc.setQueryData<SpoolmanStatus>(queryKeys.spoolmanStatus, updated);
        if (ok) setNotice(ok);
      } catch (e) {
        setError(userMessage(e));
      } finally {
        setBusy(false);
      }
    },
    [qc],
  );

  const saveConnection = useCallback(
    () =>
      mutate(
        {
          base_url: baseUrl.trim(),
          // Leave the stored key untouched when the mask is unchanged.
          api_key: apiKey === SECRET_MASK ? undefined : apiKey,
        },
        "Saved.",
      ),
    [mutate, baseUrl, apiKey],
  );

  const toggleEnabled = useCallback((next: boolean) => mutate({ enabled: next }), [mutate]);
  const toggleWrite = useCallback((next: boolean) => mutate({ write_enabled: next }), [mutate]);
  const toggleWriteForce = useCallback(
    (next: boolean) => mutate({ write_force: next }),
    [mutate],
  );

  const runTest = useCallback(async () => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      // Test what's typed in (not just the saved config), so a connection can
      // be verified before Save.
      const res = await testSpoolman({
        base_url: baseUrl.trim(),
        api_key: apiKey === SECRET_MASK ? undefined : apiKey,
      });
      if (res.connected) {
        setNotice(`Connected${res.version ? ` — Spoolman v${res.version}` : ""}.`);
      } else {
        setError(res.error || "Spoolman did not respond.");
      }
    } catch (e) {
      setError(userMessage(e));
    } finally {
      setBusy(false);
    }
  }, [baseUrl, apiKey]);

  return (
    <div className="bg-card border border-border rounded overflow-hidden">
      <div className="px-4 sm:px-6 lg:px-8 py-4 sm:py-5 border-b border-border flex items-center justify-between gap-2">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-foreground">Spoolman</h3>
          <p className="text-xs text-muted-foreground mt-0.5">
            Track filament inventory and per-print consumption with a self-hosted
            Spoolman instance. Off by default.
          </p>
        </div>
        <span
          className={`font-mono text-3xs uppercase tracking-wider px-2 py-1 rounded border flex-shrink-0 ${
            enabled && connected
              ? "text-green-600 dark:text-green-400 border-green-600/40"
              : "text-muted-foreground border-border"
          }`}
        >
          {!enabled ? "Disabled" : connected ? "Connected" : "Not connected"}
        </span>
      </div>

      <div className="p-3 sm:p-4 lg:p-6 space-y-4">
        {isLoading ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : !canEdit ? (
          <p className="text-xs text-muted-foreground italic">
            Only an administrator can configure Spoolman.
          </p>
        ) : (
          <>
            {/* Master switch */}
            <label className="flex items-center justify-between gap-3 cursor-pointer">
              <span className="text-sm text-foreground">
                Enable Spoolman integration
              </span>
              <input
                type="checkbox"
                checked={enabled}
                disabled={busy}
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
                  Base URL
                </label>
                <input
                  type="url"
                  value={baseUrl}
                  onChange={(e) => setBaseUrl(e.target.value)}
                  placeholder="http://spoolman.local:7912"
                  className={INPUT_CLASS}
                />
              </div>
              <div>
                <label className="block text-2xs text-muted-foreground mb-1">
                  API key <span className="opacity-60">(optional)</span>
                </label>
                <input
                  type="password"
                  autoComplete="off"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder="Only if Spoolman sits behind an authenticating proxy"
                  className={INPUT_CLASS}
                />
              </div>
              <div className="flex items-center gap-2">
                <button
                  type="submit"
                  disabled={busy || !baseUrl.trim()}
                  className="inline-flex items-center gap-1.5 px-4 py-2 rounded bg-primary text-primary-foreground font-mono text-xs uppercase tracking-wider hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed transition-opacity"
                >
                  {busy ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Save className="h-3.5 w-3.5" />
                  )}
                  Save
                </button>
                <button
                  type="button"
                  onClick={runTest}
                  disabled={busy || !baseUrl.trim()}
                  className="inline-flex items-center gap-1.5 px-3 py-2 rounded border border-border text-muted-foreground font-mono text-xs uppercase tracking-wider hover:bg-muted disabled:opacity-50 transition-colors"
                >
                  <PlugZap className="h-3.5 w-3.5" />
                  Test connection
                </button>
              </div>
            </form>

            {/* Write-back + double-count warning */}
            {enabled && (
              <div className="space-y-2 pt-1 border-t border-border">
                <label className="flex items-center justify-between gap-3 cursor-pointer pt-3">
                  <span className="text-sm text-foreground">
                    Write consumption back to Spoolman
                    <span className="block text-2xs text-muted-foreground">
                      Decrements the selected spool by measured filament when a
                      print completes (Moonraker-measured prints only).
                    </span>
                  </span>
                  <input
                    type="checkbox"
                    checked={!!status?.write_enabled}
                    disabled={busy}
                    onChange={(e) => toggleWrite(e.target.checked)}
                    className="h-4 w-4 flex-shrink-0"
                  />
                </label>
                {status?.native_hook_detected && (
                  <div className="space-y-2 text-2xs text-amber-600 dark:text-amber-400 bg-amber-500/10 border border-amber-500/30 rounded p-2">
                    <div className="flex items-start gap-2">
                      <AlertTriangle className="h-3.5 w-3.5 mt-0.5 flex-shrink-0" />
                      <span>
                        Moonraker&apos;s native Spoolman integration is already
                        decrementing the active spool, so PrintStash
                        automatically skips its own write-back to avoid
                        double-counting. Only override this if you have disabled
                        Moonraker&apos;s hook and want PrintStash to count
                        consumption.
                      </span>
                    </div>
                    <label className="flex items-center gap-2 cursor-pointer pl-5">
                      <input
                        type="checkbox"
                        checked={!!status?.write_force}
                        disabled={busy}
                        onChange={(e) => toggleWriteForce(e.target.checked)}
                        className="h-3.5 w-3.5 flex-shrink-0"
                      />
                      <span>
                        Write back anyway (I disabled Moonraker&apos;s hook)
                      </span>
                    </label>
                  </div>
                )}
              </div>
            )}

            {/* Inventory */}
            {enabled && connected && spools && spools.length > 0 && (
              <div className="pt-1 border-t border-border">
                <h4 className="text-2xs uppercase tracking-wider text-muted-foreground pt-3 pb-2">
                  Inventory
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
                          {s.filament_name || s.name || `Spool ${s.id}`}
                          {s.vendor_name ? (
                            <span className="text-muted-foreground">
                              {" "}
                              · {s.vendor_name}
                            </span>
                          ) : null}
                        </span>
                      </span>
                      <span className="font-mono text-xs text-muted-foreground flex-shrink-0">
                        {formatGrams(s.remaining_weight)} left
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {error && (
              <p className="text-xs text-red-600 dark:text-red-400">{error}</p>
            )}
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
  );
}
