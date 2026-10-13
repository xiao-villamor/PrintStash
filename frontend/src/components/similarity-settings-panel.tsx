import { useEffect, useState } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { ScanSearch } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { CollectionPicker } from "@/components/collection-picker";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { uiText } from "@/lib/locale";
import {
  similarityHistoryOptions,
  similarityModelChoicesOptions,
  similaritySelectionOptions,
  similaritySourcesOptions,
  similarityStatusOptions,
  useSimilarityCommands,
} from "@/lib/queries/similarity";
import { getSessionVersion } from "@/lib/session-transport";
import { useI18n } from "@/lib/i18n";
import { evidenceLabel, isSimilarityRunActive } from "@/lib/similarity";
import { listTasks, trackSimilarityRun } from "@/lib/task-center";
import { toast } from "@/lib/toast";
import { EVIDENCE_CLASSES, type SimilarityRun, type SimilaritySettings } from "@/types/similarity";
import type { CollectionNodeRead } from "@/types";

function SettingsForm({ initial, readOnly }: { initial: SimilaritySettings; readOnly: boolean }) {
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
  const preview = useQuery(similaritySelectionOptions(selection));
  const { saveSettings: save } = useSimilarityCommands();
  return (
    <form
      aria-label={t("similarity.title")}
      className="space-y-4 p-4 sm:p-5"
      onSubmit={(event) => {
        event.preventDefault();
        const session = getSessionVersion();
        save.mutate(
          {
            payload: { ...draft, class_overrides: overrides ? draft.class_overrides : {} },
            session,
          },
          {
            onSuccess: (settings) => {
              if (session !== getSessionVersion()) return;
              setDraft(settings);
              setOverrides(Object.keys(settings.class_overrides).length > 0);
              toast.success(t("similarity.saved"));
            },
            onError: (error) => {
              if (session === getSessionVersion()) toast.error(error);
            },
          },
        );
      }}
    >
      <fieldset disabled={save.isPending || readOnly} className="space-y-4">
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
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => void preview.refetch()}
                >
                  {t("similarity.retry")}
                </Button>
              </p>
            )}
          </div>
        </div>
        <details className="border-t border-border pt-3">
          <summary className="cursor-pointer text-sm font-medium">
            {t("similarity.advanced")}
          </summary>
          <div className="mt-3 grid gap-x-6 gap-y-3 sm:grid-cols-2">
            <label className="flex items-center justify-between gap-3 text-sm">
              {t("similarity.triangleCap")}
              <Input
                className="w-28 shrink-0"
                type="number"
                min={100}
                max={2000000}
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
                    value={Math.round(
                      (draft.class_overrides[key] ?? draft.minimum_confidence) * 100,
                    )}
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
      </fieldset>
      <Button type="submit" loading={save.isPending} disabled={readOnly}>
        {t("similarity.save")}
      </Button>
    </form>
  );
}

type ScopeChoice =
  | { kind: "library" }
  | { kind: "models" }
  | { kind: "chooseCollection" }
  | { kind: "source"; id: number; name: string }
  | { kind: "collection"; collection: CollectionNodeRead };
