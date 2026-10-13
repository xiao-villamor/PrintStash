import { useQueryClient } from "@tanstack/react-query";
import { refreshFleet } from "@/features/printers/queries";
import { knownUiText } from "@/lib/locale";
import { formatNumber } from "@/lib/format";
import { currentLocale } from "@/lib/locale";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import { useMemo, useState } from "react";
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
  CompatibilityPolicy,
  JobPriority,
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

export function FleetQueuePanel({
  printers,
  deps,
}: {
  printers: PrinterRead[];
  deps?: Partial<FleetQueueDeps>;
}) {
  useUiLocale();
  const { deleteJob, updateJob, retryJob, resolveJob, decideOperatorGate } = {
    ...REAL_FLEET_QUEUE_DEPS,
    ...deps,
  };
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
  const queued = jobs.filter((job) => job.state === "queued");
  const active = jobs.filter((job) => ACTIVE.has(job.state));
  const recent = jobs.filter((job) => !ACTIVE.has(job.state) && job.state !== "queued");

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
  if (jobs.length === 0) {
    return (
      <Localized>
        <EmptyState
          icon={ListOrdered}
          title={uiText("No queued print jobs")}
          description={uiText(
            "Add G-code from a model’s Send dialog to start building the fleet queue.",
          )}
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
          title={uiText("Delete queued job?")}
          description={uiText(
            "This permanently removes the pending job from the queue. It does not cancel an active printer.",
          )}
          confirmLabel={uiText("Delete job")}
        />
        <Modal
          open={resolveTarget !== null}
          onClose={() => {
            if (busy !== resolveTarget?.id) setResolveTarget(null);
          }}
          title={uiText("Resolve stale job")}
        >
          {resolveTarget && (
            <div className="space-y-4">
              <p className="text-sm text-muted-foreground">
                {uiText(
                  "Use this only when the printer is no longer running {value1}. This updates PrintStash history; it does not send a command to the printer.",
                  { value1: String(resolveTarget.remote_filename) },
                )}
              </p>
              <div className="flex flex-wrap justify-end gap-2">
                <Button
                  variant="outline"
                  disabled={busy === resolveTarget.id}
                  onClick={() => void resolveAs("cancelled")}
                >
                  {uiText("Mark cancelled")}
                </Button>
                <Button
                  variant="destructive"
                  disabled={busy === resolveTarget.id}
                  onClick={() => void resolveAs("failed")}
                >
                  {uiText("Mark failed")}
                </Button>
              </div>
            </div>
          )}
        </Modal>
        <Modal
          open={editTarget !== null && draft !== null}
          onClose={() => {
            if (busy !== editTarget?.id) {
              setEditTarget(null);
              setDraft(null);
            }
          }}
          title={uiText("Edit queue job")}
        >
          {editTarget && draft && (
            <div className="space-y-4">
              <p className="truncate text-sm text-muted-foreground">{editTarget.remote_filename}</p>
              <div className="grid gap-4 sm:grid-cols-2">
                <label className="block space-y-1.5 text-sm font-medium text-foreground">
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
                <label className="block space-y-1.5 text-sm font-medium text-foreground">
                  {uiText("Printer")}
                  <select
                    className={inputClasses}
                    value={draft.printerId}
                    disabled={draft.strategy !== "manual"}
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
                <label className="block space-y-1.5 text-sm font-medium text-foreground">
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
                <label className="block space-y-1.5 text-sm font-medium text-foreground">
                  {uiText("Queue position")}
                  <Input
                    type="number"
                    min={1}
                    value={draft.queuePosition}
                    onChange={(event) => setDraft({ ...draft, queuePosition: event.target.value })}
                  />
                </label>
                <label className="block space-y-1.5 text-sm font-medium text-foreground">
                  {uiText("Target group")}
                  <Input
                    value={draft.targetGroup}
                    disabled={draft.strategy === "manual"}
                    onChange={(event) => setDraft({ ...draft, targetGroup: event.target.value })}
                    placeholder={uiText("Any group")}
                  />
                </label>
                <label className="block space-y-1.5 text-sm font-medium text-foreground">
                  {uiText("Compatibility")}
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
                  {uiText("Cancel")}
                </Button>
                <Button
                  onClick={() => void saveEdit()}
                  loading={busy === editTarget.id}
                  disabled={draft.strategy === "manual" && !draft.printerId}
                >
                  {uiText("Save changes")}
                </Button>
              </div>
            </div>
          )}
        </Modal>
        {summaryQuery.data && (
          <>
            <div
              className="grid grid-cols-2 gap-3 lg:grid-cols-4"
              aria-label={uiText("Queue summary")}
            >
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
              <section className="space-y-2" aria-label={uiText("Fleet board")}>
                <h2 className="text-sm font-semibold text-foreground">{uiText("Fleet board")}</h2>
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
                            {printer.group || uiText("No group")} ·{" "}
                            {printer.nozzle_diameter_mm == null
                              ? uiText("Nozzle unknown")
                              : uiText("{value1} mm nozzle", {
                                  value1: String(
                                    formatNumber(printer.nozzle_diameter_mm, {
                                      minimumFractionDigits: 2,
                                      maximumFractionDigits: 2,
                                    }),
                                  ),
                                })}
                          </p>
                        </div>
                        <div className="flex flex-wrap justify-end gap-1">
                          <Badge variant="outline">{knownUiText(printer.status)}</Badge>
                          {printer.drain_mode && <Badge variant="warning">{uiText("drain")}</Badge>}
                          {printer.maintenance && (
                            <Badge variant="warning">{uiText("maintenance")}</Badge>
                          )}
                          {printer.pending_operator_release && (
                            <Badge variant="warning">{uiText("release needed")}</Badge>
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
                          <span className="font-medium text-foreground">{uiText("Loaded:")}</span>{" "}
                          {printer.loaded_slots.length
                            ? printer.loaded_slots.join(", ")
                            : uiText("Unknown")}
                        </p>
                        <p>
                          <span className="font-medium text-foreground">{uiText("Current:")}</span>{" "}
                          {printer.current_job_name || uiText("Idle")}
                          {printer.current_priority ? ` · ${printer.current_priority}` : ""}
                        </p>
                        <p className="sm:col-span-2">
                          <span className="font-medium text-foreground">{uiText("Next:")}</span>{" "}
                          {printer.next_job_name || uiText("None")}
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
          title={uiText("Queued")}
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
                      aria-label={uiText("Move {value1} up", {
                        value1: String(job.remote_filename),
                      })}
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
                      aria-label={uiText("Move {value1} down", {
                        value1: String(job.remote_filename),
                      })}
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
        <QueueSection
          title={uiText("Active")}
          jobs={active}
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
        <QueueSection
          title={uiText("Recent")}
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
                    {uiText("Release")}
                  </Button>
                  <Button
                    variant="outline"
                    size="xs"
                    disabled={busy === job.id}
                    onClick={() => void mutate(job.id, () => decideOperatorGate(job.id, "hold"))}
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
        {recent.length >= historyLimit && historyLimit < 100 && (
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
  useUiLocale();
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
          {job.copy_index != null
            ? uiText(" · copy {value1}", { value1: String(job.copy_index) })
            : ""}
        </p>
        <p className="mt-0.5 truncate text-xs text-muted-foreground">
          {job.printer_id
            ? (printerNames.get(job.printer_id) ??
              uiText("Printer {value1}", { value1: String(job.printer_id) }))
            : uiText("Unassigned")}{" "}
          · {knownUiText(job.routing_strategy)}
          {job.target_group ? ` · ${job.target_group}` : ""}
          {job.blocked_reason ? ` · ${knownUiText(job.blocked_reason)}` : ""}
        </p>
      </div>
      <Badge variant={job.priority === "rush" ? "warning" : "outline"}>
        {job.priority ?? uiText("normal")}
      </Badge>
      {job.operator_gate_state === "pending" && (
        <Badge variant="warning">{uiText("release needed")}</Badge>
      )}
      <Badge variant={job.blocked_reason || job.state === "failed" ? "warning" : "outline"}>
        {knownUiText(job.state)}
      </Badge>
      <div className="flex items-center gap-1" aria-busy={busy === job.id}>
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
        <h2 className="text-sm font-semibold text-foreground">{title}</h2>
        <div className="overflow-hidden rounded-lg border border-border bg-card shadow-sm">
          {groups.map(([key, entries]) =>
            entries[0].job.batch_id == null ? (
              row(entries[0].job, entries[0].index)
            ) : (
              <details key={key} open className="border-b border-border last:border-b-0">
                <summary className="cursor-pointer bg-muted/30 px-4 py-3 text-sm font-semibold text-foreground">
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
}: {
  printers: PrinterRead[];
  onPrintersChanged: () => void;
  deps?: Partial<FleetMaintenanceDeps>;
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
    } catch (error) {
      if (scope === getSessionVersion()) toast.error(error);
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
          payload: { category, note },
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
      <div className="space-y-5">
        {maintenance.error && (
          <div role="alert" className="text-sm text-destructive">
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
          title={mode === "window" ? uiText("Schedule maintenance") : uiText("Log maintenance")}
        >
          <div className="space-y-4">
            {mode === "window" ? (
              <>
                <label className="block space-y-1.5 text-sm font-medium text-foreground">
                  {uiText("Starts")}
                  <Input
                    type="datetime-local"
                    value={startsAt}
                    onChange={(event) => setStartsAt(event.target.value)}
                  />
                </label>
                <label className="block space-y-1.5 text-sm font-medium text-foreground">
                  {uiText("Ends")}
                  <Input
                    type="datetime-local"
                    value={endsAt}
                    onChange={(event) => setEndsAt(event.target.value)}
                  />
                </label>
                <label className="block space-y-1.5 text-sm font-medium text-foreground">
                  {uiText("Reason")}
                  <Input value={reason} onChange={(event) => setReason(event.target.value)} />
                </label>
              </>
            ) : (
              <>
                <label className="block space-y-1.5 text-sm font-medium text-foreground">
                  {uiText("Category")}
                  <Input value={category} onChange={(event) => setCategory(event.target.value)} />
                </label>
                <label className="block space-y-1.5 text-sm font-medium text-foreground">
                  {uiText("Note")}
                  <Input value={note} onChange={(event) => setNote(event.target.value)} />
                </label>
              </>
            )}
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setMode(null)} disabled={busy}>
                {uiText("Cancel")}
              </Button>
              <Button
                onClick={() => void submit()}
                loading={busy}
                disabled={mode === "window" ? !startsAt || !endsAt : !note.trim()}
              >
                {uiText("Save")}
              </Button>
            </div>
          </div>
        </Modal>
        {printers.length === 0 ? (
          <EmptyState
            icon={Wrench}
            title={uiText("No printers to maintain")}
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
                          ? printer.drain_reason || uiText("Soft drain active")
                          : uiText("Accepting scheduled work")}
                      </p>
                    </div>
                    <div className="flex gap-1">
                      {printer.is_default && <Badge>{uiText("Default")}</Badge>}
                      {printer.drain_mode && <Badge variant="warning">{uiText("Draining")}</Badge>}
                    </div>
                  </div>
                  <div className="mt-4 flex flex-wrap gap-2">
                    <Button
                      size="xs"
                      variant="outline"
                      onClick={() => void toggleRouting(printer, "default")}
                    >
                      {printer.is_default ? uiText("Unset default") : uiText("Set default")}
                    </Button>
                    <Button
                      size="xs"
                      variant="outline"
                      onClick={() => void toggleRouting(printer, "drain")}
                    >
                      {printer.drain_mode ? uiText("Resume routing") : uiText("Soft drain")}
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
                      {uiText("Schedule")}
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
                      {uiText("Log")}
                    </Button>
                  </div>
                  <div className="mt-4 space-y-2 border-t border-border pt-3 text-xs text-muted-foreground">
                    {printerWindows.slice(0, 2).map((row) => (
                      <div key={`w-${row.id}`} className="flex items-center justify-between gap-2">
                        <span>
                          {new Date(row.starts_at).toLocaleString(currentLocale())} ·{" "}
                          {row.reason || uiText("Maintenance")}
                        </span>
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          aria-label={uiText("Delete maintenance window")}
                          onClick={() =>
                            void removeMaintenance("delete-window", printer.id, row.id)
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
                          aria-label={uiText("Delete maintenance log")}
                          onClick={() => void removeMaintenance("delete-log", printer.id, row.id)}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                    ))}
                    {printerWindows.length === 0 && printerLogs.length === 0 && (
                      <p>{uiText("No maintenance activity recorded.")}</p>
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
