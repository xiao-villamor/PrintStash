import { useId, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Search, SlidersHorizontal } from "lucide-react";

import { AiSearchSetup } from "@/components/ai-search-setup";
import { InferenceEndpointForm } from "@/components/inference-endpoint-form";
import { SearchGenerationControls } from "@/components/search-generation-controls";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import {
  downloadInferenceModel,
  getSearchSettings,
  importEnvironmentEndpoint,
  listInferenceModels,
  saveSearchSettings,
} from "@/lib/api/search";
import { useI18n } from "@/lib/i18n";
import { formatBytes } from "@/lib/format";
import { toast } from "@/lib/toast";
import type { SearchSettingsRead } from "@/types/search";

function SettingsForm({ initial, onSaved }: { initial: SearchSettingsRead; onSaved: () => void }) {
  const { t } = useI18n();
  const [draft, setDraft] = useState(initial.settings);
  const helpId = useId();
  const queryClient = useQueryClient();
  const models = useQuery({ queryKey: ["ai-search", "models"], queryFn: listInferenceModels });
  const sparse = models.data?.find(
    (model) => model.id === draft.sparse_model_id && model.modality === "sparse",
  );
  const downloadSparse = useMutation({
    mutationFn: downloadInferenceModel,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["ai-search", "downloads"] });
    },
    onError: toast.error,
  });
  const chat = initial.endpoints.find(
    (endpoint) => endpoint.id === draft.chat_endpoint_id && endpoint.kind === "chat",
  );
  const save = useMutation({
    mutationFn: () => saveSearchSettings(draft),
    onSuccess: () => {
      toast.success(t("aiSearch.settingsSaved"));
      onSaved();
    },
    onError: toast.error,
  });
  const toggles = [
    ["enabled", "aiSearch.enable", "aiSearch.enableHelp"],
    ["local_models_enabled", "aiSearch.enableLocal", "aiSearch.localPermissionHelp"],
    ["download_enabled", "aiSearch.allowDownloads", "aiSearch.downloadPermissionHelp"],
  ] as const;
  return (
    <form
      className="space-y-4 p-4 sm:p-5"
      aria-label={t("aiSearch.settingsTitle")}
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <h4 className="text-sm font-semibold">{t("aiSearch.setupPermissions")}</h4>
      <div className="space-y-3">
        {toggles.map(([field, label, help]) => (
          <label key={field} className="flex items-start gap-3 text-sm">
            <Checkbox
              ariaLabel={t(label)}
              ariaDescribedBy={`${helpId}-${field}`}
              checked={draft[field]}
              onChange={(value) =>
                setDraft({
                  ...draft,
                  [field]: value,
                  sparse_expansion_enabled:
                    field === "local_models_enabled" && !value
                      ? false
                      : draft.sparse_expansion_enabled,
                })
              }
            />
            <span className="space-y-1">
              <span className="block font-medium">{t(label)}</span>
              <span id={`${helpId}-${field}`} className="block text-xs text-muted-foreground">
                {t(help)}
              </span>
            </span>
          </label>
        ))}
      </div>
      <p className="max-w-prose text-xs leading-relaxed text-muted-foreground">
        {t("aiSearch.localHelp")}
      </p>
      <details className="border-t border-border pt-3">
        <summary className="cursor-pointer text-sm font-medium">{t("aiSearch.advanced")}</summary>
        <fieldset className="mt-3 space-y-3 rounded-md border border-border p-3">
          <legend className="px-1 text-sm font-medium">{t("aiSearch.sparseTitle")}</legend>
          <p className="max-w-prose text-xs leading-relaxed text-muted-foreground">
            {t("aiSearch.sparseHelp")}
          </p>
          <label className="block space-y-1 text-sm">
            {t("aiSearch.sparseModel")}
            <select
              className="block w-full rounded-md border border-input bg-background p-2"
              value={draft.sparse_model_id ?? ""}
              onChange={(event) =>
                setDraft({
                  ...draft,
                  sparse_model_id: event.target.value || null,
                  sparse_expansion_enabled: false,
                })
              }
            >
              <option value="">{t("aiSearch.chooseModel")}</option>
              {models.data
                ?.filter((model) => model.modality === "sparse" && model.curated)
                .map((model) => (
                  <option key={model.id} value={model.id}>
                    {model.key} ·{" "}
                    {t(model.installed ? "aiSearch.installed" : "aiSearch.downloadRequired")}
                  </option>
                ))}
            </select>
          </label>
          {sparse && (
            <p className="break-all text-xs text-muted-foreground">
              {sparse.repository}@{sparse.revision} · {sparse.license} ·{" "}
              {formatBytes(sparse.size_bytes)}
            </p>
          )}
          {sparse && !sparse.installed && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              loading={downloadSparse.isPending}
              disabled={
                !initial.settings.enabled ||
                !initial.settings.local_models_enabled ||
                !initial.settings.download_enabled ||
                !sparse.runtime_available
              }
              onClick={() => downloadSparse.mutate(sparse.key)}
            >
              {t("aiSearch.downloadModel")}
            </Button>
          )}
          <label className="flex items-center gap-2 text-sm">
            <Checkbox
              ariaLabel={t("aiSearch.sparseEnable")}
              checked={draft.sparse_expansion_enabled}
              disabled={
                !draft.local_models_enabled || !sparse?.installed || !sparse.runtime_available
              }
              onChange={(value) => setDraft({ ...draft, sparse_expansion_enabled: value })}
            />
            {t("aiSearch.sparseEnable")}
          </label>
          {!sparse?.installed && (
            <p className="text-xs text-muted-foreground">{t("aiSearch.sparseMissing")}</p>
          )}
        </fieldset>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <label className="space-y-1 text-sm">
            {t("aiSearch.lexicalBackend")}
            <select
              className="block w-full rounded-md border border-input bg-background p-2"
              value={draft.lexical_backend}
              onChange={(event) =>
                setDraft({
                  ...draft,
                  lexical_backend: event.target.value === "ranked_like" ? "ranked_like" : "auto",
                })
              }
            >
              <option value="auto">{t("aiSearch.automatic")}</option>
              <option value="ranked_like">{t("aiSearch.rankedLike")}</option>
            </select>
          </label>
          {(
            [
              ["lexical_weight", "aiSearch.lexicalWeight", 0.01, 10, 0.01],
              ["semantic_weight", "aiSearch.semanticWeight", 0.01, 10, 0.01],
              ["rrf_k", "aiSearch.rankSmoothing", 1, 1000, 1],
            ] as const
          ).map(([field, label, min, max, step]) => (
            <label key={field} className="space-y-1 text-sm">
              {t(label)}
              <Input
                type="number"
                required
                min={min}
                max={max}
                step={step}
                value={draft[field]}
                onChange={(event) => setDraft({ ...draft, [field]: Number(event.target.value) })}
              />
            </label>
          ))}
          <label className="space-y-1 text-sm">
            {t("aiSearch.indexBudget")}
            <Input
              type="number"
              required
              min={1}
              max={1048576}
              value={draft.max_index_bytes / 1048576}
              onChange={(event) =>
                setDraft({ ...draft, max_index_bytes: Number(event.target.value) * 1048576 })
              }
            />
          </label>
          <label className="space-y-1 text-sm">
            {t("aiSearch.retention")}
            <Input
              type="number"
              required
              min={1}
              max={720}
              value={draft.rollback_retention_hours}
              onChange={(event) =>
                setDraft({ ...draft, rollback_retention_hours: Number(event.target.value) })
              }
            />
          </label>
          <label className="space-y-1 text-sm">
            {t("aiSearch.queryTimeout")}
            <Input
              type="number"
              required
              min={0.05}
              max={30}
              step={0.05}
              value={draft.query_timeout_seconds}
              onChange={(event) =>
                setDraft({ ...draft, query_timeout_seconds: Number(event.target.value) })
              }
            />
          </label>
          <label className="space-y-1 text-sm">
            {t("aiSearch.semanticFloor")}
            <Input
              type="number"
              required
              min={-1}
              max={1}
              step={0.01}
              value={draft.semantic_floor}
              onChange={(event) =>
                setDraft({ ...draft, semantic_floor: Number(event.target.value) })
              }
            />
          </label>
        </div>
        <label className="mt-3 block space-y-1 text-sm">
          {t("aiSearch.instanceTimezone")}
          <Input
            required
            maxLength={128}
            value={draft.timezone}
            onChange={(event) => setDraft({ ...draft, timezone: event.target.value })}
          />
        </label>
        <label className="mt-3 block space-y-1 text-sm">
          {t("aiSearch.chatEndpoint")}
          <select
            className="block w-full rounded-md border border-input bg-background p-2"
            value={draft.chat_endpoint_id ?? ""}
            onChange={(event) => {
              const endpoint = initial.endpoints.find(
                (item) => item.id === Number(event.target.value),
              );
              setDraft({
                ...draft,
                chat_endpoint_id: event.target.value ? Number(event.target.value) : null,
                captions_enabled: draft.captions_enabled && !!endpoint?.supports_images,
                nl_filters_enabled: draft.nl_filters_enabled && !!endpoint,
              });
            }}
          >
            <option value="">{t("aiSearch.none")}</option>
            {initial.endpoints
              .filter((endpoint) => endpoint.kind === "chat")
              .map((endpoint) => (
                <option key={endpoint.id} value={endpoint.id}>
                  {endpoint.model} · {endpoint.host}
                </option>
              ))}
          </select>
        </label>
        <div className="mt-3 space-y-2">
          {(
            [
              ["send_rendered_images", "aiSearch.sendRenderedImages"],
              ["send_query_images", "aiSearch.sendQueryImages"],
              ["captions_enabled", "aiSearch.enableCaptions"],
              ["nl_filters_enabled", "aiSearch.enableNl"],
            ] as const
          ).map(([field, label]) => (
            <label key={field} className="flex items-center gap-2 text-sm">
              <Checkbox
                ariaLabel={t(label)}
                ariaDescribedBy={`${helpId}-${field}`}
                checked={draft[field]}
                disabled={
                  field === "captions_enabled"
                    ? !chat?.supports_images || !draft.send_rendered_images
                    : field === "nl_filters_enabled" && !chat
                }
                onChange={(value) =>
                  setDraft({
                    ...draft,
                    [field]: value,
                    captions_enabled:
                      field === "send_rendered_images" && !value
                        ? false
                        : field === "captions_enabled"
                          ? value
                          : draft.captions_enabled,
                  })
                }
              />
              {t(label)}
            </label>
          ))}
        </div>
        <p className="mt-2 text-xs text-muted-foreground">{t("aiSearch.captionRequirements")}</p>
      </details>
      {save.isError && (
        <p role="alert" className="text-sm text-destructive">
          {t("aiSearch.settingsError")}
        </p>
      )}
      <Button type="submit" loading={save.isPending}>
        {t("aiSearch.saveSettings")}
      </Button>
    </form>
  );
}

