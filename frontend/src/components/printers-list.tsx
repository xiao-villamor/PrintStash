"use client";

import { useQueryClient } from "@tanstack/react-query";
import { queryKeys } from "@/lib/query-client";
import { usePrinterSettingsCommand } from "@/features/printers/settings-edit";
import { PrinterSettingsReview } from "@/features/printers/settings-review";

import { currentLocale } from "@/lib/locale";
import { knownUiText, uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

import { lazy, Suspense, useId, useMemo, useRef, useState } from "react";
import { Link } from "@/lib/link";
import {
  PrinterRead,
  type FleetPrinterTelemetry,
  type FleetSummary,
  type PrinterCreate,
  type PrinterStatus,
} from "@/types";
import { usePrinterCatalogCommands } from "@/features/printers/queries";
import { useFleetQueue, useFleetSummary, usePrinters } from "@/lib/queries";
import { toast } from "@/lib/toast";
import { useAuth } from "@/lib/auth-context";
import {
  PRINTER_MODEL_OPTIONS,
  PRINTER_SETUP_OPTIONS,
  providerAddress,
  providerLabel,
  setupProviderFields,
  type PrinterSetupKind,
} from "@/lib/printer-providers";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/ui/empty-state";
import { Button } from "@/components/ui/button";
import { FormField } from "@/components/ui/form-field";
import { Input, inputClasses } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { PageHeader } from "@/components/ui/page-header";
import { useSearchParams } from "@/lib/navigation";
import { TabBar } from "@/components/ui/tabs";
import { Localized } from "@/components/ui/localized";
import { useOptionalI18n } from "@/lib/i18n";
import { translateUiText } from "@/lib/locale";
import {
  FleetMaintenancePanel,
  FleetQueuePanel,
  type FleetQueueView,
} from "@/components/fleet-panels";
import {
  usePrinterArtworkPreference,
  type PrinterArtworkPreference,
} from "@/lib/printer-card-display";
import { printerArtwork } from "@/lib/orca-printer-images";
import { formatNumber, parseApiInstant } from "@/lib/format";
import { PrinterProgress } from "@/components/printer-telemetry";
import {
  ArrowUpRight,
  Fan,
  ListOrdered,
  Plus,
  RefreshCw,
  Pencil,
  Printer as PrinterIcon,
} from "lucide-react";
import { printJobArtifactLabel } from "@/lib/print-job-reproducibility";
import type { PrintJobState } from "@/types";

const FleetAddJobDialog = lazy(() =>
  import("./fleet-add-job").then((module) => ({ default: module.FleetAddJobDialog })),
);

const PrinterConnectionForm = lazy(() =>
  import("./printer-detail").then((module) => ({ default: module.PrinterSettings })),
);

// `satisfies` keeps the literal keys (so a status can't be misspelled here) while
// still requiring every PrinterStatus to have an entry.
const STATUS_PRIORITY = {
  printing: 0,
  paused: 1,
  error: 2,
  offline: 3,
  ready: 4,
  unknown: 5,
} satisfies Record<PrinterStatus, number>;

/** Resolve a <select> value back to the setup kind it was rendered from. */
function parseSetupKind(value: string): PrinterSetupKind | null {
  return PRINTER_SETUP_OPTIONS.find((option) => option.value === value)?.value ?? null;
}

export function PrintersPage() {
  const { deletePrinter } = usePrinterCatalogCommands();
  const queryClient = useQueryClient();
  useUiLocale();
  const { user } = useAuth();
  const locale = useOptionalI18n()?.locale ?? "en";
  // Shared printers cache: catalog commands invalidate
  // queryKeys.printers, so this list refetches itself after add/delete.
  const printersQuery = usePrinters({ refetchInterval: 15_000 });
  const summaryQuery = useFleetSummary({ refetchInterval: 5_000 });
  const printers = useMemo(() => printersQuery.data ?? [], [printersQuery.data]);
  const summary = summaryQuery.data;
  const fleetRows = useMemo(
    () => new Map(summary?.printers.map((row) => [row.printer_id, row])),
    [summary],
  );
  const loading = printersQuery.isLoading;
  const error = printersQuery.error instanceof Error ? printersQuery.error.message : null;
  const [addOpen, setAddOpen] = useState(false);
  const [addJobOpen, setAddJobOpen] = useState(false);
  const settingsFormId = useId();
  const [settingsFooter, setSettingsFooter] = useState<HTMLDivElement | null>(null);
  const [settingsTarget, setSettingsTarget] = useState<PrinterRead | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<PrinterRead | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const artworkPreference = usePrinterArtworkPreference();
  const [selectedGroup, setSelectedGroup] = useState<string | null>(null);
  const searchParams = useSearchParams();
  const [activeView, setActiveView] = useState<"fleet" | "queue" | "maintenance">(() =>
    searchParams.get("tab") === "queue" ? "queue" : "fleet",
  );
  const [queueView, setQueueView] = useState<FleetQueueView>("queued");
  const [healthFilter, setHealthFilter] = useState<"ready" | "attention" | null>(null);
  const canSeeQueue = Boolean(
    user?.is_superuser || printers.some((printer) => printer.access.can_print),
  );
  const canSeeMaintenance = Boolean(
    user?.is_superuser || printers.some((printer) => printer.access.can_admin),
  );
  const visiblePrinters = useMemo(
    () =>
      printers
        .filter((printer) => {
          const row = fleetRows.get(printer.id);
          return (
            (selectedGroup === null ||
              (printer.group?.trim() || "__ungrouped") === selectedGroup) &&
            (healthFilter === null ||
              (healthFilter === "ready" && isReady(printer, row)) ||
              (healthFilter === "attention" && needsAttention(printer, row)))
          );
        })
        .sort(
          (a, b) =>
            STATUS_PRIORITY[a.status] - STATUS_PRIORITY[b.status] || a.name.localeCompare(b.name),
        ),
    [printers, fleetRows, selectedGroup, healthFilter],
  );
  const printerGroups = useMemo(() => {
    const groups = new Map<string, number>();
    for (const printer of printers) {
      const group = printer.group?.trim() || "__ungrouped";
      groups.set(group, (groups.get(group) ?? 0) + 1);
    }
    return [...groups.entries()]
      .map(([name, count]) => ({ name, count }))
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [printers]);
  const attentionCount = printers.filter((printer) =>
    needsAttention(printer, fleetRows.get(printer.id)),
  ).length;
  const readyCount = printers.filter((printer) =>
    isReady(printer, fleetRows.get(printer.id)),
  ).length;
  function openQueue(view: FleetQueueView) {
    setQueueView(view);
    setActiveView("queue");
  }

  async function confirmDelete() {
    if (!deleteTarget) return;
    const printer = deleteTarget;
    setDeleteBusy(true);
    try {
      await deletePrinter(printer.id);
      toast.success(uiText('Printer "{value1}" removed', { value1: String(printer.name) }));
      setDeleteTarget(null);
    } catch (e) {
      toast.error(e);
    } finally {
      setDeleteBusy(false);
    }
  }

  return (
    <Localized>
      <>
        {addJobOpen && (
          <Suspense fallback={null}>
            <FleetAddJobDialog open onClose={() => setAddJobOpen(false)} printers={printers} />
          </Suspense>
        )}
        <ConfirmModal
          open={!!deleteTarget}
          onClose={() => setDeleteTarget(null)}
          onConfirm={confirmDelete}
          busy={deleteBusy}
          title={uiText("Remove printer?")}
          description={
            deleteTarget
              ? uiText('"{value1}" will be removed from PrintStash.', {
                  value1: String(deleteTarget.name),
                })
              : uiText("This printer will be removed from PrintStash.")
          }
          confirmLabel={uiText("Remove")}
        />
        <div className="@container/fleet flex w-full min-w-0 flex-col gap-4">
          <PageHeader
            title={uiText("Printers")}
            description={uiText(
              activeView === "queue"
                ? "Route saved G-code and follow jobs across the fleet."
                : activeView === "maintenance"
                  ? "Routing, scheduled maintenance windows and service records for each printer."
                  : "Live activity, queued work and maintenance in one place.",
            )}
            actions={
              <>
                <Button
                  variant="outline"
                  size="xs"
                  onClick={() => {
                    void printersQuery.refetch();
                    void summaryQuery.refetch();
                    void queryClient.invalidateQueries({ queryKey: queryKeys.fleetQueue });
                  }}
                >
                  <RefreshCw className="h-3.5 w-3.5" />
                  {uiText("Refresh")}
                </Button>
                <Button
                  size="xs"
                  onClick={() => {
                    if (activeView === "queue") {
                      setAddJobOpen(true);
                      return;
                    }
                    if (!user?.is_superuser) return;
                    setAddOpen(true);
                  }}
                  disabled={
                    activeView === "queue"
                      ? !printers.some((printer) => printer.access.can_print)
                      : !user?.is_superuser
                  }
                >
                  <Plus className="h-3.5 w-3.5" />
                  {activeView === "queue"
                    ? uiText("Add to queue")
                    : user?.is_superuser
                      ? uiText("Add printer")
                      : uiText("Admin required")}
                </Button>
              </>
            }
          />

          <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-border">
            <TabBar
              tabs={[
                { key: "fleet", label: translateUiText(locale, "Fleet") },
                ...(canSeeQueue
                  ? [{ key: "queue" as const, label: translateUiText(locale, "Queue") }]
                  : []),
                ...(canSeeMaintenance
                  ? [{ key: "maintenance" as const, label: translateUiText(locale, "Maintenance") }]
                  : []),
              ]}
              active={activeView}
              onChange={setActiveView}
              className="shrink-0"
              tabClassName="px-3 py-2 text-sm text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              activeTabClassName="font-semibold text-foreground"
            />
            {activeView === "fleet" && printers.length > 0 && (
              <section
                aria-label={uiText("Fleet summary")}
                className="flex flex-wrap items-center gap-x-1 gap-y-1 pb-1"
              >
                <FleetMetric
                  label={uiText("Ready")}
                  value={readyCount}
                  selected={healthFilter === "ready"}
                  onClick={() => setHealthFilter(healthFilter === "ready" ? null : "ready")}
                />
                <FleetMetric
                  label={uiText("Active jobs")}
                  value={summary?.active_jobs}
                  onClick={canSeeQueue ? () => openQueue("active") : undefined}
                />
                <FleetMetric
                  label={uiText("Queued jobs")}
                  value={summary?.queued_jobs}
                  onClick={canSeeQueue ? () => openQueue("queued") : undefined}
                />
                <FleetMetric
                  label={uiText("Needs attention")}
                  value={attentionCount}
                  selected={healthFilter === "attention"}
                  onClick={() => setHealthFilter(healthFilter === "attention" ? null : "attention")}
                />
                <label className="ml-1 flex min-w-0 max-w-full items-center gap-2 border-l border-border pl-3 text-xs text-muted-foreground">
                  {uiText("Group")}
                  <select
                    value={selectedGroup ?? ""}
                    onChange={(event) => setSelectedGroup(event.target.value || null)}
                    className="h-8 min-w-0 border border-input bg-card px-2 text-xs text-foreground"
                  >
                    <option value="">{uiText("All groups")}</option>
                    {printerGroups.map((group) => (
                      <option key={group.name} value={group.name}>
                        {group.name === "__ungrouped" ? uiText("Ungrouped") : group.name}
                      </option>
                    ))}
                  </select>
                </label>
              </section>
            )}
          </div>

          {activeView === "fleet" && (
            <>
              {error && (
                <div className="animate-panel-in rounded border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
                  {error}
                </div>
              )}

              {summaryQuery.isError && (
                <p role="status" className="text-xs text-muted-foreground">
                  {uiText("Fleet job data unavailable")}
                </p>
              )}

              {loading ? (
                <div className="grid animate-panel-in grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
                  {Array.from({ length: 3 }).map((_, i) => (
                    <div key={i} className="bg-card border border-border rounded p-5 space-y-3">
                      <Skeleton className="h-5 w-32" />
                      <Skeleton className="h-4 w-48" />
                      <Skeleton className="h-4 w-24" />
                    </div>
                  ))}
                </div>
              ) : printers.length === 0 ? (
                <EmptyState
                  icon={PrinterIcon}
                  title={uiText("No printers configured yet.")}
                  action={
                    user?.is_superuser ? (
                      <Button size="xs" onClick={() => setAddOpen(true)}>
                        <Plus className="h-3.5 w-3.5" />
                        {uiText("Add your first printer")}
                      </Button>
                    ) : undefined
                  }
                  className="animate-panel-in rounded-lg border border-border bg-card shadow-sm"
                />
              ) : (
                <div className="grid min-w-0 grid-cols-1 items-start gap-4 @5xl/fleet:grid-cols-[minmax(0,1fr)_minmax(17rem,24%)]">
                  {visiblePrinters.length === 0 ? (
                    <EmptyState
                      icon={PrinterIcon}
                      title={uiText("No matching printers")}
                      className="border border-border bg-card"
                    />
                  ) : (
                    <div className="grid min-w-0 gap-3 grid-cols-[repeat(auto-fit,minmax(min(100%,21rem),1fr))]">
                      {visiblePrinters.map((printer) => (
                        <FleetPrinterCard
                          key={printer.id}
                          printer={printer}
                          row={fleetRows.get(printer.id)}
                          artworkPreference={artworkPreference}
                          onSettings={() => setSettingsTarget(printer)}
                        />
                      ))}
                    </div>
                  )}
                  <FleetActivity
                    printers={printers}
                    openQueue={canSeeQueue ? openQueue : undefined}
                  />
                </div>
              )}
            </>
          )}

          {activeView === "queue" && (
            <FleetQueuePanel
              initialView={queueView}
              onAdd={() => setAddJobOpen(true)}
              printers={printers.filter((printer) => printer.access.can_print)}
            />
          )}
          {activeView === "maintenance" && (
            <FleetMaintenancePanel
              printers={printers.filter((printer) => printer.access.can_admin)}
              onPrintersChanged={() => {
                void printersQuery.refetch();
              }}
            />
          )}

          <Modal
            open={settingsTarget !== null}
            onClose={() => setSettingsTarget(null)}
            title={uiText("Edit printer connection")}
            description={uiText("Connect your printer using its local integration.")}
            footer={<div ref={setSettingsFooter} className="contents" />}
          >
            {settingsTarget && (
              <Suspense fallback={<Skeleton className="h-52 w-full" />}>
                <PrinterConnectionForm
                  printer={settingsTarget}
                  canEdit={settingsTarget.access.can_admin}
                  modalActions={{ formId: settingsFormId, target: settingsFooter }}
                  onCancel={() => setSettingsTarget(null)}
                  onSaved={() => setSettingsTarget(null)}
                  onRemove={() => {
                    setDeleteTarget(settingsTarget);
                    setSettingsTarget(null);
                  }}
                />
              </Suspense>
            )}
          </Modal>
          {addOpen && user?.is_superuser && (
            <AddPrinterModal
              onClose={() => setAddOpen(false)}
              onCreated={() => {
                // createPrinter already invalidated queryKeys.printers; just close.
                setAddOpen(false);
              }}
            />
          )}
        </div>
      </>
    </Localized>
  );
}

type FleetPrinterRow = FleetSummary["printers"][number];

function routingHold(printer: PrinterRead, row: FleetPrinterRow | undefined) {
  if (row?.maintenance) return uiText("Maintenance");
  if (row?.pending_operator_release) return uiText("Operator release required");
  if (row?.drain_mode ?? printer.drain_mode) return uiText("Draining");
  return null;
}

function isReady(printer: PrinterRead, row: FleetPrinterRow | undefined) {
  return (
    printer.status === "ready" && row?.current_job_id == null && routingHold(printer, row) === null
  );
}

function needsAttention(printer: PrinterRead, row: FleetPrinterRow | undefined) {
  return (
    printer.status === "error" ||
    printer.status === "offline" ||
    printer.status === "unknown" ||
    routingHold(printer, row) !== null
  );
}

function FleetMetric({
  label,
  value,
  onClick,
  selected,
}: {
  label: string;
  value: number | undefined;
  onClick?: () => void;
  selected?: boolean;
}) {
  const contents = (
    <>
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      <span className="font-mono text-xs font-semibold tabular-nums">
        {value === undefined ? "—" : formatNumber(value)}
      </span>
    </>
  );
  const classes = "flex min-w-0 items-center gap-1.5 px-2 py-1.5 text-left";
  return onClick ? (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onClick}
      className={`${classes} ${selected ? "bg-accent text-accent-foreground" : "bg-card text-card-foreground"} hover:bg-accent hover:text-accent-foreground focus-visible:z-10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring transition-transform duration-press active:scale-[0.98] motion-reduce:transition-none`}
    >
      {contents}
    </button>
  ) : (
    <div className={`${classes} bg-card text-card-foreground`}>{contents}</div>
  );
}

const STATUS_DOT = {
  ready: "bg-success",
  printing: "bg-chart-cyan",
  paused: "bg-warning",
  error: "bg-destructive",
  offline: "bg-muted-foreground",
  unknown: "bg-muted-foreground",
} satisfies Record<PrinterStatus, string>;

const ACTIVITY_VIEW = {
  queued: "queued",
  uploading: "active",
  started: "active",
  printing: "active",
  paused: "active",
  completed: "recent",
  cancelled: "recent",
  failed: "recent",
} satisfies Record<PrintJobState, FleetQueueView>;

function FleetActivity({
  printers,
  openQueue,
}: {
  printers: PrinterRead[];
  openQueue?: (view: FleetQueueView) => void;
}) {
  const activity = useFleetQueue({ refetchInterval: 8_000 });
  const printersById = new Map(printers.map((printer) => [printer.id, printer]));
  const jobs = activity.data ?? [];
  const live = jobs.filter((job) => ACTIVITY_VIEW[job.state] !== "recent").slice(0, 3);
  const recent = jobs.filter((job) => ACTIVITY_VIEW[job.state] === "recent").slice(0, 4);
  const rows = [...live, ...recent];
  return (
    <section aria-label={uiText("Fleet activity")} className="border border-border bg-card">
      <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-3">
        <h2 className="text-sm font-semibold">{uiText("Fleet activity")}</h2>
        {openQueue && (
          <Button variant="ghost" size="xs" onClick={() => openQueue("recent")}>
            {uiText("View history")}
          </Button>
        )}
      </div>
      {activity.isPending ? (
        <div role="status" aria-label={uiText("Loading")} className="space-y-2 p-4">
          <Skeleton className="h-5 w-full" />
          <Skeleton className="h-5 w-2/3" />
        </div>
      ) : activity.isError && !activity.data ? (
        <div className="space-y-2 p-4 text-xs text-muted-foreground">
          <p role="alert">{uiText("Fleet job data unavailable")}</p>
          <Button variant="outline" size="xs" onClick={() => void activity.refetch()}>
            {uiText("Retry")}
          </Button>
        </div>
      ) : rows.length === 0 ? (
        <div className="flex items-center gap-3 p-4 text-sm text-muted-foreground">
          <ListOrdered aria-hidden="true" className="size-4 shrink-0" />
          {uiText("No fleet activity yet")}
        </div>
      ) : (
        <ul className="divide-y divide-border">
          {rows.map((job) => {
            const printer = job.printer_id === null ? undefined : printersById.get(job.printer_id);
            const view = ACTIVITY_VIEW[job.state];
            const timestamp = parseApiInstant(job.finished_at ?? job.started_at ?? job.created_at);
            const name =
              job.identity?.display_name ??
              job.external_display_name ??
              job.remote_filename ??
              printJobArtifactLabel(job);
            const contents = (
              <>
                <span className="flex min-w-0 items-start justify-between gap-2">
                  <span className="truncate text-sm font-medium" title={name}>
                    {name}
                  </span>
                  <span
                    className={`shrink-0 text-xs capitalize ${job.state === "failed" ? "text-destructive" : job.state === "completed" ? "text-success" : "text-muted-foreground"}`}
                  >
                    {knownUiText(job.state)}
                  </span>
                </span>
                <span className="mt-1 flex flex-wrap items-center justify-between gap-x-2 gap-y-0.5 text-2xs text-muted-foreground">
                  <span className="truncate">
                    {printer?.name ??
                      (job.printer_id === null ? uiText("Unassigned") : uiText("Printer"))}
                  </span>
                  {timestamp ? (
                    <time dateTime={timestamp.toISOString()}>
                      {timestamp.toLocaleString(currentLocale(), {
                        month: "short",
                        day: "numeric",
                        hour: "numeric",
                        minute: "2-digit",
                      })}
                    </time>
                  ) : (
                    <span>{uiText("Not recorded")}</span>
                  )}
                </span>
              </>
            );
            const base = "block w-full min-w-0 px-4 py-3 text-left";
            const interactive = `${base} hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring`;
            return (
              <li key={job.id}>
                {view === "queued" && openQueue ? (
                  <button type="button" className={interactive} onClick={() => openQueue("queued")}>
                    {contents}
                  </button>
                ) : printer ? (
                  <Link
                    href={`/printers/${printer.id}${view === "recent" ? "?tab=jobs" : ""}`}
                    className={interactive}
                  >
                    {contents}
                  </Link>
                ) : (
                  <div className={base}>{contents}</div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

function FleetPrinterCard({
  printer,
  row,
  artworkPreference,
  onSettings,
}: {
  printer: PrinterRead;
  row: FleetPrinterRow | undefined;
  artworkPreference: PrinterArtworkPreference;
  onSettings: () => void;
}) {
  const hold = routingHold(printer, row);
  const progress = row?.progress == null ? null : row.progress * 100;
  // Older servers publish only the display label for each loaded slot.
  const loadedSlots =
    row?.loaded_material_slots ??
    row?.loaded_slots.map((label, index) => ({
      slot_key: String(index),
      label,
      material_type: null,
      color_hex: null,
    })) ??
    [];
  const address = printer.access.can_admin ? providerAddress(printer) : null;
  const model = printer.model_name || printer.detected_model;
  const artworkUrl = printerArtwork(artworkPreference === "model" ? model : null).imageUrl;
  const lastSeen = parseApiInstant(printer.last_seen_at);
  return (
    <article className="group @container/printer-card relative flex min-w-0 flex-col border border-border bg-card text-card-foreground hover:border-border-strong motion-safe:transition-transform duration-fast ease-out motion-safe:hover:-translate-y-0.5 motion-safe:active:scale-[0.99]">
      <div className="flex flex-1 items-stretch">
        {artworkPreference !== "off" && (
          <PrinterCardArtwork key={artworkUrl} url={artworkUrl} model={model} />
        )}
        <div className="grid min-w-0 flex-1 grid-cols-1 content-start gap-3 px-4 py-4 text-xs">
          <div className="flex min-w-0 flex-wrap items-start justify-between gap-x-3 gap-y-1.5">
            <div className="min-w-0">
              <h2 className="text-lg font-semibold leading-tight tracking-tight">
                <Link
                  href={`/printers/${printer.id}`}
                  className="wrap-anywhere after:absolute after:inset-0 after:z-10 focus-visible:outline-none focus-visible:after:ring-2 focus-visible:after:ring-ring"
                >
                  {printer.name}
                  <ArrowUpRight
                    className="ml-1 inline size-4 align-text-top text-muted-foreground"
                    aria-hidden="true"
                  />
                </Link>
              </h2>
              <p className="mt-1 wrap-anywhere text-xs text-muted-foreground">
                {providerLabel(printer)}
                {printer.capabilities.support_level === "beta" ? ` · ${uiText("Beta")}` : ""}
                {printer.group ? ` · ${printer.group}` : ""}
              </p>
            </div>
            <span className="inline-flex min-w-0 max-w-full items-center gap-1.5 wrap-anywhere text-xs font-medium capitalize">
              <span
                aria-hidden="true"
                className={`size-2 shrink-0 rounded-full ${STATUS_DOT[printer.status]}`}
              />
              {knownUiText(printer.status)}
            </span>
          </div>
          <div className="flex flex-wrap items-start gap-x-4 gap-y-2">
            <div className="min-w-0 flex-1 basis-28 space-y-1.5">
              <div className="flex min-w-0 items-center justify-between gap-2">
                <span className="text-muted-foreground">{uiText("Current job")}</span>
                {progress !== null && (
                  <span className="shrink-0 font-mono text-sm font-semibold tabular-nums">
                    {formatNumber(progress, { maximumFractionDigits: 0 })}%
                  </span>
                )}
              </div>
              <p
                className="truncate text-sm font-semibold"
                title={row?.current_job_name ?? undefined}
              >
                {row?.current_job_name ??
                  (row?.current_job_id != null
                    ? knownUiText(printer.status)
                    : row
                      ? uiText("No active print")
                      : "—")}
              </p>
              {progress !== null && <PrinterProgress value={progress} />}
            </div>
            {row?.telemetry && <FleetTelemetryReadings telemetry={row.telemetry} />}
          </div>
          {(row?.next_job_name || hold) && (
            <div className="flex min-w-0 flex-wrap items-baseline justify-between gap-x-3 gap-y-1 border-t border-border pt-2.5">
              {row?.next_job_name && (
                <div className="flex min-w-0 items-baseline gap-2">
                  <span className="shrink-0 text-muted-foreground">{uiText("Next job")}</span>
                  <span className="truncate" title={row.next_job_name}>
                    {row.next_job_name}
                  </span>
                </div>
              )}
              {hold && <p className="font-medium text-warning">{hold}</p>}
            </div>
          )}
          {printer.last_error && (
            <p className="line-clamp-2 break-words text-destructive" title={printer.last_error}>
              {uiText("Connection issue: ")}
              {printer.last_error}
            </p>
          )}
        </div>
      </div>
      {row && (row.nozzle_diameter_mm !== null || loadedSlots.length > 0) && (
        <div className="flex min-w-0 flex-wrap items-center gap-2 border-t border-border px-4 py-2.5">
          {row.nozzle_diameter_mm !== null && (
            <span className="font-mono text-2xs text-muted-foreground">
              {uiText("{value1} mm nozzle", { value1: formatNumber(row.nozzle_diameter_mm) })}
            </span>
          )}
          {loadedSlots.map((slot) => {
            const color =
              slot.color_hex && /^#[0-9a-f]{6}$/i.test(slot.color_hex) ? slot.color_hex : undefined;
            return (
              <span
                key={slot.slot_key}
                title={color}
                className="inline-flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-0.5 border border-border px-1.5 py-0.5 text-2xs"
              >
                {color && (
                  <span
                    className="size-2.5 shrink-0 border border-input"
                    style={{ backgroundColor: color }}
                  />
                )}
                {slot.material_type && <span className="font-medium">{slot.material_type}</span>}
                <span className="min-w-0 break-words text-muted-foreground">{slot.label}</span>
              </span>
            );
          })}
        </div>
      )}
      <div className="flex min-h-12 items-center justify-between gap-2 border-t border-border bg-muted/30 px-4 py-2">
        <div className="min-w-0 space-y-0.5">
          <div
            className={
              printer.access.can_admin ? "relative z-20 w-fit max-w-full" : "w-fit max-w-full"
            }
          >
            <PrinterModelBadge printer={printer} canEdit={printer.access.can_admin} />
          </div>
          <p
            className="truncate text-2xs text-muted-foreground"
            title={address ?? printer.notes ?? undefined}
          >
            {lastSeen
              ? uiText("Seen {value1}", {
                  value1: lastSeen.toLocaleString(currentLocale()),
                })
              : uiText(printer.last_seen_at ? "Not recorded" : "Never connected")}
            {address && ` · ${address}`}
          </p>
          {printer.notes && (
            <p className="truncate text-2xs text-muted-foreground">{printer.notes}</p>
          )}
        </div>
        {printer.access.can_admin && (
          <Button variant="outline" size="xs" className="relative z-20" onClick={onSettings}>
            {uiText("Settings")}
          </Button>
        )}
      </div>
    </article>
  );
}

function FleetTelemetryReadings({ telemetry }: { telemetry: FleetPrinterTelemetry }) {
  const readings = [
    { label: uiText("Nozzle"), value: telemetry.nozzle_c, unit: " °C" },
    { label: uiText("Bed"), value: telemetry.bed_c, unit: " °C" },
    {
      label: uiText("Part cooling fan"),
      value: telemetry.part_fan_speed == null ? null : telemetry.part_fan_speed * 100,
      unit: "%",
    },
  ];
  if (readings.every(({ value }) => value == null)) return null;

  return (
    <dl className="grid max-w-full gap-y-1 border-s border-border ps-3">
      {readings.map(({ label, value, unit }) =>
        value == null ? null : (
          <div key={label} className="flex items-baseline justify-between gap-2">
            <dt className="flex min-w-0 items-center gap-1 wrap-anywhere text-muted-foreground">
              {unit === "%" && (
                <Fan
                  aria-hidden="true"
                  className="size-3 shrink-0 transition-transform duration-fast ease-out motion-reduce:transition-none"
                  style={{ transform: `rotate(${value * 2.7}deg)` }}
                />
              )}
              {label}
            </dt>
            <dd className="whitespace-nowrap font-mono font-medium tabular-nums">
              {formatNumber(value, { maximumFractionDigits: 0 })}
              {unit}
            </dd>
          </div>
        ),
      )}
    </dl>
  );
}

function PrinterCardArtwork({ url, model }: { url: string; model: string | null | undefined }) {
  const [failed, setFailed] = useState(false);
  return (
    <div className="grid min-h-36 w-20 shrink-0 place-items-center overflow-hidden border-e border-border bg-preview-image-matte px-1.5 py-2 @xs/printer-card:w-24 @lg/printer-card:w-32">
      <img
        src={failed ? printerArtwork(null).imageUrl : url}
        alt={model ? uiText("{value1} printer", { value1: model }) : uiText("Printer")}
        referrerPolicy="no-referrer"
        onError={() => setFailed(true)}
        className="h-32 w-full object-contain motion-safe:transition-transform duration-fast ease-out motion-safe:group-hover:-translate-y-1 motion-safe:group-active:scale-[0.98]"
      />
    </div>
  );
}

const OTHER_MODEL_OPTION = "__other__";

function PrinterModelBadge({ printer, canEdit }: { printer: PrinterRead; canEdit: boolean }) {
  useUiLocale();
  const [editing, setEditing] = useState(false);
  const displayModel = printer.model_name || printer.detected_model;
  if (editing) return <PrinterModelEditor printer={printer} onClose={() => setEditing(false)} />;

  return (
    <button
      type="button"
      disabled={!canEdit}
      className="flex max-w-full items-center gap-1 truncate rounded-md text-left text-xs font-medium text-foreground enabled:hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none"
      onClick={(e) => {
        if (!canEdit) return;
        e.preventDefault();
        e.stopPropagation();
        setEditing(true);
      }}
    >
      {displayModel || (canEdit ? uiText("Set model") : uiText("Model unknown"))}
      {canEdit && <Pencil className="h-2.5 w-2.5 opacity-60" />}
    </button>
  );
}

function PrinterModelEditor({
  printer: initialPrinter,
  onClose,
}: {
  printer: PrinterRead;
  onClose: () => void;
}) {
  const [printer, setPrinter] = useState(initialPrinter);
  const command = usePrinterSettingsCommand(printer.id);
  const displayModel = printer.model_name || printer.detected_model;
  const [selected, setSelected] = useState(() =>
    displayModel && PRINTER_MODEL_OPTIONS.includes(displayModel)
      ? displayModel
      : displayModel
        ? OTHER_MODEL_OPTION
        : "",
  );
  const [customValue, setCustomValue] = useState(
    displayModel && !PRINTER_MODEL_OPTIONS.includes(displayModel) ? displayModel : "",
  );
  const saving = command.busy;

  async function save(revised = false, chosen?: string) {
    const modelName = chosen ?? (selected === OTHER_MODEL_OPTION ? customValue.trim() : selected);
    if (!modelName) return;
    try {
      await command.save(printer, { model_name: modelName }, revised);
      onClose();
    } catch (err) {
      if (!(err instanceof Error && err.name === "AbortError")) toast.error(err);
    }
  }

  return (
    <div>
      <PrinterModelPicker
        key={`${printer.edit_epoch}:${printer.edit_version}`}
        selected={selected}
        customValue={customValue}
        saving={saving}
        blocked={command.blocked}
        onSelectedChange={setSelected}
        onCustomValueChange={setCustomValue}
        onClose={onClose}
        onSave={(chosen) => {
          if (chosen) {
            setSelected(chosen);
          }
          void save(false, chosen);
        }}
      >
        <PrinterSettingsReview
          canSave={Boolean(selected && (selected !== OTHER_MODEL_OPTION || customValue.trim()))}
          state={command.state}
          original={printer}
          onReview={() => {
            void command.review().catch((err) => {
              if (!(err instanceof Error && err.name === "AbortError")) toast.error(err);
            });
          }}
          onAdopt={() => {
            const latest = command.adopt();
            setPrinter(latest);
            const model = latest.model_name || latest.detected_model;
            setSelected(
              model && PRINTER_MODEL_OPTIONS.includes(model)
                ? model
                : model
                  ? OTHER_MODEL_OPTION
                  : "",
            );
            setCustomValue(model && !PRINTER_MODEL_OPTIONS.includes(model) ? model : "");
          }}
          onSave={() => {
            void save(true);
          }}
        />
      </PrinterModelPicker>
    </div>
  );
}

function PrinterModelPicker({
  selected,
  customValue,
  saving,
  blocked,
  children,
  onSelectedChange,
  onCustomValueChange,
  onClose,
  onSave,
}: {
  selected: string;
  customValue: string;
  saving: boolean;
  blocked: boolean;
  children: React.ReactNode;
  onSelectedChange: (model: string) => void;
  onCustomValueChange: (value: string) => void;
  onClose: () => void;
  onSave: (model?: string) => void;
}) {
  useUiLocale();
  const [query, setQuery] = useState("");
  const [custom, setCustom] = useState(false);
  const [manufacturer, setManufacturer] = useState("");
  const models = PRINTER_MODEL_OPTIONS.filter((model) =>
    model.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()),
  );
  const modelHintId = useId();
  return (
    <Localized>
      <Modal
        open
        onClose={onClose}
        title={custom ? uiText("Custom printer model") : uiText("Choose printer model")}
        description={
          custom
            ? uiText("Describe a printer that is not in the model list.")
            : uiText("Choose the hardware model for this connection.")
        }
        className="max-w-160"
        footer={
          <>
            <Button variant="outline" disabled={saving} onClick={onClose}>
              {uiText("Back to printer")}
            </Button>
            {custom ? (
              <Button
                loading={saving}
                disabled={!customValue.trim() || blocked}
                onClick={() =>
                  onSave([manufacturer.trim(), customValue.trim()].filter(Boolean).join(" "))
                }
              >
                {uiText("Use this model")}
              </Button>
            ) : (
              <Button
                disabled={saving || blocked}
                onClick={() => {
                  setCustom(true);
                  onSelectedChange(OTHER_MODEL_OPTION);
                }}
              >
                {uiText("Use custom model")}
              </Button>
            )}
          </>
        }
      >
        {custom ? (
          <div className="space-y-4">
            <h3 className="text-field font-semibold">{uiText("Hardware")}</h3>
            <FormField label={uiText("Manufacturer")}>
              <Input
                value={manufacturer}
                onChange={(event) => setManufacturer(event.target.value)}
                disabled={saving}
              />
            </FormField>
            <FormField
              label={uiText("Model")}
              hint={uiText("Enter the model printed on the machine.")}
              hintId={modelHintId}
            >
              <Input
                autoFocus
                value={customValue}
                aria-describedby={modelHintId}
                onChange={(event) => onCustomValueChange(event.target.value)}
                disabled={saving}
                required
              />
            </FormField>
          </div>
        ) : (
          <>
            <FormField label={uiText("Search manufacturer / model")}>
              <Input
                autoFocus
                type="search"
                disabled={saving}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
            </FormField>
            <div className="mt-4 max-h-96 divide-y divide-border overflow-y-auto">
              {models.length ? (
                models.map((model) => (
                  <div key={model} className="flex items-start justify-between gap-4 py-3">
                    <div>
                      <strong className="block text-sm font-semibold">{model}</strong>
                      <span className="mt-1 block text-sm text-muted-foreground">
                        {uiText("Printer model")}
                      </span>
                    </div>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={saving || blocked}
                      aria-pressed={selected === model}
                      onClick={() => {
                        onSelectedChange(model);
                        onSave(model);
                      }}
                    >
                      {uiText("Select")}
                    </Button>
                  </div>
                ))
              ) : (
                <p className="py-4 text-sm text-muted-foreground">
                  {uiText("No printer models match your search.")}
                </p>
              )}
            </div>
          </>
        )}
        {children}
      </Modal>
    </Localized>
  );
}

function AddPrinterModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const formId = useId();
  const { createPrinter } = usePrinterCatalogCommands();
  useUiLocale();
  const [name, setName] = useState("");
  const [modelChoiceOpen, setModelChoiceOpen] = useState(false);
  const [modelChoice, setModelChoice] = useState("");
  const [customModel, setCustomModel] = useState("");
  const [modelName, setModelName] = useState("");
  const [setupKind, setSetupKind] = useState<PrinterSetupKind>("moonraker");
  const [url, setUrl] = useState("");
  const [moonrakerKey, setMoonrakerKey] = useState("");
  const [bambuSerial, setBambuSerial] = useState("");
  const [bambuAccessCode, setBambuAccessCode] = useState("");
  const [prusaAuthMode, setPrusaAuthMode] = useState<"digest" | "api_key">("digest");
  const [prusaUsername, setPrusaUsername] = useState("maker");
  const [prusaSecret, setPrusaSecret] = useState("");
  const [octoprintApiKey, setOctoprintApiKey] = useState("");
  const [centauriAccessCode, setCentauriAccessCode] = useState("");
  const [centauriMainboardId, setCentauriMainboardId] = useState("");
  const [notes, setNotes] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const submittingRef = useRef(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (submittingRef.current) return;
    submittingRef.current = true;
    setSubmitting(true);
    setErr(null);
    try {
      // Only the chosen provider's credentials are sent; each branch adds the
      // fields that provider actually has.
      const payload: PrinterCreate = {
        name: name.trim(),
        model_name: modelName || undefined,
        ...setupProviderFields(setupKind),
        notes: notes || undefined,
      };
      if (setupKind === "moonraker" || setupKind === "elegoo_neptune4") {
        payload.moonraker_url = url.trim();
        payload.api_key = moonrakerKey || undefined;
      } else if (setupKind === "bambu_lan") {
        payload.bambu_host = url.trim();
        payload.bambu_serial = bambuSerial.trim();
        payload.bambu_access_code = bambuAccessCode;
      } else if (setupKind === "prusalink") {
        payload.prusalink_url = url.trim();
        payload.prusalink_auth_mode = prusaAuthMode;
        if (prusaAuthMode === "digest") {
          payload.prusalink_username = prusaUsername.trim();
          payload.prusalink_password = prusaSecret;
        } else {
          payload.prusalink_api_key = prusaSecret;
        }
      } else if (
        setupKind === "elegoo_centauri_carbon" ||
        setupKind === "elegoo_centauri_carbon_2"
      ) {
        payload.elegoo_centauri_host = url.trim();
        if (setupKind === "elegoo_centauri_carbon_2") {
          payload.elegoo_centauri_access_code = centauriAccessCode;
        } else {
          payload.elegoo_centauri_mainboard_id = centauriMainboardId.trim() || undefined;
        }
      } else if (setupKind === "octoprint") {
        payload.octoprint_url = url.trim();
        payload.octoprint_api_key = octoprintApiKey;
      }
      await createPrinter(payload);
      toast.success(uiText('Printer "{value1}" added', { value1: String(name.trim()) }));
      onCreated();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : uiText("Could not add printer"));
      toast.error(e);
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  }

  return (
    <Localized>
      <Modal
        open={!modelChoiceOpen}
        onClose={() => {
          if (!submitting) onClose();
        }}
        title={uiText("Add printer")}
        description={uiText("Connect your printer using its local integration.")}
        footer={
          <>
            <Button type="button" variant="outline" onClick={onClose} disabled={submitting}>
              {uiText("Cancel")}
            </Button>
            <Button type="submit" form={formId} loading={submitting} disabled={!name || !url}>
              {uiText("Add printer")}
            </Button>
          </>
        }
      >
        <form id={formId} onSubmit={submit} className="space-y-5">
          <div className="grid gap-4 sm:grid-cols-2">
            <h3 className="col-span-full text-field font-semibold">{uiText("Printer")}</h3>
            <FormField htmlFor="printer-name" label={uiText("Name")}>
              <Input
                id="printer-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
              />
            </FormField>
            <FormField htmlFor="printer-integration" label={uiText("Integration")}>
              <select
                id="printer-integration"
                value={setupKind}
                onChange={(e) => {
                  const kind = parseSetupKind(e.target.value);
                  if (kind === null) return;
                  setSetupKind(kind);
                  setUrl("");
                }}
                className={inputClasses}
              >
                {PRINTER_SETUP_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </FormField>
            <div className="col-span-full flex flex-wrap items-center justify-between gap-3 text-field">
              <span className="text-muted-foreground">
                {modelName || uiText("No printer model selected")}
              </span>
              <Button type="button" variant="outline" onClick={() => setModelChoiceOpen(true)}>
                {uiText("Choose printer model")}
              </Button>
            </div>
          </div>
          <div className="space-y-4">
            <div>
              <h3 className="text-field font-semibold">{uiText("Connection")}</h3>
              <p className="mt-1 text-xs text-muted-foreground">
                {PRINTER_SETUP_OPTIONS.find((option) => option.value === setupKind)?.description}
              </p>
            </div>
            <FormField
              htmlFor="printer-address"
              label={
                setupKind === "prusalink"
                  ? uiText("PrusaLink URL")
                  : setupKind === "octoprint"
                    ? uiText("OctoPrint URL")
                    : setupKind === "moonraker"
                      ? uiText("Moonraker URL")
                      : setupKind === "elegoo_neptune4"
                        ? uiText("Printer URL")
                        : uiText("Printer host or IP")
              }
            >
              <Input
                id="printer-address"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder={
                  setupKind === "prusalink"
                    ? "http://mk4.local"
                    : setupKind === "octoprint"
                      ? "http://octopi.local"
                      : setupKind === "moonraker" || setupKind === "elegoo_neptune4"
                        ? "http://printer.local:7125"
                        : "192.168.1.50"
                }
                required
              />
            </FormField>
            {(setupKind === "moonraker" || setupKind === "elegoo_neptune4") && (
              <FormField
                htmlFor="moonraker-api-key"
                label={
                  <>
                    {setupKind === "elegoo_neptune4"
                      ? uiText("API key")
                      : uiText("Moonraker API key")}{" "}
                    <span className="font-normal normal-case tracking-normal opacity-60">
                      {uiText("(optional)")}
                    </span>
                  </>
                }
              >
                <Input
                  id="moonraker-api-key"
                  type="password"
                  value={moonrakerKey}
                  onChange={(e) => setMoonrakerKey(e.target.value)}
                  placeholder={uiText("Leave blank if auth is disabled")}
                />
              </FormField>
            )}
            {setupKind === "bambu_lan" && (
              <>
                <FormField htmlFor="bambu-serial" label={uiText("Printer serial")}>
                  <Input
                    id="bambu-serial"
                    value={bambuSerial}
                    onChange={(e) => setBambuSerial(e.target.value)}
                    required
                  />
                </FormField>
                <FormField htmlFor="bambu-access-code" label={uiText("LAN access code")}>
                  <Input
                    id="bambu-access-code"
                    type="password"
                    value={bambuAccessCode}
                    onChange={(e) => setBambuAccessCode(e.target.value)}
                    required
                  />
                </FormField>
              </>
            )}
            {setupKind === "prusalink" && (
              <>
                <FormField htmlFor="prusalink-auth" label={uiText("Authentication")}>
                  <select
                    id="prusalink-auth"
                    value={prusaAuthMode}
                    onChange={(e) => {
                      setPrusaAuthMode(e.target.value === "api_key" ? "api_key" : "digest");
                      setPrusaSecret("");
                    }}
                    className={inputClasses}
                  >
                    <option value="digest">{uiText("Username and password (recommended)")}</option>
                    <option value="api_key">{uiText("Legacy API key")}</option>
                  </select>
                </FormField>
                {prusaAuthMode === "digest" && (
                  <FormField htmlFor="prusalink-username" label={uiText("Username")}>
                    <Input
                      id="prusalink-username"
                      value={prusaUsername}
                      onChange={(e) => setPrusaUsername(e.target.value)}
                      required
                    />
                  </FormField>
                )}
                <FormField
                  htmlFor="prusalink-secret"
                  label={prusaAuthMode === "digest" ? uiText("Password") : uiText("API key")}
                >
                  <Input
                    id="prusalink-secret"
                    type="password"
                    value={prusaSecret}
                    onChange={(e) => setPrusaSecret(e.target.value)}
                    required
                  />
                </FormField>
              </>
            )}
            {setupKind === "octoprint" && (
              <FormField htmlFor="octoprint-api-key" label={uiText("API key")}>
                <Input
                  id="octoprint-api-key"
                  type="password"
                  value={octoprintApiKey}
                  onChange={(e) => setOctoprintApiKey(e.target.value)}
                  required
                />
              </FormField>
            )}
            {setupKind === "elegoo_centauri_carbon" && (
              <FormField
                htmlFor="centauri-mainboard-id"
                label={
                  <>
                    {uiText("Mainboard ID")}{" "}
                    <span className="font-normal normal-case tracking-normal opacity-60">
                      {uiText("(recommended)")}
                    </span>
                  </>
                }
                hintId={`${formId}-mainboard-hint`}
                hint={uiText(
                  "Needed for reliable printer commands while idle, paused, or errored.",
                )}
              >
                <Input
                  id="centauri-mainboard-id"
                  aria-describedby={`${formId}-mainboard-hint`}
                  value={centauriMainboardId}
                  onChange={(e) => setCentauriMainboardId(e.target.value)}
                  placeholder={uiText("From printer discovery or diagnostics")}
                />
              </FormField>
            )}
            {setupKind === "elegoo_centauri_carbon_2" && (
              <>
                <div className="text-xs text-warning">
                  {uiText("Enable LAN Only in printer network settings before connecting.")}
                </div>
                <FormField htmlFor="centauri-access-code" label={uiText("Printer access code")}>
                  <Input
                    id="centauri-access-code"
                    type="password"
                    value={centauriAccessCode}
                    onChange={(e) => setCentauriAccessCode(e.target.value)}
                    required
                  />
                </FormField>
              </>
            )}
            <div>
              <FormField htmlFor="printer-notes" label={uiText("Notes")}>
                <textarea
                  id="printer-notes"
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  className={inputClasses}
                  placeholder={uiText("Optional")}
                />
              </FormField>
              {err && (
                <div role="alert" className="mt-1 text-xs text-destructive">
                  {err}
                </div>
              )}
            </div>
          </div>
        </form>
      </Modal>
      {modelChoiceOpen && (
        <PrinterModelPicker
          selected={modelChoice}
          customValue={customModel}
          saving={false}
          blocked={false}
          onSelectedChange={setModelChoice}
          onCustomValueChange={setCustomModel}
          onClose={() => setModelChoiceOpen(false)}
          onSave={(chosen) => {
            setModelName(
              chosen ?? (modelChoice === OTHER_MODEL_OPTION ? customModel.trim() : modelChoice),
            );
            setModelChoiceOpen(false);
          }}
        >
          {null}
        </PrinterModelPicker>
      )}
    </Localized>
  );
}
