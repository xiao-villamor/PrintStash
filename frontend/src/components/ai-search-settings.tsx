import { SearchSettingsSnapshot } from "@/components/search-settings-snapshot";
import { useEffect, useId, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";

import { AiSearchSetup } from "@/components/ai-search-setup";
import { InferenceEndpointForm } from "@/components/inference-endpoint-form";
import { SearchGenerationControls } from "@/components/search-generation-controls";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { TabBar } from "@/components/ui/tabs";
import {
  searchSettingsOptions,
  useAiSearchCatalog,
  useSearchCommands,
  type AiSearchCatalog,
} from "@/lib/queries/search";
import { getSessionVersion } from "@/lib/session-transport";
import { parseApiError } from "@/lib/errors";
import { useI18n } from "@/lib/i18n";
import { formatBytes } from "@/lib/format";
import { toast } from "@/lib/toast";
import { captureEditingBase } from "@/lib/api/editing";
import type { SearchSettings, SearchSettingsRead } from "@/types/search";

function SettingsForm({
  initial,
  catalog,
  readOnly,
}: {
  initial: SearchSettingsRead;
  catalog: AiSearchCatalog;
  readOnly: boolean;
}) {
  const { t } = useI18n();
  const [original, setOriginal] = useState(initial);
  const [draft, setDraft] = useState(initial.settings);
  const [review, setReview] = useState<
    null | { phase: "required" | "loading" } | { phase: "ready"; snapshot: SearchSettingsRead }
  >(null);
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);
  const helpId = useId();
  const { models } = catalog;
  const { download: downloadSparse, settings: save, reviewSettings } = useSearchCommands();
  const sparse = models.data?.find(
    (model) => model.id === draft.sparse_model_id && model.modality === "sparse",
  );
  const chat = initial.endpoints.find(
    (endpoint) => endpoint.id === draft.chat_endpoint_id && endpoint.kind === "chat",
  );
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
        if (readOnly || save.isPending || (review && review.phase !== "ready")) return;
        const target = review?.phase === "ready" ? review.snapshot : original;
        if (target.edit_epoch !== original.edit_epoch) return;
        const payload: SearchSettings = { ...target.settings };
        // SAFETY: draft is the SearchSettings form state; keys index that same shape.
        for (const key of Object.keys(draft) as (keyof SearchSettings)[]) {
          if (JSON.stringify(draft[key]) !== JSON.stringify(original.settings[key]))
            Object.assign(payload, { [key]: draft[key] });
        }
        const session = getSessionVersion();
        save.mutate(
          { payload, base: captureEditingBase(target), session },
          {
            onSuccess: (result) => {
              if (!live.current || session !== getSessionVersion()) return;
              setOriginal(result);
              setDraft(result.settings);
              setReview(null);
              toast.success(t("aiSearch.settingsSaved"));
            },
            onError: (error) => {
              if (!live.current || session !== getSessionVersion()) return;
              const parsed = parseApiError(error);
              if ([0, 401, 403, 404, 412, 428].includes(parsed.status) || parsed.status >= 500)
                setReview({ phase: "required" });
              toast.error(error);
            },
          },
        );
      }}
    >
      <fieldset disabled={readOnly || save.isPending} className="space-y-4">
        <div>
          <h3 className="text-base font-semibold">{t("aiSearch.technicalTitle")}</h3>
          <p className="mt-1 max-w-prose text-sm text-muted-foreground">
            {t("aiSearch.technicalIntro")}
          </p>
        </div>
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
        <section
          className="space-y-3 border-t border-border pt-4"
          aria-label={t("aiSearch.advanced")}
        >
          <h4 className="text-sm font-semibold">{t("aiSearch.advanced")}</h4>
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
                onClick={() =>
                  downloadSparse.mutate(
                    { key: sparse.key, session: getSessionVersion() },
                    { onError: toast.error },
                  )
                }
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
        </section>
        {save.isError && (
          <p role="alert" className="text-sm text-destructive">
            {t("aiSearch.settingsError")}
          </p>
        )}
        {review && (
          <section
            aria-label={t("library.latestVersion")}
            className="space-y-3 rounded-md border border-border p-3"
          >
            <p role="alert">{t("library.saveUnconfirmed")}</p>
            <Button
              type="button"
              variant="outline"
              loading={review.phase === "loading"}
              onClick={async () => {
                const session = getSessionVersion();
                setReview({ phase: "loading" });
                try {
                  const snapshot = await reviewSettings(session);
                  if (live.current && session === getSessionVersion())
                    setReview({ phase: "ready", snapshot });
                } catch (error) {
                  if (!live.current || session !== getSessionVersion()) return;
                  setReview({ phase: "required" });
                  toast.error(error);
                }
              }}
            >
              {t("library.reviewLatest")}
            </Button>
            {review.phase === "ready" && (
              <>
                <SearchSettingsSnapshot snapshot={review.snapshot} catalog={catalog} />
                {review.snapshot.edit_epoch !== original.edit_epoch && (
                  <p role="alert">{t("aiSearch.historyChanged")}</p>
                )}
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => {
                    setOriginal(review.snapshot);
                    setDraft(review.snapshot.settings);
                    setReview(null);
                    save.reset();
                  }}
                >
                  {t("library.useLatest")}
                </Button>
              </>
            )}
          </section>
        )}
        <Button
          type="submit"
          loading={save.isPending}
          disabled={Boolean(
            review &&
            (review.phase !== "ready" || review.snapshot.edit_epoch !== original.edit_epoch),
          )}
        >
          {t(review?.phase === "ready" ? "library.retryDraft" : "aiSearch.saveSettings")}
        </Button>
      </fieldset>
    </form>
  );
}

