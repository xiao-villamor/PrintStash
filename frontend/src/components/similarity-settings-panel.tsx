import { useEffect, useState } from "react";
import { useInfiniteQuery, useMutation, useQuery } from "@tanstack/react-query";
import { ScanSearch } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { listModels } from "@/lib/api/models";
import { listExternalLibraries } from "@/lib/api/libraries";
import { listCollections } from "@/lib/api/taxonomy";
import {
  cancelSimilarityRun,
  getSimilarityStatus,
  listSimilarityRuns,
  previewSimilaritySelection,
  saveSimilaritySettings,
  startSimilarityRun,
} from "@/lib/api/similarity";
import { useI18n } from "@/lib/i18n";
import { Link } from "@/lib/link";
import { evidenceLabel, isSimilarityRunActive } from "@/lib/similarity";
import { toast } from "@/lib/toast";
import { EVIDENCE_CLASSES, type SimilaritySettings } from "@/types/similarity";

function SettingsForm({ initial, onSaved }: { initial: SimilaritySettings; onSaved: () => void }) {
  const { t } = useI18n();
  const [draft, setDraft] = useState(initial);
  const [overrides, setOverrides] = useState(Object.keys(initial.class_overrides).length > 0);
  const [selection, setSelection] = useState({
    minimum_confidence: initial.minimum_confidence,
    class_overrides: initial.class_overrides,
  });
  useEffect(() => {
    const timer = setTimeout(
      () =>
        setSelection({
          minimum_confidence: draft.minimum_confidence,
          class_overrides: overrides ? draft.class_overrides : {},
        }),
      250,
    );
    return () => clearTimeout(timer);
  }, [draft.minimum_confidence, draft.class_overrides, overrides]);
  const preview = useQuery({
    queryKey: ["similarity", "selection-preview", selection],
    queryFn: () => previewSimilaritySelection(selection),
  });
  const save = useMutation({
    mutationFn: () =>
      saveSimilaritySettings({ ...draft, class_overrides: overrides ? draft.class_overrides : {} }),
    onSuccess: () => {
      toast.success(t("similarity.saved"));
      onSaved();
    },
    onError: toast.error,
  });
  return (
    <form
      aria-label={t("similarity.title")}
      className="space-y-4 p-4 sm:p-5"
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <div className="grid gap-4 lg:grid-cols-2 lg:gap-8">
        <div className="space-y-3">
          <label className="flex items-center gap-2 text-sm font-medium">
            <Checkbox
              ariaLabel={t("similarity.enable")}
              checked={draft.enabled}
              onChange={(enabled) => setDraft({ ...draft, enabled })}
            />
            {t("similarity.enable")}
          </label>
          <p className="text-xs leading-relaxed text-muted-foreground">
            {t("similarity.localHelp")}
          </p>
          <label className="flex items-center gap-2 text-sm">
            <Checkbox
              ariaLabel={t("similarity.onIngest")}
              checked={draft.fingerprint_on_ingest}
              onChange={(fingerprint_on_ingest) => setDraft({ ...draft, fingerprint_on_ingest })}
            />
            {t("similarity.onIngest")}
          </label>
        </div>
        <div className="space-y-2">
          <label className="block space-y-2 text-sm">
            {t("similarity.threshold")}{" "}
            <output className="font-mono tabular-nums">
              {Math.round(draft.minimum_confidence * 100)}%
            </output>
            <input
              aria-label={t("similarity.threshold")}
              type="range"
              min={50}
              max={100}
              value={Math.round(draft.minimum_confidence * 100)}
              className="block w-full accent-primary"
              onChange={(event) =>
                setDraft({ ...draft, minimum_confidence: Number(event.target.value) / 100 })
              }
            />
          </label>
          {preview.data && (
            <p role="status" className="text-xs text-muted-foreground">
              {t("similarity.selectionCount", { count: preview.data.total })}
            </p>
          )}
          {preview.isError && (
            <p role="alert" className="text-xs text-destructive">
              {t("similarity.previewError")}
            </p>
          )}
        </div>
      </div>
      <details className="border-t border-border pt-3">
        <summary className="cursor-pointer text-sm font-medium">{t("similarity.advanced")}</summary>
        <div className="mt-3 grid gap-x-6 gap-y-3 sm:grid-cols-2">
          <label className="flex items-center justify-between gap-3 text-sm">
            {t("similarity.triangleCap")}
            <Input
              className="w-28 shrink-0"
              type="number"
              min={100}
              max={200000}
              required
              value={draft.triangle_cap}
              onChange={(e) => setDraft({ ...draft, triangle_cap: Number(e.target.value) })}
            />
          </label>
          <label className="flex items-center justify-between gap-3 text-sm">
            {t("similarity.samples")}
            <Input
              className="w-28 shrink-0"
              type="number"
              min={256}
              max={5000}
              required
              value={draft.sample_points}
              onChange={(e) => setDraft({ ...draft, sample_points: Number(e.target.value) })}
            />
          </label>
          <label className="flex items-center justify-between gap-3 text-sm">
            {t("similarity.candidateCap")}
            <Input
              className="w-28 shrink-0"
              type="number"
              min={1}
              max={100}
              required
              value={draft.max_candidates}
              onChange={(e) => setDraft({ ...draft, max_candidates: Number(e.target.value) })}
            />
          </label>
          <label className="flex items-center justify-between gap-3 text-sm">
            {t("similarity.cadence")}
            <Input
              className="w-28 shrink-0"
              type="number"
              min={0}
              max={168}
              required
              value={draft.schedule_hours}
              onChange={(e) => setDraft({ ...draft, schedule_hours: Number(e.target.value) })}
            />
          </label>
        </div>
        <label className="mt-4 flex items-center gap-2 text-sm">
          <Checkbox
            ariaLabel={t("similarity.classOverrides")}
            checked={overrides}
            onChange={setOverrides}
          />
          {t("similarity.classOverrides")}
        </label>
        {overrides && (
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            {EVIDENCE_CLASSES.map((key) => (
              <label key={key} className="flex items-center justify-between gap-3 text-sm">
                <span>
                  {evidenceLabel(key)}{" "}
                  {preview.data && (
                    <span className="font-mono text-xs text-muted-foreground">
                      ({preview.data.by_class[key] ?? 0})
                    </span>
                  )}
                </span>
                <Input
                  className="w-20 shrink-0"
                  type="number"
                  min={50}
                  max={100}
                  value={Math.round((draft.class_overrides[key] ?? draft.minimum_confidence) * 100)}
                  onChange={(event) =>
                    setDraft({
                      ...draft,
                      class_overrides: {
                        ...draft.class_overrides,
                        [key]: Number(event.target.value) / 100,
                      },
                    })
                  }
                />
              </label>
            ))}
          </div>
        )}
        <label className="mt-4 flex items-center gap-2 text-sm">
          <Checkbox
            ariaLabel={t("similarity.embeddings")}
            checked={draft.embeddings_enabled}
            onChange={(embeddings_enabled) => setDraft({ ...draft, embeddings_enabled })}
          />
          {t("similarity.embeddings")}
        </label>
      </details>
      <Button type="submit" loading={save.isPending}>
        {t("similarity.save")}
      </Button>
    </form>
  );
}

