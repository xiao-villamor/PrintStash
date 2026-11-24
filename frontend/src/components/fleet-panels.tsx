import { useQueryClient } from "@tanstack/react-query";
import { refreshFleet } from "@/features/printers/queries";
import { knownUiText } from "@/lib/locale";
import { currentLocale } from "@/lib/locale";
import { uiText } from "@/lib/locale";
import { dispatchName, parseApiInstant } from "@/lib/format";
import { useUiLocale } from "@/lib/i18n";
import { useMemo, useState } from "react";
import { createPortal } from "react-dom";
import {
  FileBox,
  Layers3,
  ListOrdered,
  Plus,
  GitFork,
  Network,
  Table2,
  ArrowDown,
  ArrowUp,
  CalendarClock,
  Pencil,
  RotateCcw,
  Trash2,
  Wrench,
} from "lucide-react";
import { Printer3D } from "@/components/ui/printer-3d";

import {
  deleteFleetJob,
  decideFleetOperatorGate,
  retryFleetJob,
  resolveFleetJob,
  updateFleetJob,
} from "@/lib/api";
import { useFleetQueue, useFleetSummary } from "@/lib/queries";
import { userMessage } from "@/lib/errors";
import { getSessionVersion } from "@/lib/session-transport";
import {
  usePrinterMaintenance,
  useMaintenanceMutation,
  maintenanceApi,
  type MaintenanceApi,
} from "@/features/printers/queries";
import { toast } from "@/lib/toast";
import type {
  FleetSummary,
  CompatibilityPolicy,
  JobPriority,
  PrinterRead,
  PrintJobRead,
  QueueJobUpdate,
  RoutingStrategy,
} from "@/types";
import { Button } from "@/components/ui/button";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { EmptyState } from "@/components/ui/empty-state";
import { Input, inputClasses } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { Skeleton } from "@/components/ui/skeleton";
import { Localized } from "@/components/ui/localized";
import { FleetRoutingCanvas } from "./fleet-routing-canvas";
import "./fleet-panels.css";
import "./fleet-queue-workspace.css";

const ACTIVE = new Set(["uploading", "started", "printing", "paused"]);

interface QueueEditDraft {
  strategy: RoutingStrategy;
  printerId: string;
  priority: JobPriority;
  targetGroup: string;
  compatibilityPolicy: CompatibilityPolicy;
  queuePosition: string;
}

function editDraft(job: PrintJobRead): QueueEditDraft {
  return {
    strategy: job.routing_strategy,
    printerId: job.printer_id == null ? "" : String(job.printer_id),
    priority: job.priority ?? "normal",
    targetGroup: job.target_group ?? "",
    compatibilityPolicy: job.compatibility_policy ?? "safe",
    queuePosition: String(job.queue_position),
  };
}

function parseRoutingStrategy(value: string): RoutingStrategy | null {
  return value === "manual" || value === "default" || value === "least_busy" ? value : null;
}

function parseJobPriority(value: string): JobPriority | null {
  return value === "low" || value === "normal" || value === "rush" ? value : null;
}

function parseCompatibilityPolicy(value: string): CompatibilityPolicy | null {
  return value === "safe" || value === "allow_mismatch" ? value : null;
}

/**
 * The fleet mutations `FleetQueuePanel` reaches for outside itself. Application
 * code renders the panel without a `deps` prop and gets
 * `REAL_FLEET_QUEUE_DEPS`; a test overrides the entries it wants to observe.
 *
 * The panel's reads are deliberately *not* in here: it calls `useFleetQueue`
 * and `useFleetSummary` directly, so both are statically known Hooks. A test
 * drives them by seeding the `QueryClient` cache it renders the panel under.
 */
export interface FleetQueueDeps {
  deleteJob: typeof deleteFleetJob;
  updateJob: typeof updateFleetJob;
  retryJob: typeof retryFleetJob;
  resolveJob: typeof resolveFleetJob;
  decideOperatorGate: typeof decideFleetOperatorGate;
}

const REAL_FLEET_QUEUE_DEPS: FleetQueueDeps = {
  deleteJob: deleteFleetJob,
  updateJob: updateFleetJob,
  retryJob: retryFleetJob,
  resolveJob: resolveFleetJob,
  decideOperatorGate: decideFleetOperatorGate,
};

export type FleetQueueView = "queued" | "active" | "recent";

