"use client";

import { usePrinterSettingsCommand } from "@/features/printers/settings-edit";
import { PrinterSettingsReview } from "@/features/printers/settings-review";

import { currentLocale } from "@/lib/locale";
import { knownUiText, uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

import { lazy, Suspense, useId, useMemo, useRef, useState } from "react";
import { Link } from "@/lib/link";
import { PrinterRead, type PrinterCreate, type PrinterStatus } from "@/types";
import { usePrinterCatalogCommands } from "@/features/printers/queries";
import { usePrinterDashboard, usePrinters } from "@/lib/queries";
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
import { Button, buttonVariants } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { PageHeader } from "@/components/ui/page-header";
import { TabBar } from "@/components/ui/tabs";
import { Localized } from "@/components/ui/localized";
import { useOptionalI18n } from "@/lib/i18n";
import { translateUiText } from "@/lib/locale";
import { FleetMaintenancePanel, FleetQueuePanel } from "@/components/fleet-panels";
import { usePrinterCardImagePreference } from "@/lib/printer-card-display";
import { printerArtwork } from "@/lib/orca-printer-images";
import { Plus, RefreshCw, Pencil, Printer as PrinterIcon } from "lucide-react";

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
  useUiLocale();
  const { user } = useAuth();
  const locale = useOptionalI18n()?.locale ?? "en";
  // Shared printers cache: catalog commands invalidate
  // queryKeys.printers, so this list refetches itself after add/delete.
  const printersQuery = usePrinters({ refetchInterval: 15_000 });
  const dashboardQuery = usePrinterDashboard({ refetchInterval: 15_000 });
  const printers = useMemo(() => printersQuery.data ?? [], [printersQuery.data]);
  const dashboard = dashboardQuery.data;
  const loading = printersQuery.isLoading;
  const error = printersQuery.error instanceof Error ? printersQuery.error.message : null;
  const [addOpen, setAddOpen] = useState(false);
  const [addJobOpen, setAddJobOpen] = useState(false);
  const [settingsTarget, setSettingsTarget] = useState<PrinterRead | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<PrinterRead | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const showCardImages = usePrinterCardImagePreference();
  const [selectedGroup, setSelectedGroup] = useState<string | null>(null);
  const [activeView, setActiveView] = useState<"fleet" | "queue" | "maintenance">("fleet");
  const visiblePrinters = useMemo(
    () =>
      printers
        .filter(
          (printer) => selectedGroup === null || (printer.group || "__ungrouped") === selectedGroup,
        )
        .sort(
          (a, b) =>
            (STATUS_PRIORITY[a.status] ?? 99) - (STATUS_PRIORITY[b.status] ?? 99) ||
            a.name.localeCompare(b.name),
        ),
    [printers, selectedGroup],
  );
  const statusCounts = useMemo(
    () =>
      printers.reduce<Record<string, number>>((counts, printer) => {
        counts[printer.status] = (counts[printer.status] ?? 0) + 1;
        return counts;
      }, {}),
    [printers],
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
  const attentionCount = (statusCounts.error ?? 0) + (statusCounts.offline ?? 0);

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
        <div className="flex w-full flex-col gap-6">
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
                    void dashboardQuery.refetch();
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

          <TabBar
            tabs={[
              { key: "fleet", label: translateUiText(locale, "Fleet") },
              ...(user?.is_superuser || printers.some((printer) => printer.access.can_print)
                ? [{ key: "queue" as const, label: translateUiText(locale, "Queue") }]
                : []),
              ...(user?.is_superuser || printers.some((printer) => printer.access.can_admin)
                ? [{ key: "maintenance" as const, label: translateUiText(locale, "Maintenance") }]
                : []),
            ]}
            active={activeView}
            onChange={setActiveView}
            className="border-b border-border"
            tabClassName="px-3 py-2.5 text-sm text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            activeTabClassName="font-semibold text-foreground"
          />

          {activeView === "fleet" && (
            <>
              {error && (
                <div className="animate-panel-in rounded border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
                  {error}
                </div>
              )}

              {!loading && printers.length > 0 && (
                <section aria-label={uiText("Fleet summary")} className="space-y-5">
                  <div className="flex flex-wrap gap-x-8 gap-y-3 border-b border-border pb-4">
                    {[
                      {
                        get label() {
                          return uiText("Active jobs");
                        },
                        value: dashboard?.active_jobs ?? 0,
                      },
                      {
                        get label() {
                          return uiText("Printing");
                        },
                        value: statusCounts.printing ?? 0,
                      },
                      {
                        get label() {
                          return uiText("Needs attention");
                        },
                        value: attentionCount,
                      },
                      {
                        get label() {
                          return uiText("Ready");
                        },
                        value: statusCounts.ready ?? 0,
                      },
                    ].map((item) => (
                      <div key={item.label} className="flex min-w-0 items-baseline gap-2.5">
                        <p className="text-xs text-muted-foreground">{item.label}</p>
                        <p className="text-lg font-semibold tabular-nums text-foreground">
                          {item.value}
                        </p>
                      </div>
                    ))}
                  </div>
                  <label className="flex items-center gap-2 text-xs text-foreground">
                    {uiText("Group")}
                    <select
                      value={selectedGroup ?? ""}
                      onChange={(event) => setSelectedGroup(event.target.value || null)}
                      className="h-9 border border-input bg-card px-3 text-sm"
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
                <div className="stagger-children grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
                  {visiblePrinters.map((p) => (
                    <article
                      key={p.id}
                      className="animate-card-in flex min-h-72 flex-col overflow-hidden rounded-lg border border-border bg-card"
                    >
                      {showCardImages && (
                        <PrinterCardArtwork
                          key={`${p.id}:${p.model_name || p.detected_model || "unknown"}`}
                          printer={p}
                        />
                      )}
                      {!showCardImages && (
                        <div
                          aria-hidden
                          className="flex h-28 items-center justify-center border-b border-border bg-muted text-muted-foreground"
                        >
                          <svg
                            viewBox="0 0 180 160"
                            aria-hidden="true"
                            className="h-[88px] w-[105px]"
                          >
                            <path
                              d="M36 12H140L158 28V147H20V28Z"
                              className="fill-card"
                              stroke="currentColor"
                              strokeWidth="2"
                            />
                            <path
                              d="M36 12V132H140V12M20 28H158M20 147L36 132M158 147L140 132"
                              fill="none"
                              stroke="currentColor"
                              strokeWidth="2"
                            />
                            <path
                              d="M48 40H129V117H48Z"
                              className="fill-muted"
                              stroke="currentColor"
                            />
                            <path
                              d="M48 59H129M88 40V73M81 59H95V76H81Z"
                              className="fill-card"
                              stroke="currentColor"
                            />
                            <path
                              d="M52 106L87 96L126 106L92 119Z"
                              className="fill-accent"
                              stroke="currentColor"
                            />
                            <rect
                              x="119"
                              y="17"
                              width="15"
                              height="8"
                              rx="2"
                              className="fill-primary"
                            />
                          </svg>
                        </div>
                      )}
                      <div className="flex flex-1 flex-col p-[18px]">
                        <div className="mb-2 flex items-start justify-between gap-3">
                          <Link
                            href={`/printers/${p.id}`}
                            className="text-[15px] font-semibold leading-snug text-foreground hover:underline"
                          >
                            {p.name}
                          </Link>
                          <span className="shrink-0 text-xs capitalize text-muted-foreground">
                            {knownUiText(p.status)}
                          </span>
                        </div>
                        <p className="text-[13px] capitalize text-muted-foreground">
                          {providerLabel(p)}
                          {p.capabilities.support_level === "beta"
                            ? ` · ${uiText("Beta")}`
                            : ""} ·{" "}
                          {knownUiText(p.access.role)}
                        </p>
                        <div className="my-3 space-y-1 text-xs leading-relaxed text-muted-foreground">
                          <PrinterModelBadge printer={p} canEdit={p.access.can_admin} />
                          <p className="break-all">
                            {p.access.can_admin ? providerAddress(p) : uiText("Restricted")}
                          </p>
                          <p>
                            {p.last_seen_at
                              ? uiText("Seen {value1}", {
                                  value1: new Date(p.last_seen_at).toLocaleString(currentLocale()),
                                })
                              : uiText("Never connected")}
                            {p.group ? ` · ${p.group}` : ""}
                          </p>
                          {p.notes && <p>{p.notes}</p>}
                        </div>
                        {p.last_error && (
                          <p role="alert" className="mb-3 text-xs leading-relaxed text-destructive">
                            {uiText("Connection issue: ")}
                            {p.last_error}
                          </p>
                        )}
                        <div className="mt-auto flex items-center gap-2 pt-4">
                          <Link
                            href={`/printers/${p.id}`}
                            className={buttonVariants({ variant: "outline", size: "sm" })}
                          >
                            {uiText("Open printer")}
                          </Link>
                          {p.access.can_admin && (
                            <Button variant="ghost" size="sm" onClick={() => setSettingsTarget(p)}>
                              {uiText("Settings")}
                            </Button>
                          )}
                        </div>
                      </div>
                    </article>
                  ))}
                </div>
              )}
            </>
          )}

          {activeView === "queue" && (
            <FleetQueuePanel printers={printers.filter((printer) => printer.access.can_print)} />
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
          >
            {settingsTarget && (
              <Suspense fallback={<Skeleton className="h-52 w-full" />}>
                <PrinterConnectionForm
                  printer={settingsTarget}
                  canEdit={settingsTarget.access.can_admin}
                  compact
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

function PrinterCardArtwork({ printer }: { printer: PrinterRead }) {
  useUiLocale();
  const modelName = printer.model_name || printer.detected_model;
  const artwork = printerArtwork(modelName);
  const [imageUrl, setImageUrl] = useState(artwork.imageUrl);
  const usingFallback = imageUrl === "/images/printers/generic-fdm.png";

  return (
    <div className="relative flex h-28 items-center justify-center border-b border-border bg-muted px-6 py-3">
      <img
        src={imageUrl}
        alt={uiText("{value1} printer", { value1: String(modelName || printer.name) })}
        referrerPolicy="no-referrer"
        onError={() => setImageUrl("/images/printers/generic-fdm.png")}
        className="h-full w-full object-contain"
      />
      {!usingFallback && (
        <a
          href={artwork.sourceUrl}
          target="_blank"
          rel="noreferrer noopener"
          className="absolute bottom-2 right-2 rounded bg-background/90 px-1.5 py-0.5 text-3xs text-muted-foreground hover:text-foreground"
        >
          {uiText("Image: OrcaSlicer")}
        </a>
      )}
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
      className="flex max-w-full items-center gap-1 truncate rounded-md text-left text-xs font-medium text-foreground enabled:hover:text-primary disabled:cursor-disabled"
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
            <h3 className="text-sm font-semibold">{uiText("Hardware")}</h3>
            <label className="block space-y-1.5 text-[13px]">
              {uiText("Manufacturer")}
              <input
                className="h-10 w-full border border-input bg-card px-3 text-sm"
                value={manufacturer}
                onChange={(event) => setManufacturer(event.target.value)}
                disabled={saving}
              />
            </label>
            <label className="block space-y-1.5 text-[13px]">
              {uiText("Model")}
              <input
                autoFocus
                className="h-10 w-full border border-input bg-card px-3 text-sm"
                value={customValue}
                onChange={(event) => onCustomValueChange(event.target.value)}
                disabled={saving}
                required
              />
            </label>
            <p className="text-xs text-muted-foreground">
              {uiText("Enter the model printed on the machine.")}
            </p>
          </div>
        ) : (
          <>
            <label className="block space-y-1.5 text-[13px]">
              {uiText("Search manufacturer / model")}
              <input
                autoFocus
                type="search"
                disabled={saving}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                className="h-10 w-full border border-input bg-card px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
            </label>
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
        <form
          id={formId}
          onSubmit={submit}
          className="grid gap-5 sm:grid-cols-2 [&>div]:sm:col-span-2"
        >
          <h3 className="col-span-full text-sm font-semibold">{uiText("Printer")}</h3>
          <div className="sm:!col-span-1">
            <label
              htmlFor="printer-name"
              className="block text-[13px] font-medium text-foreground mb-1.5"
            >
              {uiText("Name")}
            </label>
            <input
              id="printer-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full bg-card text-foreground text-sm border border-input rounded px-3 py-[7px] focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent"
              required
            />
          </div>
          <div className="sm:!col-span-1">
            <label
              htmlFor="printer-integration"
              className="block text-[13px] font-medium text-foreground mb-1.5"
            >
              {uiText("Integration")}
            </label>
            <select
              id="printer-integration"
              value={setupKind}
              onChange={(e) => {
                const kind = parseSetupKind(e.target.value);
                if (kind === null) return;
                setSetupKind(kind);
                setUrl("");
              }}
              className="w-full bg-card text-foreground text-sm border border-input rounded px-3 py-[7px]"
            >
              {PRINTER_SETUP_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-3 text-[13px]">
            <span className="text-muted-foreground">
              {modelName || uiText("No printer model selected")}
            </span>
            <Button type="button" variant="outline" onClick={() => setModelChoiceOpen(true)}>
              {uiText("Choose printer model")}
            </Button>
          </div>
          <div className="space-y-2">
            <h3 className="text-sm font-semibold">{uiText("Connection")}</h3>
            <p className="text-[13px] text-muted-foreground">
              {PRINTER_SETUP_OPTIONS.find((option) => option.value === setupKind)?.description}
            </p>
          </div>
          <div>
            <label
              htmlFor="printer-address"
              className="block text-[13px] font-medium text-foreground mb-1.5"
            >
              {setupKind === "prusalink"
                ? uiText("PrusaLink URL")
                : setupKind === "octoprint"
                  ? uiText("OctoPrint URL")
                  : setupKind === "moonraker"
                    ? uiText("Moonraker URL")
                    : setupKind === "elegoo_neptune4"
                      ? uiText("Printer URL")
                      : uiText("Printer host or IP")}
            </label>
            <input
              id="printer-address"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              className="w-full bg-card text-foreground text-sm border border-input rounded px-3 py-[7px] focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent"
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
          </div>
          {(setupKind === "moonraker" || setupKind === "elegoo_neptune4") && (
            <div>
              <label
                htmlFor="moonraker-api-key"
                className="block text-[13px] font-medium text-foreground mb-1.5"
              >
                {setupKind === "elegoo_neptune4" ? uiText("API key") : uiText("Moonraker API key")}{" "}
                <span className="font-normal normal-case tracking-normal opacity-60">
                  {uiText("(optional)")}
                </span>
              </label>
              <input
                id="moonraker-api-key"
                type="password"
                value={moonrakerKey}
                onChange={(e) => setMoonrakerKey(e.target.value)}
                className="w-full bg-card text-foreground text-sm border border-input rounded px-3 py-[7px] focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent"
                placeholder={uiText("Leave blank if auth is disabled")}
              />
            </div>
          )}
          {setupKind === "bambu_lan" && (
            <>
              <div>
                <label
                  htmlFor="bambu-serial"
                  className="block text-[13px] font-medium text-foreground mb-1.5"
                >
                  {uiText("Printer serial")}
                </label>
                <input
                  id="bambu-serial"
                  value={bambuSerial}
                  onChange={(e) => setBambuSerial(e.target.value)}
                  required
                  className="w-full bg-card text-foreground text-sm border border-input rounded px-3 py-[7px]"
                />
              </div>
              <div>
                <label
                  htmlFor="bambu-access-code"
                  className="block text-[13px] font-medium text-foreground mb-1.5"
                >
                  {uiText("LAN access code")}
                </label>
                <input
                  id="bambu-access-code"
                  type="password"
                  value={bambuAccessCode}
                  onChange={(e) => setBambuAccessCode(e.target.value)}
                  required
                  className="w-full bg-card text-foreground text-sm border border-input rounded px-3 py-[7px]"
                />
              </div>
            </>
          )}
          {setupKind === "prusalink" && (
            <>
              <div>
                <label
                  htmlFor="prusalink-auth"
                  className="block text-[13px] font-medium text-foreground mb-1.5"
                >
                  {uiText("Authentication")}
                </label>
                <select
                  id="prusalink-auth"
                  value={prusaAuthMode}
                  onChange={(e) => {
                    setPrusaAuthMode(e.target.value === "api_key" ? "api_key" : "digest");
                    setPrusaSecret("");
                  }}
                  className="w-full bg-card text-foreground text-sm border border-input rounded px-3 py-[7px]"
                >
                  <option value="digest">{uiText("Username and password (recommended)")}</option>
                  <option value="api_key">{uiText("Legacy API key")}</option>
                </select>
              </div>
              {prusaAuthMode === "digest" && (
                <div>
                  <label
                    htmlFor="prusalink-username"
                    className="block text-[13px] font-medium text-foreground mb-1.5"
                  >
                    {uiText("Username")}
                  </label>
                  <input
                    id="prusalink-username"
                    value={prusaUsername}
                    onChange={(e) => setPrusaUsername(e.target.value)}
                    required
                    className="w-full bg-card text-foreground text-sm border border-input rounded px-3 py-[7px]"
                  />
                </div>
              )}
              <div>
                <label
                  htmlFor="prusalink-secret"
                  className="block text-[13px] font-medium text-foreground mb-1.5"
                >
                  {prusaAuthMode === "digest" ? uiText("Password") : uiText("API key")}
                </label>
                <input
                  id="prusalink-secret"
                  type="password"
                  value={prusaSecret}
                  onChange={(e) => setPrusaSecret(e.target.value)}
                  required
                  className="w-full bg-card text-foreground text-sm border border-input rounded px-3 py-[7px]"
                />
              </div>
            </>
          )}
          {setupKind === "octoprint" && (
            <div>
              <label
                htmlFor="octoprint-api-key"
                className="block text-[13px] font-medium text-foreground mb-1.5"
              >
                {uiText("API key")}
              </label>
              <input
                id="octoprint-api-key"
                type="password"
                value={octoprintApiKey}
                onChange={(e) => setOctoprintApiKey(e.target.value)}
                required
                className="w-full bg-card text-foreground text-sm border border-input rounded px-3 py-[7px]"
              />
            </div>
          )}
          {setupKind === "elegoo_centauri_carbon" && (
            <div>
              <label
                htmlFor="centauri-mainboard-id"
                className="block text-[13px] font-medium text-foreground mb-1.5"
              >
                {uiText("Mainboard ID")}{" "}
                <span className="font-normal normal-case tracking-normal opacity-60">
                  {uiText("(recommended)")}
                </span>
              </label>
              <input
                id="centauri-mainboard-id"
                value={centauriMainboardId}
                onChange={(e) => setCentauriMainboardId(e.target.value)}
                className="w-full bg-card text-foreground text-sm border border-input rounded px-3 py-[7px]"
                placeholder={uiText("From printer discovery or diagnostics")}
              />
              <p className="mt-1.5 text-xs text-muted-foreground">
                {uiText("Needed for reliable printer commands while idle, paused, or errored.")}
              </p>
            </div>
          )}
          {setupKind === "elegoo_centauri_carbon_2" && (
            <>
              <div className="rounded border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-muted-foreground">
                {uiText("Enable LAN Only in printer network settings before connecting.")}
              </div>
              <div>
                <label
                  htmlFor="centauri-access-code"
                  className="block text-[13px] font-medium text-foreground mb-1.5"
                >
                  {uiText("Printer access code")}
                </label>
                <input
                  id="centauri-access-code"
                  type="password"
                  value={centauriAccessCode}
                  onChange={(e) => setCentauriAccessCode(e.target.value)}
                  required
                  className="w-full bg-card text-foreground text-sm border border-input rounded px-3 py-[7px]"
                />
              </div>
            </>
          )}
          <div>
            <label
              htmlFor="printer-notes"
              className="block text-[13px] font-medium text-foreground mb-1.5"
            >
              {uiText("Notes")}
            </label>
            <textarea
              rows={3}
              id="printer-notes"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              className="w-full bg-card text-foreground text-sm border border-input rounded px-3 py-[7px] focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent"
              placeholder={uiText("Optional")}
            />
          </div>
          {err && (
            <div className="rounded border border-red-300/40 bg-red-50/30 p-2 text-xs text-red-600">
              {err}
            </div>
          )}
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
