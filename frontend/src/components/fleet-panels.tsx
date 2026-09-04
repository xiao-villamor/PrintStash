import { useEffect, useMemo, useState } from "react";
import {
  ArrowDown,
  ArrowUp,
  CalendarClock,
  ListOrdered,
  Pencil,
  RotateCcw,
  Trash2,
  Wrench,
} from "lucide-react";

import {
  createMaintenanceLog,
  createMaintenanceWindow,
  deleteFleetJob,
  deleteMaintenanceLog,
  deleteMaintenanceWindow,
  decideFleetOperatorGate,
  listMaintenanceLog,
  listMaintenanceWindows,
  retryFleetJob,
  updateFleetJob,
  updatePrinterRouting,
} from "@/lib/api";
import { useFleetQueue, useFleetSummary } from "@/lib/queries";
import { toast } from "@/lib/toast";
import type {
  CompatibilityPolicy,
  JobPriority,
  MaintenanceLog,
  MaintenanceWindow,
  PrinterRead,
  PrintJobRead,
  QueueJobUpdate,
  RoutingStrategy,
} from "@/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { EmptyState } from "@/components/ui/empty-state";
import { Input, inputClasses } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { Skeleton } from "@/components/ui/skeleton";
import { Localized } from "@/components/ui/localized";

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
  decideOperatorGate: typeof decideFleetOperatorGate;
}

const REAL_FLEET_QUEUE_DEPS: FleetQueueDeps = {
  deleteJob: deleteFleetJob,
  updateJob: updateFleetJob,
  retryJob: retryFleetJob,
  decideOperatorGate: decideFleetOperatorGate,
};