export function AiSearchSettings() {
  const { t } = useI18n();
  const [section, setSection] = useState<"setup" | "indexes" | "servers" | "technical">("setup");
  const [editing, setEditing] = useState<number | "new" | null>(null);
  const settings = useQuery(searchSettingsOptions());
  const denied = settings.isError && [401, 403, 404].includes(parseApiError(settings.error).status);
  const catalog = useAiSearchCatalog(!!settings.data && !denied);
  const { importEndpoint: fromEnvironment } = useSearchCommands();
  const refresh = () => {
    void settings.refetch();
  };
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
      {settings.data && !denied && (
        <div className="border-b border-border bg-muted/30 p-3">
          <TabBar
            tabs={[
              { key: "setup", label: t("aiSearch.navSetup") },
              { key: "indexes", label: t("aiSearch.navIndexes") },
              { key: "servers", label: t("aiSearch.navServers") },
              { key: "technical", label: t("aiSearch.navTechnical") },
            ]}
            active={section}
            onChange={(next) => {
              setSection(next);
              if (next !== "servers") setEditing(null);
            }}
            showIndicator={false}
            className="grid w-full grid-cols-2 gap-1 rounded-md bg-background p-1 ring-1 ring-border sm:flex sm:w-max"
            tabClassName="min-h-10 rounded-md px-3 text-sm font-medium text-muted-foreground transition-[background-color,color,transform] duration-press active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            activeTabClassName="bg-accent text-accent-foreground"
          />
        </div>
      )}
      {settings.isError && (
        <EmptyState
          title={t("aiSearch.settingsLoadError")}
          action={<Button onClick={refresh}>{t("aiSearch.retry")}</Button>}
        />
      )}
      {denied ? null : !settings.data ? (
        <p role="status" className="p-5 text-sm">
          {t("aiSearch.loading")}
        </p>
      ) : (
        <>
          {section === "setup" ? (
            <fieldset disabled={settings.isError} className="min-w-0">
              <AiSearchSetup settings={settings.data} catalog={catalog} />
            </fieldset>
          ) : section === "indexes" ? (
            <fieldset disabled={settings.isError} className="min-w-0">
              <SearchGenerationControls settings={settings.data} catalog={catalog} />
            </fieldset>
          ) : section === "technical" ? (
            <>
              <SettingsForm initial={settings.data} catalog={catalog} readOnly={settings.isError} />
            </>
          ) : (
            <fieldset disabled={settings.isError} className="min-w-0 space-y-5 p-4 sm:p-5">
              <div>
                <h3 className="text-base font-semibold">{t("aiSearch.endpoints")}</h3>
                <p className="mt-1 max-w-prose text-sm text-muted-foreground">
                  {t("aiSearch.serversIntro")}
                </p>
              </div>
              {editing === null ? (
                <>
                  {settings.data.endpoints.length ? (
                    <ul className="divide-y divide-border rounded-md border border-border">
                      {settings.data.endpoints.map((endpoint) => (
                        <li
                          key={endpoint.id}
                          className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between"
                        >
                          <div className="min-w-0">
                            <p className="break-words text-sm font-semibold">{endpoint.model}</p>
                            <p className="mt-1 break-all text-xs text-muted-foreground">
                              {endpoint.host} ·{" "}
                              {t(
                                endpoint.kind === "embedding"
                                  ? "aiSearch.embeddingEndpoint"
                                  : "aiSearch.chatEndpoint",
                              )}
                            </p>
                          </div>
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => setEditing(endpoint.id)}
                          >
                            {t("aiSearch.editServer", { model: endpoint.model })}
                          </Button>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="rounded-md border border-border p-4 text-sm text-muted-foreground">
                      {t("aiSearch.noServers")}
                    </p>
                  )}
                  <Button onClick={() => setEditing("new")}>{t("aiSearch.newEndpoint")}</Button>
                  {settings.data.environment_endpoints.map((kind) => (
                    <Button
                      key={kind}
                      variant="outline"
                      loading={fromEnvironment.isPending}
                      onClick={() =>
                        fromEnvironment.mutate(
                          { kind, session: getSessionVersion() },
                          { onError: toast.error },
                        )
                      }
                    >
                      {t(
                        kind === "embedding"
                          ? "aiSearch.importEmbeddingEnvironment"
                          : "aiSearch.importChatEnvironment",
                      )}
                    </Button>
                  ))}
                </>
              ) : (
                <div className="space-y-4 rounded-md border border-border p-4">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <h4 className="text-sm font-semibold">
                      {editing === "new" ? t("aiSearch.newEndpoint") : t("aiSearch.editEndpoint")}
                    </h4>
                    <Button variant="ghost" size="sm" onClick={() => setEditing(null)}>
                      {t("aiSearch.backToServers")}
                    </Button>
                  </div>
                  <InferenceEndpointForm
                    key={editing}
                    initial={settings.data.endpoints.find((endpoint) => endpoint.id === editing)}
                    onSaved={() => {
                      setEditing(null);
                    }}
                  />
                </div>
              )}
            </fieldset>
          )}
        </>
      )}
    </Card>
  );
}
