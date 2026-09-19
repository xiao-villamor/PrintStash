import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { HardDrive } from "lucide-react";
import { Card } from "@/components/ui/card";
import { formatBytes } from "@/lib/format";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Localized } from "@/components/ui/localized";
import { artifactCacheApi, type ArtifactCacheRead } from "@/lib/api/artifact-cache";
import { uiText } from "@/lib/locale";
import { toast } from "@/lib/toast";

const LIMITS = [
  { name: "max_entries", labelKey: "Maximum cached files", min: 0 },
  { name: "max_fills", labelKey: "Concurrent downloads", min: 1 },
  {
    name: "fill_wait_seconds",
    labelKey: "Maximum wait for an active download (seconds)",
    min: 0,
  },
  {
    name: "verify_every_hits",
    labelKey: "Recheck digest every N reads (0 disables sampling)",
    min: 0,
  },
] as const;

export function ArtifactCacheCard({ api = artifactCacheApi }: { api?: typeof artifactCacheApi }) {
  const [value, setValue] = useState<ArtifactCacheRead | null>(null);
  const [sizes, setSizes] = useState({ max_bytes: "", headroom_bytes: "" });
  function receive(result: ArtifactCacheRead) {
    setValue(result);
    setSizes({
      max_bytes: String(result.policy.max_bytes / 1024 ** 3),
      headroom_bytes: String(result.policy.headroom_bytes / 1024 ** 3),
    });
  }
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let active = true;
    api
      .read()
      .then((result) => {
        if (!active) return;
        if (result?.policy) {
          receive(result);
          setFailed(false);
        } else {
          setFailed(true);
        }
      })
      .catch(() => {
        if (active) setFailed(true);
      });
    return () => {
      active = false;
    };
  }, [api]);
  const usage = value?.usage ?? {};
  const policy = value?.policy;
  const labels = value?.labels ?? { representation: "artifact", backend: "unknown" };
  const health = value?.health ?? "unavailable";
  const pending = Boolean(usage.maintenance_running || usage.pending_eviction_bytes);
  useEffect(() => {
    if (!pending) return;
    let active = true;
    const timer = window.setInterval(() => {
      void api
        .read()
        .then((result) => {
          if (active) {
            setValue((current) =>
              current
                ? {
                    ...current,
                    usage: result.usage,
                    health: result.health,
                    available: result.available,
                  }
                : result,
            );
            setFailed(false);
          }
        })
        .catch(() => {
          if (active) setFailed(true);
        });
    }, 1000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [api, pending]);

  async function perform(action: () => Promise<ArtifactCacheRead>) {
    setBusy(true);
    try {
      receive(await action());
      setFailed(false);
      toast.success(uiText("Artifact cache settings updated."));
    } catch (error) {
      toast.error(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Localized>
      <Card className="overflow-hidden">
        <div className="flex items-center gap-3 border-b px-4 py-4 sm:px-5">
          <HardDrive className="h-8 w-8 shrink-0 rounded-md bg-muted p-2" aria-hidden />
          <div>
            <h2 className="text-sm font-semibold">{uiText("Downloaded file cache")}</h2>
            <p className="text-xs text-muted-foreground">
              {uiText(
                "Keep local copies of remote files so previews, printing, and downloads can load faster.",
              )}
            </p>
          </div>
        </div>
        <div className="space-y-4 p-4 sm:p-5">
          {failed && (
            <div role="alert" className="space-y-2">
              <p>{uiText("Cache settings could not be loaded.")}</p>
              <Button variant="outline" disabled={busy} onClick={() => void perform(api.read)}>
                {uiText("Retry")}
              </Button>
            </div>
          )}
          {!failed && !value && (
            <p role="status" className="text-sm text-muted-foreground">
              {uiText("Loading cache settings…")}
            </p>
          )}
          {value && policy && (
            <form
              className="space-y-4"
              onSubmit={(event) => {
                event.preventDefault();
                void perform(() =>
                  api.save({
                    ...policy,
                    max_bytes: Math.round(Number(sizes.max_bytes) * 1024 ** 3),
                    headroom_bytes: Math.round(Number(sizes.headroom_bytes) * 1024 ** 3),
                  }),
                );
              }}
            >
              <label className="flex items-center gap-3">
                <Checkbox
                  checked={policy.enabled}
                  disabled={busy}
                  ariaLabel={uiText("Keep downloaded files on this machine")}
                  onChange={(checked) =>
                    setValue({ ...value, policy: { ...policy, enabled: checked === true } })
                  }
                />
                <span>{uiText("Keep downloaded files on this machine")}</span>
              </label>
              <p className="text-xs text-muted-foreground">
                {uiText(
                  policy.enabled
                    ? "Save copies of downloaded files to make them faster to open again."
                    : "Caching is off. Your files stay in their original storage. Enable it to keep local copies of remote files.",
                )}
              </p>
              {policy.enabled && (
                <>
                  <div className="grid gap-4 sm:grid-cols-2">
                    {(
                      [
                        ["max_bytes", "Cache size limit (GB)"],
                        ["headroom_bytes", "Keep free on disk (GB)"],
                      ] as const
                    ).map(([name, label]) => (
                      <label key={name} className="space-y-1 text-sm">
                        <span className="block">{uiText(label)}</span>
                        <Input
                          required
                          type="number"
                          min={0}
                          max={Number.MAX_SAFE_INTEGER / 1024 ** 3}
                          step="any"
                          value={sizes[name]}
                          disabled={busy}
                          onChange={(event) => setSizes({ ...sizes, [name]: event.target.value })}
                        />
                      </label>
                    ))}
                  </div>
                  <p className="text-xs text-muted-foreground">
                    {uiText(
                      "The cache stays within this limit and leaves the free space you choose. 1 GB = 1,024 MB.",
                    )}
                  </p>
                </>
              )}
              <details className="space-y-4 border-t">
                <summary className="cursor-pointer py-3 text-sm font-medium">
                  {uiText("Advanced cache settings")}
                </summary>
                <p className="text-sm text-muted-foreground">
                  {uiText("Policy source:")}{" "}
                  {value.source === "database"
                    ? uiText("Saved settings")
                    : uiText("Environment defaults")}
                  . {uiText("Limits apply immediately; changing the folder requires a restart.")}
                </p>
                <div className="grid gap-4 sm:grid-cols-2">
                  {LIMITS.map(({ name, labelKey, min }) => (
                    <label className="space-y-1" key={name}>
                      <span className="block text-sm">{uiText(labelKey)}</span>
                      <Input
                        required
                        type="number"
                        min={min}
                        step={1}
                        value={policy[name]}
                        disabled={busy}
                        onChange={(event) =>
                          setValue({
                            ...value,
                            policy: { ...policy, [name]: event.target.valueAsNumber },
                          })
                        }
                      />
                    </label>
                  ))}
                </div>
                <label className="block space-y-1">
                  <span className="text-sm">{uiText("Cache folder (restart required)")}</span>
                  <Input
                    required
                    value={policy.root}
                    disabled={busy}
                    onChange={(event) =>
                      setValue({ ...value, policy: { ...policy, root: event.target.value } })
                    }
                  />
                </label>
                <Button
                  type="button"
                  variant="outline"
                  disabled={busy}
                  onClick={() => void perform(api.reset)}
                >
                  {uiText("Restore cache defaults")}
                </Button>
              </details>
              {value.restart_required && (
                <p role="status" className="text-sm text-warning">
                  {uiText("Restart PrintStash to use the new cache folder.")}
                </p>
              )}
              {policy.enabled && !value.available && (
                <p role="status" className="text-sm text-warning">
                  {uiText(
                    "Caching is enabled but not available. Your files still open from their original storage. Check diagnostics below.",
                  )}
                </p>
              )}
              {(policy.enabled || (usage.bytes ?? 0) > 0) && (
                <div className="flex flex-wrap items-end justify-between gap-4 border-t py-4">
                  <div>
                    <p className="text-xs text-muted-foreground">{uiText("Downloaded copies")}</p>
                    <p className="mt-1 text-2xl font-semibold tabular-nums">
                      {formatBytes(usage.bytes ?? 0)}
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {uiText("Your original library files are kept separately.")}
                    </p>
                  </div>
                  {(usage.bytes ?? 0) > 0 && (
                    <Button
                      type="button"
                      variant="outline"
                      disabled={busy}
                      onClick={() => void perform(api.clear)}
                    >
                      {uiText("Clear cached files")}
                    </Button>
                  )}
                </div>
              )}
              <details className="space-y-3 border-t">
                <summary className="cursor-pointer py-3 text-sm font-medium">
                  {uiText("Cache diagnostics")}
                </summary>
                <p className="text-sm text-muted-foreground">
                  {usage.entries ?? 0} {uiText("files")} · {usage.leases ?? 0}{" "}
                  {uiText("active reads")}
                </p>
                <p className="text-sm text-muted-foreground">
                  {uiText("Download traffic saved")}: {formatBytes(usage.bytes_saved ?? 0)}
                </p>
                <dl className="grid grid-cols-2 gap-3 text-sm tabular-nums sm:grid-cols-3">
                  {[
                    [uiText("Cache size limit (GB)"), policy.max_bytes / 1024 ** 3],
                    [uiText("Hit ratio"), `${usage.hit_ratio_percent ?? 0}%`],

                    [uiText("Cache hits"), usage.hits ?? 0],
                    [uiText("Cache misses"), usage.misses ?? 0],
                    [uiText("Completed downloads"), usage.completed_fills ?? 0],
                    [uiText("Publication failures"), usage.publication_failures ?? 0],
                    [uiText("Corruptions"), usage.corruptions ?? 0],
                    [uiText("Cache errors"), usage.errors ?? 0],
                    [uiText("Evictions"), usage.evictions ?? 0],
                    [uiText("Bypasses"), usage.bypasses ?? 0],
                  ].map(([label, count]) => (
                    <div key={label}>
                      <dt className="text-muted-foreground">{label}</dt>
                      <dd>{count}</dd>
                    </div>
                  ))}
                </dl>
                <p className="text-xs text-muted-foreground">
                  {uiText("Last verification:")}{" "}
                  {usage.last_verification
                    ? new Date(usage.last_verification * 1000).toLocaleString()
                    : uiText("No cached files verified yet")}
                  .
                </p>
                <p className="text-xs text-muted-foreground">
                  {uiText("Representation:")} {labels.representation} ·{" "}
                  {uiText("Storage provider:")} {labels.backend}
                </p>
              </details>
              {health !== "ready" && health !== "disabled" && (
                <p role="status" className="text-sm text-warning">
                  {uiText("Cache needs attention")} ({health.replaceAll("_", " ")}).{" "}
                  {uiText("Original Vault storage remains authoritative.")}
                </p>
              )}
              {pending && (
                <p role="status" className="text-sm text-muted-foreground">
                  {uiText("Reclaiming cache space.")}{" "}
                  {formatBytes(usage.pending_eviction_bytes ?? 0)}{" "}
                  {uiText("will be freed when files are no longer in use.")}
                </p>
              )}
              <div className="flex flex-wrap gap-2">
                <Button type="submit" disabled={busy}>
                  {uiText("Save cache settings")}
                </Button>
              </div>
            </form>
          )}
        </div>
      </Card>
    </Localized>
  );
}