export function FleetQueuePanel({
  printers,
  deps,
}: {
  printers: PrinterRead[];
  deps?: Partial<FleetQueueDeps>;
}) {
  const { deleteJob, updateJob, retryJob, decideOperatorGate } = {
    ...REAL_FLEET_QUEUE_DEPS,
    ...deps,
  };
  const [historyLimit, setHistoryLimit] = useState(20);
  const queueQuery = useFleetQueue({ refetchInterval: 5_000, historyLimit });
  const summaryQuery = useFleetSummary({ refetchInterval: 5_000 });
  const [deleteTarget, setDeleteTarget] = useState<PrintJobRead | null>(null);
  const [editTarget, setEditTarget] = useState<PrintJobRead | null>(null);
  const [draft, setDraft] = useState<QueueEditDraft | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const jobs = queueQuery.data ?? [];
  const printerNames = useMemo(
    () => new Map(printers.map((printer) => [printer.id, printer.name])),
    [printers],
  );
  const queued = jobs.filter((job) => job.state === "queued");
  const active = jobs.filter((job) => ACTIVE.has(job.state));
  const recent = jobs.filter((job) => !ACTIVE.has(job.state) && job.state !== "queued");

  async function mutate<T>(jobId: number, action: () => Promise<T>): Promise<boolean> {
    setBusy(jobId);
    try {
      await action();
      await Promise.all([queueQuery.refetch(), summaryQuery.refetch()]);
      return true;
    } catch (error) {
      toast.error(error);
      return false;
    } finally {
      setBusy(null);
    }
  }

  function beginEdit(job: PrintJobRead) {
    setEditTarget(job);
    setDraft(editDraft(job));
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
      toast.success("Queue job updated");
    }
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
  if (jobs.length === 0) {
    return (
      <Localized>
        <EmptyState
          icon={ListOrdered}
          title="No queued print jobs"
          description="Add G-code from a model’s Send dialog to start building the fleet queue."
          className="rounded-lg border border-border bg-card shadow-sm"
        />
      </Localized>
    );
  }

  return (
    <Localized>
      <div className="space-y-5">
        <ConfirmModal
          open={deleteTarget !== null}
          onClose={() => setDeleteTarget(null)}
          onConfirm={() => {
            if (!deleteTarget) return;
            const id = deleteTarget.id;
            setDeleteTarget(null);
            void mutate(id, () => deleteJob(id));
          }}
          title="Delete queued job?"
          description="This permanently removes the pending job from the queue. It does not cancel an active printer."
          confirmLabel="Delete job"
        />
        <Modal
          open={editTarget !== null && draft !== null}
          onClose={() => {
            if (busy !== editTarget?.id) {
              setEditTarget(null);
              setDraft(null);
            }
          }}
          title="Edit queue job"
        >
          {editTarget && draft && (
            <div className="space-y-4">
              <p className="truncate text-sm text-muted-foreground">{editTarget.remote_filename}</p>
              <div className="grid gap-4 sm:grid-cols-2">
                <label className="block space-y-1.5 text-sm font-medium text-foreground">
                  Routing
                  <select
                    className={inputClasses}
                    value={draft.strategy}
                    onChange={(event) => {
                      const strategy = parseRoutingStrategy(event.target.value);
                      if (strategy) setDraft({ ...draft, strategy });
                    }}
                  >
                    <option value="manual">Choose printer</option>
                    <option value="default">Default printer</option>
                    <option value="least_busy">Least busy</option>
                  </select>
                </label>
                <label className="block space-y-1.5 text-sm font-medium text-foreground">
                  Printer
                  <select
                    className={inputClasses}
                    value={draft.printerId}
                    disabled={draft.strategy !== "manual"}
                    onChange={(event) => setDraft({ ...draft, printerId: event.target.value })}
                  >
                    <option value="">Choose printer</option>
                    {printers.map((printer) => (
                      <option key={printer.id} value={printer.id}>
                        {printer.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="block space-y-1.5 text-sm font-medium text-foreground">
                  Priority
                  <select
                    className={inputClasses}
                    value={draft.priority}
                    onChange={(event) => {
                      const priority = parseJobPriority(event.target.value);
                      if (priority) setDraft({ ...draft, priority });
                    }}
                  >
                    <option value="low">Low</option>
                    <option value="normal">Normal</option>
                    <option value="rush">Rush</option>
                  </select>
                </label>
                <label className="block space-y-1.5 text-sm font-medium text-foreground">
                  Queue position
                  <Input
                    type="number"
                    min={1}
                    value={draft.queuePosition}
                    onChange={(event) => setDraft({ ...draft, queuePosition: event.target.value })}
                  />
                </label>
                <label className="block space-y-1.5 text-sm font-medium text-foreground">
                  Target group
                  <Input
                    value={draft.targetGroup}
                    disabled={draft.strategy === "manual"}
                    onChange={(event) => setDraft({ ...draft, targetGroup: event.target.value })}
                    placeholder="Any group"
                  />
                </label>
                <label className="block space-y-1.5 text-sm font-medium text-foreground">
                  Compatibility
                  <select
                    className={inputClasses}
                    value={draft.compatibilityPolicy}
                    onChange={(event) => {
                      const compatibilityPolicy = parseCompatibilityPolicy(event.target.value);
                      if (compatibilityPolicy) setDraft({ ...draft, compatibilityPolicy });
                    }}
                  >
                    <option value="safe">Require compatible material</option>
                    <option value="allow_mismatch">Allow mismatch</option>
                  </select>
                </label>
              </div>
              <div className="flex justify-end gap-2">
                <Button
                  variant="outline"
                  onClick={() => {
                    setEditTarget(null);
                    setDraft(null);
                  }}
                  disabled={busy === editTarget.id}
                >
                  Cancel
                </Button>
                <Button
                  onClick={() => void saveEdit()}
                  loading={busy === editTarget.id}
                  disabled={draft.strategy === "manual" && !draft.printerId}
                >
                  Save changes
                </Button>
              </div>
            </div>
          )}
        </Modal>
        {summaryQuery.data && (
          <>
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4" aria-label="Queue summary">
              {[
                ["Queued", summaryQuery.data.queued_jobs],
                ["Active", summaryQuery.data.active_jobs],
                ["Blocked", summaryQuery.data.attention_jobs],
                ["Draining", summaryQuery.data.draining_printers],
              ].map(([label, value]) => (
                <div key={label} className="rounded-lg border border-border bg-card p-4 shadow-sm">
                  <p className="text-xs font-medium text-muted-foreground">{label}</p>
                  <p className="mt-2 font-mono text-2xl font-semibold tabular-nums text-foreground">
                    {value}
                  </p>
                </div>
              ))}
            </div>
            {(summaryQuery.data.printers?.length ?? 0) > 0 && (
              <section className="space-y-2" aria-label="Fleet board">
                <h2 className="text-sm font-semibold text-foreground">Fleet board</h2>
                <div className="grid gap-3 lg:grid-cols-2">
                  {summaryQuery.data.printers?.map((printer) => (
                    <article
                      key={printer.printer_id}
                      className="rounded-lg border border-border bg-card p-4 shadow-sm"
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <h3 className="font-semibold text-foreground">{printer.name}</h3>
                          <p className="mt-1 text-xs text-muted-foreground">
                            {printer.group || "No group"} ·{" "}
                            {printer.nozzle_diameter_mm == null
                              ? "Nozzle unknown"
                              : `${printer.nozzle_diameter_mm.toFixed(2)} mm nozzle`}
                          </p>
                        </div>
                        <div className="flex flex-wrap justify-end gap-1">
                          <Badge variant="outline">{printer.status}</Badge>
                          {printer.drain_mode && <Badge variant="warning">drain</Badge>}
                          {printer.maintenance && <Badge variant="warning">maintenance</Badge>}
                          {printer.pending_operator_release && (
                            <Badge variant="warning">release needed</Badge>
                          )}
                        </div>
                      </div>
                      {printer.progress != null && (
                        <div className="mt-3 h-1.5 overflow-hidden rounded bg-muted">
                          <div
                            className="h-full bg-primary"
                            style={{
                              width: `${Math.max(0, Math.min(100, printer.progress * 100))}%`,
                            }}
                          />
                        </div>
                      )}
                      <div className="mt-3 grid gap-2 text-xs text-muted-foreground sm:grid-cols-2">
                        <p>
                          <span className="font-medium text-foreground">Loaded:</span>{" "}
                          {printer.loaded_slots.length
                            ? printer.loaded_slots.join(", ")
                            : "Unknown"}
                        </p>
                        <p>
                          <span className="font-medium text-foreground">Current:</span>{" "}
                          {printer.current_job_name || "Idle"}
                          {printer.current_priority ? ` · ${printer.current_priority}` : ""}
                        </p>
                        <p className="sm:col-span-2">
                          <span className="font-medium text-foreground">Next:</span>{" "}
                          {printer.next_job_name || "None"}
                          {printer.next_priority ? ` · ${printer.next_priority}` : ""}
                        </p>
                      </div>
                    </article>
                  ))}
                </div>
              </section>
            )}
          </>
        )}
        <QueueSection
          title="Queued"
          jobs={queued}
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
                      aria-label={`Move ${job.remote_filename} up`}
                      disabled={busy === job.id || laneIndex === 0}
                      onClick={() =>
                        void mutate(job.id, () => updateJob(job.id, { queue_position: laneIndex }))
                      }
                    >
                      <ArrowUp className="h-3.5 w-3.5" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      aria-label={`Move ${job.remote_filename} down`}
                      disabled={busy === job.id || laneIndex === lane.length - 1}
                      onClick={() =>
                        void mutate(job.id, () =>
                          updateJob(job.id, { queue_position: laneIndex + 2 }),
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
                aria-label={`Edit ${job.remote_filename}`}
                disabled={busy === job.id}
                onClick={() => beginEdit(job)}
              >
                <Pencil className="h-3.5 w-3.5" />
              </Button>
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label={`Delete ${job.remote_filename}`}
                disabled={busy === job.id}
                onClick={() => setDeleteTarget(job)}
              >
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            </>
          )}
        />
        <QueueSection title="Active" jobs={active} printerNames={printerNames} busy={busy} />
        <QueueSection
          title="Recent"
          jobs={recent}
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
                    onClick={() => void mutate(job.id, () => decideOperatorGate(job.id, "release"))}
                  >
                    Release
                  </Button>
                  <Button
                    variant="outline"
                    size="xs"
                    disabled={busy === job.id}
                    onClick={() => void mutate(job.id, () => decideOperatorGate(job.id, "hold"))}
                  >
                    Hold
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
                  Retry
                </Button>
              )}
            </>
          )}
        />
        {recent.length >= historyLimit && historyLimit < 100 && (
          <div className="flex justify-center">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setHistoryLimit((value) => Math.min(value + 20, 100))}
            >
              Load older jobs
            </Button>
          </div>
        )}
      </div>
    </Localized>
  );
}