function FarmDispatchBoard({
  rows,
  queued,
  selected,
  onSelect,
}: {
  rows: FleetSummary["printers"];
  queued: PrintJobRead[];
  selected: string;
  onSelect: (id: string, view?: FleetQueueView) => void;
}) {
  if (!rows.length) return null;
  const printing = rows.filter((row) => row.status === "printing").length;
  const laneCounts = new Map<number, number>();
  const queuedById = new Map(queued.map((job) => [job.id, job]));
  for (const job of queued) {
    if (job.printer_id != null)
      laneCounts.set(job.printer_id, (laneCounts.get(job.printer_id) ?? 0) + 1);
  }
  return (
    <div className="farm-dispatch-board">
      <table aria-label={uiText("Farm dispatch")}>
        <caption>
          <div className="farm-board-title">
            <span>{uiText("Farm dispatch")}</span>
            <div className="farm-capacity">
              <span>
                {uiText("{running} of {total} printing", { running: printing, total: rows.length })}
              </span>
              <div className="farm-capacity-track" aria-hidden="true">
                {rows.map((row) => (
                  <span key={row.printer_id} data-state={row.status} />
                ))}
              </div>
            </div>
          </div>
        </caption>
        <thead>
          <tr>
            <th scope="col">{uiText("Printer")}</th>
            <th scope="col">{uiText("Current job")}</th>
            <th scope="col">{uiText("Next job")}</th>
            <th scope="col">{uiText("Routing")}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const id = String(row.printer_id);
            const hold = row.maintenance
              ? uiText("Maintenance")
              : row.pending_operator_release
                ? uiText("Operator release required")
                : row.drain_mode
                  ? uiText("Draining")
                  : null;
            const next = row.next_job_id != null ? queuedById.get(row.next_job_id) : null;
            const depth = laneCounts.get(row.printer_id) ?? 0;
            const running = row.status === "printing" || row.status === "paused";
            const review = !running && row.current_job_id != null;
            const percent =
              running && row.current_job_name && row.progress != null
                ? Math.round(row.progress * 100)
                : null;
            return (
              <tr key={id} data-selected={selected === id}>
                <th scope="row">
                  <Button
                    variant="ghost"
                    size="sm"
                    aria-pressed={selected === id}
                    onClick={() => onSelect(selected === id ? "all" : id)}
                  >
                    {row.name}
                  </Button>
                  <small>{row.group}</small>
                </th>
                <td>
                  <span
                    className={row.current_job_name ? "farm-job-name" : "text-muted-foreground"}
                  >
                    {row.current_job_name ? (
                      <span title={row.current_job_name}>{dispatchName(row.current_job_name)}</span>
                    ) : (
                      uiText("No active print")
                    )}
                  </span>
                  {review && (
                    <Button variant="link" size="xs" onClick={() => onSelect(id, "active")}>
                      {uiText("Review active job")}
                    </Button>
                  )}
                  {running && (percent != null || row.remaining_minutes != null) && (
                    <div className="farm-job-progress">
                      {percent != null && (
                        <>
                          <progress aria-label={uiText("Progress")} value={percent} max={100} />
                          <span>{percent}%</span>
                        </>
                      )}
                      {row.remaining_minutes != null && (
                        <small>
                          {uiText("{value1} min remaining", { value1: row.remaining_minutes })}
                        </small>
                      )}
                    </div>
                  )}
                </td>
                <td>
                  <span className={row.next_job_name ? "farm-job-name" : "text-muted-foreground"}>
                    {row.next_job_name ? (
                      <span title={row.next_job_name}>{dispatchName(row.next_job_name)}</span>
                    ) : (
                      uiText("No queued job")
                    )}
                  </span>
                  {row.next_job_name && (
                    <small>
                      {[
                        row.next_priority && knownUiText(row.next_priority),
                        depth > 1 && uiText("{value1} queued", { value1: depth }),
                        next?.blocked_reason && knownUiText(next.blocked_reason),
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    </small>
                  )}
                </td>
                <td>
                  <strong className="farm-routing-state" data-state={hold ? "held" : row.status}>
                    {hold ?? knownUiText(row.status)}
                  </strong>
                  {hold && <small>{knownUiText(row.status)}</small>}
                  {row.pending_operator_release && (
                    <Button variant="link" size="xs" onClick={() => onSelect(id, "recent")}>
                      {uiText("Review release")}
                    </Button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function FleetQueuePanel({
  printers,
  initialView = "queued",
  onAdd,
  deps,
}: {
  printers: PrinterRead[];
  initialView?: FleetQueueView;
  onAdd?: () => void;
  deps?: Partial<FleetQueueDeps>;
}) {
  useUiLocale();
  const { deleteJob, updateJob, retryJob, resolveJob, decideOperatorGate } = {
    ...REAL_FLEET_QUEUE_DEPS,
    ...deps,
  };
  const [mapView, setMapView] = useState(true);
  const [queueView, setQueueView] = useState<FleetQueueView>(initialView);
  const [printerFilter, setPrinterFilter] = useState("all");
  const [groupFilter, setGroupFilter] = useState("all");
  const [historyLimit, setHistoryLimit] = useState(20);
  const commandClient = useQueryClient();
  const queueQuery = useFleetQueue({ refetchInterval: 5_000, historyLimit });
  const summaryQuery = useFleetSummary({ refetchInterval: 5_000 });
  const [deleteTarget, setDeleteTarget] = useState<PrintJobRead | null>(null);
  const [editTarget, setEditTarget] = useState<PrintJobRead | null>(null);
  const [resolveTarget, setResolveTarget] = useState<PrintJobRead | null>(null);
  const [draft, setDraft] = useState<QueueEditDraft | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const jobs = queueQuery.data ?? [];
  const printerNames = useMemo(
    () => new Map(printers.map((printer) => [printer.id, printer.name])),
    [printers],
  );
  const controllablePrinterIds = useMemo(
    () => new Set(printers.filter((printer) => printer.access.can_control).map(({ id }) => id)),
    [printers],
  );
  const groups = [
    ...new Set(printers.flatMap((printer) => (printer.group ? [printer.group] : []))),
  ].sort();
  function matchesFilters(job: PrintJobRead) {
    const group =
      job.target_group ?? printers.find((printer) => printer.id === job.printer_id)?.group;
    return (
      (printerFilter === "all" ||
        (job.printer_id == null ? "unassigned" : String(job.printer_id)) === printerFilter) &&
      (groupFilter === "all" || groupFilter === group)
    );
  }
  const queued = jobs.filter((job) => job.state === "queued");
  const active = jobs.filter((job) => ACTIVE.has(job.state));
  const farmRows = (summaryQuery.data?.printers ?? []).filter(
    (row) =>
      printerNames.has(row.printer_id) && (groupFilter === "all" || row.group === groupFilter),
  );
  const recent = jobs.filter((job) => !ACTIVE.has(job.state) && job.state !== "queued");

  const filtered = printerFilter !== "all" || groupFilter !== "all";
  const emptyState = filtered
    ? uiText("No jobs match these filters.")
    : queueView === "queued"
      ? uiText("The queue is empty")
      : queueView === "active"
        ? uiText("No active prints")
        : uiText("No recent jobs");
  const emptyAction = filtered ? (
    <Button
      variant="outline"
      size="sm"
      onClick={() => {
        setPrinterFilter("all");
        setGroupFilter("all");
      }}
    >
      {uiText("Clear filters")}
    </Button>
  ) : queueView === "queued" && active.length ? (
    <Button variant="outline" size="sm" onClick={() => setQueueView("active")}>
      {uiText("View active jobs")}
    </Button>
  ) : queueView === "queued" && recent.length ? (
    <Button variant="outline" size="sm" onClick={() => setQueueView("recent")}>
      {uiText("View recent jobs")}
    </Button>
  ) : queueView === "queued" && onAdd ? (
    <Button variant="outline" size="sm" onClick={onAdd}>
      <Plus size={15} />
      {uiText("Add to queue")}
    </Button>
  ) : null;
  async function mutate<T>(jobId: number, action: () => Promise<T>): Promise<boolean> {
    const session = getSessionVersion();
    setBusy(jobId);
    try {
      await action();
      if (session !== getSessionVersion()) return false;
      refreshFleet(commandClient, session);
      await Promise.all([queueQuery.refetch(), summaryQuery.refetch()]);
      return true;
    } catch (error) {
      toast.error(error);
      return false;
    } finally {
      setBusy(null);
    }
  }

  function beginEdit(job: PrintJobRead, destination?: PrinterRead) {
    setEditTarget(job);
    const initial = editDraft(job);
    setDraft(
      destination
        ? {
            ...initial,
            strategy: "manual",
            printerId: String(destination.id),
            targetGroup: initial.targetGroup === destination.group ? initial.targetGroup : "",
          }
        : initial,
    );
  }

  async function saveEdit() {
    if (!editTarget || !draft) return;
    const payload: QueueJobUpdate = { expected_updated_at: editTarget.updated_at };
    const targetGroup = draft.targetGroup.trim() || null;
    const queuePosition = Math.max(1, Number(draft.queuePosition) || 1);
    const routingChanged =
      draft.strategy !== editTarget.routing_strategy ||
      (draft.strategy === "manual" && Number(draft.printerId) !== editTarget.printer_id) ||
      targetGroup !== (editTarget.target_group ?? null) ||
      draft.compatibilityPolicy !== (editTarget.compatibility_policy ?? "safe");
    if (routingChanged) {
      payload.strategy = draft.strategy;
      payload.printer_id = draft.strategy === "manual" ? Number(draft.printerId) : null;
    }
    if (draft.priority !== (editTarget.priority ?? "normal")) payload.priority = draft.priority;
    if (targetGroup !== (editTarget.target_group ?? null)) payload.target_group = targetGroup;
    if (draft.compatibilityPolicy !== (editTarget.compatibility_policy ?? "safe")) {
      payload.compatibility_policy = draft.compatibilityPolicy;
    }
    if (queuePosition !== editTarget.queue_position) payload.queue_position = queuePosition;
    const saved = await mutate(editTarget.id, () => updateJob(editTarget.id, payload));
    if (saved) {
      setEditTarget(null);
      setDraft(null);
      toast.success(uiText("Queue job updated"));
    }
  }

  async function resolveAs(resolution: "cancelled" | "failed") {
    if (!resolveTarget) return;
    const resolved = await mutate(resolveTarget.id, () => resolveJob(resolveTarget.id, resolution));
    if (resolved) setResolveTarget(null);
  }

  if (queueQuery.isLoading) {
    return (
      <Localized>
        <div className="space-y-3">
          {Array.from({ length: 3 }).map((_, index) => (
            <Skeleton key={index} className="h-20 w-full" />
          ))}
        </div>
      </Localized>
    );
  }

  return (
    <Localized>
      <div className="fleet-panels space-y-4">
        <ConfirmModal
          open={deleteTarget !== null}
          onClose={() => setDeleteTarget(null)}
          onConfirm={() => {
            if (!deleteTarget) return;
            const id = deleteTarget.id;
            setDeleteTarget(null);
            void mutate(id, () => deleteJob(id));
          }}
          title={uiText("Delete queued job?")}
          description={uiText(
            "This permanently removes the pending job from the queue. It does not cancel an active printer.",
          )}
          confirmLabel={uiText("Delete job")}
        />
        <Modal
          open={resolveTarget !== null}
          variant="confirmation"
          dismissible={resolveTarget === null || busy !== resolveTarget.id}
          onClose={() => setResolveTarget(null)}
          title={uiText("Resolve stale job")}
          description={
            resolveTarget &&
            uiText(
              "Use this only when the printer is no longer running {value1}. This updates PrintStash history; it does not send a command to the printer.",
              { value1: String(resolveTarget.remote_filename) },
            )
          }
          footer={
            resolveTarget && (
              <>
                <Button
                  variant="outline"
                  className="border-input"
                  disabled={busy === resolveTarget.id}
                  data-autofocus={busy === resolveTarget.id ? undefined : ""}
                  onClick={() => setResolveTarget(null)}
                >
                  {uiText("Cancel")}
                </Button>
                <Button
                  variant="outline"
                  disabled={busy === resolveTarget.id}
                  onClick={() => void resolveAs("cancelled")}
                >
                  {uiText("Mark cancelled")}
                </Button>
                <Button
                  disabled={busy === resolveTarget.id}
                  onClick={() => void resolveAs("failed")}
                >
                  {uiText("Mark failed")}
                </Button>
              </>
            )
          }
        />
        <Modal
          open={editTarget !== null && draft !== null}
          onClose={() => {
            if (busy !== editTarget?.id) {
              setEditTarget(null);
              setDraft(null);
            }
          }}
          title={uiText("Edit queue job")}
          description={uiText(
            "Adjust the queue without changing the Model’s recommended Revision.",
          )}
          footer={
            editTarget && draft ? (
              <>
                <Button
                  variant="outline"
                  onClick={() => {
                    setEditTarget(null);
                    setDraft(null);
                  }}
                  disabled={busy === editTarget.id}
                >
                  {uiText("Cancel")}
                </Button>
                <Button
                  onClick={() => void saveEdit()}
                  loading={busy === editTarget.id}
                  disabled={
                    (draft.strategy === "manual" && !draft.printerId) ||
                    !Number.isInteger(Number(draft.queuePosition)) ||
                    Number(draft.queuePosition) < 1
                  }
                >
                  {uiText("Save job")}
                </Button>
              </>
            ) : undefined
          }
        >
          {editTarget && draft && (
            <div className="space-y-5">
              <dl className="grid grid-cols-[110px_minmax(0,1fr)] gap-4 bg-plate p-4 text-[13px]">
                <dt className="text-muted-foreground">{uiText("Revision")}</dt>
                <dd className="break-all font-medium">{editTarget.remote_filename}</dd>
              </dl>
              <fieldset className="space-y-4 border-t border-border pt-4">
                <legend className="pr-3 text-sm font-semibold">{uiText("Routing")}</legend>
                <label className="block space-y-2 text-[13px] font-medium">
                  {uiText("Routing")}
                  <select
                    className={inputClasses}
                    value={draft.strategy}
                    onChange={(event) => {
                      const strategy = parseRoutingStrategy(event.target.value);
                      if (strategy) setDraft({ ...draft, strategy });
                    }}
                  >
                    <option value="manual">{uiText("Choose printer")}</option>
                    <option value="default">{uiText("Default printer")}</option>
                    <option value="least_busy">{uiText("Least busy")}</option>
                  </select>
                </label>
                {draft.strategy === "manual" && (
                  <label className="block space-y-2 text-[13px] font-medium">
                    {uiText("Printer")}
                    <select
                      className={inputClasses}
                      aria-label={uiText("Printer")}
                      value={draft.printerId}
                      onChange={(event) => setDraft({ ...draft, printerId: event.target.value })}
                    >
                      <option value="">{uiText("Choose printer")}</option>
                      {printers.map((printer) => (
                        <option key={printer.id} value={printer.id}>
                          {printer.name}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                <label className="block space-y-2 text-[13px] font-medium">
                  {uiText("Target group")}
                  <Input
                    value={draft.targetGroup}
                    disabled={draft.strategy === "manual"}
                    onChange={(event) => setDraft({ ...draft, targetGroup: event.target.value })}
                    placeholder={uiText("Any group")}
                  />
                </label>
              </fieldset>
              <fieldset className="space-y-4 border-t border-border pt-4">
                <legend className="pr-3 text-sm font-semibold">{uiText("Queue settings")}</legend>
                <div className="grid gap-4 sm:grid-cols-2">
                  <label className="block space-y-2 text-[13px] font-medium">
                    {uiText("Priority")}
                    <select
                      className={inputClasses}
                      value={draft.priority}
                      onChange={(event) => {
                        const priority = parseJobPriority(event.target.value);
                        if (priority) setDraft({ ...draft, priority });
                      }}
                    >
                      <option value="low">{uiText("Low")}</option>
                      <option value="normal">{uiText("Normal")}</option>
                      <option value="rush">{uiText("Rush")}</option>
                    </select>
                  </label>
                  <label className="block space-y-2 text-[13px] font-medium">
                    {uiText("Queue position")}
                    <Input
                      type="number"
                      min={1}
                      step={1}
                      value={draft.queuePosition}
                      onChange={(event) =>
                        setDraft({ ...draft, queuePosition: event.target.value })
                      }
                    />
                  </label>
                </div>
                <label className="block space-y-2 text-[13px] font-medium">
                  {uiText("Material compatibility")}
                  <select
                    className={inputClasses}
                    value={draft.compatibilityPolicy}
                    onChange={(event) => {
                      const compatibilityPolicy = parseCompatibilityPolicy(event.target.value);
                      if (compatibilityPolicy) setDraft({ ...draft, compatibilityPolicy });
                    }}
                  >
                    <option value="safe">{uiText("Require compatible material")}</option>
                    <option value="allow_mismatch">{uiText("Allow mismatch")}</option>
                  </select>
                </label>
              </fieldset>
            </div>
          )}
        </Modal>
        {summaryQuery.isError && (
          <div role="alert">
            {userMessage(summaryQuery.error)}{" "}
            <Button size="sm" variant="outline" onClick={() => void summaryQuery.refetch()}>
              {uiText("Retry")}
            </Button>
          </div>
        )}
        {!!summaryQuery.data?.printers.length && (
          <section className="routing-map-shell" aria-label={uiText("Farm routing")}>
            <header className="routing-map-title">
              <h2 className="routing-map-heading">
                <GitFork size={20} />
                {uiText("Farm routing")}
              </h2>
              <div className="routing-map-actions">
                <Button
                  size="sm"
                  variant="ghost"
                  aria-pressed={mapView}
                  onClick={() => setMapView(true)}
                >
                  <Network />
                  {uiText("Map")}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  aria-pressed={!mapView}
                  onClick={() => setMapView(false)}
                >
                  <Table2 />
                  {uiText("Table")}
                </Button>
              </div>
            </header>
            {mapView ? (
              <FleetRoutingCanvas
                rows={farmRows}
                printers={printers}
                jobs={queued.filter(matchesFilters)}
                onEdit={beginEdit}
                onAdd={onAdd}
                onJobs={(id, running) => {
                  setPrinterFilter(id);
                  setQueueView(running ? "active" : "queued");
                }}
              />
            ) : (
              <FarmDispatchBoard
                queued={queued}
                rows={farmRows}
                selected={printerFilter}
                onSelect={(id, view) => {
                  setPrinterFilter(id);
                  if (view) setQueueView(view);
                }}
              />
            )}
          </section>
        )}
        <div className="queue-register">
          <div className="fleet-filter-row queue-register-toolbar">
            <h2>
              <ListOrdered size={19} />
              {uiText("Print queue")}
            </h2>
            <div className="farm-queue-scopes" role="group" aria-label={uiText("View")}>
              {(["queued", "active", "recent"] as const).map((view) => {
                const label =
                  view === "queued"
                    ? uiText("Queued")
                    : view === "active"
                      ? uiText("Active")
                      : uiText("Recent");
                return (
                  <Button
                    key={view}
                    size="sm"
                    variant={queueView === view ? "secondary" : "ghost"}
                    aria-label={label}
                    aria-pressed={queueView === view}
                    onClick={() => setQueueView(view)}
                  >
                    {label}
                    {view !== "recent" && (
                      <span>
                        {(view === "queued" ? queued : active).filter(matchesFilters).length}
                      </span>
                    )}
                  </Button>
                );
              })}
            </div>
            <label>
              {uiText("Printer")}
              <select
                className={inputClasses}
                aria-label={uiText("Printer")}
                value={printerFilter}
                onChange={(event) => setPrinterFilter(event.target.value)}
              >
                <option value="all">{uiText("All printers")}</option>
                <option value="unassigned">{uiText("Unassigned")}</option>
                {printers.map((printer) => (
                  <option key={printer.id} value={printer.id}>
                    {printer.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              {uiText("Group")}
              <select
                className={inputClasses}
                aria-label={uiText("Group")}
                value={groupFilter}
                onChange={(event) => {
                  setGroupFilter(event.target.value);
                  setPrinterFilter("all");
                }}
              >
                <option value="all">{uiText("All groups")}</option>
                {groups.map((group) => (
                  <option key={group} value={group}>
                    {group}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {queueQuery.isError && (
            <div role="alert" className="space-y-2 text-sm text-destructive">
              <p>{userMessage(queueQuery.error)}</p>
              <Button variant="outline" size="sm" onClick={() => void queueQuery.refetch()}>
                {uiText("Retry")}
              </Button>
            </div>
          )}
          {queueView === "queued" && (
            <QueueSection
              emptyState={emptyState}
              emptyAction={emptyAction}
              title={uiText("Queued")}
              jobs={queued.filter(matchesFilters)}
              printerNames={printerNames}
              busy={busy}
              actions={(job) => (
                <>
                  {(() => {
                    const lane = queued.filter(
                      (row) => (row.priority ?? "normal") === (job.priority ?? "normal"),
                    );
                    const laneIndex = lane.findIndex((row) => row.id === job.id);
                    return (
                      <>
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          aria-label={uiText("Move {value1} up", {
                            value1: String(job.remote_filename),
                          })}
                          disabled={busy === job.id || laneIndex === 0}
                          onClick={() =>
                            void mutate(job.id, () =>
                              updateJob(job.id, {
                                queue_position: laneIndex,
                                expected_updated_at: job.updated_at,
                              }),
                            )
                          }
                        >
                          <ArrowUp className="h-3.5 w-3.5" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          aria-label={uiText("Move {value1} down", {
                            value1: String(job.remote_filename),
                          })}
                          disabled={busy === job.id || laneIndex === lane.length - 1}
                          onClick={() =>
                            void mutate(job.id, () =>
                              updateJob(job.id, {
                                queue_position: laneIndex + 2,
                                expected_updated_at: job.updated_at,
                              }),
                            )
                          }
                        >
                          <ArrowDown className="h-3.5 w-3.5" />
                        </Button>
                      </>
                    );
                  })()}
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label={uiText("Edit {value1}", { value1: String(job.remote_filename) })}
                    disabled={busy === job.id}
                    onClick={() => beginEdit(job)}
                  >
                    <Pencil className="h-3.5 w-3.5" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label={uiText("Delete {value1}", { value1: String(job.remote_filename) })}
                    disabled={busy === job.id}
                    onClick={() => setDeleteTarget(job)}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </>
              )}
            />
          )}
          {queueView === "active" && (
            <QueueSection
              emptyState={emptyState}
              emptyAction={emptyAction}
              title={uiText("Active")}
              jobs={active.filter(matchesFilters)}
              printerNames={printerNames}
              busy={busy}
              actions={(job) =>
                job.printer_id != null && controllablePrinterIds.has(job.printer_id) ? (
                  <Button
                    variant="outline"
                    size="xs"
                    disabled={busy === job.id}
                    onClick={() => setResolveTarget(job)}
                  >
                    {uiText("Resolve")}
                  </Button>
                ) : null
              }
            />
          )}
          {queueView === "recent" && (
            <QueueSection
              emptyState={emptyState}
              emptyAction={emptyAction}
              title={uiText("Recent")}
              jobs={recent.filter(matchesFilters)}
              printerNames={printerNames}
              busy={busy}
              actions={(job) => (
                <>
                  {job.operator_gate_state === "pending" && (
                    <>
                      <Button
                        variant="outline"
                        size="xs"
                        disabled={busy === job.id}
                        onClick={() =>
                          void mutate(job.id, () => decideOperatorGate(job.id, "release"))
                        }
                      >
                        {uiText("Release")}
                      </Button>
                      <Button
                        variant="outline"
                        size="xs"
                        disabled={busy === job.id}
                        onClick={() =>
                          void mutate(job.id, () => decideOperatorGate(job.id, "hold"))
                        }
                      >
                        {uiText("Hold")}
                      </Button>
                    </>
                  )}
                  {job.retryable && (
                    <Button
                      variant="outline"
                      size="xs"
                      disabled={busy === job.id}
                      onClick={() => void mutate(job.id, () => retryJob(job.id))}
                    >
                      <RotateCcw className="h-3.5 w-3.5" />
                      {uiText("Retry")}
                    </Button>
                  )}
                </>
              )}
            />
          )}
          {queueView === "recent" && recent.length >= historyLimit && historyLimit < 100 && (
            <div className="flex justify-center">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setHistoryLimit((value) => Math.min(value + 20, 100))}
              >
                {uiText("Load older jobs")}
              </Button>
            </div>
          )}
        </div>
      </div>
    </Localized>
  );
}

function QueueSection({
  title,
  emptyState,
  emptyAction,
  jobs,
  printerNames,
  busy,
  actions,
}: {
  title: string;
  emptyState: string;
  emptyAction: React.ReactNode;
  jobs: PrintJobRead[];
  printerNames: Map<number, string>;
  busy: number | null;
  actions?: (job: PrintJobRead, index: number) => React.ReactNode;
}) {
  useUiLocale();
  const groups = Array.from(
    jobs.reduce((result, job, index) => {
      const key = job.batch_id == null ? `job-${job.id}` : `batch-${job.batch_id}`;
      const current = result.get(key) ?? [];
      current.push({ job, index });
      result.set(key, current);
      return result;
    }, new Map<string, Array<{ job: PrintJobRead; index: number }>>()),
  );
  const row = (job: PrintJobRead, index: number) => (
    <div key={job.id} className="queue-register-row">
      <div className="queue-register-name">
        <span className="queue-file-mark">
          <FileBox size={17} />
        </span>
        <div>
          <strong title={job.remote_filename}>
            {dispatchName(job.remote_filename)}
            {job.copy_index != null
              ? uiText(" · copy {value1}", { value1: String(job.copy_index) })
              : ""}
          </strong>
          <small>
            {job.state === "queued"
              ? uiText("Position {value1}", { value1: String(job.queue_position) })
              : "#" + job.id}{" "}
            · {knownUiText(job.priority ?? "normal")}
            {job.plate_index != null && " · " + uiText("Sliced plate") + " " + job.plate_index}
          </small>
        </div>
      </div>
      <div className="queue-register-destination">
        <Printer3D size={15} />
        <div>
          <strong>
            {job.printer_id
              ? (printerNames.get(job.printer_id) ??
                uiText("Printer {value1}", { value1: String(job.printer_id) }))
              : uiText("Unassigned")}
          </strong>
          <small>
            {knownUiText(job.routing_strategy)}
            {job.target_group && " · " + job.target_group}
          </small>
        </div>
      </div>
      <div
        className="queue-register-state"
        data-state={
          job.blocked_reason || job.operator_gate_state === "pending" ? "held" : job.state
        }
      >
        <strong>
          <i />
          {knownUiText(job.state)}
        </strong>
        {job.blocked_reason && <small>{knownUiText(job.blocked_reason)}</small>}
        {job.operator_gate_state === "pending" && <small>{uiText("release needed")}</small>}
        {["printing", "paused"].includes(job.state) && (
          <div className="queue-register-progress">
            <progress aria-label={uiText("Progress")} max={1} value={job.progress} />
            <span>{Math.round(job.progress * 100)}%</span>
          </div>
        )}
      </div>
      <div className="queue-register-actions" aria-busy={busy === job.id}>
        {actions?.(job, index)}
      </div>
    </div>
  );
  return (
    <Localized>
      <section
        className="space-y-2"
        aria-label={uiText("{value1} print jobs", { value1: String(title) })}
      >
        <div className="queue-register-entries">
          {jobs.length ? (
            <div className="queue-register-columns" aria-hidden="true">
              <span>{uiText("Revision")}</span>
              <span>{uiText("Destination")}</span>
              <span>{uiText("Status")}</span>
              <span>{uiText("Actions")}</span>
            </div>
          ) : (
            <div className="queue-register-empty">
              <Layers3 size={22} />
              <strong>{emptyState}</strong>
              {emptyAction}
            </div>
          )}
          {groups.map(([key, entries]) =>
            entries[0].job.batch_id == null ? (
              row(entries[0].job, entries[0].index)
            ) : (
              <details key={key} open className="border-b border-border last:border-b-0">
                <summary className="cursor-action bg-muted/30 px-4 py-3 text-sm font-semibold text-foreground">
                  {uiText("Batch #{value1} · {value2} copies", {
                    value1: String(entries[0].job.batch_id ?? ""),
                    value2: String(entries.length ?? ""),
                  })}
                </summary>
                {entries.map(({ job, index }) => row(job, index))}
              </details>
            ),
          )}
        </div>
      </section>
    </Localized>
  );
}

/**
 * Everything `FleetMaintenancePanel` reaches for outside itself, on the same
 * terms as `FleetQueueDeps`: omit `deps` in application code, override entries
 * in a test.
 */
export type FleetMaintenanceDeps = MaintenanceApi;

export function FleetMaintenancePanel({
  printers,
  onPrintersChanged,
  deps,
  compact = false,
  actionsHost,
}: {
  printers: PrinterRead[];
  onPrintersChanged: () => void;
  deps?: Partial<FleetMaintenanceDeps>;
  compact?: boolean;
  actionsHost?: HTMLDivElement | null;
}) {
  useUiLocale();
  const api = { ...maintenanceApi, ...deps };
  const maintenance = usePrinterMaintenance(
    printers.map((printer) => printer.id),
    api,
  );
  const mutation = useMaintenanceMutation(api);
  const { windows, logs } = maintenance;
  const [selected, setSelected] = useState<PrinterRead | null>(null);
  const [mode, setMode] = useState<"window" | "log" | null>(null);
  const [startsAt, setStartsAt] = useState("");
  const [endsAt, setEndsAt] = useState("");
  const [reason, setReason] = useState("");
  const [category, setCategory] = useState("service");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [printerFilter, setPrinterFilter] = useState("all");
  const [windowFilter, setWindowFilter] = useState<"upcoming" | "all">("upcoming");
  const [performedAt, setPerformedAt] = useState(() => {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  });
  const [deleteTarget, setDeleteTarget] = useState<{
    kind: "delete-window" | "delete-log";
    printerId: number;
    id: number;
    session: number;
  } | null>(null);
  const visiblePrinters = printers.filter(
    (printer) => printerFilter === "all" || String(printer.id) === printerFilter,
  );
  const validWindow =
    !!startsAt &&
    !!endsAt &&
    Number.isFinite(new Date(startsAt).getTime()) &&
    new Date(endsAt).getTime() > new Date(startsAt).getTime();

  async function toggleRouting(printer: PrinterRead, field: "default" | "drain") {
    const scope = getSessionVersion();
    try {
      await mutation.mutateAsync({
        kind: "routing",
        printerId: printer.id,
        payload:
          field === "default"
            ? { is_default: !printer.is_default }
            : {
                drain_mode: !printer.drain_mode,
                drain_reason: printer.drain_mode ? null : "Manual soft drain",
              },
      });
      if (scope === getSessionVersion()) onPrintersChanged();
    } catch (error) {
      if (scope === getSessionVersion()) toast.error(error);
    }
  }

  async function removeMaintenance(
    kind: "delete-window" | "delete-log",
    printerId: number,
    id: number,
  ) {
    const scope = getSessionVersion();
    try {
      await mutation.mutateAsync({ kind, printerId, id });
      return scope === getSessionVersion();
    } catch (error) {
      if (scope === getSessionVersion()) toast.error(error);
      return false;
    }
  }

  async function submit() {
    if (!selected) return;
    const scope = getSessionVersion();
    setBusy(true);
    try {
      if (mode === "window") {
        await mutation.mutateAsync({
          kind: "create-window",
          printerId: selected.id,
          payload: {
            starts_at: new Date(startsAt).toISOString(),
            ends_at: new Date(endsAt).toISOString(),
            reason: reason || null,
          },
        });
      } else if (mode === "log") {
        await mutation.mutateAsync({
          kind: "create-log",
          printerId: selected.id,
          payload: { category, note, performed_at: new Date(`${performedAt}T00:00`).toISOString() },
        });
      } else {
        throw new Error("maintenance_mode_missing");
      }
      if (scope !== getSessionVersion()) return;
      setMode(null);
      setSelected(null);
      setNote("");
      setReason("");
      toast.success(uiText("Maintenance updated"));
    } catch (error) {
      if (scope === getSessionVersion()) toast.error(error);
    } finally {
      if (scope === getSessionVersion()) setBusy(false);
    }
  }

  return (
    <Localized>
      <div
        className={`fleet-panels space-y-4 ${compact ? "printer-reference-surface printer-maintenance-reference" : ""}`}
      >
        {actionsHost &&
          createPortal(
            <div className="flex flex-wrap gap-2">
              <Button
                size="sm"
                disabled={printers.length === 0}
                onClick={() => {
                  setSelected(printers[0] ?? null);
                  setMode("window");
                }}
              >
                {uiText("Schedule window")}
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={printers.length === 0}
                onClick={() => {
                  setSelected(printers[0] ?? null);
                  setMode("log");
                }}
              >
                {uiText("Log service")}
              </Button>
            </div>,
            actionsHost,
          )}
        {!compact && (
          <div className="fleet-filter-row">
            <label>
              {uiText("Printer")}
              <select
                className={inputClasses}
                aria-label={uiText("Printer")}
                value={printerFilter}
                onChange={(event) => setPrinterFilter(event.target.value)}
              >
                <option value="all">{uiText("All printers")}</option>
                {printers.map((printer) => (
                  <option key={printer.id} value={printer.id}>
                    {printer.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              {uiText("Windows")}
              <select
                className={inputClasses}
                aria-label={uiText("Windows")}
                value={windowFilter}
                onChange={(event) => {
                  if (event.target.value === "all" || event.target.value === "upcoming")
                    setWindowFilter(event.target.value);
                }}
              >
                <option value="upcoming">{uiText("Upcoming windows")}</option>
                <option value="all">{uiText("All windows")}</option>
              </select>
            </label>
          </div>
        )}
        {maintenance.error && (
          <div role="alert" className="space-y-2 text-sm text-destructive">
            <p>{userMessage(maintenance.error)}</p>
            <Button variant="outline" onClick={() => void maintenance.retry()}>
              {uiText("Retry")}
            </Button>
          </div>
        )}
        <Modal
          open={mode !== null}
          onClose={() => {
            if (!busy) setMode(null);
          }}
          title={mode === "window" ? uiText("Schedule maintenance") : uiText("Log printer service")}
          description={
            mode === "window"
              ? uiText("Reserve a window and record why the printer is unavailable.")
              : uiText("Keep a dated record of maintenance on this printer.")
          }
          footer={
            <>
              <Button variant="outline" onClick={() => setMode(null)} disabled={busy}>
                {uiText("Cancel")}
              </Button>
              <Button
                onClick={() => void submit()}
                loading={busy}
                disabled={
                  !selected ||
                  (mode === "window"
                    ? !validWindow
                    : !note.trim() || !category.trim() || !performedAt)
                }
              >
                {uiText(mode === "window" ? "Schedule window" : "Save service log")}
              </Button>
            </>
          }
        >
          <div className="space-y-5">
            {compact ? (
              <dl className="grid grid-cols-[110px_minmax(0,1fr)] gap-4 bg-plate p-4 text-[13px]">
                <dt className="text-muted-foreground">{uiText("Printer")}</dt>
                <dd className="font-medium">{selected?.name}</dd>
              </dl>
            ) : (
              <label className="block space-y-2 text-[13px] font-medium">
                {uiText("Printer")}
                <select
                  className={inputClasses}
                  value={selected?.id ?? ""}
                  onChange={(event) =>
                    setSelected(
                      printers.find((printer) => String(printer.id) === event.target.value) ?? null,
                    )
                  }
                >
                  <option value="" disabled>
                    {uiText("Choose printer")}
                  </option>
                  {printers.map((printer) => (
                    <option key={printer.id} value={printer.id}>
                      {printer.name}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {mode === "window" ? (
              <fieldset className="space-y-4 border-t border-border pt-4">
                <legend className="pr-3 text-sm font-semibold">
                  {uiText("Maintenance window")}
                </legend>
                <div className="grid gap-4 sm:grid-cols-2">
                  <label className="block space-y-2 text-[13px] font-medium">
                    {uiText("Starts")}
                    <Input
                      type="datetime-local"
                      value={startsAt}
                      onChange={(event) => setStartsAt(event.target.value)}
                    />
                  </label>
                  <label className="block space-y-2 text-[13px] font-medium">
                    {uiText("Ends")}
                    <Input
                      type="datetime-local"
                      value={endsAt}
                      onChange={(event) => setEndsAt(event.target.value)}
                    />
                  </label>
                </div>
                <label className="block space-y-2 text-[13px] font-medium">
                  {uiText("Reason")}
                  <textarea
                    rows={3}
                    value={reason}
                    onChange={(event) => setReason(event.target.value)}
                    className={`${inputClasses} min-h-24 py-2`}
                  />
                </label>
                {startsAt && endsAt && !validWindow && (
                  <p role="alert" className="text-xs text-destructive">
                    {uiText("The maintenance end must be after its start.")}
                  </p>
                )}
              </fieldset>
            ) : (
              <fieldset className="space-y-4 border-t border-border pt-4">
                <legend className="pr-3 text-sm font-semibold">{uiText("Service record")}</legend>
                <div className="grid gap-4 sm:grid-cols-2">
                  <label className="block space-y-2 text-[13px] font-medium">
                    {uiText("Date")}
                    <Input
                      type="date"
                      value={performedAt}
                      onChange={(event) => setPerformedAt(event.target.value)}
                    />
                  </label>
                  <label className="block space-y-2 text-[13px] font-medium">
                    {uiText("Category")}
                    <Input value={category} onChange={(event) => setCategory(event.target.value)} />
                  </label>
                </div>
                <label className="block space-y-2 text-[13px] font-medium">
                  {uiText("Service notes")}
                  <textarea
                    rows={4}
                    value={note}
                    onChange={(event) => setNote(event.target.value)}
                    className={`${inputClasses} min-h-28 py-2`}
                  />
                </label>
              </fieldset>
            )}
          </div>
        </Modal>
        <ConfirmModal
          open={deleteTarget !== null}
          title={uiText("Delete maintenance record?")}
          description={uiText(
            "Remove this maintenance record? Printer routing and active prints remain unchanged.",
          )}
          confirmLabel={uiText("Delete")}
          busy={mutation.isPending}
          onClose={() => {
            if (!mutation.isPending) setDeleteTarget(null);
          }}
          onConfirm={() => {
            if (!deleteTarget || deleteTarget.session !== getSessionVersion()) return;
            const target = deleteTarget;
            void removeMaintenance(target.kind, target.printerId, target.id).then((saved) => {
              if (saved) setDeleteTarget(null);
            });
          }}
        />
        {printers.length === 0 ? (
          <EmptyState
            icon={Wrench}
            title={uiText("No printers to maintain")}
            className="border border-border bg-card"
          />
        ) : (
          <section
            className={compact ? "printer-reference-list" : "fleet-list-panel"}
            aria-label={uiText("Routing & service")}
          >
            {!compact && <h2>{uiText("Routing & service")}</h2>}
            {visiblePrinters.length === 0 && (
              <p className="p-5 text-[13px] text-muted-foreground">
                {uiText("No maintenance records match these filters.")}
              </p>
            )}
            {visiblePrinters.map((printer) => {
              const printerWindows = windows.filter((row) => {
                if (row.printer_id !== printer.id) return false;
                if (compact || windowFilter === "all") return true;
                const end = parseApiInstant(row.ends_at);
                return end !== null && end.getTime() >= Date.now();
              });
              const printerLogs =
                compact || windowFilter === "all"
                  ? logs.filter((row) => row.printer_id === printer.id)
                  : [];
              return (
                <div key={printer.id} className="border-b border-border last:border-b-0">
                  <div className="fleet-plain-row">
                    <div>
                      <h3 className="text-[13px] font-semibold">
                        {compact ? uiText("Routing") : printer.name}
                      </h3>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {printer.group ? `${printer.group} · ` : ""}
                        {printer.is_default ? `${uiText("Default printer")} · ` : ""}
                        {printer.drain_mode
                          ? printer.drain_reason || uiText("Soft drain active")
                          : uiText("Accepting scheduled work")}
                      </p>
                    </div>
                    <div>
                      <Button
                        size="xs"
                        variant="outline"
                        disabled={mutation.isPending}
                        onClick={() => void toggleRouting(printer, "default")}
                      >
                        {uiText(printer.is_default ? "Unset default" : "Set default")}
                      </Button>
                      <Button
                        size="xs"
                        variant="outline"
                        disabled={mutation.isPending}
                        onClick={() => void toggleRouting(printer, "drain")}
                      >
                        {uiText(printer.drain_mode ? "Resume routing" : "Soft drain")}
                      </Button>
                      {!compact && (
                        <>
                          {" "}
                          <Button
                            size="xs"
                            variant="outline"
                            onClick={() => {
                              setSelected(printer);
                              setMode("window");
                            }}
                          >
                            <CalendarClock className="h-3.5 w-3.5" aria-hidden />
                            {uiText("Schedule window")}
                          </Button>
                          <Button
                            size="xs"
                            variant="outline"
                            onClick={() => {
                              setSelected(printer);
                              setMode("log");
                            }}
                          >
                            <Wrench className="h-3.5 w-3.5" aria-hidden />
                            {uiText("Log service")}
                          </Button>
                        </>
                      )}
                    </div>
                  </div>
                  {compact && (
                    <div className="printer-service-subhead">
                      <h3>{uiText("Maintenance windows")}</h3>
                      {!printerWindows.length && (
                        <p>
                          {uiText(
                            maintenance.loadingWindows
                              ? "Loading…"
                              : "No maintenance windows scheduled.",
                          )}
                        </p>
                      )}
                    </div>
                  )}
                  {printerWindows.map((row) => (
                    <div key={`window-${row.id}`} className="fleet-plain-row">
                      <div>
                        <h3 className="text-[13px] font-medium">
                          {compact
                            ? uiText("Maintenance window")
                            : `${printer.name} · ${uiText("Maintenance window")}`}
                        </h3>
                        <p className="mt-1 text-xs text-muted-foreground">
                          {parseApiInstant(row.starts_at)?.toLocaleString(currentLocale()) ??
                            uiText("Not recorded")}{" "}
                          –{" "}
                          {parseApiInstant(row.ends_at)?.toLocaleString(currentLocale()) ??
                            uiText("Not recorded")}
                          {row.reason ? ` · ${row.reason}` : ""}
                        </p>
                      </div>
                      <div>
                        <Button
                          size="xs"
                          variant="ghost"
                          disabled={mutation.isPending}
                          aria-label={uiText("Delete maintenance window")}
                          onClick={() =>
                            setDeleteTarget({
                              kind: "delete-window",
                              printerId: printer.id,
                              id: row.id,
                              session: getSessionVersion(),
                            })
                          }
                        >
                          <Trash2 className="h-3.5 w-3.5" aria-hidden />
                          {uiText("Delete")}
                        </Button>
                      </div>
                    </div>
                  ))}
                  {compact && (
                    <div className="printer-service-subhead">
                      <h3>{uiText("Service history")}</h3>
                      {!printerLogs.length && (
                        <p>
                          {uiText(
                            maintenance.loadingLogs ? "Loading…" : "No service entries recorded.",
                          )}
                        </p>
                      )}
                    </div>
                  )}
                  {printerLogs.map((row) => (
                    <div key={`log-${row.id}`} className="fleet-plain-row">
                      <div>
                        <h3 className="text-[13px] font-medium">
                          {compact ? row.category : `${printer.name} · ${row.category}`}
                        </h3>
                        <p className="mt-1 text-xs text-muted-foreground">
                          {new Date(row.performed_at).toLocaleDateString(currentLocale())} ·{" "}
                          {row.note}
                        </p>
                      </div>
                      <div>
                        <Button
                          size="xs"
                          variant="ghost"
                          disabled={mutation.isPending}
                          aria-label={uiText("Delete maintenance log")}
                          onClick={() =>
                            setDeleteTarget({
                              kind: "delete-log",
                              printerId: printer.id,
                              id: row.id,
                              session: getSessionVersion(),
                            })
                          }
                        >
                          <Trash2 className="h-3.5 w-3.5" aria-hidden />
                          {uiText("Delete")}
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              );
            })}
          </section>
        )}
        <p className="text-xs leading-relaxed text-muted-foreground">
          {uiText(
            compact
              ? "Scheduled windows and soft drain affect new assignments only. The current print keeps running."
              : "Drain stops assigning new jobs. It does not interrupt an active print.",
          )}
        </p>
      </div>
    </Localized>
  );
}
