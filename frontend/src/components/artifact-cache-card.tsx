import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Localized } from "@/components/ui/localized";
import { Skeleton } from "@/components/ui/skeleton";
import { TabBar } from "@/components/ui/tabs";
import {
  artifactCacheApi,
  type ArtifactCacheRead,
  type ArtifactCachePolicy,
} from "@/lib/api/artifact-cache";
import { useArtifactCache } from "@/lib/queries/settings-artifact-cache";
import { captureEditingBase } from "@/lib/api/editing";
import { parseApiError } from "@/lib/errors";
import type { EditingBase } from "@/types/editing";
import { formatBytes } from "@/lib/format";
import { uiText } from "@/lib/locale";
import { toast } from "@/lib/toast";

const SIZE_LIMITS = [
  { name: "max_bytes", labelKey: "Maximum cache size" },
  { name: "headroom_bytes", labelKey: "Minimum free space" },
] as const;

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

type SizeName = (typeof SIZE_LIMITS)[number]["name"];
type SizeUnit = "MB" | "GB";
const MB = 1024 ** 2;
const GB = 1024 ** 3;

function sizeUnit(bytes: number): SizeUnit {
  return bytes >= GB ? "GB" : "MB";
}

function unitsForPolicy(policy: ArtifactCacheRead["policy"]) {
  return {
    max_bytes: sizeUnit(policy.max_bytes),
    headroom_bytes: sizeUnit(policy.headroom_bytes),
  };
}

function draftsForPolicy(policy: ArtifactCacheRead["policy"], units: Record<SizeName, SizeUnit>) {
  return {
    max_bytes: Number(
      (policy.max_bytes / (units.max_bytes === "GB" ? GB : MB)).toPrecision(4),
    ).toString(),
    headroom_bytes: Number(
      (policy.headroom_bytes / (units.headroom_bytes === "GB" ? GB : MB)).toPrecision(4),
    ).toString(),
  };
}

function readableCacheSize(bytes: number): string {
  if (bytes === 0) return "0 MB";
  if (bytes < MB) return "<1 MB";
  return formatBytes(bytes);
}

