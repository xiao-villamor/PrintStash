/**
 * Settings → Background work: what every job-running process is doing.
 *
 * One framed work surface (DESIGN.md): current activity and the path to a
 * model's own preview status come first. Queue tools and worker controls are
 * separate views below. It refreshes when the events socket reports a Job
 * change, and on a slow interval as a fallback, because a notice can be dropped.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  AlertTriangle,
  Bell,
  CheckCircle2,
  ChevronDown,
  Cpu,
  Images,
  Layers,
  RefreshCw,
  Server,
  SlidersHorizontal,
} from "lucide-react";
import { Link } from "react-router-dom";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { TabBar } from "@/components/ui/tabs";
import {
  cancelQueuedJobs,
  cancelJob,
  getWorkOverview,
  listWorkJobs,
  regenerateDerivatives,
  retryJob,
  setLaneConcurrency,
} from "@/lib/api";
import { subscribeEvents } from "@/lib/events";
import { getErrorMessage, userMessage } from "@/lib/errors";
import { useUiLocale } from "@/lib/i18n";
import { currentLocale, uiText } from "@/lib/locale";
import { toast } from "@/lib/toast";
import type { DerivativeKind, JobStatus, WorkDefinition, WorkLane, WorkOverview } from "@/types";

/** The panel's server boundary, so a test drives it without a fetch layer. */
export interface BackgroundWorkApi {
  overview: () => Promise<WorkOverview>;
  jobs: () => Promise<JobStatus[]>;
  cancelJob: (jobId: string) => Promise<JobStatus>;
  setLane: (lane: string, concurrency: number | null) => Promise<WorkOverview>;
  cancelQueued: (definition: string) => Promise<{ cancelled: number }>;
  regenerate: (
    kind: string,
    mode: "missing" | "all",
  ) => Promise<{ kind: string; mode: "missing" | "all" }>;
  retry: (jobId: string) => Promise<JobStatus>;
}

const WORK_API: BackgroundWorkApi = {
  overview: getWorkOverview,
  jobs: listWorkJobs,
  cancelJob,
  setLane: setLaneConcurrency,
  cancelQueued: cancelQueuedJobs,
  regenerate: regenerateDerivatives,
  retry: retryJob,
};

/** A fallback refresh for dropped notices; the socket is the primary signal. */
const REFRESH_MS = 10_000;