export function SimilaritySettingsPanel() {
  const { t } = useI18n();
  const status = useQuery({ queryKey: ["similarity", "status"], queryFn: getSimilarityStatus });
  const history = useInfiniteQuery({
    queryKey: ["similarity", "runs"],
    queryFn: ({ pageParam }: { pageParam: number | undefined }) => listSimilarityRuns(pageParam),
    initialPageParam: undefined,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    refetchInterval: (query) =>
      query.state.data?.pages.some((page) => page.items.some(isSimilarityRunActive)) ? 1500 : false,
  });
  const collections = useQuery({
    queryKey: ["similarity", "collections"],
    queryFn: () => listCollections(),
  });
  const [scope, setScope] = useState("library");
  const [modelQuery, setModelQuery] = useState("");
  const [modelOffset, setModelOffset] = useState(0);
  const [selectedModels, setSelectedModels] = useState<Record<number, string>>({});
  const [debouncedQuery, setDebouncedQuery] = useState("");
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedQuery(modelQuery);
      setModelOffset(0);
    }, 250);
    return () => clearTimeout(timer);
  }, [modelQuery]);
  const modelChoices = useQuery({
    queryKey: ["similarity", "model-choices", debouncedQuery, modelOffset],
    queryFn: () => listModels({ q: debouncedQuery, limit: 25, offset: modelOffset }),
    enabled: scope === "models",
  });
  const sources = useQuery({ queryKey: ["similarity", "sources"], queryFn: listExternalLibraries });
  const refresh = () => {
    void history.refetch();
    void status.refetch();
  };
  const start = useMutation({
    mutationFn: () =>
      scope === "library"
        ? startSimilarityRun("library")
        : scope === "models"
          ? startSimilarityRun("models", Object.keys(selectedModels).map(Number))
          : scope.startsWith("source:")
            ? startSimilarityRun("sources", [Number(scope.slice(7))])
            : startSimilarityRun("collections", [Number(scope)]),
    onSuccess: refresh,
    onError: toast.error,
  });
  const cancel = useMutation({
    mutationFn: cancelSimilarityRun,
    onSuccess: refresh,
    onError: toast.error,
  });
  return (
    <Card className="overflow-hidden">
      <div className="flex items-start gap-3 border-b px-4 py-4 sm:px-5">
        <ScanSearch className="h-8 w-8 shrink-0 rounded-md bg-muted p-1.5" aria-hidden />
        <div>
          <h3 className="text-sm font-semibold">{t("similarity.title")}</h3>
          <p className="mt-1 text-xs text-muted-foreground">{t("similarity.intro")}</p>
        </div>
      </div>
      {status.isError ? (
        <EmptyState
          title={t("similarity.loadError")}
          action={<Button onClick={() => void status.refetch()}>{t("similarity.retry")}</Button>}
        />
      ) : status.data?.settings ? (
        <SettingsForm
          key={JSON.stringify(status.data.settings)}
          initial={status.data.settings}
          onSaved={refresh}
        />
      ) : (
        <p role="status" className="p-5 text-sm text-muted-foreground">
          {t("similarity.loading")}
        </p>
      )}
      {status.data && (
        <div className="flex flex-wrap gap-2 border-t px-4 py-3">
          <Badge variant="outline">
            {t(
              status.data.capabilities.step ? "similarity.stepReady" : "similarity.stepUnavailable",
            )}
          </Badge>
          <Badge variant="outline">
            {t(
              status.data.capabilities.local_embeddings
                ? "similarity.embeddingsReady"
                : "similarity.embeddingsUnavailable",
            )}
          </Badge>
        </div>
      )}
      <div className="flex flex-wrap items-end gap-3 border-y bg-muted/30 p-3">
        <label className="min-w-0 basis-full space-y-1 text-xs sm:basis-72">
          {t("similarity.scope")}
          <select
            className="block w-full rounded-md border border-input bg-background p-2 text-sm"
            value={scope}
            onChange={(event) => setScope(event.target.value)}
          >
            <option value="library">{t("similarity.library")}</option>
            <option value="models">{t("similarity.modelSet")}</option>
            {sources.data?.map((source) => (
              <option key={`source:${source.id}`} value={`source:${source.id}`}>
                {t("similarity.sourceScope", { name: source.name })}
              </option>
            ))}
            {collections.data?.map((collection) => (
              <option key={collection.id} value={collection.id}>
                {collection.path}
              </option>
            ))}
          </select>
        </label>
        <Button
          loading={start.isPending}
          disabled={
            !status.data?.enabled || (scope === "models" && !Object.keys(selectedModels).length)
          }
          onClick={() => start.mutate()}
        >
          {t("similarity.start")}
        </Button>
        <Button asChild variant="outline">
          <Link href="/library/similar">{t("similarity.review")}</Link>
        </Button>
      </div>
      {scope === "models" && (
        <div className="space-y-3 border-b p-4">
          <label className="block space-y-1 text-sm">
            {t("similarity.selectModels")}
            <Input value={modelQuery} onChange={(event) => setModelQuery(event.target.value)} />
          </label>
          <p className="text-xs text-muted-foreground">
            {t("similarity.selectedModels", { count: Object.keys(selectedModels).length })}
          </p>
          {modelChoices.isPending && (
            <p role="status" className="text-xs text-muted-foreground">
              {t("similarity.loading")}
            </p>
          )}
          {modelChoices.isError && (
            <p role="alert" className="text-xs text-destructive">
              {t("similarity.loadError")}
            </p>
          )}
          {modelChoices.data?.length === 0 && (
            <p className="text-xs text-muted-foreground">{t("similarity.noModelMatches")}</p>
          )}
          <ul className="grid gap-2 sm:grid-cols-2">
            {modelChoices.data?.map((model) => (
              <li key={model.id}>
                <label className="flex items-center gap-2 text-sm">
                  <Checkbox
                    ariaLabel={model.name}
                    checked={model.id in selectedModels}
                    disabled={
                      !(model.id in selectedModels) && Object.keys(selectedModels).length >= 1000
                    }
                    onChange={(checked) =>
                      setSelectedModels((current) => {
                        const next = { ...current };
                        if (checked) next[model.id] = model.name;
                        else delete next[model.id];
                        return next;
                      })
                    }
                  />
                  {model.name}
                </label>
              </li>
            ))}
          </ul>
          <div className="flex flex-wrap gap-2">
            {Object.keys(selectedModels).length > 0 && (
              <Button variant="ghost" size="sm" onClick={() => setSelectedModels({})}>
                {t("similarity.clearSelection")}
              </Button>
            )}
            <Button
              variant="outline"
              size="sm"
              disabled={modelOffset === 0}
              onClick={() => setModelOffset(Math.max(0, modelOffset - 25))}
            >
              {t("similarity.previous")}
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={(modelChoices.data?.length ?? 0) < 25}
              onClick={() => setModelOffset(modelOffset + 25)}
            >
              {t("Next")}
            </Button>
          </div>
        </div>
      )}
      <div className="px-4 py-3">
        <h4 className="text-sm font-semibold">{t("similarity.history")}</h4>
      </div>
      {history.isError && (
        <p role="alert" className="px-4 pb-4 text-sm text-destructive">
          {t("similarity.loadError")}
        </p>
      )}
      {history.data?.pages[0]?.items.length === 0 && (
        <p className="px-4 pb-4 text-sm text-muted-foreground">{t("similarity.noRuns")}</p>
      )}
      <ul className="divide-y divide-border">
        {history.data?.pages
          .flatMap((page) => page.items)
          .map((run) => (
            <li
              key={run.id}
              className="flex flex-wrap items-center justify-between gap-3 px-4 py-3"
            >
              <div className="min-w-0 space-y-1">
                <p className="text-sm font-medium">{t(`similarity.run.${run.state}`)}</p>
                <p className="text-xs text-muted-foreground">
                  {t("similarity.progress", {
                    processed: run.counters.artifacts_processed ?? 0,
                    verified: run.counters.verified ?? 0,
                  })}
                </p>
                {!!run.counters.partial && (
                  <p className="text-xs text-muted-foreground">
                    {t("similarity.partial", { count: run.counters.partial })}
                  </p>
                )}
                {!!run.counters.unsupported && (
                  <p className="text-xs text-muted-foreground">
                    {t("similarity.unsupported", { count: run.counters.unsupported })}
                  </p>
                )}
                {!!run.counters.skipped_by_budget && (
                  <p className="text-xs text-warning">{t("similarity.budget")}</p>
                )}
                {!!run.counters.embedded && (
                  <p className="text-xs text-muted-foreground">
                    {t("similarity.embeddingProgress", { count: run.counters.embedded })}
                  </p>
                )}
                {run.checkpoint?.embedding_failure_code && (
                  <p className="text-xs text-warning">{t("similarity.embeddingSkipped")}</p>
                )}
                {run.state === "failed" && (
                  <p className="text-xs text-destructive">{t("similarity.runFailed")}</p>
                )}
              </div>
              {isSimilarityRunActive(run) && (
                <Button
                  variant="outline"
                  size="sm"
                  disabled={run.state === "cancelling" || cancel.isPending}
                  onClick={() => cancel.mutate(run.id)}
                >
                  {t("similarity.cancel")}
                </Button>
              )}
            </li>
          ))}
      </ul>
      {history.hasNextPage && (
        <div className="border-t p-3">
          <Button
            variant="outline"
            disabled={history.isFetchingNextPage}
            onClick={() => void history.fetchNextPage()}
          >
            {t("similarity.more")}
          </Button>
        </div>
      )}
    </Card>
  );
}