function QueueSection({
  title,
  jobs,
  printerNames,
  busy,
  actions,
}: {
  title: string;
  jobs: PrintJobRead[];
  printerNames: Map<number, string>;
  busy: number | null;
  actions?: (job: PrintJobRead, index: number) => React.ReactNode;
}) {
  if (jobs.length === 0) return null;
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
    <div
      key={job.id}
      className="flex items-center gap-3 border-b border-border px-4 py-3 last:border-b-0"
    >
      <span className="w-7 shrink-0 font-mono text-xs tabular-nums text-muted-foreground">
        {job.state === "queued" ? job.queue_position : "—"}
      </span>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-foreground">
          {job.remote_filename}
          {job.copy_index != null ? ` · copy ${job.copy_index}` : ""}
        </p>
        <p className="mt-0.5 truncate text-xs text-muted-foreground">
          {job.printer_id
            ? (printerNames.get(job.printer_id) ?? `Printer ${job.printer_id}`)
            : "Unassigned"}{" "}
          · {job.routing_strategy.replace("_", " ")}
          {job.target_group ? ` · ${job.target_group}` : ""}
          {job.blocked_reason ? ` · ${job.blocked_reason.replaceAll("_", " ")}` : ""}
        </p>
      </div>
      <Badge variant={job.priority === "rush" ? "warning" : "outline"}>
        {job.priority ?? "normal"}
      </Badge>
      {job.operator_gate_state === "pending" && <Badge variant="warning">release needed</Badge>}
      <Badge variant={job.blocked_reason || job.state === "failed" ? "warning" : "outline"}>
        {job.state}
      </Badge>
      <div className="flex items-center gap-1" aria-busy={busy === job.id}>
        {actions?.(job, index)}
      </div>
    </div>
  );
  return (
    <Localized>
      <section className="space-y-2" aria-label={`${title} print jobs`}>
        <h2 className="text-sm font-semibold text-foreground">{title}</h2>
        <div className="overflow-hidden rounded-lg border border-border bg-card shadow-sm">
          {groups.map(([key, entries]) =>
            entries[0].job.batch_id == null ? (
              row(entries[0].job, entries[0].index)
            ) : (
              <details key={key} open className="border-b border-border last:border-b-0">
                <summary className="cursor-pointer bg-muted/30 px-4 py-3 text-sm font-semibold text-foreground">
                  Batch #{entries[0].job.batch_id} · {entries.length} copies
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
export interface FleetMaintenanceDeps {
  listWindows: typeof listMaintenanceWindows;
  listLog: typeof listMaintenanceLog;
  createWindow: typeof createMaintenanceWindow;
  createLog: typeof createMaintenanceLog;
  deleteWindow: typeof deleteMaintenanceWindow;
  deleteLog: typeof deleteMaintenanceLog;
  updateRouting: typeof updatePrinterRouting;
}

const REAL_FLEET_MAINTENANCE_DEPS: FleetMaintenanceDeps = {
  listWindows: listMaintenanceWindows,
  listLog: listMaintenanceLog,
  createWindow: createMaintenanceWindow,
  createLog: createMaintenanceLog,
  deleteWindow: deleteMaintenanceWindow,
  deleteLog: deleteMaintenanceLog,
  updateRouting: updatePrinterRouting,
};

export function FleetMaintenancePanel({
  printers,
  onPrintersChanged,
  deps,
}: {
  printers: PrinterRead[];
  onPrintersChanged: () => void;
  deps?: Partial<FleetMaintenanceDeps>;
}) {
  const { listWindows, listLog, createWindow, createLog, deleteWindow, deleteLog, updateRouting } =
    { ...REAL_FLEET_MAINTENANCE_DEPS, ...deps };
  const [windows, setWindows] = useState<MaintenanceWindow[]>([]);
  const [logs, setLogs] = useState<MaintenanceLog[]>([]);
  const [selected, setSelected] = useState<PrinterRead | null>(null);
  const [mode, setMode] = useState<"window" | "log" | null>(null);
  const [startsAt, setStartsAt] = useState("");
  const [endsAt, setEndsAt] = useState("");
  const [reason, setReason] = useState("");
  const [category, setCategory] = useState("service");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  async function load() {
    const [allWindows, allLogs] = await Promise.all([
      Promise.all(printers.map((printer) => listWindows(printer.id))),
      Promise.all(printers.map((printer) => listLog(printer.id))),
    ]);
    setWindows(allWindows.flat());
    setLogs(allLogs.flat());
  }
  useEffect(() => {
    // `load` awaits the maintenance API before it touches state; the rule follows the call but not the `await` inside it.
    // oxlint-disable-next-line react/set-state-in-effect -- setState here is asynchronous, after the fetch resolves
    void load().catch(toast.error);
  }, [printers]); // eslint-disable-line react-hooks/exhaustive-deps

  async function toggleRouting(printer: PrinterRead, field: "default" | "drain") {
    try {
      await updateRouting(
        printer.id,
        field === "default"
          ? { is_default: !printer.is_default }
          : {
              drain_mode: !printer.drain_mode,
              drain_reason: printer.drain_mode ? null : "Manual soft drain",
            },
      );
      onPrintersChanged();
    } catch (error) {
      toast.error(error);
    }
  }

  async function submit() {
    if (!selected) return;
    setBusy(true);
    try {
      if (mode === "window") {
        await createWindow(selected.id, {
          starts_at: new Date(startsAt).toISOString(),
          ends_at: new Date(endsAt).toISOString(),
          reason: reason || null,
        });
      } else {
        await createLog(selected.id, { category, note });
      }
      setMode(null);
      setSelected(null);
      setNote("");
      setReason("");
      await load();
      toast.success("Maintenance updated");
    } catch (error) {
      toast.error(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Localized>
      <div className="space-y-5">
        <Modal
          open={mode !== null}
          onClose={() => {
            if (!busy) setMode(null);
          }}
          title={mode === "window" ? "Schedule maintenance" : "Log maintenance"}
        >
          <div className="space-y-4">
            {mode === "window" ? (
              <>
                <label className="block space-y-1.5 text-sm font-medium text-foreground">
                  Starts
                  <Input
                    type="datetime-local"
                    value={startsAt}
                    onChange={(event) => setStartsAt(event.target.value)}
                  />
                </label>
                <label className="block space-y-1.5 text-sm font-medium text-foreground">
                  Ends
                  <Input
                    type="datetime-local"
                    value={endsAt}
                    onChange={(event) => setEndsAt(event.target.value)}
                  />
                </label>
                <label className="block space-y-1.5 text-sm font-medium text-foreground">
                  Reason
                  <Input value={reason} onChange={(event) => setReason(event.target.value)} />
                </label>
              </>
            ) : (
              <>
                <label className="block space-y-1.5 text-sm font-medium text-foreground">
                  Category
                  <Input value={category} onChange={(event) => setCategory(event.target.value)} />
                </label>
                <label className="block space-y-1.5 text-sm font-medium text-foreground">
                  Note
                  <Input value={note} onChange={(event) => setNote(event.target.value)} />
                </label>
              </>
            )}
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setMode(null)} disabled={busy}>
                Cancel
              </Button>
              <Button
                onClick={() => void submit()}
                loading={busy}
                disabled={mode === "window" ? !startsAt || !endsAt : !note.trim()}
              >
                Save
              </Button>
            </div>
          </div>
        </Modal>
        {printers.length === 0 ? (
          <EmptyState
            icon={Wrench}
            title="No printers to maintain"
            className="rounded-lg border border-border bg-card"
          />
        ) : (
          <div className="grid gap-4 lg:grid-cols-2">
            {printers.map((printer) => {
              const printerWindows = windows.filter((row) => row.printer_id === printer.id);
              const printerLogs = logs.filter((row) => row.printer_id === printer.id);
              return (
                <section
                  key={printer.id}
                  className="rounded-lg border border-border bg-card p-4 shadow-sm"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <h2 className="font-semibold text-foreground">{printer.name}</h2>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {printer.drain_mode
                          ? printer.drain_reason || "Soft drain active"
                          : "Accepting scheduled work"}
                      </p>
                    </div>
                    <div className="flex gap-1">
                      {printer.is_default && <Badge>Default</Badge>}
                      {printer.drain_mode && <Badge variant="warning">Draining</Badge>}
                    </div>
                  </div>
                  <div className="mt-4 flex flex-wrap gap-2">
                    <Button
                      size="xs"
                      variant="outline"
                      onClick={() => void toggleRouting(printer, "default")}
                    >
                      {printer.is_default ? "Unset default" : "Set default"}
                    </Button>
                    <Button
                      size="xs"
                      variant="outline"
                      onClick={() => void toggleRouting(printer, "drain")}
                    >
                      {printer.drain_mode ? "Resume routing" : "Soft drain"}
                    </Button>
                    <Button
                      size="xs"
                      variant="outline"
                      onClick={() => {
                        setSelected(printer);
                        setMode("window");
                      }}
                    >
                      <CalendarClock className="h-3.5 w-3.5" />
                      Schedule
                    </Button>
                    <Button
                      size="xs"
                      variant="outline"
                      onClick={() => {
                        setSelected(printer);
                        setMode("log");
                      }}
                    >
                      <Wrench className="h-3.5 w-3.5" />
                      Log
                    </Button>
                  </div>
                  <div className="mt-4 space-y-2 border-t border-border pt-3 text-xs text-muted-foreground">
                    {printerWindows.slice(0, 2).map((row) => (
                      <div key={`w-${row.id}`} className="flex items-center justify-between gap-2">
                        <span>
                          {new Date(row.starts_at).toLocaleString()} · {row.reason || "Maintenance"}
                        </span>
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          aria-label="Delete maintenance window"
                          onClick={() =>
                            void deleteWindow(printer.id, row.id).then(load).catch(toast.error)
                          }
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                    ))}
                    {printerLogs.slice(0, 2).map((row) => (
                      <div key={`l-${row.id}`} className="flex items-center justify-between gap-2">
                        <span>
                          {row.category} · {row.note}
                        </span>
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          aria-label="Delete maintenance log"
                          onClick={() =>
                            void deleteLog(printer.id, row.id).then(load).catch(toast.error)
                          }
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                    ))}
                    {printerWindows.length === 0 && printerLogs.length === 0 && (
                      <p>No maintenance activity recorded.</p>
                    )}
                  </div>
                </section>
              );
            })}
          </div>
        )}
      </div>
    </Localized>
  );
}