function formatDateTime(value: string): string {
  return new Intl.DateTimeFormat(currentLocale(), {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function derivativeLabel(kind: DerivativeKind): string {
  switch (kind) {
    case "metadata":
      return uiText("File details");
    case "thumbnail":
      return uiText("Model images");
    case "toolpath":
      return uiText("Toolpath previews");
  }
}

type Pending =
  | { kind: "cancel"; definition: WorkDefinition }
  | { kind: "cancel-job"; job: JobStatus }
  | { kind: "regenerate"; derivative: DerivativeKind };

function SectionHeader({
  icon: Icon,
  title,
  description,
}: {
  icon: typeof Activity;
  title: string;
  description: string;
}) {
  return (
    <div className="flex items-start gap-3 border-b px-4 py-4 sm:px-5">
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-muted">
        <Icon className="h-4 w-4 text-muted-foreground" aria-hidden />
      </div>
      <div className="min-w-0">
        <h3 className="text-sm font-semibold">{title}</h3>
        <p className="text-xs text-muted-foreground">{description}</p>
      </div>
    </div>
  );
}

function LaneRow({
  lane,
  busy,
  onSave,
}: {
  lane: WorkLane;
  busy: boolean;
  onSave: (concurrency: number | null) => void;
}) {
  // The row is keyed by its saved concurrency, so a saved change remounts it
  // with a fresh draft instead of syncing the draft from an effect.
  const [draft, setDraft] = useState(String(lane.concurrency));
  const parsed = Number(draft);
  const valid = Number.isInteger(parsed) && parsed >= 1 && parsed <= 64;
  return (
    <li className="flex flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center sm:px-5">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-sm">{lane.name}</span>
          {lane.overridden && <Badge variant="secondary">{uiText("Overridden")}</Badge>}
          {lane.partitioned && <Badge variant="outline">{uiText("Per partition")}</Badge>}
        </div>
        <p className="text-xs text-muted-foreground">
          {uiText("{running} running · {queued} queued · default {default}", {
            running: lane.running,
            queued: lane.queued,
            default: lane.default_concurrency,
          })}
        </p>
      </div>
      <div className="flex items-center gap-2">
        <Input
          type="number"
          min={1}
          max={64}
          className="w-20"
          value={draft}
          aria-label={uiText("Concurrency for {lane}", { lane: lane.name })}
          onChange={(event) => setDraft(event.target.value)}
        />
        <Button
          size="sm"
          variant="outline"
          disabled={!valid || parsed === lane.concurrency || busy}
          onClick={() => onSave(parsed)}
        >
          {uiText("Save")}
        </Button>
        {lane.overridden && (
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => onSave(null)}>
            {uiText("Reset")}
          </Button>
        )}
      </div>
    </li>
  );
}

export function BackgroundWorkPanel({ api = WORK_API }: { api?: BackgroundWorkApi }) {
  useUiLocale();
  const [overview, setOverview] = useState<WorkOverview | null>(null);
  const [activeJobs, setActiveJobs] = useState<JobStatus[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const [activeView, setActiveView] = useState<"types" | "workers">("types");
  const advancedRef = useRef<HTMLDetailsElement>(null);

  const refresh = useCallback(() => {
    Promise.all([api.overview(), api.jobs()])
      .then(([nextOverview, nextJobs]) => {
        setOverview(nextOverview);
        setActiveJobs(
          nextJobs.filter(
            (job) =>
              job.state === "queued" || job.state === "running" || job.state === "interrupted",
          ),
        );
        setError(null);
      })
      .catch((cause: unknown) => setError(userMessage(cause)));
  }, [api]);

  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, REFRESH_MS);
    const stop = subscribeEvents((notice) => {
      if (notice.type === "job" || notice.type === "resync") refresh();
    });
    return () => {
      window.clearInterval(timer);
      stop();
    };
  }, [refresh]);

  const derivativeKinds = useMemo(
    () => [...new Set((overview?.definitions ?? []).flatMap((d) => d.derivative_kinds))].sort(),
    [overview],
  );
  const running = overview?.definitions.reduce((total, d) => total + d.running, 0);
  const waiting = overview?.definitions.reduce((total, d) => total + d.queued, 0);
  const staleWorkers = overview?.executors.filter((executor) => executor.stale).length;

  async function act(key: string, work: () => Promise<void>) {
    setBusy(key);
    try {
      await work();
    } catch (cause) {
      toast.error(cause);
    } finally {
      setBusy(null);
    }
  }

  function saveLane(lane: WorkLane, concurrency: number | null) {
    void act(`lane:${lane.name}`, async () => {
      setOverview(await api.setLane(lane.name, concurrency));
      toast.success(uiText("Lane {lane} updated", { lane: lane.name }));
    });
  }

  function confirmPending() {
    const action = pending;
    if (!action) return;
    void act("confirm", async () => {
      if (action.kind === "cancel") {
        const { cancelled } = await api.cancelQueued(action.definition.name);
        toast.success(uiText("{count} queued Jobs cancelled", { count: cancelled }));
      } else if (action.kind === "cancel-job") {
        await api.cancelJob(action.job.job_id);
        toast.success(uiText("Job cancelled"));
      } else {
        await api.regenerate(action.derivative, "all");
        toast.success(uiText("Re-deriving every {kind}", { kind: action.derivative }));
      }
      setPending(null);
      refresh();
    });
  }

  function deriveMissing(kind: DerivativeKind) {
    void act(`missing:${kind}`, async () => {
      await api.regenerate(kind, "missing");
      toast.success(uiText("Deriving missing {kind}", { kind }));
      refresh();
    });
  }

  function retry(job: JobStatus) {
    void act(`retry:${job.job_id}`, async () => {
      await api.retry(job.job_id);
      toast.success(uiText("Job queued again"));
      refresh();
    });
  }

  return (
    <Card className="overflow-hidden animate-panel-in">
      <div className="flex items-center justify-between gap-3 border-b bg-muted/30 p-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold">{uiText("Background work")}</h2>
          <p className="text-xs text-muted-foreground">
            {uiText("See work that continues while you use PrintStash.")}
          </p>
        </div>
        <Button size="sm" variant="outline" onClick={refresh} aria-label={uiText("Refresh")}>
          <RefreshCw className="h-4 w-4" aria-hidden />
        </Button>
      </div>

      {error && (
        <div role="alert" className="flex items-center gap-2 border-b px-4 py-3 text-sm sm:px-5">
          <AlertTriangle className="h-4 w-4 text-warning" aria-hidden />
          {error}
        </div>
      )}

      {!overview && !error ? (
        <div className="space-y-3 p-4 sm:p-5" aria-busy="true">
          <Skeleton className="h-6 w-1/3" />
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-full" />
        </div>
      ) : overview ? (
        <>
          <SectionHeader
            icon={Activity}
            title={uiText("What's happening now")}
            description={uiText("A quick view of work across your vault.")}
          />
          <div className="grid grid-cols-3 gap-2 border-b px-4 py-4 sm:gap-3 sm:px-5">
            <div className="flex flex-col items-start gap-1 sm:flex-row sm:items-center sm:gap-3">
              <Activity className="h-4 w-4 shrink-0 text-primary" aria-hidden />
              <div>
                <p className="text-lg font-semibold leading-tight">{running}</p>
                <p className="text-xs text-muted-foreground">{uiText("Running now")}</p>
              </div>
            </div>
            <div className="flex flex-col items-start gap-1 sm:flex-row sm:items-center sm:gap-3">
              <Layers className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
              <div>
                <p className="text-lg font-semibold leading-tight">{waiting}</p>
                <p className="text-xs text-muted-foreground">{uiText("Waiting to start")}</p>
              </div>
            </div>
            <div className="flex flex-col items-start gap-1 sm:flex-row sm:items-center sm:gap-3">
              {overview.failed_jobs.length > 0 ? (
                <AlertTriangle className="h-4 w-4 shrink-0 text-warning" aria-hidden />
              ) : (
                <CheckCircle2 className="h-4 w-4 shrink-0 text-success" aria-hidden />
              )}
              <div>
                <p className="text-lg font-semibold leading-tight">{overview.failed_jobs.length}</p>
                <p className="text-xs text-muted-foreground">{uiText("Recent failures")}</p>
              </div>
            </div>
          </div>

          <SectionHeader
            icon={Images}
            title={uiText("Checking a model's preview?")}
            description={uiText("This page shows totals for the whole vault.")}
          />
          <div className="space-y-3 border-b px-4 py-4 text-sm sm:px-5">
            <p>
              {uiText(
                "Open the model and look in Files. Preparing or failed previews appear beside the file, with a retry option when available.",
              )}
            </p>
            <p className="flex items-start gap-2 text-xs text-muted-foreground">
              <Bell className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
              {uiText("Uploads and imports also appear in the Tasks menu.")}
            </p>
            <Link
              to="/"
              className="inline-flex rounded text-sm font-medium text-primary underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {uiText("Browse models")}
            </Link>
          </div>

          <SectionHeader
            icon={Activity}
            title={uiText("In progress")}
            description={uiText("Work that is running or waiting to start.")}
          />
          {activeJobs.length > 0 && (
            <ul className="divide-y divide-border border-b">
              {activeJobs.map((job) => (
                <li
                  key={job.job_id}
                  className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-start sm:px-5"
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-medium">{job.label ?? job.kind}</span>
                      <Badge variant={job.state === "running" ? "secondary" : "outline"}>
                        {job.state === "running"
                          ? uiText("Running now")
                          : uiText("Waiting to start")}
                      </Badge>
                    </div>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {job.current_item ??
                        (job.stage ? uiText(job.stage) : uiText("Waiting for a worker"))}
                      {job.total !== null ? ` · ${job.processed} / ${job.total}` : ""}
                      {job.progress !== null ? ` · ${Math.round(job.progress)}%` : ""}
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {job.started_at
                        ? uiText("Started {when}", { when: formatDateTime(job.started_at) })
                        : uiText("Queued {when}", { when: formatDateTime(job.created_at) })}
                    </p>
                    {job.model_id !== null && (
                      <Link
                        to={`/models/${job.model_id}`}
                        className="mt-1 inline-block text-xs font-medium text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      >
                        {uiText("Open model")}
                      </Link>
                    )}
                    {job.progress !== null && (
                      <div
                        role="progressbar"
                        aria-label={job.label ?? job.kind}
                        aria-valuenow={job.progress}
                        aria-valuemin={0}
                        aria-valuemax={100}
                        className="mt-2 h-1.5 overflow-hidden rounded bg-muted"
                      >
                        <div className="h-full bg-primary" style={{ width: `${job.progress}%` }} />
                      </div>
                    )}
                  </div>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => setPending({ kind: "cancel-job", job })}
                  >
                    {uiText("Cancel job")}
                  </Button>
                </li>
              ))}
            </ul>
          )}
          {activeJobs.length === 0 && (
            <p className="border-b px-4 py-4 text-sm text-muted-foreground sm:px-5">
              {uiText("Nothing is running or waiting right now.")}
            </p>
          )}

          {staleWorkers ? (
            <div
              role="alert"
              className="flex flex-wrap items-center gap-2 border-b px-4 py-3 text-sm sm:px-5"
            >
              <AlertTriangle className="h-4 w-4 text-warning" aria-hidden />
              <span>{uiText("Workers not responding: {count}.", { count: staleWorkers })}</span>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  setActiveView("workers");
                  if (advancedRef.current) advancedRef.current.open = true;
                }}
              >
                {uiText("View workers")}
              </Button>
            </div>
          ) : null}

          <SectionHeader
            icon={AlertTriangle}
            title={uiText("Recent failures")}
            description={uiText(
              "Preview or metadata failures across the library: {count}. Review and retry recent work here.",
              { count: overview.failed_derivatives },
            )}
          />
          {overview.failed_jobs.length === 0 ? (
            <p className="border-b px-4 py-4 text-sm text-muted-foreground sm:px-5">
              {uiText("No recent failures.")}
            </p>
          ) : (
            <ul className="divide-y divide-border border-b">
              {overview.failed_jobs.map((job) => (
                <li
                  key={job.job_id}
                  className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:px-5"
                >
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium">{job.label ?? job.kind}</p>
                    <p className="text-xs text-muted-foreground">
                      {getErrorMessage(job.error ?? "unknown")}
                    </p>
                  </div>
                  {job.retryable && (
                    <Button
                      size="sm"
                      variant="outline"
                      loading={busy === `retry:${job.job_id}`}
                      onClick={() => retry(job)}
                    >
                      {uiText("Retry")}
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          )}

          <details ref={advancedRef} className="group">
            <summary className="flex cursor-pointer list-none items-start gap-3 px-4 py-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:px-5">
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-muted">
                <SlidersHorizontal className="h-4 w-4 text-muted-foreground" aria-hidden />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-semibold">{uiText("Advanced controls")}</span>
                <span className="block text-xs text-muted-foreground">
                  {uiText("Review every queue, rebuild previews, or adjust worker limits.")}
                </span>
              </span>
              <ChevronDown
                className="mt-2 h-4 w-4 shrink-0 text-muted-foreground group-open:rotate-180"
                aria-hidden
              />
            </summary>
            <div className="border-y bg-muted/30 p-3">
              <TabBar
                tabs={[
                  { key: "types", label: uiText("All work types") },
                  { key: "workers", label: uiText("Worker settings") },
                ]}
                active={activeView}
                onChange={setActiveView}
                className="inline-flex gap-1 rounded-md bg-muted p-1"
                tabClassName="rounded-sm px-3 py-1.5 text-xs font-medium text-muted-foreground transition-[color,background-color,transform] duration-press active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                activeTabClassName="bg-accent text-accent-foreground"
                showIndicator={false}
              />
            </div>

            {activeView === "workers" ? (
              <>
                <SectionHeader
                  icon={Layers}
                  title={uiText("How many jobs run at once")}
                  description={uiText(
                    "Advanced limits for each work lane. Changes apply to every worker immediately.",
                  )}
                />
                <ul className="divide-y divide-border border-b">
                  {overview.lanes.map((lane) => (
                    <LaneRow
                      key={`${lane.name}:${lane.concurrency}`}
                      lane={lane}
                      busy={busy === `lane:${lane.name}`}
                      onSave={(concurrency) => saveLane(lane, concurrency)}
                    />
                  ))}
                </ul>

                <SectionHeader
                  icon={Server}
                  title={uiText("Worker processes")}
                  description={uiText("The processes currently handling background work.")}
                />
                <ul className="divide-y divide-border">
                  {overview.executors.map((executor) => (
                    <li
                      key={executor.executor_id}
                      className="flex flex-wrap items-center gap-2 px-4 py-3 sm:px-5"
                    >
                      <span className="text-sm font-medium">{executor.hostname}</span>
                      <Badge variant="outline">{executor.role}</Badge>
                      <span className="font-mono text-2xs text-muted-foreground">
                        {uiText("v{value1}", { value1: executor.app_version })}
                      </span>
                      {executor.stale ? (
                        <Badge variant="warning">{uiText("Not responding")}</Badge>
                      ) : (
                        <Badge variant="success">{uiText("Healthy")}</Badge>
                      )}
                    </li>
                  ))}
                </ul>
              </>
            ) : (
              <>
                <SectionHeader
                  icon={Activity}
                  title={uiText("Queues by type")}
                  description={uiText("Every kind of work, including ones with nothing waiting.")}
                />
                <ul className="divide-y divide-border border-b">
                  {overview.definitions.map((definition) => (
                    <li
                      key={definition.name}
                      className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:px-5"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-sm font-medium">{definition.label}</span>
                          <span className="font-mono text-2xs text-muted-foreground">
                            {definition.lane}
                          </span>
                          {definition.failed > 0 && (
                            <Badge variant="warning">
                              {uiText("{count} failed", { count: definition.failed })}
                            </Badge>
                          )}
                        </div>
                        <p className="text-xs text-muted-foreground">
                          {uiText("{running} running · {queued} queued · {completed} completed", {
                            running: definition.running,
                            queued: definition.queued,
                            completed: definition.completed,
                          })}
                          {definition.next_due_at
                            ? ` · ${uiText("next {when}", { when: formatDateTime(definition.next_due_at) })}`
                            : ""}
                        </p>
                      </div>
                      {definition.queued > 0 && (
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => setPending({ kind: "cancel", definition })}
                        >
                          {uiText("Cancel queued")}
                        </Button>
                      )}
                    </li>
                  ))}
                </ul>

                {derivativeKinds.length > 0 && (
                  <>
                    <SectionHeader
                      icon={Cpu}
                      title={uiText("Rebuild previews and details")}
                      description={uiText(
                        "Previews and metadata derived from each Artifact. Current outputs stay visible until their replacements are ready.",
                      )}
                    />
                    <ul className="divide-y divide-border border-b">
                      {derivativeKinds.map((kind) => (
                        <li
                          key={kind}
                          className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:px-5"
                        >
                          <span className="min-w-0 flex-1">
                            <span className="block text-sm font-medium">
                              {derivativeLabel(kind)}
                            </span>
                            <span className="font-mono text-2xs text-muted-foreground">{kind}</span>
                          </span>
                          <div className="flex gap-2">
                            <Button
                              size="sm"
                              variant="outline"
                              loading={busy === `missing:${kind}`}
                              onClick={() => deriveMissing(kind)}
                            >
                              {uiText("Derive missing")}
                            </Button>
                            <Button
                              size="sm"
                              variant="outline"
                              onClick={() => setPending({ kind: "regenerate", derivative: kind })}
                            >
                              {uiText("Regenerate all")}
                            </Button>
                          </div>
                        </li>
                      ))}
                    </ul>
                  </>
                )}
              </>
            )}
          </details>
        </>
      ) : null}

      <ConfirmModal
        open={pending !== null}
        onClose={() => setPending(null)}
        onConfirm={confirmPending}
        busy={busy === "confirm"}
        title={
          pending?.kind === "cancel"
            ? uiText("Cancel queued {label} Jobs?", { label: pending.definition.label })
            : pending?.kind === "cancel-job"
              ? uiText("Cancel {label}?", { label: pending.job.label ?? pending.job.kind })
              : uiText("Regenerate every {kind}?", {
                  kind: pending?.kind === "regenerate" ? pending.derivative : "",
                })
        }
        description={
          pending?.kind === "cancel"
            ? uiText("Jobs that have not started are withdrawn. Running Jobs finish normally.")
            : pending?.kind === "cancel-job"
              ? uiText("This stops the selected job and releases its pending work.")
              : uiText(
                  "Every Artifact is derived again in the background. Current outputs stay visible until their replacements are ready.",
                )
        }
        confirmLabel={
          pending?.kind === "cancel"
            ? uiText("Cancel queued")
            : pending?.kind === "cancel-job"
              ? uiText("Cancel job")
              : uiText("Regenerate")
        }
      />
    </Card>
  );
}
