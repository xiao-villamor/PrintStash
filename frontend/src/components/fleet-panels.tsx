import { useQueryClient } from "@tanstack/react-query";
import { refreshFleet } from "@/features/printers/queries";
import { knownUiText } from "@/lib/locale";
import { currentLocale } from "@/lib/locale";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import { useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { ArrowDown, ArrowUp, CalendarClock, Pencil, RotateCcw, Trash2, Wrench } from "lucide-react";

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
import { Button } from "@/components/ui/button";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { EmptyState } from "@/components/ui/empty-state";
import { Input, inputClasses } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { Skeleton } from "@/components/ui/skeleton";
import { Localized } from "@/components/ui/localized";
import "./fleet-panels.css";

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
  const [queueView, setQueueView] = useState<"queued" | "active" | "recent">("queued");
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
      (printerFilter === "all" || String(job.printer_id) === printerFilter) &&
      (groupFilter === "all" || groupFilter === group)
    );
  }
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
        <div className="fleet-filter-row">
          <label>
            {uiText("View")}
            <select
              className={inputClasses}
              aria-label={uiText("View")}
              value={queueView}
              onChange={(event) => {
                const next = event.target.value;
                if (next === "queued" || next === "active" || next === "recent") setQueueView(next);
              }}
            >
              <option value="queued">{uiText("Queued")}</option>
              <option value="active">{uiText("Active")}</option>
              <option value="recent">{uiText("Recent")}</option>
            </select>
          </label>
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
            {uiText("Group")}
            <select
              className={inputClasses}
              aria-label={uiText("Group")}
              value={groupFilter}
              onChange={(event) => setGroupFilter(event.target.value)}
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
        {summaryQuery.data?.printers?.some(
          (printer) => printer.current_job_name || printer.next_job_name,
        ) && (
          <div className="border border-border bg-accent px-3.5 py-3 text-xs leading-relaxed text-foreground">
            {summaryQuery.data.printers
              .filter((printer) => printer.current_job_name || printer.next_job_name)
              .map((printer) => (
                <span key={printer.printer_id} className="mr-4 inline-block">
                  {printer.name} · {printer.current_job_name ?? uiText("Idle")}
                  {printer.next_job_name ? ` · ${uiText("Next:")} ${printer.next_job_name}` : ""}
                </span>
              ))}
          </div>
        )}
        {queueView === "queued" && (
          <QueueSection
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
                            updateJob(job.id, { queue_position: laneIndex }),
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
        )}
        {queueView === "active" && (
          <QueueSection
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
        <p className="text-xs leading-relaxed text-muted-foreground">
          {uiText(
            "Held jobs keep their position. Older jobs load on demand. Queue changes do not change a Model’s recommended Revision.",
          )}
        </p>
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
    <div key={job.id} className="fleet-plain-row">
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
          {` · ${knownUiText(job.priority ?? "normal")} · ${knownUiText(job.state)}`}
          {job.state === "queued"
            ? ` · ${uiText("Position {value1}", { value1: String(job.queue_position) })}`
            : ""}
          {job.operator_gate_state === "pending" ? ` · ${uiText("release needed")}` : ""}
          {job.blocked_reason ? ` · ${knownUiText(job.blocked_reason)}` : ""}
        </p>
      </div>
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
        <div className="fleet-list-panel">
          <h2>{uiText("{value1} jobs", { value1: title })}</h2>
          {jobs.length === 0 && (
            <p className="p-5 text-[13px] text-muted-foreground">
              {uiText("No jobs match these filters.")}
            </p>
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
                {uiText("Add log entry")}
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
              const printerWindows = windows.filter(
                (row) =>
                  row.printer_id === printer.id &&
                  (compact ||
                    windowFilter === "all" ||
                    new Date(row.ends_at).getTime() >= Date.now()),
              );
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
                  {printerWindows.map((row) => (
                    <div key={`window-${row.id}`} className="fleet-plain-row">
                      <div>
                        <h3 className="text-[13px] font-medium">
                          {printer.name} · {uiText("Maintenance window")}
                        </h3>
                        <p className="mt-1 text-xs text-muted-foreground">
                          {new Date(row.starts_at).toLocaleString(currentLocale())} –{" "}
                          {new Date(row.ends_at).toLocaleString(currentLocale())}
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
                  {printerLogs.map((row) => (
                    <div key={`log-${row.id}`} className="fleet-plain-row">
                      <div>
                        <h3 className="text-[13px] font-medium">
                          {printer.name} · {row.category}
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