export function SimilaritySettingsPanel() {
  const { t } = useI18n();
  const status = useQuery(similarityStatusOptions());
  const history = useInfiniteQuery(similarityHistoryOptions());
  const [scope, setScope] = useState<ScopeChoice>({ kind: "library" });
  const [scopeCollection, setScopeCollection] = useState<CollectionNodeRead | null>(null);
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
  const modelChoices = useQuery(
    similarityModelChoicesOptions(debouncedQuery, modelOffset, scope.kind === "models"),
  );
  const sources = useQuery(similaritySourcesOptions());
  const availableSources = sources.data?.kind === "enabled" ? sources.data.items : [];
  const command: { scope: SimilarityRun["scope"]; ids: number[] } | null =
    scope.kind === "chooseCollection"
      ? null
      : scope.kind === "library"
        ? { scope: "library", ids: [] }
        : scope.kind === "models"
          ? {
              scope: "models",
              ids: Object.keys(selectedModels)
                .map(Number)
                .sort((a, b) => a - b),
            }
          : scope.kind === "source"
            ? { scope: "sources", ids: [scope.id] }
            : { scope: "collections", ids: [scope.collection.id] };
  const scopeValue =
    scope.kind === "source"
      ? `source:${scope.id}`
      : scope.kind === "collection"
        ? String(scope.collection.id)
        : scope.kind === "chooseCollection"
          ? "pick"
          : scope.kind;
  // Administrators can see other users' runs in history, but the server's
  // duplicate guard applies to this user's runs only.
  const ownActiveRunIds = new Set(
    listTasks()
      .filter((task) => task.status === "pending" || task.status === "running")
      .flatMap((task) => (task.similarityRunId === undefined ? [] : [task.similarityRunId])),
  );
  const activeRun = history.data?.pages
    .flatMap((page) => page.items)
    .find(
      (run) =>
        ownActiveRunIds.has(run.id) &&
        isSimilarityRunActive(run) &&
        command !== null &&
        run.scope === command.scope &&
        run.scope_ids.length === command.ids.length &&
        run.scope_ids.every((id, index) => id === command.ids[index]),
    );
  const { start, cancel } = useSimilarityCommands();
  return (
    <Card className="overflow-hidden">
      <div className="flex items-start gap-3 border-b px-4 py-4 sm:px-5">
        <ScanSearch className="h-8 w-8 shrink-0 rounded-md bg-muted p-1.5" aria-hidden />
        <div>
          <h3 className="text-sm font-semibold">{t("similarity.runTitle")}</h3>
          <p className="mt-1 text-xs text-muted-foreground">{t("similarity.runHelp")}</p>
        </div>
      </div>
      <details className="border-b">
        <summary className="cursor-pointer px-4 py-3 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
          {t("similarity.analysisOptions")}
        </summary>
        {status.isError && (
          <EmptyState
            title={t("similarity.loadError")}
            action={<Button onClick={() => void status.refetch()}>{t("similarity.retry")}</Button>}
          />
        )}
        {status.data?.settings ? (
          <SettingsForm initial={status.data.settings} readOnly={status.isError} />
        ) : (
          !status.isError && (
            <p role="status" className="p-5 text-sm text-muted-foreground">
              {t("similarity.loading")}
            </p>
          )
        )}
        {status.data && (
          <div className="flex flex-wrap gap-2 border-t px-4 py-3">
            <Badge variant="outline">
              {t(
                status.data.capabilities.step
                  ? "similarity.stepReady"
                  : "similarity.stepUnavailable",
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
      </details>
      <div className="flex flex-wrap items-end gap-3 border-y bg-muted/30 p-3">
        <div className="min-w-0 basis-full space-y-1 text-xs sm:basis-72">
          {t("similarity.scope")}
          <select
            className="block w-full rounded-md border border-input bg-background p-2 text-sm"
            value={scopeValue}
            onChange={(event) => {
              const value = event.target.value;
              if (value === "library" || value === "models") setScope({ kind: value });
              else if (value === "pick") setScope({ kind: "chooseCollection" });
              else if (value.startsWith("source:")) {
                const source = availableSources.find((item) => item.id === Number(value.slice(7)));
                if (!source) throw new Error("A current source choice is required");
                setScope({ kind: "source", id: source.id, name: source.name });
              } else {
                if (!scopeCollection || value !== String(scopeCollection.id))
                  throw new Error("A captured collection choice is required");
                setScope({ kind: "collection", collection: scopeCollection });
              }
            }}
            aria-label={t("similarity.scope")}
          >
            <option value="library">{t("similarity.library")}</option>
            <option value="models">{t("similarity.modelSet")}</option>
            {scope.kind === "source" && !availableSources.some((item) => item.id === scope.id) && (
              <option value={`source:${scope.id}`}>
                {t("similarity.sourceScope", { name: scope.name })}
              </option>
            )}
            {availableSources.map((source) => (
              <option key={`source:${source.id}`} value={`source:${source.id}`}>
                {t("similarity.sourceScope", { name: source.name })}
              </option>
            ))}
            <option value="pick">{uiText("Choose existing collection")}</option>
            {scopeCollection && (
              <option value={scopeCollection.id}>{scopeCollection.display_path}</option>
            )}
          </select>
          {scope.kind === "chooseCollection" && (
            <CollectionPicker
              minRole="edit"
              selectedPath={null}
              emptyLabel={uiText("No editable collections.")}
              onSelect={(collection) => {
                if (collection === null) throw new Error("A collection is required");
                setScopeCollection(collection);
                setScope({ kind: "collection", collection });
              }}
            />
          )}
        </div>
        <Button
          loading={start.isPending}
          disabled={
            !status.data?.enabled ||
            command === null ||
            (scope.kind === "source" &&
              !availableSources.some((source) => source.id === scope.id)) ||
            (scope.kind === "models" && command.ids.length === 0) ||
            !!activeRun
          }
          onClick={() => {
            if (command === null) throw new Error("An analysis scope is required");
            const session = getSessionVersion();
            start.mutate(
              { ...command, session },
              {
                onSuccess: (run) => {
                  if (session === getSessionVersion()) trackSimilarityRun(run);
                },
                onError: (error) => {
                  if (session === getSessionVersion()) toast.error(error);
                },
              },
            );
          }}
        >
          {t("similarity.start")}
        </Button>
        {activeRun && (
          <p role="status" className="text-xs text-muted-foreground">
            {t("similarity.activeScope")}
          </p>
        )}
      </div>
      {sources.isError && (
        <div role="alert" className="p-3 text-sm text-destructive">
          {t("similarity.loadError")}{" "}
          <Button variant="ghost" size="sm" onClick={() => void sources.refetch()}>
            {t("similarity.retry")}
          </Button>
        </div>
      )}
      {scope.kind === "models" && (
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
              <Button variant="ghost" size="sm" onClick={() => void modelChoices.refetch()}>
                {t("similarity.retry")}
              </Button>
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
      <details className="border-t">
        <summary className="cursor-pointer px-4 py-3 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
          {t("similarity.history")}
        </summary>
        {history.isError && (
          <p role="alert" className="px-4 pb-4 text-sm text-destructive">
            {t("similarity.loadError")}
            <Button
              variant="ghost"
              size="sm"
              onClick={() =>
                void (history.isFetchNextPageError ? history.fetchNextPage() : history.refetch())
              }
            >
              {t("similarity.retry")}
            </Button>
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
                    onClick={() => {
                      const session = getSessionVersion();
                      cancel.mutate(
                        { id: run.id, session },
                        {
                          onError: (error) => {
                            if (session === getSessionVersion()) toast.error(error);
                          },
                        },
                      );
                    }}
                  >
                    {t("similarity.cancel")}
                  </Button>
                )}
              </li>
            ))}
        </ul>
        {history.hasNextPage && !history.isFetchNextPageError && (
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
      </details>
    </Card>
  );
}
