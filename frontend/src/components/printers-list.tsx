"use client";

import { useQueryClient } from "@tanstack/react-query";
import { queryKeys } from "@/lib/query-client";
import { usePrinterSettingsCommand } from "@/features/printers/settings-edit";
import { PrinterSettingsReview } from "@/features/printers/settings-review";

import { knownUiText, uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

import { lazy, Suspense, useEffect, useId, useMemo, useRef, useState } from "react";
import { Link } from "@/lib/link";
import {
  PrinterRead,
  type FleetPrinterTelemetry,
  type FleetSummary,
  type PrinterCreate,
  type PrinterStatus,
} from "@/types";
import { FLEET_LIVE_REFRESH_MS, usePrinterCatalogCommands } from "@/features/printers/queries";
import { useFleetSummary, usePrinters } from "@/lib/queries";
import { toast } from "@/lib/toast";
import { useAuth } from "@/lib/auth-context";
import {
  PRINTER_MODEL_OPTIONS,
  PRINTER_SETUP_OPTIONS,
  providerLabel,
  setupProviderFields,
  type PrinterSetupKind,
} from "@/lib/printer-providers";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/ui/empty-state";
import { Button } from "@/components/ui/button";
import { FieldErrors } from "@/components/ui/field-errors";
import { useFormValidation } from "@/lib/form-validation";
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
import { formatNumber } from "@/lib/format";
import { PrinterProgress } from "@/components/printer-telemetry";
import {
  ArrowUpRight,
  Check,
  Fan,
  Layers,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Settings,
  Trash2,
} from "lucide-react";
import { Printer3D } from "@/components/ui/printer-3d";
import { DropdownMenu } from "@/components/ui/dropdown-menu";
import { PrinterStatusBadge } from "@/components/printer-status-badge";

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

const FLEET_GROUPING_KEY = "printstash.fleet.group-printers";

function printerGroup(printer: PrinterRead): string | null {
  return printer.group?.trim() || null;
}

function groupOptionValue(name: string | null): string {
  return name === null ? "ungrouped" : `group:${name}`;
}

function groupPrinters(printers: PrinterRead[]) {
  const groups = new Map<string | null, PrinterRead[]>();
  for (const printer of printers) {
    const name = printerGroup(printer);
    const members = groups.get(name);
    if (members) members.push(printer);
    else groups.set(name, [printer]);
  }
  return [...groups.entries()].sort(([a], [b]) => {
    if (a === null) return 1;
    if (b === null) return -1;
    return a.localeCompare(b);
  });
}

/** Resolve a <select> value back to the setup kind it was rendered from. */
function parseSetupKind(value: string): PrinterSetupKind | null {
  return PRINTER_SETUP_OPTIONS.find((option) => option.value === value)?.value ?? null;
}

/** A host or IP, optionally with a scheme, port and path; `://` or `http://` alone is not one. */
function looksLikePrinterAddress(value: string): boolean {
  const rest = value.trim().replace(/^[a-z][a-z0-9+.-]*:\/\//i, "");
  return /^[a-z0-9]([a-z0-9._-]*[a-z0-9])?(:\d{1,5})?(\/\S*)?$/i.test(rest);
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
  const printers = useMemo(() => printersQuery.data ?? [], [printersQuery.data]);
  const loading = printersQuery.isLoading;
  const error = printersQuery.error instanceof Error ? printersQuery.error.message : null;
  const [addOpen, setAddOpen] = useState(false);
  const [addJobOpen, setAddJobOpen] = useState(false);
  const settingsFormId = useId();
  const [settingsFooter, setSettingsFooter] = useState<HTMLDivElement | null>(null);
  const [settingsTarget, setSettingsTarget] = useState<PrinterRead | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<PrinterRead | null>(null);
  const [modelTarget, setModelTarget] = useState<PrinterRead | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const artworkPreference = usePrinterArtworkPreference();
  const [selectedGroup, setSelectedGroup] = useState<string | null>(null);
  const [grouped, setGrouped] = useState(() => {
    try {
      return localStorage.getItem(FLEET_GROUPING_KEY) === "true";
    } catch {
      return false;
    }
  });
  const searchParams = useSearchParams();
  const [requestedView, setActiveView] = useState<"fleet" | "queue" | "maintenance">(() =>
    searchParams.get("tab") === "queue" ? "queue" : "fleet",
  );
  const [queueView, setQueueView] = useState<FleetQueueView>("queued");
  const [healthFilter, setHealthFilter] = useState<"ready" | "attention" | null>(null);
  const canSeeQueue = Boolean(
    user?.is_superuser || printers.some((printer) => printer.access.can_print),
  );
  const manageablePrinters = printers.filter((printer) => printer.access.can_admin);
  const canManagePrinters = Boolean(user?.is_superuser || manageablePrinters.length);
  const activeView = requestedView;
  const summaryQuery = useFleetSummary({
    refetchInterval: activeView === "fleet" ? FLEET_LIVE_REFRESH_MS : undefined,
  });
  const summary = summaryQuery.data;
  const fleetRows = useMemo(
    () => new Map(summary?.printers.map((row) => [row.printer_id, row])),
    [summary],
  );
  const visiblePrinters = useMemo(
    () =>
      printers
        .filter((printer) => {
          const row = fleetRows.get(printer.id);
          return (
            (selectedGroup === null || groupOptionValue(printerGroup(printer)) === selectedGroup) &&
            (healthFilter === null ||
              (healthFilter === "ready" && isReady(printer, row)) ||
              (healthFilter === "attention" && needsAttention(printer, row)))
          );
        })
        .sort(
          (a, b) =>
            STATUS_PRIORITY[liveStatus(a, fleetRows.get(a.id))] -
              STATUS_PRIORITY[liveStatus(b, fleetRows.get(b.id))] || a.name.localeCompare(b.name),
        ),
    [printers, fleetRows, selectedGroup, healthFilter],
  );
  const printerGroups = useMemo(() => groupPrinters(printers), [printers]);
  const visibleGroups = useMemo(
    () => (grouped ? groupPrinters(visiblePrinters) : []),
    [grouped, visiblePrinters],
  );
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

  function toggleGrouping() {
    const next = !grouped;
    setGrouped(next);
    try {
      localStorage.setItem(FLEET_GROUPING_KEY, String(next));
    } catch {
      // The current page still applies the layout if storage is unavailable.
    }
  }

  function renderCards(list: PrinterRead[]) {
    return list.map((printer) => (
      <FleetPrinterCard
        key={printer.id}
        printer={printer}
        row={fleetRows.get(printer.id)}
        artworkPreference={artworkPreference}
        grouped={grouped}
        onSettings={setSettingsTarget}
        onRemove={setDeleteTarget}
        onEditModel={setModelTarget}
      />
    ));
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
                  size="sm"
                  onClick={() => {
                    void printersQuery.refetch();
                    void summaryQuery.refetch();
                    void queryClient.invalidateQueries({ queryKey: queryKeys.fleetQueue });
                  }}
                >
                  <RefreshCw className="h-4 w-4" aria-hidden />
                  {uiText("Refresh")}
                </Button>
                <Button
                  size="sm"
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
                  <Plus className="h-4 w-4" aria-hidden />
                  {activeView === "queue"
                    ? uiText("Add to queue")
                    : user?.is_superuser
                      ? uiText("Add printer")
                      : uiText("Admin required")}
                </Button>
              </>
            }
          />

          <div className="flex flex-col items-stretch gap-5">
            <TabBar
              variant="framed"
              tabs={[
                { key: "fleet", label: translateUiText(locale, "Fleet") },
                ...(canSeeQueue
                  ? [{ key: "queue" as const, label: translateUiText(locale, "Queue") }]
                  : []),
                ...(canManagePrinters
                  ? [
                      {
                        key: "maintenance" as const,
                        label: translateUiText(locale, "Maintenance"),
                      },
                    ]
                  : []),
              ]}
              active={activeView}
              onChange={setActiveView}
              className="self-start"
            />
            {activeView === "fleet" && printers.length > 0 && (
              <section
                aria-label={uiText("Fleet summary")}
                className="flex flex-wrap items-center justify-between gap-3"
              >
                <div className="grid w-full grid-cols-2 overflow-hidden rounded-lg border border-border bg-card sm:flex sm:w-auto sm:divide-x sm:divide-border max-sm:[&>*:nth-child(even)]:border-l max-sm:[&>*:nth-child(n+3)]:border-t max-sm:[&>*]:border-border">
                  <FleetMetric
                    label={uiText("Ready")}
                    tone="ready"
                    value={readyCount}
                    selected={healthFilter === "ready"}
                    onClick={() => setHealthFilter(healthFilter === "ready" ? null : "ready")}
                  />
                  <FleetMetric
                    label={uiText("Active jobs")}
                    tone="active"
                    value={summary?.active_jobs}
                    onClick={canSeeQueue ? () => openQueue("active") : undefined}
                  />
                  <FleetMetric
                    label={uiText("Queued jobs")}
                    tone="queued"
                    value={summary?.queued_jobs}
                    onClick={canSeeQueue ? () => openQueue("queued") : undefined}
                  />
                  <FleetMetric
                    label={uiText("Needs attention")}
                    tone="attention"
                    value={attentionCount}
                    selected={healthFilter === "attention"}
                    onClick={() =>
                      setHealthFilter(healthFilter === "attention" ? null : "attention")
                    }
                  />
                </div>
                <div className="flex min-w-0 max-w-full flex-wrap items-center gap-3">
                  <Button
                    type="button"
                    variant="outline"
                    size="xs"
                    aria-pressed={grouped}
                    onClick={toggleGrouping}
                    className="aria-pressed:bg-accent aria-pressed:text-accent-foreground dark:aria-pressed:bg-surface-bright"
                  >
                    {grouped ? (
                      <Check className="h-3.5 w-3.5" aria-hidden />
                    ) : (
                      <Layers className="h-3.5 w-3.5" aria-hidden />
                    )}
                    {uiText("Group printers")}
                  </Button>
                  <select
                    aria-label={uiText("Group")}
                    value={selectedGroup ?? ""}
                    onChange={(event) => setSelectedGroup(event.target.value || null)}
                    className="h-8 w-37.5 min-w-0 px-2.5 text-sm text-foreground"
                  >
                    <option value="">{uiText("All groups")}</option>
                    {printerGroups.map(([name]) => (
                      <option key={groupOptionValue(name)} value={groupOptionValue(name)}>
                        {name ?? uiText("Ungrouped")}
                      </option>
                    ))}
                  </select>
                </div>
              </section>
            )}
          </div>

          {activeView === "fleet" && error && (
            <div
              role="alert"
              className="animate-panel-in rounded border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive"
            >
              {error}
            </div>
          )}

          {activeView === "fleet" && (
            <>
              {summaryQuery.isError && (
                <p role="status" className="text-xs text-muted-foreground">
                  {uiText("Fleet job data unavailable")}
                </p>
              )}

              {loading ? (
                <div className="grid animate-panel-in grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
                  {Array.from({ length: 3 }).map((_, i) => (
                    <div key={i} className="bg-card border border-border rounded-lg p-5 space-y-3">
                      <Skeleton className="h-5 w-32" />
                      <Skeleton className="h-4 w-48" />
                      <Skeleton className="h-4 w-24" />
                    </div>
                  ))}
                </div>
              ) : printers.length === 0 ? (
                <EmptyState
                  icon={Printer3D}
                  title={uiText("No printers configured yet.")}
                  action={
                    user?.is_superuser ? (
                      <Button size="xs" onClick={() => setAddOpen(true)}>
                        {uiText("Add your first printer")}
                      </Button>
                    ) : undefined
                  }
                  className="animate-panel-in rounded-lg border border-border bg-card shadow-sm"
                />
              ) : (
                <div className="min-w-0">
                  {visiblePrinters.length === 0 ? (
                    <EmptyState
                      icon={Printer3D}
                      title={uiText("No matching printers")}
                      className="rounded-lg border border-border bg-card"
                    />
                  ) : grouped ? (
                    <div className="flex flex-wrap items-start gap-5 pt-2">
                      {visibleGroups.map(([name, members]) => {
                        const label = name ?? uiText("Ungrouped");
                        return (
                          <section
                            key={groupOptionValue(name)}
                            aria-label={label}
                            className="relative w-fit max-w-full rounded-xl border-2 border-dotted border-border-strong bg-card/35 p-3 sm:p-4"
                          >
                            <h2 className="absolute inset-x-3 top-0 -translate-y-1/2 text-xs font-medium">
                              <span className="inline-flex max-w-full items-center gap-1.5 rounded-sm bg-background px-1.5">
                                <span className="min-w-0 truncate">{label}</span>
                                <span className="shrink-0 tabular-nums text-muted-foreground">
                                  {formatNumber(members.length)}
                                </span>
                              </span>
                            </h2>
                            <div className="flex min-w-0 flex-wrap gap-4">
                              {renderCards(members)}
                            </div>
                          </section>
                        );
                      })}
                    </div>
                  ) : (
                    <div className="grid min-w-0 gap-4 grid-cols-[repeat(auto-fit,minmax(min(100%,26rem),1fr))]">
                      {renderCards(visiblePrinters)}
                    </div>
                  )}
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
              printers={manageablePrinters}
              onPrintersChanged={() => {
                void printersQuery.refetch();
              }}
            />
          )}

          <Modal
            open={settingsTarget !== null}
            onClose={() => setSettingsTarget(null)}
            title={uiText("Edit printer connection")}
            description={settingsTarget?.name}
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
          {modelTarget && (
            <PrinterModelEditor
              key={modelTarget.id}
              printer={modelTarget}
              onClose={() => setModelTarget(null)}
            />
          )}
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

function isPrinterStatus(value: string | undefined): value is PrinterStatus {
  return value !== undefined && Object.hasOwn(STATUS_PRIORITY, value);
}

function liveStatus(printer: PrinterRead, row: FleetPrinterRow | undefined): PrinterStatus {
  return isPrinterStatus(row?.status) ? row.status : printer.status;
}

function routingHold(printer: PrinterRead, row: FleetPrinterRow | undefined) {
  if (row?.maintenance) return uiText("Maintenance");
  if (row?.pending_operator_release) return uiText("Operator release required");
  if (row?.drain_mode ?? printer.drain_mode) return uiText("Draining");
  return null;
}

function isReady(printer: PrinterRead, row: FleetPrinterRow | undefined) {
  return (
    liveStatus(printer, row) === "ready" &&
    row?.current_job_id == null &&
    routingHold(printer, row) === null
  );
}

function needsAttention(printer: PrinterRead, row: FleetPrinterRow | undefined) {
  const status = liveStatus(printer, row);
  return (
    status === "error" ||
    status === "offline" ||
    status === "unknown" ||
    routingHold(printer, row) !== null
  );
}

const FLEET_METRIC_COLORS = {
  ready: "text-success",
  active: "text-primary",
  queued: "text-foreground",
  attention: "text-warning",
};

function FleetMetric({
  label,
  tone,
  value,
  onClick,
  selected,
}: {
  label: string;
  tone: keyof typeof FLEET_METRIC_COLORS;
  value: number | undefined;
  onClick?: () => void;
  selected?: boolean;
}) {
  const contents = (
    <>
      <span
        className={`font-mono text-xl font-semibold leading-none tabular-nums ${value ? FLEET_METRIC_COLORS[tone] : "text-muted-foreground"}`}
      >
        {value === undefined ? "—" : formatNumber(value)}
      </span>
      <span className="text-xs text-muted-foreground">{label}</span>
    </>
  );
  const classes = "flex min-w-26 flex-col items-start gap-0.5 px-4 py-2.5 text-left";
  return onClick ? (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onClick}
      className={`${classes} ${selected ? "bg-accent text-accent-foreground" : "bg-card text-card-foreground"} hover:bg-muted hover:text-foreground focus-visible:z-10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring transition-transform duration-press active:scale-[0.98] motion-reduce:transition-none`}
    >
      {contents}
    </button>
  ) : (
    <div className={`${classes} bg-card text-card-foreground`}>{contents}</div>
  );
}

function FleetPrinterCard({
  printer,
  row,
  artworkPreference,
  grouped,
  onSettings,
  onRemove,
  onEditModel,
}: {
  printer: PrinterRead;
  row: FleetPrinterRow | undefined;
  artworkPreference: PrinterArtworkPreference;
  grouped: boolean;
  onSettings: (printer: PrinterRead) => void;
  onRemove: (printer: PrinterRead) => void;
  onEditModel: (printer: PrinterRead) => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [menuPoint, setMenuPoint] = useState<{ x: number; y: number } | null>(null);
  const menuOpener = useRef<HTMLAnchorElement | null>(null);
  const status = liveStatus(printer, row);
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
  const model = printer.model_name || printer.detected_model;
  const Heading = grouped ? "h3" : "h2";
  const artworkUrl = printerArtwork(artworkPreference === "model" ? model : null).imageUrl;
  const itemClass =
    "flex min-h-9 w-full items-center gap-2.5 rounded-md px-3 py-2 text-left text-sm hover:bg-muted focus-visible:bg-accent focus-visible:outline-none";
  const openMenu = (point: { x: number; y: number } | null, target: HTMLElement) => {
    menuOpener.current = target.querySelector<HTMLAnchorElement>("a[href]");
    setMenuPoint(point);
    setMenuOpen(true);
  };
  return (
    <DropdownMenu
      open={menuOpen}
      onOpenChange={setMenuOpen}
      portal
      align="start"
      anchorPoint={menuPoint}
      returnFocusRef={menuOpener}
      className={grouped ? "w-104 max-w-full shrink-0" : "h-full min-w-0"}
      aria-label={uiText("Printer actions")}
      contentClassName="w-60 border border-border bg-popover p-1 text-popover-foreground shadow-lg"
      trigger={
        <article
          onContextMenu={(event) => {
            event.preventDefault();
            openMenu(
              event.clientX || event.clientY ? { x: event.clientX, y: event.clientY } : null,
              event.currentTarget,
            );
          }}
          onKeyDown={(event) => {
            if (event.key === "ContextMenu" || (event.shiftKey && event.key === "F10")) {
              event.preventDefault();
              openMenu(null, event.currentTarget);
            }
          }}
          className="group @container/printer-card relative flex h-full min-w-0 flex-col overflow-hidden rounded-lg border border-border bg-card text-card-foreground hover:border-border-strong motion-safe:transition-transform duration-fast ease-out motion-safe:hover:-translate-y-0.5 motion-safe:active:scale-[0.99]"
        >
          <div className="flex flex-1 items-stretch">
            {artworkPreference !== "off" && (
              <PrinterCardArtwork key={artworkUrl} url={artworkUrl} model={model} />
            )}
            <div className="grid min-w-0 flex-1 grid-cols-1 content-start gap-3 px-4 py-4 text-xs">
              <div className="flex min-w-0 items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <Heading className="truncate text-lg font-semibold leading-tight tracking-tight">
                    <Link
                      href={`/printers/${printer.id}`}
                      title={printer.name}
                      className="after:absolute after:inset-0 after:z-10 focus-visible:outline-none focus-visible:after:ring-2 focus-visible:after:ring-ring"
                    >
                      {printer.name}
                      <ArrowUpRight
                        className="ml-1 inline size-4 align-text-top text-muted-foreground"
                        aria-hidden="true"
                      />
                    </Link>
                  </Heading>
                  <p className="mt-1 truncate text-xs text-muted-foreground">
                    {providerLabel(printer)}
                    {printer.capabilities.support_level === "beta" ? ` · ${uiText("Beta")}` : ""}
                    {!grouped && printer.group ? ` · ${printer.group}` : ""}
                  </p>
                </div>
                <div className="shrink-0">
                  <PrinterStatusBadge status={status} />
                </div>
              </div>
              <div className="@container/printer-details">
                <div className="flex flex-col gap-3 @min-[16rem]/printer-details:flex-row @min-[16rem]/printer-details:items-start">
                  <div className="min-w-0 flex-1 space-y-1.5">
                    <div className="flex min-h-5 items-center justify-between gap-2">
                      <span className="truncate text-muted-foreground">
                        {uiText("Current job")}
                      </span>
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
                          ? knownUiText(status)
                          : row
                            ? uiText("No active print")
                            : "—")}
                    </p>
                    <div className="h-1.5">
                      {progress !== null && <PrinterProgress value={progress} />}
                    </div>
                  </div>
                  {row?.telemetry && <FleetTelemetryReadings telemetry={row.telemetry} />}
                </div>
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
                  slot.color_hex && /^#[0-9a-f]{6}$/i.test(slot.color_hex)
                    ? slot.color_hex
                    : undefined;
                return (
                  <span
                    key={slot.slot_key}
                    title={color}
                    className="inline-flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-0.5 rounded-md border border-border px-1.5 py-0.5 text-2xs"
                  >
                    {color && (
                      <span
                        className="size-2.5 shrink-0 rounded-md border border-input"
                        style={{ backgroundColor: color }}
                      />
                    )}
                    {slot.material_type && (
                      <span className="font-medium">{slot.material_type}</span>
                    )}
                    <span className="min-w-0 break-words text-muted-foreground">{slot.label}</span>
                  </span>
                );
              })}
            </div>
          )}
        </article>
      }
    >
      <Link
        href={`/printers/${printer.id}`}
        role="menuitem"
        className={itemClass}
        onClick={() => setMenuOpen(false)}
      >
        <ArrowUpRight className="size-4 text-muted-foreground" aria-hidden />
        {uiText("Open page")}
      </Link>
      {printer.access.can_admin && (
        <>
          <button
            type="button"
            role="menuitem"
            className={itemClass}
            onClick={() => {
              setMenuOpen(false);
              onSettings(printer);
            }}
          >
            <Settings className="size-4 text-muted-foreground" aria-hidden />
            {uiText("Edit connection")}
          </button>
          <button
            type="button"
            role="menuitem"
            className={itemClass}
            onClick={() => {
              setMenuOpen(false);
              onEditModel(printer);
            }}
          >
            <Pencil className="size-4 text-muted-foreground" aria-hidden />
            {uiText("Set model")}
          </button>
          <div role="separator" className="my-1 border-t border-border" />
          <button
            type="button"
            role="menuitem"
            className={`${itemClass} text-destructive hover:bg-destructive/10 focus-visible:bg-destructive/10`}
            onClick={() => {
              setMenuOpen(false);
              onRemove(printer);
            }}
          >
            <Trash2 className="size-4" aria-hidden />
            {uiText("Remove printer")}
          </button>
        </>
      )}
    </DropdownMenu>
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
    <dl className="grid min-w-0 gap-y-1 border-s border-border ps-2 @min-[16rem]/printer-details:w-36 @min-[16rem]/printer-details:shrink-0">
      {readings.map(({ label, value, unit }) =>
        value == null ? null : (
          <div key={label} className="flex items-baseline justify-between gap-1.5">
            <dt className="min-w-0 truncate text-muted-foreground" title={label}>
              {unit === "%" && (
                <Fan
                  aria-hidden="true"
                  className="me-1 inline size-3 align-text-bottom transition-transform duration-fast ease-out motion-reduce:transition-none"
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
    <div className="grid min-h-36 w-20 shrink-0 place-items-center overflow-hidden border-e border-border bg-preview-image-matte px-1.5 py-2 dark:bg-muted @xs/printer-card:w-28 @lg/printer-card:w-36">
      <img
        src={failed ? printerArtwork(null).imageUrl : url}
        alt={model ? uiText("{value1} printer", { value1: model }) : uiText("Printer")}
        referrerPolicy="no-referrer"
        onError={() => setFailed(true)}
        className="h-36 w-full object-contain motion-safe:transition-transform duration-fast ease-out motion-safe:group-hover:-translate-y-1 motion-safe:group-active:scale-[0.98]"
      />
    </div>
  );
}

const OTHER_MODEL_OPTION = "__other__";

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

const MODEL_BRANDS = [
  "Bambu Lab",
  "Prusa",
  "Elegoo",
  "Creality",
  "Voron",
  "RatRig",
  "Sovol",
  "Anycubic",
  "Flashforge",
  "Qidi",
];

/** Group model names under their manufacturer, in the list's own order. */
function groupModelsByBrand(models: string[]): [string, string[]][] {
  const groups = new Map<string, string[]>();
  for (const model of models) {
    const brand =
      MODEL_BRANDS.find((name) => model.startsWith(`${name} `) || model === name) ??
      model.split(" ")[0]!;
    groups.set(brand, [...(groups.get(brand) ?? []), model]);
  }
  return [...groups];
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
  const groups = groupModelsByBrand(models);
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
                variant="outline"
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
            <div className="relative">
              <Search
                aria-hidden="true"
                className="pointer-events-none absolute start-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
              />
              <Input
                autoFocus
                type="search"
                aria-label={uiText("Search manufacturer / model")}
                placeholder={uiText("Search manufacturer / model")}
                disabled={saving}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                className="ps-9"
              />
            </div>
            <div className="mt-3 max-h-96 space-y-4 overflow-y-auto pe-1">
              {groups.length ? (
                groups.map(([brand, brandModels]) => (
                  <section key={brand} aria-label={brand}>
                    <h3 className="px-2 pb-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                      {brand}
                    </h3>
                    <ul className="space-y-0.5">
                      {brandModels.map((model) => (
                        <li key={model}>
                          <button
                            type="button"
                            disabled={saving || blocked}
                            aria-pressed={selected === model}
                            onClick={() => {
                              onSelectedChange(model);
                              onSave(model);
                            }}
                            className="flex min-h-10 w-full items-center justify-between gap-3 rounded-md px-3 py-2 text-left text-sm hover:bg-muted focus-visible:bg-accent focus-visible:outline-none disabled:pointer-events-none disabled:opacity-60 aria-pressed:bg-accent aria-pressed:text-accent-foreground aria-pressed:font-medium"
                          >
                            {model}
                            {selected === model && (
                              <Check aria-hidden="true" className="size-4 shrink-0 text-primary" />
                            )}
                          </button>
                        </li>
                      ))}
                    </ul>
                  </section>
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
  const addressInput = useRef<HTMLInputElement>(null);
  const addressProblem =
    url.trim() !== "" && !looksLikePrinterAddress(url)
      ? uiText("Enter an address like http://192.168.1.20:7125")
      : "";
  useEffect(() => {
    addressInput.current?.setCustomValidity(addressProblem);
  }, [addressProblem]);
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
  const validation = useFormValidation();

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
            <Button type="submit" form={formId} loading={submitting}>
              {uiText("Add printer")}
            </Button>
          </>
        }
      >
        <form
          id={formId}
          {...validation.formProps}
          onSubmit={(event) => validation.handle(event, submit)}
          className="space-y-5"
        >
          <FieldErrors errors={validation.errors}>
            <div className="grid gap-4 sm:grid-cols-2">
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
            </div>
            <div className="space-y-4 border-y border-border py-4">
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
                hint={uiText(
                  "Use the printer's IP address. Names ending in .local usually can't be resolved from the PrintStash container.",
                )}
                hintId="printer-address-hint"
              >
                <Input
                  id="printer-address"
                  ref={addressInput}
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  aria-describedby="printer-address-hint"
                  placeholder={
                    setupKind === "prusalink" || setupKind === "octoprint"
                      ? "http://192.168.1.50"
                      : setupKind === "moonraker" || setupKind === "elegoo_neptune4"
                        ? "http://192.168.1.50:7125"
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
                      <option value="digest">
                        {uiText("Username and password (recommended)")}
                      </option>
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
            </div>
            <div className="grid gap-4">
              <div className="flex flex-col gap-3 rounded-md border border-border bg-muted/30 p-3 sm:flex-row sm:items-center">
                <Printer3D className="size-5 shrink-0 text-muted-foreground" aria-hidden />
                <div className="min-w-0 flex-1">
                  <p className="text-field font-medium">
                    {uiText("Printer model")}{" "}
                    <span className="font-normal text-muted-foreground">
                      {uiText("(optional)")}
                    </span>
                  </p>
                  <p className="wrap-anywhere text-xs text-muted-foreground">
                    {modelName || uiText("Used to show your printer’s image.")}
                  </p>
                </div>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="w-full sm:w-auto"
                  onClick={() => setModelChoiceOpen(true)}
                >
                  {uiText("Choose printer model")}
                </Button>
              </div>
              <FormField htmlFor="printer-notes" label={uiText("Notes")} optional>
                <textarea
                  id="printer-notes"
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  className={inputClasses}
                  rows={2}
                />
              </FormField>
            </div>
            {err && (
              <p role="alert" className="text-sm text-destructive">
                {err}
              </p>
            )}
          </FieldErrors>
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
