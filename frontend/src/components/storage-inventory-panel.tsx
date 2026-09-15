import { useCallback, useEffect, useState } from "react";
import { HardDrive, RefreshCw } from "lucide-react";
import { Link } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import {
  cleanupStorageCache,
  cleanupStorageStaging,
  getCollectionStorage,
  getModelStorage,
  getStorageCapacityActivity,
  getStorageCleanupOpportunities,
  getStorageInventory,
  sampleStorageInventory,
  type CollectionStorageRow,
  type CleanupOpportunity,
  type ModelStorageRow,
  type StorageCapacityActivity,
  type StorageInventoryReport,
} from "@/lib/api/storage-inventory";
import { useI18n } from "@/lib/i18n";
import { formatBytes } from "@/lib/format";
import { knownUiText } from "@/lib/locale";

const PAGE_SIZE = 10;

function bytes(value: number | null, locale: string): string {
  if (value === null) return knownUiText("Unknown");
  return new Intl.NumberFormat(locale, {
    style: "unit",
    unit: "gigabyte",
    maximumFractionDigits: 2,
  }).format(value / 1024 ** 3);
}

export function StorageInventoryPanel() {
  const { locale, t } = useI18n();
  const [report, setReport] = useState<StorageInventoryReport | null>(null);
  const [activity, setActivity] = useState<StorageCapacityActivity | null>(null);
  const [collections, setCollections] = useState<CollectionStorageRow[]>([]);
  const [cleanupPreviews, setCleanupPreviews] = useState<CleanupOpportunity[]>([]);
  const [cleanupState, setCleanupState] = useState<"loading" | "ready" | "error">("loading");
  const [collectionOffset, setCollectionOffset] = useState(0);
  const [models, setModels] = useState<ModelStorageRow[]>([]);
  const [modelOffset, setModelOffset] = useState(0);
  const [selectedCollection, setSelectedCollection] = useState<CollectionStorageRow | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  const [cleanupTarget, setCleanupTarget] = useState<"staging" | "cache" | null>(null);
  const [result, setResult] = useState<string | null>(null);

  const loadCollectionPage = useCallback(async (offset: number) => {
    try {
      const rows = await getCollectionStorage(offset, PAGE_SIZE);
      setCollections(Array.isArray(rows) ? rows : []);
      setCollectionOffset(offset);
    } catch {
      setCollections([]);
    }
  }, []);

  const load = useCallback(async () => {
    try {
      const current = await getStorageInventory();
      setReport(current);
      setError(false);
      const [nextActivity, , nextCleanup] = await Promise.allSettled([
        getStorageCapacityActivity(),
        loadCollectionPage(0),
        getStorageCleanupOpportunities(),
      ]);
      if (
        nextActivity.status === "fulfilled" &&
        Array.isArray(nextActivity.value.active_reservations) &&
        Array.isArray(nextActivity.value.recent_denials)
      ) {
        setActivity(nextActivity.value);
      }
      if (nextCleanup.status === "fulfilled" && Array.isArray(nextCleanup.value)) {
        setCleanupPreviews(nextCleanup.value);
        setCleanupState("ready");
      } else {
        setCleanupPreviews([]);
        setCleanupState("error");
      }
    } catch {
      setError(true);
    }
  }, [loadCollectionPage]);

  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect -- state updates follow the request continuation.
    void load();
  }, [load]);

  async function refresh() {
    setBusy(true);
    try {
      await sampleStorageInventory();
      await load();
    } catch {
      setError(true);
    } finally {
      setBusy(false);
    }
  }

  async function cleanup() {
    setBusy(true);
    try {
      if (cleanupTarget === "cache") {
        const cleaned = await cleanupStorageCache();
        setResult(
          t("{count} derived cache objects queued for verified cleanup.", {
            count: cleaned.enqueued,
          }),
        );
      } else {
        const cleaned = await cleanupStorageStaging();
        setResult(
          t("{files} files removed; {leases} expired leases cleared.", {
            files: cleaned.files_removed,
            leases: cleaned.leases_removed,
          }),
        );
      }
      setCleanupTarget(null);
      await load();
    } catch {
      setError(true);
    } finally {
      setBusy(false);
    }
  }

  async function showModels(collection: CollectionStorageRow, offset = 0) {
    try {
      const rows = await getModelStorage(collection.collection_id ?? 0, offset, PAGE_SIZE);
      setModels(Array.isArray(rows) ? rows : []);
      setSelectedCollection(collection);
      setModelOffset(offset);
    } catch {
      setModels([]);
    }
  }

  const current = report?.inventory;
  const samples = [...(report?.history ?? [])].reverse();
  const largestSample = Math.max(1, ...samples.map((sample) => sample.owned_bytes));
  const historyPoints = samples
    .map(
      (sample, index) =>
        `${12 + (index * 576) / Math.max(1, samples.length - 1)},${108 - (sample.owned_bytes / largestSample) * 96}`,
    )
    .join(" ");

  return (
    <Card className="overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b px-4 py-4 sm:px-5">
        <div className="flex min-w-0 items-center gap-3">
          <HardDrive className="h-8 w-8 rounded-md bg-muted p-2" />
          <div>
            <h2 className="text-sm font-semibold">{t("Library storage")}</h2>
            <p className="text-xs text-muted-foreground">
              {t("See how much space your library uses and free up space when needed.")}
            </p>
          </div>
        </div>
        <Button variant="outline" size="sm" disabled={busy} onClick={refresh}>
          <RefreshCw className="mr-2 h-4 w-4" />
          {t("Refresh measurement")}
        </Button>
      </div>
      {error && (
        <div role="alert" className="border-b p-4 text-sm text-destructive">
          {t("Storage insights could not be loaded. Retry the measurement.")}
        </div>
      )}
      {!current && !error && (
        <p role="status" className="p-4 text-sm text-muted-foreground">
          {t("Loading storage insights…")}
        </p>
      )}
      {current && (
        <>
          <div className="grid gap-6 p-5 sm:grid-cols-2">
            <div>
              <p className="text-sm text-muted-foreground">{t("Library files")}</p>
              <p className="mt-2 text-3xl font-semibold tracking-tight tabular-nums">
                {bytes(current.unique_owned_bytes, locale)}
              </p>
              <p className="mt-2 text-xs text-muted-foreground">
                {t("Space used by files stored in your library.")}
              </p>
            </div>
            <div>
              <p className="text-sm text-muted-foreground">{t("Free space")}</p>
              <p className="mt-2 text-3xl font-semibold tracking-tight tabular-nums">
                {bytes(
                  current.volumes.find((volume) => volume.roles.includes("vault"))?.free_bytes ??
                    null,
                  locale,
                )}
              </p>
              <p className="mt-2 text-xs text-muted-foreground">
                {t("Available where your library files are stored.")}
              </p>
            </div>
          </div>
          {current.unknown_object_count > 0 && (
            <p className="px-4 py-3 text-xs text-muted-foreground">
              {t("Some file sizes are unknown, so usage may be higher.")}
            </p>
          )}
          <div className="divide-y divide-border">
            {current.volumes
              .filter((volume) => volume.status !== "available")
              .map((volume) => (
                <div
                  key={volume.domain_id}
                  className="flex flex-wrap items-start justify-between gap-3 px-4 py-3 text-sm"
                >
                  <div>
                    <p className="font-medium">
                      {t("Free space")}: {bytes(volume.free_bytes, locale)}
                    </p>
                  </div>
                  <p
                    className={
                      volume.status === "blocked" ? "text-destructive" : "text-muted-foreground"
                    }
                  >
                    {volume.status === "blocked"
                      ? t("Storage is full — free up space to continue")
                      : volume.status === "available"
                        ? t("Space available")
                        : t("Free space is unknown")}
                  </p>
                </div>
              ))}
          </div>
          <div className="px-4 text-xs text-muted-foreground">
            {current.latest_audit && current.latest_audit.unclaimed_object_count > 0 && (
              <p className="mt-2 text-warning">
                {t(
                  "Latest audit found {count} unattributed storage objects. They are observations, never cleanup authority.",
                  { count: current.latest_audit.unclaimed_object_count },
                )}{" "}
                {t("Known size: {size}. Unknown sizes: {count}.", {
                  size:
                    current.latest_audit.unclaimed_bytes === null
                      ? t("Unknown")
                      : formatBytes(current.latest_audit.unclaimed_bytes),
                  count: current.latest_audit.unknown_size_count,
                })}
              </p>
            )}
          </div>
          <details className="border-t">
            <summary className="cursor-pointer px-4 py-3 text-sm font-medium">
              {t("Storage breakdown and diagnostics")}
            </summary>
            <dl className="grid gap-3 border-b p-4 text-sm sm:grid-cols-2">
              <div>
                <dt>{t("Linked files")}</dt>
                <dd>{bytes(current.external_referenced_bytes, locale)}</dd>
              </div>
              <div>
                <dt>{t("Temporary files")}</dt>
                <dd>{bytes(current.temporary_bytes, locale)}</dd>
              </div>
            </dl>
            {current.volumes.map((volume) => (
              <p key={volume.domain_id} className="px-4 py-2 text-xs text-muted-foreground">
                {t("Set aside for running tasks: {reserved} · Kept free: {headroom}", {
                  reserved: bytes(volume.reserved_bytes, locale),
                  headroom: bytes(volume.headroom_bytes, locale),
                })}
              </p>
            ))}
            <div className="border-b px-4 py-3 text-xs text-muted-foreground">
              <p>
                {t("Usage includes known sizes only. {count} objects have no recorded size.", {
                  count: current.unknown_object_count,
                })}
              </p>
              <p className="mt-1">
                {t("Provider measurement")}:{" "}
                {current.provider_capacity.measured_at
                  ? new Date(current.provider_capacity.measured_at).toLocaleString(locale)
                  : t("Not yet measured")}
                .{" "}
                {t("Capacity evidence is {status} ({method}, {reliability}).", {
                  status: current.provider_capacity.status,
                  method: current.provider_capacity.method,
                  reliability: current.provider_capacity.reliability,
                })}
              </p>
            </div>
            <section className="border-t px-4 py-3">
              <h3 className="text-sm font-semibold">{t("Usage by category")}</h3>
              <div className="mt-2 divide-y divide-border">
                {current.buckets
                  .filter((bucket) => bucket.logical_bytes > 0)
                  .map((bucket) => (
                    <div
                      key={`${bucket.category}-${bucket.lifecycle}`}
                      className="flex flex-wrap justify-between gap-2 py-2 text-xs"
                    >
                      <span>
                        {knownUiText(bucket.category, locale)} ·{" "}
                        {knownUiText(bucket.lifecycle, locale)}
                      </span>
                      <span className="tabular-nums">{bytes(bucket.logical_bytes, locale)}</span>
                    </div>
                  ))}
                {current.logical_bytes === 0 && (
                  <p className="py-2 text-sm text-muted-foreground">
                    {t("No recorded library bytes.")}
                  </p>
                )}
              </div>
            </section>
            <section className="border-t px-4 py-3">
              <h3 className="text-sm font-semibold">{t("Growth forecast")}</h3>
              {samples.length > 1 && (
                <figure className="mt-3">
                  <svg
                    role="img"
                    aria-label={t("Recorded owned storage over time")}
                    viewBox="0 0 600 120"
                    className="h-28 w-full text-primary"
                  >
                    <title>{t("Recorded owned storage over time")}</title>
                    <polyline
                      points={historyPoints}
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                    />
                  </svg>
                  <figcaption className="flex justify-between text-xs text-muted-foreground">
                    <span>{new Date(samples[0].sampled_at).toLocaleDateString(locale)}</span>
                    <span>{t("{size} maximum", { size: bytes(largestSample, locale) })}</span>
                    <span>
                      {new Date(samples[samples.length - 1].sampled_at).toLocaleDateString(locale)}
                    </span>
                  </figcaption>
                </figure>
              )}
              <p className="mt-1 text-xs text-muted-foreground">
                {report.forecast.days_remaining !== null
                  ? t("Estimated {days} days of headroom at recent growth.", {
                      days: Math.floor(report.forecast.days_remaining),
                    })
                  : t(
                      "A forecast needs seven daily samples spanning at least seven days, stable positive growth, and known capacity.",
                    )}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                {t(
                  "{count} samples over {days} days · {confidence} confidence. Forecasts do not authorize writes.",
                  {
                    count: report.forecast.sample_count,
                    days: Math.floor(report.forecast.window_days),
                    confidence: report.forecast.confidence,
                  },
                )}
              </p>
            </section>
            <section className="grid gap-4 border-t p-4 lg:grid-cols-2">
              <div>
                <h3 className="text-sm font-semibold">{t("Capacity activity")}</h3>
                <h4 className="mt-3 text-xs font-medium">{t("Active reservations")}</h4>
                {activity?.active_reservations.length ? (
                  <ul className="mt-1 space-y-1 text-xs text-muted-foreground">
                    {activity.active_reservations.map((reservation, index) => (
                      <li key={`${reservation.operation_kind}-${reservation.created_at}-${index}`}>
                        {t("{operation} · {size}", {
                          operation: reservation.operation_kind,
                          size: bytes(reservation.required_bytes, locale),
                        })}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="mt-1 text-xs text-muted-foreground">
                    {t("No active reservations.")}
                  </p>
                )}
                <h4 className="mt-3 text-xs font-medium">{t("Recent blocked operations")}</h4>
                {activity?.recent_denials.length ? (
                  <ul className="mt-1 space-y-1 text-xs text-muted-foreground">
                    {activity.recent_denials.map((denial, index) => (
                      <li key={`${denial.operation_kind}-${denial.occurred_at}-${index}`}>
                        {t("{operation} · {size}", {
                          operation: denial.operation_kind,
                          size: bytes(denial.required_bytes, locale),
                        })}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="mt-1 text-xs text-muted-foreground">
                    {t("No recent blocked operations.")}
                  </p>
                )}
              </div>
              <div>
                <h3 className="text-sm font-semibold">{t("Collection storage")}</h3>
                {collections.length ? (
                  <div className="mt-2 divide-y divide-border">
                    {collections.map((collection) => (
                      <div
                        key={collection.collection_id ?? "uncollected"}
                        className="flex items-center justify-between gap-3 py-2 text-xs"
                      >
                        <span className="min-w-0 truncate">
                          {collection.name} · {bytes(collection.logical_bytes, locale)}
                        </span>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => void showModels(collection)}
                        >
                          {t("View Models")}
                        </Button>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="mt-2 text-xs text-muted-foreground">
                    {t("No visible Collection storage.")}
                  </p>
                )}
                <div className="mt-2 flex gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={collectionOffset === 0}
                    onClick={() =>
                      void loadCollectionPage(Math.max(0, collectionOffset - PAGE_SIZE))
                    }
                  >
                    {t("Back")}
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={collections.length < PAGE_SIZE}
                    onClick={() => void loadCollectionPage(collectionOffset + PAGE_SIZE)}
                  >
                    {t("Next")}
                  </Button>
                </div>
              </div>
            </section>
            {selectedCollection && (
              <section className="border-t px-4 py-3">
                <h3 className="text-sm font-semibold">
                  {t("Model storage")} · {selectedCollection.name}
                </h3>
                {models.length ? (
                  <div className="mt-2 divide-y divide-border">
                    {models.map((model) => (
                      <div key={model.model_id} className="flex justify-between gap-3 py-2 text-xs">
                        <span className="min-w-0 truncate">{model.name}</span>
                        <span className="tabular-nums">{bytes(model.logical_bytes, locale)}</span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="mt-2 text-xs text-muted-foreground">
                    {t("No visible Models on this page.")}
                  </p>
                )}
                <div className="mt-2 flex gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={modelOffset === 0}
                    onClick={() =>
                      void showModels(selectedCollection, Math.max(0, modelOffset - PAGE_SIZE))
                    }
                  >
                    {t("Back")}
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={models.length < PAGE_SIZE}
                    onClick={() => void showModels(selectedCollection, modelOffset + PAGE_SIZE)}
                  >
                    {t("Next")}
                  </Button>
                </div>
              </section>
            )}
          </details>
          <section className="flex flex-wrap items-center justify-between gap-3 border-t bg-muted/30 p-4">
            <div className="min-w-0">
              <h3 className="text-sm font-semibold">{t("Free up space")}</h3>

              <p className="mt-1 text-xs text-muted-foreground">
                {t(
                  "Remove temporary files that are no longer needed, or review trash and backups.",
                )}
              </p>
              {cleanupState === "error" && (
                <p className="mt-2 text-sm text-warning">
                  {t("Cleanup could not be checked. Refresh the measurement to try again.")}
                </p>
              )}
              {cleanupState === "loading" && (
                <p role="status" className="mt-2 text-sm">
                  {t("Checking for unused files…")}
                </p>
              )}
              {cleanupState === "ready" &&
                !cleanupPreviews.some(
                  (preview) => preview.available && preview.candidate_count > 0,
                ) && <p className="mt-2 text-sm">{t("No files need cleaning up right now.")}</p>}
            </div>
            <div className="flex flex-wrap gap-2">
              {cleanupPreviews.some(
                (preview) =>
                  preview.owner === "staging" && preview.available && preview.candidate_count > 0,
              ) && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setCleanupTarget("staging")}
                  disabled={busy}
                >
                  {t("Clean up temporary files")}
                </Button>
              )}
              {cleanupPreviews.some(
                (preview) =>
                  preview.owner === "cache" && preview.available && preview.candidate_count > 0,
              ) && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setCleanupTarget("cache")}
                  disabled={
                    busy ||
                    !cleanupPreviews.some(
                      (preview) => preview.owner === "cache" && preview.candidate_count > 0,
                    )
                  }
                >
                  {t("Clear derived cache")}
                </Button>
              )}
              <Button asChild variant="outline" size="sm">
                <Link to="/settings?section=trash">{t("Review eligible trash")}</Link>
              </Button>
              <Button asChild variant="outline" size="sm">
                <Link to="/settings?section=backup">{t("Review retained backups")}</Link>
              </Button>
            </div>
          </section>
          {result && (
            <p role="status" className="border-t p-4 text-sm">
              {result}
            </p>
          )}
        </>
      )}
      <ConfirmModal
        open={cleanupTarget !== null}
        onClose={() => setCleanupTarget(null)}
        onConfirm={() => void cleanup()}
        title={
          cleanupTarget === "cache" ? t("Clear derived cache?") : t("Clean up expired staging?")
        }
        description={
          cleanupTarget === "cache"
            ? t(
                "Only rebuildable derived STL cache objects with verified ownership receipts are eligible. Thumbnails and original Artifacts are retained. This action is recorded in the audit log.",
              )
            : t(
                "Only expired staging with verified ownership is eligible. Uncertain files are retained. This action is recorded in the audit log.",
              )
        }
        confirmLabel={t("Clean up")}
        busy={busy}
      />
    </Card>
  );
}
