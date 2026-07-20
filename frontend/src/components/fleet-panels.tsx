import { useEffect, useMemo, useState } from "react";
import { ArrowDown, ArrowUp, CalendarClock, ListOrdered, RotateCcw, Trash2, Wrench } from "lucide-react";

import {
  cancelFleetJob,
  createMaintenanceLog,
  createMaintenanceWindow,
  deleteMaintenanceLog,
  deleteMaintenanceWindow,
  listMaintenanceLog,
  listMaintenanceWindows,
  retryFleetJob,
  updateFleetJob,
  updatePrinterRouting,
} from "@/lib/api";
import { useFleetQueue, useFleetSummary } from "@/lib/queries";
import { toast } from "@/lib/toast";
import type { MaintenanceLog, MaintenanceWindow, PrinterRead, PrintJobRead } from "@/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { Skeleton } from "@/components/ui/skeleton";
import { Localized } from "@/components/ui/localized";

const ACTIVE = new Set(["uploading", "started", "printing", "paused"]);

export function FleetQueuePanel({ printers }: { printers: PrinterRead[] }) {
  const [historyLimit, setHistoryLimit] = useState(20);
  const queueQuery = useFleetQueue({ refetchInterval: 5_000, historyLimit });
  const summaryQuery = useFleetSummary({ refetchInterval: 5_000 });
  const [cancelTarget, setCancelTarget] = useState<PrintJobRead | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const jobs = queueQuery.data ?? [];
  const printerNames = useMemo(
    () => new Map(printers.map((printer) => [printer.id, printer.name])),
    [printers],
  );
  const queued = jobs.filter((job) => job.state === "queued");
  const active = jobs.filter((job) => ACTIVE.has(job.state));
  const recent = jobs.filter((job) => !ACTIVE.has(job.state) && job.state !== "queued");

  async function mutate(jobId: number, action: () => Promise<unknown>) {
    setBusy(jobId);
    try {
      await action();
      await Promise.all([queueQuery.refetch(), summaryQuery.refetch()]);
    } catch (error) {
      toast.error(error);
    } finally {
      setBusy(null);
    }
  }

  if (queueQuery.isLoading) {
    return <Localized><div className="space-y-3">{Array.from({ length: 3 }).map((_, index) => <Skeleton key={index} className="h-20 w-full" />)}</div></Localized>;
  }
  if (jobs.length === 0) {
    return (
      <Localized><EmptyState
          icon={ListOrdered}
          title="No queued print jobs"
          description="Add G-code from a model’s Send dialog to start building the fleet queue."
          className="rounded-lg border border-border bg-card shadow-sm"
        /></Localized>
    );
  }

  return (
    <Localized><div className="space-y-5">
      <ConfirmModal
        open={cancelTarget !== null}
        onClose={() => setCancelTarget(null)}
        onConfirm={() => {
          if (!cancelTarget) return;
          const id = cancelTarget.id;
          setCancelTarget(null);
          void mutate(id, () => cancelFleetJob(id));
        }}
        title="Cancel queued print?"
        description="This removes work from scheduling. It does not cancel an active printer."
        confirmLabel="Cancel job"
      />
      {summaryQuery.data && (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4" aria-label="Queue summary">
          {[
            ["Queued", summaryQuery.data.queued_jobs],
            ["Active", summaryQuery.data.active_jobs],
            ["Blocked", summaryQuery.data.attention_jobs],
            ["Draining", summaryQuery.data.draining_printers],
          ].map(([label, value]) => (
            <div key={label} className="rounded-lg border border-border bg-card p-4 shadow-sm">
              <p className="text-xs font-medium text-muted-foreground">{label}</p>
              <p className="mt-2 font-mono text-2xl font-semibold tabular-nums text-foreground">{value}</p>
            </div>
          ))}
        </div>
      )}
      <QueueSection
        title="Queued"
        jobs={queued}
        printerNames={printerNames}
        busy={busy}
        actions={(job, index) => (
          <>
            <Button variant="ghost" size="icon-sm" aria-label={`Move ${job.remote_filename} up`} disabled={busy === job.id || index === 0} onClick={() => void mutate(job.id, () => updateFleetJob(job.id, { queue_position: index }))}><ArrowUp className="h-3.5 w-3.5" /></Button>
            <Button variant="ghost" size="icon-sm" aria-label={`Move ${job.remote_filename} down`} disabled={busy === job.id || index === queued.length - 1} onClick={() => void mutate(job.id, () => updateFleetJob(job.id, { queue_position: index + 2 }))}><ArrowDown className="h-3.5 w-3.5" /></Button>
            <Button variant="ghost" size="icon-sm" aria-label={`Cancel ${job.remote_filename}`} disabled={busy === job.id} onClick={() => setCancelTarget(job)}><Trash2 className="h-3.5 w-3.5" /></Button>
          </>
        )}
      />
      <QueueSection title="Active" jobs={active} printerNames={printerNames} busy={busy} />
      <QueueSection
        title="Recent"
        jobs={recent}
        printerNames={printerNames}
        busy={busy}
        actions={(job) => job.retryable ? <Button variant="outline" size="xs" disabled={busy === job.id} onClick={() => void mutate(job.id, () => retryFleetJob(job.id))}><RotateCcw className="h-3.5 w-3.5" />Retry</Button> : null}
      />
      {recent.length >= historyLimit && historyLimit < 100 && (
        <div className="flex justify-center">
          <Button variant="outline" size="sm" onClick={() => setHistoryLimit((value) => Math.min(value + 20, 100))}>
            Load older jobs
          </Button>
        </div>
      )}
    </div></Localized>
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
  return (
    <Localized><section className="space-y-2" aria-label={`${title} print jobs`}>
      <h2 className="text-sm font-semibold text-foreground">{title}</h2>
      <div className="overflow-hidden rounded-lg border border-border bg-card shadow-sm">
        {jobs.map((job, index) => (
          <div key={job.id} className="flex items-center gap-3 border-b border-border px-4 py-3 last:border-b-0">
            <span className="w-7 shrink-0 font-mono text-xs tabular-nums text-muted-foreground">{job.state === "queued" ? job.queue_position : "—"}</span>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium text-foreground">{job.remote_filename}</p>
              <p className="mt-0.5 truncate text-xs text-muted-foreground">
                {job.printer_id ? printerNames.get(job.printer_id) ?? `Printer ${job.printer_id}` : "Unassigned"} · {job.routing_strategy.replace("_", " ")}
                {job.blocked_reason ? ` · ${job.blocked_reason.replaceAll("_", " ")}` : ""}
              </p>
            </div>
            <Badge variant={job.blocked_reason || job.state === "failed" ? "warning" : "outline"}>{job.state}</Badge>
            <div className="flex items-center gap-1" aria-busy={busy === job.id}>{actions?.(job, index)}</div>
          </div>
        ))}
      </div>
    </section></Localized>
  );
}

export function FleetMaintenancePanel({
  printers,
  onPrintersChanged,
}: {
  printers: PrinterRead[];
  onPrintersChanged: () => void;
}) {
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
      Promise.all(printers.map((printer) => listMaintenanceWindows(printer.id))),
      Promise.all(printers.map((printer) => listMaintenanceLog(printer.id))),
    ]);
    setWindows(allWindows.flat());
    setLogs(allLogs.flat());
  }
  useEffect(() => { void load().catch(toast.error); }, [printers]); // eslint-disable-line react-hooks/exhaustive-deps

  async function toggleRouting(printer: PrinterRead, field: "default" | "drain") {
    try {
      await updatePrinterRouting(printer.id, field === "default" ? { is_default: !printer.is_default } : { drain_mode: !printer.drain_mode, drain_reason: printer.drain_mode ? null : "Manual soft drain" });
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
        await createMaintenanceWindow(selected.id, { starts_at: new Date(startsAt).toISOString(), ends_at: new Date(endsAt).toISOString(), reason: reason || null });
      } else {
        await createMaintenanceLog(selected.id, { category, note });
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
    <Localized><div className="space-y-5">
      <Modal open={mode !== null} onClose={() => { if (!busy) setMode(null); }} title={mode === "window" ? "Schedule maintenance" : "Log maintenance"}>
        <div className="space-y-4">
          {mode === "window" ? (
            <>
              <label className="block space-y-1.5 text-sm font-medium text-foreground">Starts<Input type="datetime-local" value={startsAt} onChange={(event) => setStartsAt(event.target.value)} /></label>
              <label className="block space-y-1.5 text-sm font-medium text-foreground">Ends<Input type="datetime-local" value={endsAt} onChange={(event) => setEndsAt(event.target.value)} /></label>
              <label className="block space-y-1.5 text-sm font-medium text-foreground">Reason<Input value={reason} onChange={(event) => setReason(event.target.value)} /></label>
            </>
          ) : (
            <>
              <label className="block space-y-1.5 text-sm font-medium text-foreground">Category<Input value={category} onChange={(event) => setCategory(event.target.value)} /></label>
              <label className="block space-y-1.5 text-sm font-medium text-foreground">Note<Input value={note} onChange={(event) => setNote(event.target.value)} /></label>
            </>
          )}
          <div className="flex justify-end gap-2"><Button variant="outline" onClick={() => setMode(null)} disabled={busy}>Cancel</Button><Button onClick={() => void submit()} loading={busy} disabled={mode === "window" ? !startsAt || !endsAt : !note.trim()}>Save</Button></div>
        </div>
      </Modal>
      {printers.length === 0 ? <EmptyState icon={Wrench} title="No printers to maintain" className="rounded-lg border border-border bg-card" /> : (
        <div className="grid gap-4 lg:grid-cols-2">
          {printers.map((printer) => {
            const printerWindows = windows.filter((row) => row.printer_id === printer.id);
            const printerLogs = logs.filter((row) => row.printer_id === printer.id);
            return (
              <section key={printer.id} className="rounded-lg border border-border bg-card p-4 shadow-sm">
                <div className="flex items-start justify-between gap-3"><div><h2 className="font-semibold text-foreground">{printer.name}</h2><p className="mt-1 text-xs text-muted-foreground">{printer.drain_mode ? printer.drain_reason || "Soft drain active" : "Accepting scheduled work"}</p></div><div className="flex gap-1">{printer.is_default && <Badge>Default</Badge>}{printer.drain_mode && <Badge variant="warning">Draining</Badge>}</div></div>
                <div className="mt-4 flex flex-wrap gap-2"><Button size="xs" variant="outline" onClick={() => void toggleRouting(printer, "default")}>{printer.is_default ? "Unset default" : "Set default"}</Button><Button size="xs" variant="outline" onClick={() => void toggleRouting(printer, "drain")}>{printer.drain_mode ? "Resume routing" : "Soft drain"}</Button><Button size="xs" variant="outline" onClick={() => { setSelected(printer); setMode("window"); }}><CalendarClock className="h-3.5 w-3.5" />Schedule</Button><Button size="xs" variant="outline" onClick={() => { setSelected(printer); setMode("log"); }}><Wrench className="h-3.5 w-3.5" />Log</Button></div>
                <div className="mt-4 space-y-2 border-t border-border pt-3 text-xs text-muted-foreground">
                  {printerWindows.slice(0, 2).map((row) => <div key={`w-${row.id}`} className="flex items-center justify-between gap-2"><span>{new Date(row.starts_at).toLocaleString()} · {row.reason || "Maintenance"}</span><Button variant="ghost" size="icon-sm" aria-label="Delete maintenance window" onClick={() => void deleteMaintenanceWindow(printer.id, row.id).then(load).catch(toast.error)}><Trash2 className="h-3.5 w-3.5" /></Button></div>)}
                  {printerLogs.slice(0, 2).map((row) => <div key={`l-${row.id}`} className="flex items-center justify-between gap-2"><span>{row.category} · {row.note}</span><Button variant="ghost" size="icon-sm" aria-label="Delete maintenance log" onClick={() => void deleteMaintenanceLog(printer.id, row.id).then(load).catch(toast.error)}><Trash2 className="h-3.5 w-3.5" /></Button></div>)}
                  {printerWindows.length === 0 && printerLogs.length === 0 && <p>No maintenance activity recorded.</p>}
                </div>
              </section>
            );
          })}
        </div>
      )}
    </div></Localized>
  );
}
