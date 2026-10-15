import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Archive,
  ArrowLeft,
  Box,
  ChevronRight,
  Clock3,
  Cloud,
  Database,
  Folder,
  HardDrive,
  Images,
  Info,
  RefreshCw,
  TrendingUp,
  Trash2,
} from "lucide-react";
import { Link } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { Skeleton } from "@/components/ui/skeleton";
import {
  cleanupStorageCache,
  cleanupStorageStaging,
  getModelStorage,
  sampleStorageInventory,
  type CollectionStorageRow,
  type ModelStorageRow,
} from "@/lib/api/storage-inventory";
import {
  COLLECTION_PAGE_SIZE as PAGE_SIZE,
  collectionStorageOptions,
  storageInsightsKeys,
  storageInsightsOptions,
} from "@/lib/queries/storage-insights";
import { useI18n } from "@/lib/i18n";
import { useMediaQuery } from "@/lib/use-media-query";
import { bytesByWhere, placeKind } from "@/lib/storage-locations";
import { formatBytes } from "@printstash/domain/format";
import { knownUiText } from "@/lib/locale";

const DESKTOP_MODEL_PAGE_SIZE = 8;
const MOBILE_MODEL_PAGE_SIZE = 5;

function bytes(value: number | null, locale: string): string {
  if (value === null) return knownUiText("Unknown");
  return formatBytes(value, locale);
}

function capacityBytes(value: number | null, locale: string): string {
  if (value === 0) return "0 MB";
  if (value !== null && value < 1024 ** 2) return "<1 MB";
  return bytes(value, locale);
}

/** This computer against remote storage: two figures and one proportional bar. */
function PlacesSplit({ here, remote, locale }: { here: number; remote: number; locale: string }) {
  const { t } = useI18n();
  const total = Math.max(1, here + remote);
  return (
    <div className="mt-3">
      <div className="grid grid-cols-2 gap-4">
        <div>
          <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <span className="h-2 w-2 rounded-full bg-foreground/75" aria-hidden />
            {t("On this computer")}
          </span>
          <strong className="text-xl font-semibold tabular-nums">{bytes(here, locale)}</strong>
        </div>
        <div>
          <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <span className="h-2 w-2 rounded-full bg-primary/70" aria-hidden />
            {t("In remote storage")}
          </span>
          <strong className="text-xl font-semibold tabular-nums">{bytes(remote, locale)}</strong>
        </div>
      </div>
      <div
        role="img"
        aria-label={t("{here} on this computer · {remote} in remote storage", {
          here: bytes(here, locale),
          remote: bytes(remote, locale),
        })}
        className="mt-2 flex h-2.5 overflow-hidden rounded-full bg-muted"
      >
        <span className="h-full bg-foreground/75" style={{ width: `${(here / total) * 100}%` }} />
        <span className="h-full bg-primary/70" style={{ width: `${(remote / total) * 100}%` }} />
      </div>
    </div>
  );
}

