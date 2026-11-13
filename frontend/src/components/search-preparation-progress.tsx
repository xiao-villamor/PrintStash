import { AlertTriangle, Check, ChevronDown } from "lucide-react";
import { formatDuration, timeAgo } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import { isMessageKey } from "@/lib/locale";
import type { SearchGeneration } from "@/types/search";

/** Phases a building text index passes through, in the order the indexer runs them. */
const PREPARATION_PHASES = [
  "reconcile",
  "backfill",
  "final_reconcile",
  "verify",
  "verify_failed",
  "ready",
] as const;
type PreparationPhase = (typeof PREPARATION_PHASES)[number];

function preparationPhase(phase: string): PreparationPhase {
  const known = PREPARATION_PHASES.find((candidate) => candidate === phase);
  if (!known) throw new Error("search_generation_phase_invalid");
  return known;
}

const STAGES = [
  { key: "aiSearch.stage.reconcile", phases: ["reconcile"] },
  { key: "aiSearch.stage.backfill", phases: ["backfill"] },
  { key: "aiSearch.stage.finalReconcile", phases: ["final_reconcile"] },
  { key: "aiSearch.stage.verify", phases: ["verify", "verify_failed"] },
] as const satisfies readonly { key: string; phases: readonly PreparationPhase[] }[];

const PHASE_HELP = {
  reconcile: "aiSearch.phaseHelp.reconcile",
  backfill: "aiSearch.phaseHelp.backfill",
  final_reconcile: "aiSearch.phaseHelp.finalReconcile",
  verify: "aiSearch.phaseHelp.verify",
  verify_failed: "aiSearch.phaseHelp.verifyFailed",
  ready: "aiSearch.phaseHelp.ready",
} as const satisfies Record<PreparationPhase, string>;

type StageState = "done" | "current" | "failed" | "pending";

const STAGE_STATE_LABEL = {
  done: "aiSearch.stageState.done",
  current: "aiSearch.stageState.current",
  failed: "aiSearch.stageState.failed",
  pending: "aiSearch.stageState.pending",
} as const satisfies Record<StageState, string>;

/** Quiet progress, with the same neutral treatment in every AI preparation step. */
export function SearchProgressBar({
  label,
  value,
  max,
}: {
  label: string;
  value: number;
  max: number;
}) {
  const limit = Math.max(1, max);
  const current = Math.min(limit, Math.max(0, value));
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={limit}
      aria-valuenow={current}
      className="h-1.5 overflow-hidden bg-muted"
    >
      <div
        className="h-full origin-left bg-muted-foreground"
        style={{ transform: `scaleX(${current / limit})` }}
      />
    </div>
  );
}

/** Keep the current operation and counts visible; implementation details are optional. */
export function SearchPreparationProgress({ generation }: { generation: SearchGeneration }) {
  const { t } = useI18n();
  const phase = preparationPhase(generation.phase);
  const labelKey = `aiSearch.phase.${phase}`;
  if (!isMessageKey(labelKey)) throw new Error("search_generation_phase_invalid");
  const indexing = phase === "backfill";
  const percent = Math.min(
    100,
    Math.round((generation.indexed / Math.max(1, generation.eligible)) * 100),
  );
  return (
    <div className="max-w-prose space-y-3">
      <div className="flex items-baseline justify-between gap-4">
        <p role="status" className="text-sm font-medium">
          {t(labelKey)}
        </p>
        {indexing && (
          <span className="font-mono text-xs tabular-nums text-muted-foreground">{percent}%</span>
        )}
      </div>
      {indexing && (
        <>
          <SearchProgressBar
            label={t("Library preparation progress")}
            value={generation.indexed}
            max={generation.eligible}
          />
          <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 text-xs tabular-nums text-muted-foreground">
            <p>
              {t("{done} of {total} searchable entries prepared", {
                done: generation.indexed,
                total: generation.eligible,
              })}
            </p>
            {generation.eta_seconds !== null && (
              <p>{t("aiSearch.eta", { time: formatDuration(generation.eta_seconds) })}</p>
            )}
          </div>
          {generation.quarantined > 0 && (
            <p className="text-xs text-warning">
              {t("aiSearch.quarantined", { count: generation.quarantined })}
            </p>
          )}
        </>
      )}
      <details className="group border-t border-border pt-3">
        <summary className="flex items-center gap-2 text-xs text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-details-marker]:hidden">
          <ChevronDown className="h-3.5 w-3.5 shrink-0 group-open:rotate-180" aria-hidden />
          {t("aiSearch.preparationDetails")}
        </summary>
        <div className="pt-4">
          <SearchPreparationStages generation={generation} />
        </div>
      </details>
    </div>
  );
}

