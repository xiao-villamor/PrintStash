import { useEffect, useState } from "react";
import { useInfiniteQuery, useMutation, useQuery } from "@tanstack/react-query";
import { Box, ScanSearch } from "lucide-react";

import { SimilaritySearch } from "@/components/similarity-search";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { EmptyState } from "@/components/ui/empty-state";
import {
  findModelSimilar,
  getSimilarityRun,
  getSimilarityStatus,
  listSimilarityCandidates,
} from "@/lib/api/similarity";
import { listCollections } from "@/lib/api/taxonomy";
import { useI18n } from "@/lib/i18n";
import { Link } from "@/lib/link";
import { evidenceDescription, evidenceLabel, isSimilarityRunActive } from "@/lib/similarity";
import { toast } from "@/lib/toast";
import { useAuthenticatedAssetUrl } from "@/lib/use-authenticated-asset-url";
import {
  EVIDENCE_CLASSES,
  type ReviewState,
  type SimilarityCandidate,
  type SimilarityFilters,
  type SimilarityModel,
} from "@/types/similarity";

const REVIEW_STATES: ReviewState[] = ["open", "confirmed", "later", "rejected"];
const FILTER_DEBOUNCE_MS = 250;

function ModelLabel({ model }: { model: SimilarityModel }) {
  const image = useAuthenticatedAssetUrl(
    model.thumbnail_file_id ? `/api/v1/files/${model.thumbnail_file_id}/thumbnail` : null,
  );
  return (
    <Link
      href={`/models/${model.id}`}
      className="flex min-w-0 items-center gap-3 rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <span className="flex h-10 w-10 sm:h-12 sm:w-12 shrink-0 items-center justify-center overflow-hidden rounded-md bg-muted">
        {image ? (
          <img src={image} alt="" className="h-full w-full object-contain" />
        ) : (
          <Box className="h-6 w-6 text-muted-foreground" aria-hidden />
        )}
      </span>
      <span className="min-w-0 break-words text-sm font-medium">{model.name}</span>
    </Link>
  );
}

export function SimilarityRow({ candidate }: { candidate: SimilarityCandidate }) {
  const { t } = useI18n();
  return (
    <li className="grid gap-3 px-4 py-4 sm:px-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1.3fr)_auto] lg:items-center">
      <div className="grid grid-cols-2 gap-3 lg:contents">
        <ModelLabel model={candidate.model_a} />
        <ModelLabel model={candidate.model_b} />
      </div>
      <div className="min-w-0 space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="outline">{evidenceLabel(candidate.evidence_class)}</Badge>
          <span className="font-mono text-xs tabular-nums text-muted-foreground">
            {Math.round(candidate.confidence * 100)}%
          </span>
          {candidate.freshness === "stale" && (
            <Badge variant="outline">{t("similarity.stale")}</Badge>
          )}
        </div>
        <p className="text-xs leading-relaxed text-muted-foreground">
          {evidenceDescription(candidate)}
        </p>
      </div>
      <Button asChild variant="outline" size="sm" className="justify-self-start">
        <Link href={`/library/similar/${candidate.id}`}>{t("similarity.compare")}</Link>
      </Button>
    </li>
  );
}