export function AiSearchSettings() {
  const { t } = useI18n();
  const [advanced, setAdvanced] = useState(false);
  const [editing, setEditing] = useState<number | null>(null);
  const settings = useQuery({ queryKey: ["ai-search", "settings"], queryFn: getSearchSettings });
  const refresh = () => {
    void settings.refetch();
  };
  const fromEnvironment = useMutation({
    mutationFn: importEnvironmentEndpoint,
    onSuccess: refresh,
    onError: toast.error,
  });
  return (
    <Card className="overflow-hidden">
      <div className="flex items-start gap-3 border-b border-border px-4 py-4 sm:px-5">
        <Search className="h-8 w-8 shrink-0 rounded-md bg-muted p-1.5" aria-hidden />
        <div>
          <h2 className="text-sm font-semibold">{t("aiSearch.settingsTitle")}</h2>
          <p className="mt-1 max-w-prose text-xs text-muted-foreground">
            {t("aiSearch.settingsIntro")}
          </p>
        </div>
      </div>
      {settings.data && (
        <div className="border-b border-border bg-muted/30 px-4 py-3 sm:px-5">
          <Button variant="ghost" onClick={() => setAdvanced(!advanced)}>
            {advanced ? (
              <ArrowLeft className="h-4 w-4" aria-hidden />
            ) : (
              <SlidersHorizontal className="h-4 w-4" aria-hidden />
            )}
            {t(advanced ? "Back to guided setup" : "Advanced AI controls")}
          </Button>
        </div>
      )}
      {settings.isError ? (
        <EmptyState
          title={t("aiSearch.settingsLoadError")}
          action={<Button onClick={refresh}>{t("aiSearch.retry")}</Button>}
        />
      ) : !settings.data ? (
        <p role="status" className="p-5 text-sm">
          {t("aiSearch.loading")}
        </p>
      ) : (
        <>
          {!advanced ? (
            <AiSearchSetup settings={settings.data} onSaved={refresh} />
          ) : (
            <>
              <SettingsForm
                key={JSON.stringify(settings.data.settings)}
                initial={settings.data}
                onSaved={refresh}
              />
              <SearchGenerationControls settings={settings.data} />
              <details className="border-t border-border p-4 sm:p-5">
                <summary className="cursor-pointer text-sm font-semibold">
                  {t("aiSearch.endpoints")}
                </summary>
                <div className="mt-4 space-y-4">
                  <label className="block space-y-1 text-sm">
                    {t("aiSearch.editEndpoint")}
                    <select
                      className="block w-full rounded-md border border-input bg-background p-2"
                      value={editing ?? ""}
                      onChange={(event) =>
                        setEditing(event.target.value ? Number(event.target.value) : null)
                      }
                    >
                      <option value="">{t("aiSearch.newEndpoint")}</option>
                      {settings.data.endpoints.map((endpoint) => (
                        <option key={endpoint.id} value={endpoint.id}>
                          {endpoint.model} · {endpoint.host}
                        </option>
                      ))}
                    </select>
                  </label>
                  <InferenceEndpointForm
                    key={editing ?? "new"}
                    initial={settings.data.endpoints.find((endpoint) => endpoint.id === editing)}
                    onSaved={() => {
                      setEditing(null);
                      refresh();
                    }}
                  />
                  {settings.data.environment_endpoints.map((kind) => (
                    <Button
                      key={kind}
                      variant="outline"
                      loading={fromEnvironment.isPending}
                      onClick={() => fromEnvironment.mutate(kind)}
                    >
                      {t(
                        kind === "embedding"
                          ? "aiSearch.importEmbeddingEnvironment"
                          : "aiSearch.importChatEnvironment",
                      )}
                    </Button>
                  ))}
                </div>
              </details>
            </>
          )}
        </>
      )}
    </Card>
  );
}