/** Every step a preparation runs, which one is running now, and what it is doing. */
export function SearchPreparationStages({ generation }: { generation: SearchGeneration }) {
  const { t } = useI18n();
  const phase = preparationPhase(generation.phase);
  const current =
    phase === "ready"
      ? STAGES.length
      : STAGES.findIndex((stage) => stage.phases.some((candidate) => candidate === phase));
  const stateOf = (index: number): StageState =>
    index < current
      ? "done"
      : index > current
        ? "pending"
        : phase === "verify_failed"
          ? "failed"
          : "current";
  return (
    <div className="space-y-3">
      <ol aria-label={t("aiSearch.stages")} className="space-y-2 text-sm">
        {STAGES.map((stage, index) => {
          const state = stateOf(index);
          return (
            <li
              key={stage.key}
              aria-current={state === "current" || state === "failed" ? "step" : undefined}
              className={`flex items-center gap-2 ${
                state === "pending"
                  ? "text-muted-foreground"
                  : state === "failed"
                    ? "font-medium text-warning"
                    : state === "current"
                      ? "font-medium text-foreground"
                      : "text-foreground"
              }`}
            >
              <span
                aria-hidden
                className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-border text-2xs"
              >
                {state === "done" ? (
                  <Check className="h-3 w-3 text-muted-foreground" aria-hidden />
                ) : state === "failed" ? (
                  <AlertTriangle className="h-3 w-3" aria-hidden />
                ) : (
                  index + 1
                )}
              </span>
              {t(stage.key)}
              <span className="sr-only">{t(STAGE_STATE_LABEL[state])}</span>
            </li>
          );
        })}
      </ol>
      <p className="max-w-prose text-sm text-muted-foreground">
        {t(PHASE_HELP[phase], { model: generation.model })}
      </p>
    </div>
  );
}

/** What the last verification established about a text index. */
export function SearchIndexHealth({ generation }: { generation: SearchGeneration }) {
  const { t } = useI18n();
  const rows: { label: string; value: string; tone: "normal" | "warning" }[] = [
    {
      label: t("aiSearch.health.entries"),
      value: t("aiSearch.health.entriesValue", {
        indexed: generation.indexed,
        total: generation.eligible,
      }),
      tone: generation.indexed < generation.eligible ? "warning" : "normal",
    },
    {
      label: t("aiSearch.health.retry"),
      value: String(generation.quarantined),
      tone: generation.quarantined > 0 ? "warning" : "normal",
    },
    {
      label: t("aiSearch.health.truncated"),
      value: String(generation.truncated_count),
      tone: "normal",
    },
    { label: t("aiSearch.health.model"), value: generation.model, tone: "normal" },
    {
      label: t("aiSearch.health.verified"),
      value: generation.verified_at
        ? t("aiSearch.health.verifiedValue", { time: timeAgo(generation.verified_at) })
        : t("aiSearch.health.notVerified"),
      tone: generation.verified_at ? "normal" : "warning",
    },
  ];
  return (
    <section aria-label={t("aiSearch.health.title")} className="border-t border-border">
      <div className="flex items-start gap-3 px-4 pb-3 pt-4 sm:px-5">
        <Check className="h-8 w-8 shrink-0 bg-muted p-2 text-muted-foreground" aria-hidden />
        <div>
          <h4 className="text-sm font-semibold">{t("aiSearch.health.title")}</h4>
          <p className="mt-0.5 max-w-prose text-xs text-muted-foreground">
            {t("aiSearch.health.help")}
          </p>
        </div>
      </div>
      <dl className="px-4 pb-2 text-sm sm:pl-16 sm:pr-5">
        {rows.map((row) => (
          <div
            key={row.label}
            className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 border-b border-border py-3 last:border-0"
          >
            <dt>{row.label}</dt>
            <dd
              className={`min-w-0 break-words text-right font-mono tabular-nums ${row.tone === "warning" ? "text-warning" : ""}`}
            >
              {row.value}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