export function StorageInventoryPanel() {
  const { locale, t } = useI18n();
  const modelPageSize = useMediaQuery("(min-width: 640px)")
    ? DESKTOP_MODEL_PAGE_SIZE
    : MOBILE_MODEL_PAGE_SIZE;
  const previousModelPageSize = useRef(modelPageSize);
  const client = useQueryClient();
  const summaryRead = useQuery(storageInsightsOptions());
  const [collectionOffset, setCollectionOffset] = useState(0);
  const collectionsRead = useQuery(collectionStorageOptions(collectionOffset));
  const report = summaryRead.data?.report ?? null;
  const activity = summaryRead.data?.activity ?? null;
  const cleanupPreviews = summaryRead.data?.cleanup ?? [];
  const collections = collectionsRead.data?.rows ?? [];
  const hasMoreCollections = collectionsRead.data?.hasMore ?? false;
  const [models, setModels] = useState<ModelStorageRow[]>([]);
  const [hasMoreModels, setHasMoreModels] = useState(false);
  const [modelOffset, setModelOffset] = useState(0);
  const [selectedCollection, setSelectedCollection] = useState<CollectionStorageRow | null>(null);
  const modelRequestRef = useRef(0);
  const [loadingCollectionId, setLoadingCollectionId] = useState<number | null>(null);
  const [modelsBusy, setModelsBusy] = useState(false);
  const [modelsError, setModelsError] = useState(false);
  const [busy, setBusy] = useState(false);
  const [commandFailed, setCommandFailed] = useState(false);
  const error = commandFailed || summaryRead.isError;
  const [cleanupTarget, setCleanupTarget] = useState<"staging" | "cache" | null>(null);
  const [result, setResult] = useState<string | null>(null);

  function showCollectionPage(offset: number) {
    setCollectionOffset(offset);
    closeModels();
  }

  /** Re-read every cached insight; the current figures stay on screen meanwhile. */
  async function reload() {
    await client.invalidateQueries({ queryKey: storageInsightsKeys.all });
    if (client.getQueryState(storageInsightsKeys.summary)?.status === "error") {
      throw new Error("storage_insights_unavailable");
    }
  }

  async function refresh() {
    setBusy(true);
    try {
      await sampleStorageInventory();
      await reload();
      setCommandFailed(false);
    } catch {
      setCommandFailed(true);
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
      await reload();
      setCommandFailed(false);
    } catch {
      setCommandFailed(true);
    } finally {
      setBusy(false);
    }
  }

  const showModels = useCallback(
    async (collection: CollectionStorageRow, offset = 0) => {
      const collectionId = collection.collection_id ?? 0;
      const requestId = ++modelRequestRef.current;
      setLoadingCollectionId(collectionId);
      setModelsBusy(true);
      setModelsError(false);
      try {
        const rows = await getModelStorage(collectionId, offset, modelPageSize + 1);
        if (requestId !== modelRequestRef.current) return;
        setModels(Array.isArray(rows) ? rows.slice(0, modelPageSize) : []);
        setHasMoreModels(Array.isArray(rows) && rows.length > modelPageSize);
        setModelOffset(offset);
        setSelectedCollection(collection);
      } catch {
        if (requestId !== modelRequestRef.current) return;
        setModels([]);
        setHasMoreModels(false);
        setModelsError(true);
        setModelOffset(offset);
        setSelectedCollection(collection);
      } finally {
        if (requestId === modelRequestRef.current) {
          setModelsBusy(false);
          setLoadingCollectionId(null);
        }
      }
    },
    [modelPageSize],
  );

  useEffect(() => {
    if (previousModelPageSize.current === modelPageSize) return;
    if (!selectedCollection) {
      previousModelPageSize.current = modelPageSize;
      return;
    }
    const timeout = window.setTimeout(() => {
      previousModelPageSize.current = modelPageSize;
      void showModels(selectedCollection, 0);
    }, 0);
    return () => window.clearTimeout(timeout);
  }, [modelPageSize, selectedCollection, showModels]);

  function closeModels() {
    modelRequestRef.current += 1;
    setSelectedCollection(null);
    setLoadingCollectionId(null);
    setModelsBusy(false);
  }

  const current = report?.inventory;
  const liveBytes =
    current?.buckets.reduce(
      (total, bucket) =>
        total +
        (bucket.lifecycle === "live"
          ? Math.max(0, bucket.logical_bytes - bucket.external_bytes)
          : 0),
      0,
    ) ?? 0;
  const generatedBytes =
    current?.buckets.reduce(
      (total, bucket) => total + (bucket.lifecycle === "owned" ? bucket.logical_bytes : 0),
      0,
    ) ?? 0;
  const trashBytes =
    current?.buckets.reduce(
      (total, bucket) =>
        total +
        (bucket.lifecycle === "trash"
          ? Math.max(0, bucket.logical_bytes - bucket.external_bytes)
          : 0),
      0,
    ) ?? 0;
  const groups = [
    { label: t("Model and print files"), value: liveBytes, Icon: Box },
    { label: t("Previews and generated files"), value: generatedBytes, Icon: Images },
    { label: t("Files in trash"), value: trashBytes, Icon: Trash2 },
    { label: t("Backup copies"), value: current?.backup_bytes ?? 0, Icon: Archive },
  ];
  const otherBytes = Math.max(
    0,
    (current?.unique_owned_bytes ?? 0) - groups.reduce((total, group) => total + group.value, 0),
  );
  if (otherBytes > 0)
    groups.push({ label: t("Other stored files"), value: otherBytes, Icon: Database });
  const visibleGroups = groups.filter((group) => group.value > 0);
  const largestGroup = Math.max(1, ...visibleGroups.map((group) => group.value));
  const actionableCleanup = cleanupPreviews
    .filter((preview) => preview.available && preview.candidate_count > 0)
    .sort((a, b) => b.candidate_bytes - a.candidate_bytes);
  const cleanupLabels = {
    staging: t("Expired temporary files"),
    cache: t("Generated cache files"),
    trash: t("Trash ready for review"),
    backups: t("Backups ready for review"),
  };
  const cleanupIcons = { staging: Clock3, cache: Images, trash: Trash2, backups: Archive };
  const capacityStatus = current?.volumes.some((volume) => volume.status === "blocked")
    ? "blocked"
    : current?.volumes.length && current.volumes.every((volume) => volume.status === "available")
      ? "available"
      : "unknown";
  const availableBytes =
    current?.provider_capacity.available_bytes ??
    (current?.volumes.length === 1 ? current.volumes[0].free_bytes : null);
  const samples = [...(report?.history ?? [])].sort((a, b) =>
    a.sampled_at.localeCompare(b.sampled_at),
  );
  const firstSample = samples[0];
  const lastSample = samples.at(-1);
  const historyChange =
    firstSample && lastSample ? lastSample.owned_bytes - firstSample.owned_bytes : 0;
  const largestSample = Math.max(1, ...samples.map((sample) => sample.owned_bytes));
  const firstTimestamp = firstSample ? Date.parse(firstSample.sampled_at) : 0;
  const historyDuration = lastSample ? Date.parse(lastSample.sampled_at) - firstTimestamp : 0;
  const historyPoints = samples
    .map(
      (sample, index) =>
        `${12 + (historyDuration > 0 ? ((Date.parse(sample.sampled_at) - firstTimestamp) / historyDuration) * 296 : (index * 296) / Math.max(1, samples.length - 1))},${84 - (sample.owned_bytes / largestSample) * 68}`,
    )
    .join(" ");
  const bucketLabels = new Map([
    ["step", t("STEP files")],
    ["stl", t("STL files")],
    ["3mf", t("3MF files")],
    ["thumbnail", t("Preview images")],
    ["stl_cache", t("Generated STL files")],
    ["staging", t("Temporary uploads")],
    ["backups", t("Backup copies")],
  ]);
  const lifecycleLabels = new Map([
    ["live", t("In library")],
    ["trash", t("In trash")],
    ["owned", t("Generated here")],
    ["temporary", t("Temporary")],
    ["replica", t("Backup copy")],
  ]);

  return (
    <Card className="overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b px-4 py-4 sm:px-5">
        <div className="flex min-w-0 items-center gap-3">
          <HardDrive className="h-8 w-8 rounded-md bg-muted p-2" />
          <div>
            <h2 className="text-sm font-semibold">{t("Storage insights")}</h2>
            <p className="text-xs text-muted-foreground">
              {t("See what uses space and how your storage changes.")}
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
        <div
          role="status"
          aria-label={t("Loading storage insights…")}
          className="space-y-5 p-4 sm:p-5"
        >
          <div className="grid grid-cols-2 gap-4 md:grid-cols-3">
            {[0, 1, 2].map((item) => (
              <div key={item} className="space-y-3">
                <Skeleton className="h-4 w-24" />
                <Skeleton className="h-8 w-28 max-w-full" />
              </div>
            ))}
          </div>
          <Skeleton className="h-24 w-full" />
          <span className="sr-only">{t("Loading storage insights…")}</span>
        </div>
      )}
      {current && (
        <>
          <dl className="grid grid-cols-2 border-b md:grid-cols-[1.4fr_1fr_1fr]">
            <div className="col-span-2 border-b bg-muted/30 px-4 py-4 md:col-span-1 md:border-b-0 md:border-r">
              <dt className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
                <Database className="h-4 w-4" aria-hidden />
                {t("Files stored here")}
              </dt>
              <dd className="mt-2 text-2xl font-semibold tracking-tight tabular-nums">
                {capacityBytes(current.unique_owned_bytes, locale)}
              </dd>
            </div>
            <div className="border-r px-4 py-4">
              <dt className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
                <HardDrive className="h-4 w-4" aria-hidden />
                {t("Free space")}
              </dt>
              <dd className="mt-2 text-xl font-semibold tracking-tight tabular-nums">
                {capacityBytes(availableBytes, locale)}
              </dd>
              <p
                className={`mt-1 text-xs ${capacityStatus === "blocked" ? "text-destructive" : capacityStatus === "available" ? "text-success" : "text-muted-foreground"}`}
              >
                {capacityStatus === "blocked"
                  ? t("New allocations blocked")
                  : capacityStatus === "available"
                    ? t("Capacity available")
                    : t("Capacity unknown")}
              </p>
            </div>
            <div className="px-4 py-4">
              <dt className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
                <Clock3 className="h-4 w-4" aria-hidden />
                {t("Temporary files")}
              </dt>
              <dd className="mt-2 text-xl font-semibold tracking-tight tabular-nums">
                {capacityBytes(current.temporary_bytes, locale)}
              </dd>
            </div>
          </dl>
          {current.unknown_object_count > 0 && (
            <div className="flex items-start gap-2 border-b bg-muted/20 px-4 py-2.5 text-xs text-muted-foreground">
              <Info className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
              <p>
                {t("Usage includes known sizes only. {count} objects have no recorded size.", {
                  count: current.unknown_object_count,
                })}
              </p>
            </div>
          )}
          {current.latest_audit && current.latest_audit.unclaimed_object_count > 0 && (
            <div className="flex items-start gap-2 border-b px-4 py-2.5 text-xs text-warning">
              <Info className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
              <p>
                {t(
                  "Latest audit found {count} unattributed storage objects. They are observations, never cleanup authority.",
                  { count: current.latest_audit.unclaimed_object_count },
                )}{" "}
                {t("Known size: {size}. Unknown sizes: {count}.", {
                  size:
                    current.latest_audit.unclaimed_bytes === null
                      ? t("Unknown")
                      : bytes(current.latest_audit.unclaimed_bytes, locale),
                  count: current.latest_audit.unknown_size_count,
                })}
              </p>
            </div>
          )}
          {current.locations.length > 0 && (
            <section
              aria-labelledby="storage-by-place-title"
              className="border-b px-4 py-4 sm:px-5"
            >
              <div className="flex items-center gap-2">
                <HardDrive className="h-4 w-4 text-muted-foreground" aria-hidden />
                <h3 id="storage-by-place-title" className="text-sm font-semibold">
                  {t("Where your files live")}
                </h3>
              </div>
              <PlacesSplit
                here={bytesByWhere(current.locations).here}
                remote={bytesByWhere(current.locations).remote}
                locale={locale}
              />
              <ul className="mt-4 space-y-3">
                {current.locations.map((place) => {
                  const kind = placeKind(place);
                  const Icon = place.remote ? Cloud : HardDrive;
                  const largest = Math.max(1, ...current.locations.map((row) => row.logical_bytes));
                  return (
                    <li key={place.library_id ?? "vault"}>
                      <div className="flex items-center justify-between gap-3 text-xs">
                        <span className="flex min-w-0 items-center gap-2">
                          <Icon
                            className="h-3.5 w-3.5 shrink-0 text-muted-foreground"
                            aria-hidden
                          />
                          <span className="truncate font-medium">
                            {place.kind === "vault" ? t("Vault") : place.label}
                          </span>
                          <span className="truncate text-muted-foreground">
                            {kind === "vault-local" && t("This computer")}
                            {kind === "vault-remote" &&
                              t("Remote storage · {transport}", { transport: place.transport })}
                            {kind === "mounted" && t("Mounted folder")}
                            {kind === "remote-source" &&
                              t("{transport} · {connection}", {
                                transport: place.transport.toUpperCase(),
                                connection: place.connection ?? t("Remote storage"),
                              })}
                          </span>
                        </span>
                        <strong className="shrink-0 font-semibold tabular-nums">
                          {bytes(place.logical_bytes, locale)}
                        </strong>
                      </div>
                      <span
                        className="mt-1.5 block h-1.5 overflow-hidden rounded-full bg-muted"
                        aria-hidden
                      >
                        <span
                          className={`block h-full origin-left ${place.remote ? "bg-primary/70" : "bg-foreground/75"}`}
                          style={{
                            transform:
                              "scaleX(" + Math.max(0.015, place.logical_bytes / largest) + ")",
                          }}
                        />
                      </span>
                      {place.kind === "library" && (
                        <p className="mt-1 text-2xs text-muted-foreground">
                          {t("{count} files read in place", { count: place.count })}
                        </p>
                      )}
                    </li>
                  );
                })}
              </ul>
              <p className="mt-3 text-xs text-muted-foreground">
                {t(
                  "Library sources stay where they are. Their previews and catalog are kept in the vault.",
                )}
              </p>
            </section>
          )}
          <div className="grid border-b lg:grid-cols-2">
            <section
              aria-labelledby="storage-by-purpose-title"
              className="border-b px-4 py-4 sm:px-5 lg:border-b-0 lg:border-r"
            >
              <div className="flex items-center gap-2">
                <Box className="h-4 w-4 text-muted-foreground" aria-hidden />
                <h3 id="storage-by-purpose-title" className="text-sm font-semibold">
                  {t("What uses space")}
                </h3>
              </div>
              {visibleGroups.length ? (
                <div className="mt-4 space-y-3">
                  {visibleGroups.map((group) => (
                    <div key={group.label}>
                      <div className="flex items-center justify-between gap-3 text-xs">
                        <span className="flex min-w-0 items-center gap-2">
                          <group.Icon
                            className="h-3.5 w-3.5 shrink-0 text-muted-foreground"
                            aria-hidden
                          />
                          <span className="truncate">{group.label}</span>
                        </span>
                        <strong className="shrink-0 font-semibold tabular-nums">
                          {bytes(group.value, locale)}
                        </strong>
                      </div>
                      <span
                        className="mt-1.5 block h-1.5 overflow-hidden rounded-full bg-muted"
                        aria-hidden
                      >
                        <span
                          className="block h-full origin-left bg-foreground/75"
                          style={{
                            transform:
                              "scaleX(" + Math.max(0.015, group.value / largestGroup) + ")",
                          }}
                        />
                      </span>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="mt-4 text-sm text-muted-foreground">
                  {t("No recorded library bytes.")}
                </p>
              )}
              <p className="mt-3 text-xs text-muted-foreground">
                {t("Based on recorded file sizes. Shared files may appear in more than one total.")}
              </p>
            </section>
            <section aria-labelledby="storage-history-title" className="px-4 py-4 sm:px-5">
              <div className="flex items-center gap-2">
                <TrendingUp className="h-4 w-4 text-muted-foreground" aria-hidden />
                <h3 id="storage-history-title" className="text-sm font-semibold">
                  {t("Storage over time")}
                </h3>
              </div>
              {samples.length > 1 && firstSample && lastSample ? (
                <div className="mt-4">
                  <p className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                    <strong className="text-xl font-semibold tracking-tight tabular-nums">
                      {historyChange > 0 ? "+" : historyChange < 0 ? "−" : ""}
                      {bytes(Math.abs(historyChange), locale)}
                    </strong>
                    <span className="text-xs text-muted-foreground">
                      {t("Since {date}", {
                        date: new Date(firstSample.sampled_at).toLocaleDateString(locale),
                      })}
                    </span>
                  </p>
                  <figure className="mt-2">
                    <svg
                      role="img"
                      aria-label={t("Recorded owned storage over time")}
                      viewBox="0 0 320 96"
                      className="h-24 w-full text-foreground"
                    >
                      <title>{t("Recorded owned storage over time")}</title>
                      <line
                        x1="12"
                        y1="84"
                        x2="308"
                        y2="84"
                        stroke="currentColor"
                        className="text-border"
                      />
                      <polygon
                        points={["12,84", historyPoints, "308,84"].join(" ")}
                        fill="currentColor"
                        fillOpacity="0.08"
                      />
                      <polyline
                        points={historyPoints}
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                      <circle
                        cx="308"
                        cy={84 - (lastSample.owned_bytes / largestSample) * 68}
                        r="3"
                        fill="currentColor"
                      />
                    </svg>
                    <figcaption className="flex justify-between text-xs text-muted-foreground">
                      <span>{new Date(firstSample.sampled_at).toLocaleDateString(locale)}</span>
                      <span>{new Date(lastSample.sampled_at).toLocaleDateString(locale)}</span>
                    </figcaption>
                  </figure>
                </div>
              ) : (
                <p className="mt-4 text-sm text-muted-foreground">
                  {t("Take another measurement to see changes over time.")}
                </p>
              )}
              {report.forecast.days_remaining !== null && (
                <p className="mt-3 text-xs text-muted-foreground">
                  {t("Estimated {days} days of headroom at recent growth.", {
                    days: Math.floor(report.forecast.days_remaining),
                  })}
                </p>
              )}
            </section>
          </div>
          <details className="border-b">
            <summary className="cursor-pointer px-4 py-3 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
              {t("Measurement details")}
            </summary>
            <div className="grid border-t lg:grid-cols-2">
              <section className="px-4 py-4 sm:px-5 lg:border-r">
                <h3 className="text-sm font-semibold">{t("Files by type")}</h3>
                <div className="mt-2 divide-y divide-border">
                  {current.buckets
                    .filter((bucket) => bucket.logical_bytes > 0)
                    .map((bucket) => (
                      <div
                        key={bucket.category + "-" + bucket.lifecycle}
                        className="flex items-center justify-between gap-3 py-2 text-xs"
                      >
                        <span className="min-w-0">
                          <span className="block font-medium">
                            {bucketLabels.get(bucket.category) ??
                              knownUiText(bucket.category, locale)}
                          </span>
                          <span className="mt-0.5 block text-muted-foreground">
                            {lifecycleLabels.get(bucket.lifecycle) ??
                              knownUiText(bucket.lifecycle, locale)}
                          </span>
                        </span>
                        <strong className="shrink-0 font-semibold tabular-nums">
                          {bytes(bucket.logical_bytes, locale)}
                        </strong>
                      </div>
                    ))}
                  {current.buckets.every((bucket) => bucket.logical_bytes === 0) && (
                    <p className="py-2 text-sm text-muted-foreground">
                      {t("No recorded library bytes.")}
                    </p>
                  )}
                </div>
              </section>
              <section className="px-4 py-4 text-xs sm:px-5">
                <dl className="grid gap-3 sm:grid-cols-2">
                  <div>
                    <dt className="text-muted-foreground">{t("Logical references")}</dt>
                    <dd className="mt-1 font-medium tabular-nums">
                      {bytes(current.logical_bytes, locale)}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-muted-foreground">{t("External references")}</dt>
                    <dd className="mt-1 font-medium tabular-nums">
                      {bytes(current.external_referenced_bytes, locale)}
                    </dd>
                  </div>
                </dl>
                <p className="mt-4 text-muted-foreground">
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
                <div className="mt-3 divide-y divide-border border-y">
                  {current.volumes.map((volume) => (
                    <div
                      key={volume.domain_id}
                      className="flex flex-wrap justify-between gap-2 py-2"
                    >
                      <span className="capitalize">{volume.roles.join(" · ")}</span>
                      <span className="tabular-nums">
                        {t("Free space")}: {bytes(volume.free_bytes, locale)}
                      </span>
                    </div>
                  ))}
                </div>
                <p className="mt-3 text-muted-foreground">
                  {report.forecast.days_remaining === null
                    ? t(
                        "A forecast needs seven daily samples spanning at least seven days, stable positive growth, and known capacity.",
                      )
                    : t("Estimated {days} days of headroom at recent growth.", {
                        days: Math.floor(report.forecast.days_remaining),
                      })}
                </p>
                <p className="mt-1 text-muted-foreground">
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
            </div>
          </details>
          <section
            className="flex h-[36rem] flex-col border-t px-4 py-4 sm:h-[28rem] sm:px-5"
            aria-labelledby="collection-storage-title"
          >
            <h3 id="collection-storage-title" className="text-sm font-semibold">
              {t("Collection storage")}
            </h3>
            <p className="mt-1 text-xs text-muted-foreground">
              {t("Recorded size by collection. Shared files can count twice.")}
            </p>
            <div
              key={
                selectedCollection
                  ? `models-${selectedCollection.collection_id ?? 0}`
                  : "collections"
              }
              className="mt-3 min-h-0 flex-1 overflow-y-auto"
            >
              {selectedCollection ? (
                <div
                  role="region"
                  aria-label={t("Models in {collection}", { collection: selectedCollection.name })}
                  aria-busy={modelsBusy}
                >
                  <Button variant="ghost" size="sm" className="-ml-2 mb-2" onClick={closeModels}>
                    <ArrowLeft className="mr-2 h-4 w-4" aria-hidden />
                    {t("All collections")}
                  </Button>
                  <div className="flex flex-wrap items-end justify-between gap-2">
                    <div>
                      <h4 className="text-sm font-semibold">
                        {t("Models in {collection}", { collection: selectedCollection.name })}
                      </h4>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {selectedCollection.model_count === 1
                          ? t("1 model")
                          : t("{count} models", { count: selectedCollection.model_count })}
                      </p>
                    </div>
                    <strong className="text-base font-semibold tabular-nums">
                      {bytes(selectedCollection.logical_bytes, locale)}
                    </strong>
                  </div>
                  {modelsBusy && models.length === 0 ? (
                    <p role="status" className="mt-4 text-sm text-muted-foreground">
                      {t("Loading models…")}
                    </p>
                  ) : modelsError ? (
                    <div role="alert" className="mt-4 flex flex-wrap items-center gap-3 text-sm">
                      <span>{t("Could not load this collection's models.")}</span>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => void showModels(selectedCollection, modelOffset)}
                      >
                        {t("Retry")}
                      </Button>
                    </div>
                  ) : models.length ? (
                    <ul className="mt-4 grid gap-x-8 sm:grid-cols-2">
                      {models.map((model) => (
                        <li key={model.model_id} className="min-w-0 border-b border-border">
                          <Button
                            asChild
                            variant="ghost"
                            size="sm"
                            className="h-auto w-full justify-start gap-3 px-1 py-3 text-left"
                          >
                            <Link to={`/models/${model.model_id}`}>
                              <Box className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
                              <span
                                className="min-w-0 flex-1 truncate text-sm font-medium"
                                title={model.name}
                              >
                                {model.name}
                              </span>
                              <span className="shrink-0 text-sm font-semibold tabular-nums">
                                {bytes(model.logical_bytes, locale)}
                              </span>
                              <ChevronRight
                                className="h-4 w-4 shrink-0 text-muted-foreground"
                                aria-hidden
                              />
                              <span className="sr-only">{t("Open model")}</span>
                            </Link>
                          </Button>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="mt-4 text-sm text-muted-foreground">
                      {t("No models with recorded files in this collection.")}
                    </p>
                  )}
                </div>
              ) : collections.length ? (
                <ul className="grid gap-x-8 sm:grid-cols-2">
                  {collections.map((collection) => {
                    const collectionId = collection.collection_id ?? 0;
                    const isLoading = loadingCollectionId === collectionId;
                    return (
                      <li
                        key={collection.collection_id ?? "uncollected"}
                        className="min-w-0 border-b border-border"
                      >
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-auto min-w-0 w-full justify-start gap-3 px-1 py-3 text-left"
                          onClick={() => {
                            if (isLoading) closeModels();
                            else void showModels(collection);
                          }}
                        >
                          <Folder className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
                          <span className="min-w-0 flex-1">
                            <span
                              className="block truncate text-sm font-medium"
                              title={collection.name}
                            >
                              {collection.name}
                            </span>
                            <span className="mt-0.5 block text-xs text-muted-foreground">
                              {isLoading
                                ? t("Loading models…")
                                : collection.model_count === 1
                                  ? t("1 model")
                                  : t("{count} models", { count: collection.model_count })}
                            </span>
                          </span>
                          <span className="shrink-0 text-sm font-semibold tabular-nums">
                            {bytes(collection.logical_bytes, locale)}
                          </span>
                          <ChevronRight
                            className="h-4 w-4 shrink-0 text-muted-foreground"
                            aria-hidden
                          />
                          <span className="sr-only">{t("View Models")}</span>
                        </Button>
                      </li>
                    );
                  })}
                </ul>
              ) : (
                <p className="text-xs text-muted-foreground">
                  {t("No visible Collection storage.")}
                </p>
              )}
            </div>
            {selectedCollection
              ? (modelOffset > 0 || hasMoreModels) && (
                  <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-border pt-3">
                    <p className="text-xs tabular-nums text-muted-foreground">
                      {t("Showing models {first}–{last}", {
                        first: modelOffset + 1,
                        last: modelOffset + models.length,
                      })}
                    </p>
                    <div className="flex gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={modelsBusy || modelOffset === 0}
                        onClick={() =>
                          void showModels(
                            selectedCollection,
                            Math.max(0, modelOffset - modelPageSize),
                          )
                        }
                      >
                        {t("Previous page")}
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={modelsBusy || !hasMoreModels}
                        onClick={() =>
                          void showModels(selectedCollection, modelOffset + modelPageSize)
                        }
                      >
                        {t("Next page")}
                      </Button>
                    </div>
                  </div>
                )
              : (collectionOffset > 0 || hasMoreCollections) && (
                  <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-border pt-3">
                    <p className="text-xs tabular-nums text-muted-foreground">
                      {t("Showing collections {first}–{last}", {
                        first: collectionOffset + 1,
                        last: collectionOffset + collections.length,
                      })}
                    </p>
                    <div className="flex gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={collectionOffset === 0}
                        onClick={() =>
                          showCollectionPage(Math.max(0, collectionOffset - PAGE_SIZE))
                        }
                      >
                        {t("Previous page")}
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={!hasMoreCollections}
                        onClick={() => showCollectionPage(collectionOffset + PAGE_SIZE)}
                      >
                        {t("Next page")}
                      </Button>
                    </div>
                  </div>
                )}
          </section>
          {activity &&
            (activity.active_reservations.length > 0 || activity.recent_denials.length > 0) && (
              <section
                className="border-t px-4 py-4 sm:px-5"
                aria-labelledby="storage-activity-title"
              >
                <h3 id="storage-activity-title" className="text-sm font-semibold">
                  {t("Recent storage activity")}
                </h3>
                <div className="mt-3 grid gap-5 sm:grid-cols-2">
                  {activity.active_reservations.length > 0 && (
                    <div>
                      <h4 className="text-xs font-medium">{t("Active reservations")}</h4>
                      <ul className="mt-2 space-y-1 text-xs text-muted-foreground">
                        {activity.active_reservations.map((reservation, index) => (
                          <li
                            key={`${reservation.operation_kind}-${reservation.created_at}-${index}`}
                          >
                            {t("{operation} · {size}", {
                              operation: reservation.operation_kind,
                              size: bytes(reservation.required_bytes, locale),
                            })}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {activity.recent_denials.length > 0 && (
                    <div>
                      <h4 className="text-xs font-medium">{t("Recent blocked operations")}</h4>
                      <ul className="mt-2 space-y-1 text-xs text-muted-foreground">
                        {activity.recent_denials.map((denial, index) => (
                          <li key={`${denial.operation_kind}-${denial.occurred_at}-${index}`}>
                            {t("{operation} · {size}", {
                              operation: denial.operation_kind,
                              size: bytes(denial.required_bytes, locale),
                            })}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              </section>
            )}
          {actionableCleanup.length > 0 && (
            <section className="border-t" aria-labelledby="cleanup-title">
              <h3
                id="cleanup-title"
                className="border-b bg-muted/30 px-4 py-3 text-sm font-semibold sm:px-5"
              >
                {t("Free up space")}
              </h3>
              <div className="divide-y divide-border px-4 sm:px-5">
                {actionableCleanup.map((preview) => {
                  const Icon = cleanupIcons[preview.owner];
                  return (
                    <div
                      key={preview.owner}
                      className="flex flex-wrap items-center justify-between gap-3 py-3"
                    >
                      <div className="flex min-w-0 items-center gap-3">
                        <Icon className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
                        <div>
                          <p className="text-sm font-medium">{cleanupLabels[preview.owner]}</p>
                          <p className="text-xs text-muted-foreground">
                            {t("Items: {count}", { count: preview.candidate_count })}
                          </p>
                        </div>
                      </div>
                      <div className="flex flex-wrap items-center gap-3">
                        <strong className="text-sm font-semibold tabular-nums">
                          {bytes(preview.candidate_bytes, locale)}
                        </strong>
                        {preview.owner === "staging" && (
                          <Button
                            variant="outline"
                            size="sm"
                            disabled={busy}
                            onClick={() => setCleanupTarget("staging")}
                          >
                            {t("Clean up expired staging")}
                          </Button>
                        )}
                        {preview.owner === "cache" && (
                          <Button
                            variant="outline"
                            size="sm"
                            disabled={busy}
                            onClick={() => setCleanupTarget("cache")}
                          >
                            {t("Clear derived cache")}
                          </Button>
                        )}
                        {preview.owner === "trash" && (
                          <Button asChild variant="outline" size="sm">
                            <Link to="/settings?section=trash">{t("Review eligible trash")}</Link>
                          </Button>
                        )}
                        {preview.owner === "backups" && (
                          <Button asChild variant="outline" size="sm">
                            <Link to="/settings?section=backups">
                              {t("Review retained backups")}
                            </Link>
                          </Button>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </section>
          )}
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
