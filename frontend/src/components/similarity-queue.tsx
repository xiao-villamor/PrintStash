import { useCallback, useEffect, useId, useState, type ComponentProps } from "react";
import { useLocation } from "@tanstack/react-router";
import { useRouter, useHistoryAction } from "@/lib/navigation";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { Box, Columns2, ScanSearch, SlidersHorizontal } from "lucide-react";

import { SimilaritySearch } from "@/components/similarity-search";
import { CollectionPicker } from "@/components/collection-picker";
import { Badge } from "@/components/ui/badge";
import { TabBar } from "@/components/ui/tabs";
import { useSearchParams } from "@/lib/navigation";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { EmptyState } from "@/components/ui/empty-state";
import {
  similarityCandidatesOptions,
  similarityRunOptions,
  similarityStatusOptions,
  useSimilarityCommands,
} from "@/lib/queries/similarity";
import { getSessionVersion } from "@/lib/session-transport";
import { uiText } from "@/lib/locale";
import { cn } from "@/lib/utils";
import { useI18n } from "@/lib/i18n";
import { Link } from "@/lib/link";
import {
  evidenceDescription,
  evidenceLabel,
  isSimilarityRunActive,
  readSimilarityFilters,
  similarityFiltersHref,
} from "@/lib/similarity";
import { toast } from "@/lib/toast";
import { useViewportAssetUrl } from "@/lib/use-viewport-admission";
import type { CollectionNodeRead } from "@/types";
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
  const { url: image, ref } = useViewportAssetUrl(
    model.thumbnail_file_id ? `/api/v1/files/${model.thumbnail_file_id}/thumbnail` : null,
  );
  return (
    <Link
      href={`/models/${model.id}`}
      className="flex min-w-0 items-center gap-3 rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <span
        ref={ref}
        className="flex h-16 w-16 shrink-0 items-center justify-center overflow-hidden rounded-md bg-muted"
      >
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

export function SimilarityRow({
  candidate,
  returnTo,
}: {
  candidate: SimilarityCandidate;
  returnTo?: string;
}) {
  const { t } = useI18n();
  return (
    <li className="grid gap-4 px-4 py-4 sm:px-5 @3xl/similarity:grid-cols-[minmax(0,1fr)_auto] @3xl/similarity:items-center">
      <div className="min-w-0 space-y-3">
        <div className="grid gap-3 @lg/similarity:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] @lg/similarity:items-center">
          <ModelLabel model={candidate.model_a} />
          <Columns2
            className="hidden h-4 w-4 text-muted-foreground @lg/similarity:block"
            aria-hidden
          />
          <ModelLabel model={candidate.model_b} />
        </div>
        <div className="min-w-0 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium">{evidenceLabel(candidate.evidence_class)}</span>
            <span className="font-mono text-xs tabular-nums text-muted-foreground">
              {Math.round(candidate.confidence * 100)}%
            </span>
            {candidate.freshness === "stale" && (
              <Badge variant="outline">{t("similarity.stale")}</Badge>
            )}
          </div>
          <p
            className={
              candidate.freshness === "stale"
                ? "text-xs text-warning"
                : "text-xs text-muted-foreground"
            }
          >
            {evidenceDescription(candidate)}
          </p>
        </div>
      </div>
      <Button asChild size="sm" className="justify-self-end">
        <Link
          href={`/library/similar/${candidate.id}${returnTo ? `?returnTo=${encodeURIComponent(returnTo)}` : ""}`}
        >
          {t("similarity.compare")}
        </Link>
      </Button>
    </li>
  );
}

function SimilarityEmptyState({
  modelDetail,
  ...props
}: ComponentProps<typeof EmptyState> & { modelDetail: boolean }) {
  if (!modelDetail) return <EmptyState {...props} />;
  return (
    <div className="space-y-2 p-4">
      <p className="text-sm font-medium text-on-surface">{props.title}</p>
      {props.description && (
        <p className="font-mono text-xs leading-relaxed text-on-surface-variant">
          {props.description}
        </p>
      )}
      {props.action}
    </div>
  );
}

export function SimilarityQueue({ modelId }: { modelId?: number }) {
  const modelDetail = modelId !== undefined;
  const { t } = useI18n();
  const navigation = useRouter();
  const searchParams = useSearchParams();
  const location = useLocation();
  const navigationAction = useHistoryAction();
  const filterOwner = useId();
  const currentUrl = location.pathname + location.searchStr;
  const [filterDraft, setFilterDraft] = useState(() => ({
    entry: location.state.__TSR_key,
    modelId,
    filters: readSimilarityFilters(modelId === undefined ? searchParams : new URLSearchParams()),
    dirty: false,
  }));
  // Router replacements may commit after the next input event. Only a new
  // navigation (including Back/Forward) may replace the live draft with URL state.
  if (filterDraft.modelId !== modelId || filterDraft.entry !== location.state.__TSR_key) {
    const ownReplacement =
      modelId === undefined &&
      filterDraft.modelId === modelId &&
      navigationAction === "REPLACE" &&
      location.state?.similarityFilterOwner === filterOwner;
    setFilterDraft({
      entry: location.state.__TSR_key,
      modelId,
      filters:
        ownReplacement || (modelId !== undefined && filterDraft.modelId === modelId)
          ? filterDraft.filters
          : readSimilarityFilters(modelId === undefined ? searchParams : new URLSearchParams()),
      dirty: ownReplacement && filterDraft.dirty,
    });
  }
  const filters = filterDraft.filters;
  const setFilters = useCallback((patch: Partial<SimilarityFilters>) => {
    setFilterDraft((current) => {
      const filters = { ...current.filters, ...patch };
      return {
        ...current,
        filters,
        dirty: true,
      };
    });
  }, []);
  const filterUrl = similarityFiltersHref(filters);
  useEffect(() => {
    if (modelId !== undefined || !filterDraft.dirty || currentUrl === filterUrl) return;
    navigation.replace(filterUrl, {
      state: { similarityFilterOwner: filterOwner },
    });
  }, [modelId, filterDraft.dirty, currentUrl, filterUrl, navigation, filterOwner]);

  // Input intent is immediate. Only the bounded candidate read waits for rapid
  // slider/key changes to settle, so route acknowledgements cannot reset it.
  const [requestFilters, setRequestFilters] = useState(filters);
  const filtersSettling = requestFilters !== filters;
  useEffect(() => {
    if (!filtersSettling) return;
    const timer = setTimeout(() => setRequestFilters(filters), FILTER_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [filters, filtersSettling]);
  const threshold =
    filters.minimum_confidence === undefined ? null : Math.round(filters.minimum_confidence * 100);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const filtersId = useId();
  const [startedRun, setStartedRun] = useState<{ modelId: number; id: number } | null>(null);
  const runId = startedRun?.modelId === modelId ? (startedRun?.id ?? null) : null;
  const [collectionChoice, setCollectionChoice] = useState<CollectionNodeRead | null>(null);
  const [choosingCollection, setChoosingCollection] = useState(false);
  const matchingCollection =
    collectionChoice?.id === filters.collection_id ? collectionChoice : null;
  const status = useQuery(similarityStatusOptions());
  const run = useQuery(similarityRunOptions(runId));
  const queue = useInfiniteQuery({
    ...similarityCandidatesOptions(
      requestFilters,
      modelId,
      Boolean(run.data && isSimilarityRunActive(run.data)),
    ),
    enabled: !filtersSettling,
  });
  const { find } = useSimilarityCommands();
  const { refetch: refreshCandidates } = queue;
  const runFinished = run.data ? !isSimilarityRunActive(run.data) : false;
  useEffect(() => {
    if (runFinished) void refreshCandidates();
  }, [runFinished, refreshCandidates]);
  const rows = queue.data?.pages.flatMap((page) => page.items) ?? [];
  const activeFilters = [
    filters.evidence_class ? evidenceLabel(filters.evidence_class) : null,
    filters.freshness !== "current"
      ? t(filters.freshness === "stale" ? "similarity.stale" : "similarity.allFreshness")
      : null,
    filters.minimum_confidence !== undefined
      ? `${t("similarity.threshold")}: ${Math.round(filters.minimum_confidence * 100)}%`
      : null,
    filters.collection_id !== undefined
      ? (matchingCollection?.display_path ??
        t("similarity.collectionNumber", { id: filters.collection_id }))
      : null,
    filters.file_type?.toUpperCase(),
    filters.source
      ? t(filters.source === "vault" ? "similarity.vault" : "similarity.external")
      : null,
    filters.known_good ? t("similarity.knownGood") : null,
  ].filter((value) => value !== null && value !== undefined);
  function clearFilters() {
    setCollectionChoice(null);
    setChoosingCollection(false);
    setFilterDraft((current) => {
      const filters: SimilarityFilters = {
        review_state: current.filters.review_state,
        freshness: "current",
      };
      return {
        ...current,
        filters,
        dirty: true,
      };
    });
  }
  const modelReturnParams = new URLSearchParams(searchParams);
  modelReturnParams.set("tab", "similar");
  const returnTo = modelId === undefined ? filterUrl : `/models/${modelId}?${modelReturnParams}`;
  const QueueFrame = modelDetail ? "div" : Card;
  return (
    <>
      {modelDetail && (
        <h2 className="mb-4 text-base font-semibold text-on-surface">{t("similarity.review")}</h2>
      )}
      <SimilaritySearch modelId={modelId} />
      <QueueFrame
        role="region"
        aria-label={t("similarity.review")}
        className={cn(
          "@container/similarity min-w-0 overflow-hidden",
          modelDetail && "bg-transparent text-on-surface",
        )}
      >
        <div
          className={cn(
            "flex flex-wrap items-center gap-3 border-b bg-muted/30 p-3",
            modelDetail && "border-outline-variant bg-surface",
          )}
        >
          <div className="min-w-0 basis-full @3xl/similarity:flex-1 @3xl/similarity:basis-auto">
            <TabBar
              tabs={REVIEW_STATES.map((state) => ({ key: state, label: t(`similarity.${state}`) }))}
              active={filters.review_state ?? "open"}
              onChange={(review_state) => setFilters({ review_state })}
              className="grid w-full grid-cols-2 gap-1 @sm/similarity:grid-cols-4 @3xl/similarity:flex"
              tabClassName="h-9 min-w-0 whitespace-nowrap rounded-md px-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-popover-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring @sm/similarity:px-0 @3xl/similarity:px-3"
              activeTabClassName="bg-accent text-accent-foreground"
              showIndicator={false}
            />
          </div>
          <div className="flex w-full flex-wrap items-center justify-end gap-2 @3xl/similarity:w-auto">
            <Button
              variant="outline"
              size="sm"
              aria-expanded={filtersOpen}
              aria-controls={filtersId}
              onClick={() => setFiltersOpen(!filtersOpen)}
            >
              <SlidersHorizontal className="h-4 w-4" aria-hidden />
              {uiText("Filters")}
              {activeFilters.length > 0 && (
                <span className="font-mono text-xs">{activeFilters.length}</span>
              )}
            </Button>
            {modelId !== undefined && (
              <Button
                size="sm"
                onClick={() => {
                  if (modelId === undefined) throw new Error("A Model is required");
                  const session = getSessionVersion();
                  find.mutate(
                    { id: modelId, session },
                    {
                      onSuccess: (result) => {
                        if (session === getSessionVersion())
                          setStartedRun({ modelId, id: result.run.id });
                      },
                      onError: (error) => {
                        if (session === getSessionVersion()) toast.error(error);
                      },
                    },
                  );
                }}
                loading={find.isPending && find.variables?.id === modelId}
                disabled={status.isError || !status.data?.enabled}
              >
                {t("similarity.find")}
              </Button>
            )}
          </div>
          <div
            id={filtersId}
            hidden={!filtersOpen}
            className="min-w-0 basis-full border-t border-border pt-3"
          >
            <div className="flex flex-wrap items-end gap-3">
              <label className="min-w-0 flex-1 basis-32 space-y-1 text-xs">
                {t("similarity.allClasses")}
                <select
                  className="block w-full rounded-md border border-input bg-background p-2 text-sm"
                  value={filters.evidence_class ?? ""}
                  onChange={(event) =>
                    setFilters({
                      evidence_class: EVIDENCE_CLASSES.find(
                        (value) => value === event.target.value,
                      ),
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
              <label className="min-w-0 flex-1 basis-32 space-y-1 text-xs">
                {t("similarity.current")}
                <select
                  className="block w-full rounded-md border border-input bg-background p-2 text-sm"
                  value={filters.freshness ?? ""}
                  onChange={(event) =>
                    setFilters({
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
                  onChange={(event) =>
                    setFilters({ minimum_confidence: Number(event.target.value) / 100 })
                  }
                />
              </label>
              {!modelId && (
                <div className="min-w-0 flex-1 basis-32 space-y-1 text-xs">
                  {t("similarity.collection")}
                  <select
                    className="block rounded-md border border-input bg-background p-2 text-sm"
                    aria-label={t("similarity.collection")}
                    value={choosingCollection ? "pick" : (filters.collection_id ?? "")}
                    onChange={(event) => {
                      if (event.target.value === "pick") setChoosingCollection(true);
                      else {
                        setChoosingCollection(false);
                        setCollectionChoice(null);
                        setFilters({ collection_id: undefined });
                      }
                    }}
                  >
                    <option value="">{t("similarity.library")}</option>
                    <option value="pick">{uiText("Choose existing collection")}</option>
                    {matchingCollection && (
                      <option value={matchingCollection.id}>
                        {matchingCollection.display_path}
                      </option>
                    )}
                    {filters.collection_id !== undefined && !matchingCollection && (
                      <option value={filters.collection_id}>
                        {t("similarity.collectionNumber", { id: filters.collection_id })}
                      </option>
                    )}
                  </select>
                  {choosingCollection && (
                    <CollectionPicker
                      minRole="view"
                      selectedPath={null}
                      emptyLabel={uiText("No collections found.")}
                      onSelect={(collection) => {
                        if (collection === null) throw new Error("A collection is required");
                        setCollectionChoice(collection);
                        setFilters({ collection_id: collection.id });
                        setChoosingCollection(false);
                      }}
                    />
                  )}
                </div>
              )}
              <label className="min-w-0 flex-1 basis-32 space-y-1 text-xs">
                {t("similarity.allFormats")}
                <select
                  className="block rounded-md border border-input bg-background p-2 text-sm"
                  value={filters.file_type ?? ""}
                  onChange={(event) => setFilters({ file_type: event.target.value || undefined })}
                >
                  <option value="">{t("similarity.allFormats")}</option>
                  {["stl", "obj", "glb", "gltf", "ply", "3dm", "fbx", "3mf", "step"].map(
                    (value) => (
                      <option key={value} value={value}>
                        {value.toUpperCase()}
                      </option>
                    ),
                  )}
                </select>
              </label>
              <label className="min-w-0 flex-1 basis-32 space-y-1 text-xs">
                {t("similarity.allSources")}
                <select
                  className="block rounded-md border border-input bg-background p-2 text-sm"
                  value={filters.source ?? ""}
                  onChange={(event) =>
                    setFilters({
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
                  onChange={(checked) => setFilters({ known_good: checked || undefined })}
                />
                {t("similarity.knownGood")}
              </label>
            </div>
          </div>
        </div>
        {activeFilters.length > 0 && (
          <div className="flex flex-wrap items-center justify-between gap-2 border-b px-4 py-2 text-xs text-muted-foreground">
            <p className="min-w-0 break-words">{activeFilters.join(" · ")}</p>
            <Button variant="ghost" size="sm" onClick={clearFilters}>
              {uiText("Clear filters")}
            </Button>
          </div>
        )}
        <div className={cn("border-b px-4 py-3 sm:px-5", modelDetail && "border-outline-variant")}>
          {!modelDetail && <h2 className="text-sm font-semibold">{t("similarity.review")}</h2>}
          <p
            className={cn(
              "text-xs text-muted-foreground",
              modelDetail ? "font-mono leading-relaxed text-on-surface-variant" : "mt-1",
            )}
          >
            {t("similarity.reviewHelp")}
          </p>
        </div>
        {status.isError && (
          <div role="alert" className="border-b p-3 text-sm text-destructive">
            {t("similarity.loadError")}{" "}
            <Button variant="ghost" size="sm" onClick={() => void status.refetch()}>
              {t("similarity.retry")}
            </Button>
          </div>
        )}
        {run.isError && (
          <div role="alert" className="border-b p-3 text-sm text-destructive">
            {t("similarity.loadError")}{" "}
            <Button variant="ghost" size="sm" onClick={() => void run.refetch()}>
              {t("similarity.retry")}
            </Button>
          </div>
        )}
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
        {filtersSettling || queue.isPending ? (
          <p role="status" className="p-6 text-sm text-muted-foreground">
            {t("similarity.loading")}
          </p>
        ) : queue.isError && !queue.data ? (
          <SimilarityEmptyState
            modelDetail={modelDetail}
            title={t("similarity.loadError")}
            action={<Button onClick={() => void queue.refetch()}>{t("similarity.retry")}</Button>}
          />
        ) : rows.length ? (
          <ul className="divide-y divide-border">
            {rows.map((candidate) => (
              <SimilarityRow key={candidate.id} candidate={candidate} returnTo={returnTo} />
            ))}
          </ul>
        ) : (
          <SimilarityEmptyState
            modelDetail={modelDetail}
            icon={ScanSearch}
            title={t(
              activeFilters.length
                ? "similarity.emptyFiltered"
                : filters.review_state === "confirmed"
                  ? "similarity.emptyConfirmed"
                  : filters.review_state === "later"
                    ? "similarity.emptyLater"
                    : filters.review_state === "rejected"
                      ? "similarity.emptyRejected"
                      : "similarity.empty",
            )}
            description={t(
              activeFilters.length
                ? "similarity.emptyFilteredHelp"
                : filters.review_state !== "open"
                  ? "similarity.emptyReviewedHelp"
                  : status.data?.enabled === false
                    ? "similarity.queueDisabledHelp"
                    : "similarity.emptyHelp",
            )}
            action={
              activeFilters.length > 0 ? (
                <Button variant="outline" onClick={clearFilters}>
                  {uiText("Clear filters")}
                </Button>
              ) : undefined
            }
          />
        )}
        {!filtersSettling && queue.isError && queue.data && (
          <div role="alert" className="space-y-2 border-t p-3 text-sm text-destructive">
            <p>{t("similarity.loadError")}</p>
            <Button
              variant="outline"
              onClick={() =>
                void (queue.isFetchNextPageError ? queue.fetchNextPage() : queue.refetch())
              }
            >
              {t("similarity.retry")}
            </Button>
          </div>
        )}
        {!filtersSettling && queue.hasNextPage && !queue.isFetchNextPageError && (
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
      </QueueFrame>
    </>
  );
}