export function ArtifactCacheCard({ api = artifactCacheApi }: { api?: typeof artifactCacheApi }) {
  const owner = useArtifactCache(api);
  const value = owner.query.data;
  const failed = owner.query.isError;
  const denied =
    owner.retired || (failed && [401, 403, 404].includes(parseApiError(owner.query.error).status));
  const [draft, setDraft] = useState<{
    base: EditingBase;
    original: ArtifactCachePolicy;
    policy: ArtifactCachePolicy;
  } | null>(null);
  const [review, setReview] = useState<
    | { phase: "idle" }
    | {
        phase: "required" | "loading";
        action: "save" | "reset";
        problem: "conflict" | "unconfirmed";
      }
    | {
        phase: "ready";
        action: "save" | "reset";
        problem: "conflict" | "unconfirmed";
        snapshot: ArtifactCacheRead;
      }
  >({ phase: "idle" });
  const [detailView, setDetailView] = useState<"overview" | "limits" | "activity">("overview");
  const policy = draft?.policy ?? value?.policy;
  const sizeUnits = policy
    ? unitsForPolicy(draft?.original ?? policy)
    : ({ max_bytes: "GB", headroom_bytes: "GB" } as const);
  const [localSizes, setLocalSizes] = useState<Record<SizeName, string> | null>(null);
  const sizeDrafts =
    localSizes ??
    (policy ? draftsForPolicy(policy, sizeUnits) : { max_bytes: "", headroom_bytes: "" });
  function setValue(next: ArtifactCacheRead) {
    if (!value) return;
    setDraft((current) => ({
      base: current?.base ?? captureEditingBase(value),
      original: current?.original ?? value.policy,
      policy: next.policy,
    }));
  }
  const usage = value?.usage ?? {};
  const labels = value?.labels ?? { representation: "artifact", backend: "unknown" };
  const health = value?.health ?? "unavailable";
  const pending = Boolean(usage.maintenance_running || usage.pending_eviction_bytes);
  const busy = owner.busy || failed || review.phase !== "idle";
  async function perform(kind: "save" | "reset" | "clear", revised?: ArtifactCacheRead) {
    if (!value || !policy) return;
    try {
      const base = captureEditingBase(revised ?? draft?.base ?? value);
      const keys: (keyof ArtifactCachePolicy)[] = [
        "enabled",
        "root",
        "max_bytes",
        "max_entries",
        "max_fills",
        "headroom_bytes",
        "verify_every_hits",
        "fill_wait_seconds",
      ];
      const submitted =
        revised && draft
          ? keys.reduce(
              (result, key) =>
                draft.original[key] === policy[key] ? result : { ...result, [key]: policy[key] },
              revised.policy,
            )
          : policy;
      await owner.execute(
        kind === "clear"
          ? { kind }
          : kind === "reset"
            ? { kind, base }
            : { kind, base, policy: submitted },
      );
      if (!owner.current()) return;
      if (kind !== "clear") {
        setDraft(null);
        setLocalSizes(null);
        setReview({ phase: "idle" });
      }
      toast.success(uiText("Artifact cache settings updated."));
    } catch (error) {
      if (!owner.current()) return;
      const status = parseApiError(error).status;
      if (kind !== "clear" && ([0, 412, 428].includes(status) || status >= 500))
        setReview({
          phase: "required",
          action: kind,
          problem: status === 412 || status === 428 ? "conflict" : "unconfirmed",
        });
      toast.error(error);
    }
  }
  async function reviewCurrent() {
    if (review.phase === "idle") return;
    setReview({ ...review, phase: "loading" });
    const result = await owner.query.refetch();
    if (!owner.current()) return;
    setReview(
      result.isError || !result.data
        ? { ...review, phase: "required" }
        : { ...review, phase: "ready", snapshot: result.data },
    );
  }

  return (
    <Localized>
      <Card>
        <CardHeader className="border-b border-border">
          <CardTitle>{uiText("Remote file cache")}</CardTitle>
          <CardDescription>
            {uiText(
              "Reuse verified remote files for previews, printing, and downloads. Original files remain in Vault storage.",
            )}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5 pt-5">
          {(failed || denied) && (
            <div role="alert" className="space-y-2">
              <p>{uiText("Cache settings could not be loaded.")}</p>
              <Button
                variant="outline"
                disabled={owner.busy}
                onClick={() => void owner.query.refetch()}
              >
                {uiText("Retry")}
              </Button>
            </div>
          )}
          {!failed && !value && (
            <div role="status" aria-label={uiText("Loading cache settings…")} className="space-y-5">
              <Skeleton className="h-10 w-64 max-w-full" />
              <Skeleton className="h-4 w-48 max-w-full" />
              <Skeleton className="h-10 w-72 max-w-full" />
              <div className="grid gap-4 sm:grid-cols-3">
                {[0, 1, 2].map((item) => (
                  <Skeleton key={item} className="h-16 w-full" />
                ))}
              </div>
              <span className="sr-only">{uiText("Loading cache settings…")}</span>
            </div>
          )}
          {!denied && review.phase !== "idle" && (
            <section role="alert" className="space-y-3 rounded border border-border p-4">
              <p>
                {uiText(
                  review.problem === "conflict"
                    ? "library.editConflict"
                    : "library.saveUnconfirmed",
                )}
              </p>
              <Button
                disabled={owner.busy || review.phase === "loading"}
                onClick={() => void reviewCurrent()}
              >
                {uiText("Review current values")}
              </Button>
              {review.phase === "ready" && (
                <>
                  <dl aria-label={uiText("library.latestVersion")} className="space-y-1 text-sm">
                    {[
                      [
                        uiText("Enable remote Artifact cache"),
                        uiText(review.snapshot.policy.enabled ? "Enabled" : "Disabled"),
                      ],
                      [uiText("Cache folder (restart required)"), review.snapshot.policy.root],
                      ...SIZE_LIMITS.map(({ name, labelKey }) => [
                        uiText(labelKey),
                        readableCacheSize(review.snapshot.policy[name]),
                      ]),
                      ...LIMITS.map(({ name, labelKey }) => [
                        uiText(labelKey),
                        String(review.snapshot.policy[name]),
                      ]),
                    ].map(([label, content]) => (
                      <div key={label}>
                        <dt>{label}</dt>
                        <dd>{content}</dd>
                      </div>
                    ))}
                  </dl>
                  <Button
                    disabled={owner.busy}
                    onClick={() => {
                      setDraft({
                        base: captureEditingBase(review.snapshot),
                        original: review.snapshot.policy,
                        policy: review.snapshot.policy,
                      });
                      setLocalSizes(null);
                      setReview({ phase: "idle" });
                    }}
                  >
                    {uiText("Use current values")}
                  </Button>
                  <Button
                    disabled={
                      owner.busy ||
                      review.snapshot.edit_epoch !== (draft?.base ?? value)?.edit_epoch
                    }
                    onClick={() => void perform(review.action, review.snapshot)}
                  >
                    {uiText("Save revised changes")}
                  </Button>
                </>
              )}
            </section>
          )}
          {!denied && value && policy && (
            <form
              className="space-y-4"
              onSubmit={(event) => {
                event.preventDefault();
                void perform("save");
              }}
            >
              <label className="flex min-h-11 items-center gap-3 font-medium">
                <Checkbox
                  checked={policy.enabled}
                  disabled={busy}
                  ariaLabel={uiText("Enable remote Artifact cache")}
                  onChange={(checked) =>
                    setValue({ ...value, policy: { ...policy, enabled: checked === true } })
                  }
                />
                <span>{uiText("Enable remote Artifact cache")}</span>
              </label>
              <p className="text-sm tabular-nums text-muted-foreground">
                {readableCacheSize(usage.bytes ?? 0)} {uiText("cached")} · {usage.entries ?? 0}{" "}
                {uiText("files")}
                {(usage.leases ?? 0) > 0 && (
                  <>
                    {" "}
                    · {usage.leases} {uiText("active reads")}
                  </>
                )}
              </p>
              <div className="border-t border-border pt-4">
                <TabBar
                  tabs={[
                    { key: "overview", label: uiText("Overview") },
                    { key: "limits", label: uiText("Cache limits") },
                    { key: "activity", label: uiText("Activity") },
                  ]}
                  active={detailView}
                  onChange={setDetailView}
                  className="inline-flex rounded-md bg-muted/40 p-1"
                  tabClassName="rounded-sm px-3 py-2 text-sm text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  activeTabClassName="bg-accent text-accent-foreground"
                  showIndicator={false}
                />
              </div>
              {detailView === "overview" && (
                <div
                  role="tabpanel"
                  aria-label={uiText("Overview")}
                  className="grid gap-3 text-sm sm:grid-cols-3"
                >
                  <p>
                    <span className="block text-muted-foreground">{uiText("Policy source:")}</span>
                    {value.source === "database"
                      ? uiText("Saved settings")
                      : uiText("Environment defaults")}
                  </p>
                  <p>
                    <span className="block text-muted-foreground">{uiText("Hit ratio")}</span>
                    {usage.hit_ratio_percent ?? 0}%
                  </p>
                  <p>
                    <span className="block text-muted-foreground">
                      {uiText("Last verification:")}
                    </span>
                    {usage.last_verification
                      ? new Date(usage.last_verification * 1000).toLocaleString()
                      : uiText("No cached files verified yet")}
                  </p>
                </div>
              )}
              {detailView === "limits" && (
                <div role="tabpanel" aria-label={uiText("Cache limits")} className="space-y-4">
                  <p className="text-sm text-muted-foreground">
                    {uiText("Policy source:")}{" "}
                    {value.source === "database"
                      ? uiText("Saved settings")
                      : uiText("Environment defaults")}
                    . {uiText("Limits apply immediately; changing the folder requires a restart.")}
                  </p>
                  <div className="grid gap-4 sm:grid-cols-2 2xl:grid-cols-3">
                    {SIZE_LIMITS.map(({ name, labelKey }) => (
                      <label className="space-y-1" key={name}>
                        <span className="block text-sm">{uiText(labelKey)}</span>
                        <div className="relative">
                          <Input
                            required
                            type="number"
                            min={0}
                            max={Math.floor(
                              Number.MAX_SAFE_INTEGER / (sizeUnits[name] === "GB" ? GB : MB),
                            )}
                            step="any"
                            value={sizeDrafts[name]}
                            disabled={busy}
                            aria-label={`${uiText(labelKey)} (${sizeUnits[name]})`}
                            className="pr-12"
                            onChange={(event) => {
                              const draft = event.target.value;
                              setLocalSizes({ ...sizeDrafts, [name]: draft });
                              const number = event.target.valueAsNumber;
                              if (Number.isFinite(number) && number >= 0) {
                                setValue({
                                  ...value,
                                  policy: {
                                    ...policy,
                                    [name]: Math.round(
                                      number * (sizeUnits[name] === "GB" ? GB : MB),
                                    ),
                                  },
                                });
                              }
                            }}
                          />
                          <span
                            aria-hidden="true"
                            className="pointer-events-none absolute inset-y-0 right-3 flex items-center text-xs font-medium text-muted-foreground"
                          >
                            {sizeUnits[name]}
                          </span>
                        </div>
                      </label>
                    ))}
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
                </div>
              )}
              {value.restart_required && (
                <p role="status" className="text-sm text-warning">
                  {uiText("Restart PrintStash to use the new cache folder.")}
                </p>
              )}
              {!value.available && (
                <p className="text-sm text-muted-foreground">
                  {uiText("The cache is unavailable. Files are read from their original storage.")}
                </p>
              )}
              {health !== "ready" && health !== "disabled" && (
                <p role="status" className="text-sm text-warning">
                  {uiText("Cache needs attention")} ({health.replaceAll("_", " ")}).{" "}
                  {uiText("Original Vault storage remains authoritative.")}
                </p>
              )}
              {detailView === "activity" && (
                <div role="tabpanel" aria-label={uiText("Activity")} className="space-y-4">
                  <dl className="grid grid-cols-2 gap-3 text-sm tabular-nums sm:grid-cols-3">
                    {[
                      [uiText("Maximum cache size"), readableCacheSize(policy.max_bytes)],
                      [uiText("Hit ratio"), `${usage.hit_ratio_percent ?? 0}%`],
                      [uiText("Provider data saved"), readableCacheSize(usage.bytes_saved ?? 0)],
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
                </div>
              )}
              {pending && (
                <p role="status" className="text-sm text-muted-foreground">
                  {uiText("Reclaiming cache space.")}{" "}
                  {readableCacheSize(usage.pending_eviction_bytes ?? 0)}{" "}
                  {uiText("wait for active reads to finish.")}
                </p>
              )}
              <div className="flex flex-wrap gap-2">
                <Button type="submit" disabled={busy}>
                  {uiText("Save cache settings")}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  disabled={busy}
                  onClick={() => void perform("reset")}
                >
                  {uiText("Reset to environment defaults")}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  disabled={busy}
                  onClick={() => void perform("clear")}
                >
                  {uiText("Clear cached files")}
                </Button>
              </div>
              <p className="text-xs text-muted-foreground">
                {uiText(
                  "Files in use stay available until their active reads finish. Clearing does not remove your Artifacts.",
                )}
              </p>
            </form>
          )}
        </CardContent>
      </Card>
    </Localized>
  );
}