export function SimilarityQueue({ modelId }: { modelId?: number }) {
  const { t } = useI18n();
  const [filters, setFilters] = useState<SimilarityFilters>({
    review_state: "open",
    freshness: "current",
  });
  const [threshold, setThreshold] = useState<number | null>(null);
  const [runId, setRunId] = useState<number | null>(null);
  const status = useQuery({ queryKey: ["similarity", "status"], queryFn: getSimilarityStatus });
  const collections = useQuery({
    queryKey: ["similarity", "collections"],
    queryFn: () => listCollections(),
    enabled: modelId === undefined,
  });
  const run = useQuery({
    queryKey: ["similarity", "run", runId],
    queryFn: () => getSimilarityRun(runId ?? 0),
    enabled: runId !== null,
    refetchInterval: (query) =>
      !query.state.data || isSimilarityRunActive(query.state.data) ? 1500 : false,
  });
  const queue = useInfiniteQuery({
    queryKey: ["similarity", "candidates", modelId, filters],
    queryFn: ({ pageParam }: { pageParam: string | null }) =>
      listSimilarityCandidates({
        ...filters,
        model_id: modelId,
        cursor: pageParam ?? undefined,
        limit: 25,
      }),
    initialPageParam: null,
    getNextPageParam: (page) => page.next_cursor,
    refetchInterval: run.data && isSimilarityRunActive(run.data) ? 1500 : false,
  });
  const find = useMutation({
    mutationFn: () => findModelSimilar(modelId ?? 0),
    onSuccess: (result) => {
      setRunId(result.run.id);
      void queue.refetch();
    },
    onError: toast.error,
  });
  useEffect(() => {
    if (threshold === null) return;
    const timer = setTimeout(
      () => setFilters((current) => ({ ...current, minimum_confidence: threshold / 100 })),
      FILTER_DEBOUNCE_MS,
    );
    return () => clearTimeout(timer);
  }, [threshold]);
  const { refetch: refreshCandidates } = queue;
  const runFinished = run.data ? !isSimilarityRunActive(run.data) : false;
  useEffect(() => {
    if (runFinished) void refreshCandidates();
  }, [runFinished, refreshCandidates]);
  const rows = queue.data?.pages.flatMap((page) => page.items) ?? [];
  return (
    <>
      <SimilaritySearch modelId={modelId} />
      <Card className="overflow-hidden">
        <div className="flex flex-wrap items-end gap-3 border-b bg-muted/30 p-3">
          <label className="space-y-1 text-xs">
            {t("similarity.review")}
            <select
              className="block w-full rounded-md border border-input bg-background p-2 text-sm"
              value={filters.review_state}
              onChange={(event) =>
                setFilters({
                  ...filters,
                  review_state: REVIEW_STATES.find((value) => value === event.target.value),
                })
              }
            >
              {REVIEW_STATES.map((state) => (
                <option key={state} value={state}>
                  {t(`similarity.${state}`)}
                </option>
              ))}
            </select>
          </label>
          <label className="space-y-1 text-xs">
            {t("similarity.allClasses")}
            <select
              className="block w-full rounded-md border border-input bg-background p-2 text-sm"
              value={filters.evidence_class ?? ""}
              onChange={(event) =>
                setFilters({
                  ...filters,
                  evidence_class: EVIDENCE_CLASSES.find((value) => value === event.target.value),
                })
              }
            >
              <option value="">{t("similarity.allClasses")}</option>
              {EVIDENCE_CLASSES.map((value) => (
                <option key={value} value={value}>
                  {evidenceLabel(value)}
                </option>
              ))}
            </select>
          </label>
          <label className="space-y-1 text-xs">
            {t("similarity.current")}
            <select
              className="block w-full rounded-md border border-input bg-background p-2 text-sm"
              value={filters.freshness ?? ""}
              onChange={(event) =>
                setFilters({
                  ...filters,
                  freshness:
                    event.target.value === "current"
                      ? "current"
                      : event.target.value === "stale"
                        ? "stale"
                        : undefined,
                })
              }
            >
              <option value="">{t("similarity.allFreshness")}</option>
              <option value="current">{t("similarity.current")}</option>
              <option value="stale">{t("similarity.stale")}</option>
            </select>
          </label>
          {modelId !== undefined && (
            <Button
              onClick={() => find.mutate()}
              loading={find.isPending}
              disabled={!status.data?.enabled}
            >
              {t("similarity.find")}
            </Button>
          )}
          <details className="min-w-0 basis-full">
            <summary className="cursor-pointer text-xs font-medium">
              {t("similarity.advanced")}
            </summary>
            <div className="mt-3 flex flex-wrap items-end gap-3">
              <label className="min-w-48 flex-1 space-y-1 text-xs">
                {t("similarity.threshold")}{" "}
                <output>
                  {threshold ??
                    Math.round((status.data?.selection?.minimum_confidence ?? 0.9) * 100)}
                  %
                </output>
                <input
                  type="range"
                  min={50}
                  max={100}
                  value={
                    threshold ??
                    Math.round((status.data?.selection?.minimum_confidence ?? 0.9) * 100)
                  }
                  aria-label={t("similarity.threshold")}
                  className="block w-full accent-primary"
                  onChange={(event) => setThreshold(Number(event.target.value))}
                />
              </label>
              {!modelId && (
                <label className="space-y-1 text-xs">
                  {t("similarity.collection")}
                  <select
                    className="block rounded-md border border-input bg-background p-2 text-sm"
                    value={filters.collection_id ?? ""}
                    onChange={(event) =>
                      setFilters({
                        ...filters,
                        collection_id: event.target.value ? Number(event.target.value) : undefined,
                      })
                    }
                  >
                    <option value="">{t("similarity.library")}</option>
                    {collections.data?.map((row) => (
                      <option key={row.id} value={row.id}>
                        {row.path}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <label className="space-y-1 text-xs">
                {t("similarity.allFormats")}
                <select
                  className="block rounded-md border border-input bg-background p-2 text-sm"
                  value={filters.file_type ?? ""}
                  onChange={(event) =>
                    setFilters({ ...filters, file_type: event.target.value || undefined })
                  }
                >
                  <option value="">{t("similarity.allFormats")}</option>
                  {["stl", "obj", "3mf", "step"].map((value) => (
                    <option key={value} value={value}>
                      {value.toUpperCase()}
                    </option>
                  ))}
                </select>
              </label>
              <label className="space-y-1 text-xs">
                {t("similarity.allSources")}
                <select
                  className="block rounded-md border border-input bg-background p-2 text-sm"
                  value={filters.source ?? ""}
                  onChange={(event) =>
                    setFilters({
                      ...filters,
                      source:
                        event.target.value === "vault"
                          ? "vault"
                          : event.target.value === "external"
                            ? "external"
                            : undefined,
                    })
                  }
                >
                  <option value="">{t("similarity.allSources")}</option>
                  <option value="vault">{t("similarity.vault")}</option>
                  <option value="external">{t("similarity.external")}</option>
                </select>
              </label>
              <label className="flex items-center gap-2 py-2 text-xs">
                <Checkbox
                  ariaLabel={t("similarity.knownGood")}
                  checked={filters.known_good ?? false}
                  onChange={(checked) =>
                    setFilters({ ...filters, known_good: checked || undefined })
                  }
                />
                {t("similarity.knownGood")}
              </label>
            </div>
          </details>
        </div>
        {run.data && (
          <div role="status" className="space-y-1 border-b px-4 py-3 text-sm">
            <p>
              {t(`similarity.run.${run.data.state}`)} ·{" "}
              {t("similarity.progress", {
                processed: run.data.counters.artifacts_processed ?? 0,
                verified: run.data.counters.verified ?? 0,
              })}
            </p>
            {!!run.data.counters.embedded && (
              <p className="text-xs text-muted-foreground">
                {t("similarity.embeddingProgress", { count: run.data.counters.embedded })}
              </p>
            )}
            {run.data.checkpoint?.embedding_failure_code && (
              <p className="text-xs text-warning">{t("similarity.embeddingSkipped")}</p>
            )}
            {!!run.data.counters.skipped_by_budget && (
              <p className="text-xs text-warning">{t("similarity.budget")}</p>
            )}
            {!!run.data.counters.unsupported && (
              <p className="text-xs text-muted-foreground">
                {t("similarity.unsupported", { count: run.data.counters.unsupported })}
              </p>
            )}
          </div>
        )}
        {queue.isPending ? (
          <p role="status" className="p-6 text-sm text-muted-foreground">
            {t("similarity.loading")}
          </p>
        ) : queue.isError ? (
          <EmptyState
            title={t("similarity.loadError")}
            action={<Button onClick={() => void queue.refetch()}>{t("similarity.retry")}</Button>}
          />
        ) : rows.length ? (
          <ul className="divide-y divide-border">
            {rows.map((candidate) => (
              <SimilarityRow key={candidate.id} candidate={candidate} />
            ))}
          </ul>
        ) : (
          <EmptyState
            icon={ScanSearch}
            title={t(status.data?.enabled === false ? "similarity.disabled" : "similarity.empty")}
            description={t(
              status.data?.enabled === false ? "similarity.disabledHelp" : "similarity.emptyHelp",
            )}
          />
        )}
        {queue.hasNextPage && (
          <div className="border-t p-3">
            <Button
              variant="outline"
              loading={queue.isFetchingNextPage}
              onClick={() => void queue.fetchNextPage()}
            >
              {t("similarity.more")}
            </Button>
          </div>
        )}
      </Card>
    </>
  );
}
