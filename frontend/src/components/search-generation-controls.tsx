import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { listIngestJobs } from "@/lib/api/models";
import {
  actOnSearchGeneration,
  cancelInferenceDownload,
  deleteInferenceModel,
  downloadInferenceModel,
  estimateSearchGeneration,
  listInferenceModels,
  listSearchGenerations,
  prepareSearchGeneration,
  validateInferenceModel,
} from "@/lib/api/search";
import { formatBytes, formatDuration, timeAgo } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import { isMessageKey } from "@/lib/locale";
import { toast } from "@/lib/toast";
import type { GenerationProposal, SearchGeneration, SearchSettingsRead } from "@/types/search";

export function SearchGenerationControls({ settings }: { settings: SearchSettingsRead }) {
  const { t } = useI18n();
  const statusLabel = (key: string) => t(isMessageKey(key) ? key : "aiSearch.unspecified");
  const [selection, setSelection] = useState("");
  const [profile, setProfile] =
    useState<NonNullable<GenerationProposal["profile"]>>("semantic_text");
  const [aggregation, setAggregation] = useState<"mean" | "max">("mean");
  const [indexBackend, setIndexBackend] = useState<GenerationProposal["index_backend"]>("auto");
  const [quantization, setQuantization] = useState<GenerationProposal["quantization"]>("float32");
  const [dimension, setDimension] = useState(0);
  const [autoActivate, setAutoActivate] = useState(true);
  const [customPrefixes, setCustomPrefixes] = useState(false);
  const [queryPrefix, setQueryPrefix] = useState("");
  const [documentPrefix, setDocumentPrefix] = useState("");
  const models = useQuery({ queryKey: ["ai-search", "models"], queryFn: listInferenceModels });
  const generations = useQuery({
    queryKey: ["ai-search", "generations"],
    queryFn: listSearchGenerations,
    refetchInterval: (query) =>
      query.state.data?.some((generation) => generation.state === "building") ? 5000 : false,
  });
  const downloads = useQuery({
    queryKey: ["ai-search", "downloads"],
    queryFn: async () => (await listIngestJobs()).filter((job) => job.kind === "model_download"),
    refetchInterval: (query) =>
      query.state.data?.some((job) => job.state === "pending" || job.state === "running")
        ? 1500
        : 15000,
  });
  const finished =
    downloads.data
      ?.filter((job) => job.state === "completed")
      .map((job) => job.job_id)
      .join(",") ?? "";
  const refreshModels = models.refetch;
  useEffect(() => {
    if (finished) void refreshModels();
  }, [finished, refreshModels]);
  const local = models.data?.find((model) => selection === `local:${model.id}`);
  const remote = settings.endpoints.find((endpoint) => selection === `endpoint:${endpoint.id}`);
  const proposal: GenerationProposal | null =
    local || remote
      ? {
          ...(local ? { local_model_id: local.id } : { endpoint_id: remote?.id }),
          index_backend: indexBackend,
          quantization,
          profile,
          aggregation: profile === "multiview" ? aggregation : "mean",
          auto_activate: autoActivate,
        }
      : null;
  if (proposal && dimension) proposal.index_dimension = dimension;
  if (proposal && customPrefixes && profile === "semantic_text") {
    proposal.query_prefix = queryPrefix;
    proposal.document_prefix = documentPrefix;
  }
  const canPrepare =
    !!proposal &&
    settings.settings.enabled &&
    (!local ||
      (local.installed && local.runtime_available && settings.settings.local_models_enabled));
  const estimate = useMutation({ mutationFn: estimateSearchGeneration, onError: toast.error });
  const prepare = useMutation({
    mutationFn: prepareSearchGeneration,
    onSuccess: () => {
      void generations.refetch();
      toast.success(t("aiSearch.buildStarted"));
    },
    onError: toast.error,
  });
  const action = useMutation({
    mutationFn: ({
      generation,
      action,
    }: {
      generation: SearchGeneration;
      action: "activate" | "cancel" | "retry";
    }) => actOnSearchGeneration(generation, action),
    onSuccess: () => {
      void generations.refetch();
    },
    onError: toast.error,
  });
  const download = useMutation({
    mutationFn: downloadInferenceModel,
    onSuccess: () => {
      void downloads.refetch();
    },
    onError: toast.error,
  });
  const cancel = useMutation({
    mutationFn: cancelInferenceDownload,
    onSuccess: () => {
      void downloads.refetch();
    },
    onError: toast.error,
  });
  const validate = useMutation({
    mutationFn: validateInferenceModel,
    onSuccess: () => toast.success(t("aiSearch.modelReady")),
    onError: toast.error,
  });
  const remove = useMutation({
    mutationFn: deleteInferenceModel,
    onSuccess: () => {
      setSelection("");
      void models.refetch();
    },
    onError: toast.error,
  });
  const currentEstimate =
    estimate.variables && JSON.stringify(estimate.variables) === JSON.stringify(proposal)
      ? estimate.data
      : undefined;
  const dimensions = local?.mrl_dimensions ?? remote?.mrl_dimensions ?? [];
  const building = generations.data?.some(
    (generation) => generation.state === "building" && generation.profile === profile,
  );
  const active = generations.data?.filter((generation) => generation.state === "active") ?? [];
  return (
    <>
      <div className="border-t border-border bg-muted/30 px-4 py-4 sm:px-5">
        <h4 className="text-sm font-semibold">{t("aiSearch.activeIndex")}</h4>
        {generations.isPending ? (
          <p role="status" className="mt-1 text-sm text-muted-foreground">
            {t("aiSearch.loading")}
          </p>
        ) : active.length ? (
          active.map((generation) => (
            <p key={generation.id} className="mt-1 break-words text-sm">
              {statusLabel(`aiSearch.profile.${generation.profile}`)} · {generation.model}
            </p>
          ))
        ) : (
          <p className="mt-1 text-sm text-muted-foreground">{t("aiSearch.noActiveIndex")}</p>
        )}
      </div>
      <div className="space-y-4 border-t border-border p-4 sm:p-5">
        <h4 className="text-sm font-semibold">{t("aiSearch.prepareIndex")}</h4>
        <p className="max-w-prose text-xs text-muted-foreground">{t("aiSearch.pendingHelp")}</p>
        <label className="block space-y-1 text-sm">
          {t("aiSearch.indexPurpose")}
          <select
            className="block w-full rounded-md border border-input bg-background p-2"
            value={profile}
            onChange={(event) => {
              const value = event.target.value;
              if (
                value === "semantic_text" ||
                value === "thumbnail" ||
                value === "multiview" ||
                value === "point_cloud"
              ) {
                setProfile(value);
                setSelection("");
                setDimension(0);
              }
            }}
          >
            <option value="semantic_text">{t("aiSearch.profile.semantic_text")}</option>
            <option value="thumbnail">{t("aiSearch.profile.thumbnail")}</option>
            <option value="multiview">{t("aiSearch.profile.multiview")}</option>
            <option value="point_cloud">{t("aiSearch.profile.point_cloud")}</option>
          </select>
        </label>
        {profile !== "semantic_text" && (
          <p className="max-w-prose text-xs text-muted-foreground">
            {t(
              profile === "thumbnail"
                ? "aiSearch.thumbnailHelp"
                : profile === "point_cloud"
                  ? "aiSearch.pointCloudHelp"
                  : "aiSearch.multiviewHelp",
            )}
          </p>
        )}
        {profile === "multiview" && (
          <label className="block space-y-1 text-sm">
            {t("aiSearch.aggregation")}
            <select
              className="block w-full rounded-md border border-input bg-background p-2"
              value={aggregation}
              onChange={(event) => {
                if (event.target.value === "mean" || event.target.value === "max")
                  setAggregation(event.target.value);
              }}
            >
              <option value="mean">{t("aiSearch.meanViews")}</option>
              <option value="max">{t("aiSearch.bestView")}</option>
            </select>
          </label>
        )}
        <label className="block space-y-1 text-sm">
          {t("aiSearch.modelOrServer")}
          <select
            className="block w-full rounded-md border border-input bg-background p-2"
            value={selection}
            onChange={(event) => {
              setSelection(event.target.value);
              setDimension(0);
            }}
          >
            <option value="">{t("aiSearch.chooseModel")}</option>
            <optgroup label={t("aiSearch.localModels")}>
              {models.data
                ?.filter(
                  (model) =>
                    model.modality ===
                    (profile === "semantic_text"
                      ? "text"
                      : profile === "point_cloud"
                        ? "point_cloud"
                        : "text_image"),
                )
                .map((model) => (
                  <option key={model.id} value={`local:${model.id}`}>
                    {model.key} ·{" "}
                    {t(model.installed ? "aiSearch.installed" : "aiSearch.downloadRequired")}
                  </option>
                ))}
            </optgroup>
            {profile === "semantic_text" && (
              <optgroup label={t("aiSearch.compatibleServers")}>
                {settings.endpoints
                  .filter((endpoint) => endpoint.kind === "embedding")
                  .map((endpoint) => (
                    <option key={endpoint.id} value={`endpoint:${endpoint.id}`}>
                      {endpoint.model} · {endpoint.host}
                    </option>
                  ))}
              </optgroup>
            )}
          </select>
        </label>
        {models.isError && (
          <p role="alert" className="text-sm text-destructive">
            {t("aiSearch.modelsError")}
          </p>
        )}
        {local && (
          <div className="space-y-2">
            <dl className="grid gap-x-4 gap-y-1 text-xs sm:grid-cols-2">
              <div>
                <dt className="text-muted-foreground">{t("aiSearch.languages")}</dt>
                <dd>{local.languages.join(", ") || t("aiSearch.unspecified")}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">{t("aiSearch.license")}</dt>
                <dd>{local.license ?? t("aiSearch.unspecified")}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">{t("aiSearch.modelSize")}</dt>
                <dd>{formatBytes(local.size_bytes)}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">{t("aiSearch.nativeDimension")}</dt>
                <dd>{local.native_dimension}</dd>
              </div>
              <div className="min-w-0 sm:col-span-2">
                <dt className="text-muted-foreground">{t("aiSearch.provenance")}</dt>
                <dd className="break-all">
                  {local.repository ?? local.key}@{local.revision}
                </dd>
              </div>
            </dl>
            {!local.runtime_available && (
              <p role="status" className="text-sm text-warning">
                {t("aiSearch.runtimeUnavailable")}
              </p>
            )}
            <div className="flex flex-wrap gap-2">
              {!local.installed && local.curated && (
                <Button
                  variant="outline"
                  size="sm"
                  loading={download.isPending}
                  disabled={
                    !settings.settings.enabled ||
                    !settings.settings.local_models_enabled ||
                    !settings.settings.download_enabled ||
                    !local.runtime_available ||
                    downloads.data?.some(
                      (job) => job.state === "running" || job.state === "pending",
                    )
                  }
                  onClick={() => download.mutate(local.key)}
                >
                  {t("aiSearch.downloadModel")}
                </Button>
              )}
              {local.installed && (
                <Button
                  variant="outline"
                  size="sm"
                  loading={validate.isPending}
                  disabled={!local.runtime_available}
                  onClick={() => validate.mutate(local.id)}
                >
                  {t("aiSearch.verifyModel")}
                </Button>
              )}
              {local.installed && !local.referenced && (
                <Button
                  variant="ghost"
                  size="sm"
                  loading={remove.isPending}
                  onClick={() => remove.mutate(local.id)}
                >
                  {t("aiSearch.removeModel")}
                </Button>
              )}
            </div>
          </div>
        )}
        {remote && (
          <p className="text-sm text-muted-foreground">
            {t("aiSearch.indexDisclosure", { host: remote.host })}
          </p>
        )}
        <details className="space-y-4 border-t pt-3">
          <summary className="cursor-pointer text-sm font-medium">
            {t("aiSearch.indexAdvanced")}
          </summary>
          <p className="text-xs text-muted-foreground">{t("aiSearch.indexDefaultsHelp")}</p>
          <details>
            <summary className="cursor-pointer text-sm font-medium">
              {t("aiSearch.offlineCustom")}
            </summary>
            <p className="mt-2 max-w-prose text-xs leading-relaxed text-muted-foreground">
              {t("aiSearch.offlineHelp")}
            </p>
            <Button variant="ghost" size="sm" onClick={() => void models.refetch()}>
              {t("aiSearch.refreshModels")}
            </Button>
          </details>
          <div className="grid gap-3 sm:grid-cols-3">
            <label className="space-y-1 text-sm">
              {t("aiSearch.indexDimension")}
              <select
                className="block w-full rounded-md border border-input bg-background p-2"
                value={dimension}
                onChange={(event) => setDimension(Number(event.target.value))}
              >
                <option value={0}>{t("aiSearch.nativeDimension")}</option>
                {dimensions.map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </label>
            <label className="space-y-1 text-sm">
              {t("aiSearch.quantization")}
              <select
                className="block w-full rounded-md border border-input bg-background p-2"
                value={quantization}
                onChange={(event) => {
                  const value = event.target.value;
                  if (value === "float32" || value === "int8" || value === "binary")
                    setQuantization(value);
                }}
              >
                <option value="float32">{t("aiSearch.float32")}</option>
                <option value="int8">{t("aiSearch.int8")}</option>
                <option value="binary">{t("aiSearch.binary")}</option>
              </select>
            </label>
            <label className="space-y-1 text-sm">
              {t("aiSearch.backend")}
              <select
                className="block w-full rounded-md border border-input bg-background p-2"
                value={indexBackend}
                onChange={(event) => {
                  const value = event.target.value;
                  if (value === "auto" || value === "numpy") setIndexBackend(value);
                }}
              >
                <option value="auto">{t("aiSearch.autoBackend")}</option>
                <option value="numpy">{t("aiSearch.portableBackend")}</option>
              </select>
            </label>
          </div>
          {profile === "semantic_text" && (
            <details>
              <summary className="cursor-pointer text-sm font-medium">
                {t("aiSearch.inputRecipe")}
              </summary>
              <label className="mt-3 flex items-center gap-2 text-sm">
                <Checkbox
                  ariaLabel={t("aiSearch.customPrefixes")}
                  checked={customPrefixes}
                  onChange={setCustomPrefixes}
                />
                {t("aiSearch.customPrefixes")}
              </label>
              {customPrefixes && (
                <div className="mt-3 grid gap-3 sm:grid-cols-2">
                  <label className="space-y-1 text-sm">
                    {t("aiSearch.queryPrefix")}
                    <Input
                      value={queryPrefix}
                      maxLength={256}
                      onChange={(event) => setQueryPrefix(event.target.value)}
                    />
                  </label>
                  <label className="space-y-1 text-sm">
                    {t("aiSearch.documentPrefix")}
                    <Input
                      value={documentPrefix}
                      maxLength={256}
                      onChange={(event) => setDocumentPrefix(event.target.value)}
                    />
                  </label>
                </div>
              )}
            </details>
          )}
          <label className="flex items-center gap-2 text-sm">
            <Checkbox
              ariaLabel={t("aiSearch.autoActivate")}
              checked={autoActivate}
              onChange={setAutoActivate}
            />
            {t("aiSearch.autoActivate")}
          </label>
        </details>
        <p role="status" className="text-sm text-muted-foreground">
          {!settings.settings.enabled
            ? t("aiSearch.enableFirst")
            : !proposal
              ? t("aiSearch.chooseFirst")
              : local && !settings.settings.local_models_enabled
                ? t("aiSearch.localFirst")
                : local && !local.runtime_available
                  ? t("aiSearch.runtimeUnavailable")
                  : local && !local.installed
                    ? t(
                        settings.settings.download_enabled
                          ? "aiSearch.downloadFirst"
                          : "aiSearch.allowFirst",
                      )
                    : building
                      ? t("aiSearch.alreadyBuilding")
                      : currentEstimate?.fits_budget === false
                        ? t("aiSearch.overBudget")
                        : t("aiSearch.readyToBuild")}
        </p>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            loading={estimate.isPending}
            disabled={!proposal || (!!local && !local.installed)}
            onClick={() => {
              if (proposal) estimate.mutate(proposal);
            }}
          >
            {t("aiSearch.estimate")}
          </Button>
          <Button
            loading={prepare.isPending}
            disabled={!canPrepare || building || currentEstimate?.fits_budget === false}
            onClick={() => {
              if (proposal) prepare.mutate(proposal);
            }}
          >
            {t("aiSearch.buildIndex")}
          </Button>
        </div>
        {currentEstimate && (
          <p role="status" className="text-sm text-muted-foreground">
            {t(
              profile === "semantic_text"
                ? "aiSearch.estimateResult"
                : "aiSearch.estimateVisualResult",
              {
                count: currentEstimate.passages,
                size: formatBytes(currentEstimate.estimated_bytes),
                existing: formatBytes(currentEstimate.existing_bytes),
                time:
                  currentEstimate.estimated_seconds === null
                    ? t("aiSearch.estimateUnknown")
                    : formatDuration(currentEstimate.estimated_seconds),
              },
            )}
            {!currentEstimate.fits_budget && (
              <span className="block text-destructive">{t("aiSearch.overBudget")}</span>
            )}
          </p>
        )}
        {prepare.isError && (
          <p role="alert" className="text-sm text-destructive">
            {t("aiSearch.buildError")}
          </p>
        )}
      </div>
      {!!downloads.data?.length && (
        <div className="border-t border-border px-4 py-3 sm:px-5">
          <h4 className="text-sm font-semibold">{t("aiSearch.downloads")}</h4>
          <ul className="mt-2 divide-y divide-border">
            {downloads.data.map((job) => (
              <li
                key={job.job_id}
                className="flex flex-wrap items-center justify-between gap-3 py-3"
              >
                <div className="min-w-0 flex-1">
                  <p className="text-sm">{t(`aiSearch.download.${job.state}`)}</p>
                  <progress
                    aria-label={t("aiSearch.downloadProgress")}
                    className="mt-2 h-2 w-full accent-primary"
                    value={job.progress ?? 0}
                    max={100}
                  />
                  <p className="mt-1 text-xs text-muted-foreground">
                    {formatBytes(job.processed)} / {formatBytes(job.total)}
                  </p>
                  {job.error && (
                    <p className="text-xs text-destructive">
                      {t("aiSearch.downloadError")} <span className="break-all">{job.error}</span>
                    </p>
                  )}
                </div>
                {(job.state === "pending" || job.state === "running") && (
                  <Button
                    variant="outline"
                    size="sm"
                    loading={cancel.isPending}
                    onClick={() => cancel.mutate(job.job_id)}
                  >
                    {t("aiSearch.cancel")}
                  </Button>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="border-t border-border px-4 py-3 sm:px-5">
        <h4 className="text-sm font-semibold">{t("aiSearch.history")}</h4>
        {generations.isError && (
          <p role="alert" className="mt-2 text-sm text-destructive">
            {t("aiSearch.historyError")}
          </p>
        )}
        {generations.data?.length === 0 && (
          <p className="mt-2 text-sm text-muted-foreground">{t("aiSearch.noHistory")}</p>
        )}
        <ul className="mt-2 divide-y divide-border">
          {generations.data?.map((generation) => (
            <li
              key={generation.id}
              className="flex flex-wrap items-start justify-between gap-3 py-3"
            >
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2 break-words text-sm font-medium">
                  {statusLabel(`aiSearch.profile.${generation.profile}`)} · {generation.model}{" "}
                  <Badge variant="outline">
                    {statusLabel(`aiSearch.generation.${generation.state}`)}
                  </Badge>
                </div>
                <p className="mt-1 text-xs text-muted-foreground">
                  {statusLabel(`aiSearch.phase.${generation.phase}`)} ·{" "}
                  {t("aiSearch.indexProgress", {
                    indexed: generation.indexed,
                    total: generation.eligible,
                  })}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {generation.index_dimension} · {generation.quantization} ·{" "}
                  {generation.effective_backend}
                </p>
                {generation.eta_seconds !== null && (
                  <p className="mt-1 text-xs text-muted-foreground">
                    {t("aiSearch.eta", { time: formatDuration(generation.eta_seconds) })}
                  </p>
                )}
                {generation.last_activity_at && (
                  <p className="mt-1 text-xs text-muted-foreground">
                    {t("aiSearch.lastActivity", { time: timeAgo(generation.last_activity_at) })}
                  </p>
                )}
                {generation.quarantined > 0 && (
                  <p className="mt-1 text-xs text-warning">
                    {t("aiSearch.quarantined", { count: generation.quarantined })}
                  </p>
                )}
                {generation.truncated_count > 0 && (
                  <p className="mt-1 text-xs text-muted-foreground">
                    {t("aiSearch.truncatedInputs", { count: generation.truncated_count })}
                  </p>
                )}
                {generation.error_code && (
                  <p className="mt-1 break-all text-xs text-destructive">
                    {t("aiSearch.generationError")} {generation.error_code}
                  </p>
                )}
              </div>
              <div className="flex flex-wrap gap-2">
                {generation.version_token && (
                  <>
                    {generation.state === "building" && generation.phase === "ready" && (
                      <Button
                        size="sm"
                        loading={action.isPending}
                        onClick={() => action.mutate({ generation, action: "activate" })}
                      >
                        {t("aiSearch.activate")}
                      </Button>
                    )}
                    {generation.state === "building" && (
                      <Button
                        size="sm"
                        variant="outline"
                        loading={action.isPending}
                        onClick={() => action.mutate({ generation, action: "cancel" })}
                      >
                        {t("aiSearch.cancel")}
                      </Button>
                    )}
                    {["active", "building"].includes(generation.state) &&
                      (generation.quarantined > 0 || !!generation.error_code) && (
                        <Button
                          size="sm"
                          variant="outline"
                          loading={action.isPending}
                          onClick={() => action.mutate({ generation, action: "retry" })}
                        >
                          {t("aiSearch.retry")}
                        </Button>
                      )}
                  </>
                )}
              </div>
            </li>
          ))}
        </ul>
      </div>
    </>
  );
}
