import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, HardDrive, Server, ArrowRight } from "lucide-react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { InferenceEndpointForm } from "@/components/inference-endpoint-form";
import { listIngestJobs } from "@/lib/api/models";
import {
  actOnSearchGeneration,
  cancelInferenceDownload,
  downloadInferenceModel,
  estimateSearchGeneration,
  getSearchStatus,
  listInferenceModels,
  listSearchGenerations,
  prepareSearchGeneration,
  saveSearchSettings,
} from "@/lib/api/search";
import { formatBytes } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import type { GenerationProposal, SearchSettingsRead } from "@/types/search";

/** The first-run path prepares text search; specialist search types stay in advanced controls. */
export function AiSearchSetup({
  settings,
  onSaved,
}: {
  settings: SearchSettingsRead;
  onSaved: () => void;
}) {
  const { t } = useI18n();
  const client = useQueryClient();
  const [path, setPath] = useState<"local" | "server" | null>(
    settings.settings.enabled
      ? settings.settings.local_models_enabled
        ? "local"
        : "server"
      : null,
  );
  const [selected, setSelected] = useState("");
  const [endpointId, setEndpointId] = useState<number | null>(null);
  const [changing, setChanging] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const models = useQuery({ queryKey: ["ai-search", "models"], queryFn: listInferenceModels });
  const generations = useQuery({
    queryKey: ["ai-search", "generations"],
    queryFn: listSearchGenerations,
    refetchInterval: (query) =>
      query.state.data?.some((g) => g.state === "building") ? 3000 : false,
  });
  const downloads = useQuery({
    queryKey: ["ai-search", "downloads"],
    queryFn: async () => (await listIngestJobs()).filter((job) => job.kind === "model_download"),
    refetchInterval: (query) =>
      query.state.data?.some((job) => job.state === "running" || job.state === "pending")
        ? 1500
        : false,
  });
  const completed =
    downloads.data
      ?.filter((job) => job.state === "completed")
      .map((job) => job.job_id)
      .join(",") ?? "";
  const refreshModels = models.refetch;
  useEffect(() => {
    if (completed) void refreshModels();
  }, [completed, refreshModels]);
  const choices = (models.data ?? [])
    .filter(
      (model) =>
        model.modality === "text" && model.runtime_available && (model.curated || model.installed),
    )
    .sort((a, b) => Number(b.installed) - Number(a.installed) || a.size_bytes - b.size_bytes);
  const local = choices.find((model) => model.id === selected) ?? choices[0];
  const servers = settings.endpoints.filter((endpoint) => endpoint.kind === "embedding");
  const remote = servers.find((endpoint) => endpoint.id === endpointId) ?? servers[0];
  const building = generations.data?.find(
    (g) => g.profile === "semantic_text" && g.state === "building",
  );
  const active = generations.data?.find(
    (g) => g.profile === "semantic_text" && g.state === "active",
  );
  const availability = useQuery({
    queryKey: [
      "ai-search",
      "setup-availability",
      active?.id,
      settings.settings.enabled,
      settings.settings.local_models_enabled,
    ],
    queryFn: getSearchStatus,
    enabled: settings.settings.enabled && !!active,
  });
  const failedPreparation = generations.data?.find(
    (generation) => generation.profile === "semantic_text" && generation.state === "failed",
  );
  const downloading = downloads.data?.find(
    (job) => job.state === "running" || job.state === "pending",
  );
  const failedDownload = downloads.data?.find((job) => job.state === "failed");
  const enabled =
    settings.settings.enabled && (path !== "local" || settings.settings.local_models_enabled);
  const change = useMutation({
    mutationFn: async (action: "enable" | "download" | "prepare" | "cancel" | "activate") => {
      setMessage(null);
      if (action === "enable") {
        const result = await saveSearchSettings({
          ...settings.settings,
          enabled: true,
          local_models_enabled: path === "local" ? true : settings.settings.local_models_enabled,
        });
        client.setQueryData(["ai-search", "settings"], result);
        onSaved();
      } else if (action === "download" && local) {
        if (!settings.settings.download_enabled) {
          const result = await saveSearchSettings({ ...settings.settings, download_enabled: true });
          client.setQueryData(["ai-search", "settings"], result);
          onSaved();
        }
        await downloadInferenceModel(local.key);
        await downloads.refetch();
      } else if (action === "prepare") {
        const proposal: GenerationProposal = {
          ...(path === "local" ? { local_model_id: local?.id } : { endpoint_id: remote?.id }),
          profile: "semantic_text",
          index_backend: "auto",
          quantization: "float32",
          auto_activate: true,
        };
        const estimate = await estimateSearchGeneration(proposal);
        if (!estimate.fits_budget) {
          setMessage(
            t(
              "There is not enough space in the AI search budget. Increase it in Advanced AI controls, then try again.",
            ),
          );
          return;
        }
        await prepareSearchGeneration(proposal);
        await generations.refetch();
        setChanging(false);
      } else if (action === "cancel") {
        if (building) await actOnSearchGeneration(building, "cancel");
        else if (downloading) await cancelInferenceDownload(downloading.job_id);
        await Promise.all([generations.refetch(), downloads.refetch()]);
      } else if (action === "activate" && building) {
        await actOnSearchGeneration(building, "activate");
        await generations.refetch();
      }
    },
    onError: () =>
      setMessage(t("This step could not be completed. Your library is safe. Try again.")),
  });
  const busy = change.isPending;
  if (models.isPending || generations.isPending || downloads.isPending || availability.isLoading)
    return (
      <p role="status" className="p-5">
        {t("Checking AI Search…")}
      </p>
    );
  if (models.isError || generations.isError || downloads.isError || availability.isError)
    return (
      <div className="space-y-3 p-5">
        <p role="alert">{t("AI setup could not be loaded.")}</p>
        <Button
          variant="outline"
          onClick={() => {
            void models.refetch();
            void generations.refetch();
            void downloads.refetch();
            if (active && settings.settings.enabled) void availability.refetch();
          }}
        >
          {t("Retry")}
        </Button>
      </div>
    );
  const ready =
    settings.settings.enabled && active && availability.data?.semantic_ready && !changing;
  const step = ready ? 3 : enabled && path ? 2 : 1;
  return (
    <div>
      <ol
        aria-label={t("AI setup progress")}
        className="grid grid-cols-3 gap-2 border-b bg-muted/30 px-4 py-4 text-xs sm:px-5"
      >
        {[t("Choose location"), t("Prepare search"), t("Start searching")].map((label, index) => (
          <li
            key={label}
            aria-current={step === index + 1 ? "step" : undefined}
            className={`flex items-center gap-2 ${step === index + 1 ? "font-semibold text-foreground" : "text-muted-foreground"}`}
          >
            <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-border">
              {step > index + 1 ? <Check className="h-3 w-3" aria-hidden /> : index + 1}
            </span>
            {label}
          </li>
        ))}
      </ol>
      <div className="space-y-5 p-4 sm:p-6">
        {message && (
          <p role="alert" className="text-sm text-destructive">
            {message}
          </p>
        )}
        {settings.settings.enabled &&
          active &&
          availability.data &&
          !availability.data.semantic_ready && (
            <p role="status" className="text-sm text-warning">
              {t(
                "Your saved search is not available right now. Review the setup below or open Advanced AI controls.",
              )}
            </p>
          )}
        {!ready && !building && failedPreparation && (
          <p role="status" className="text-sm text-warning">
            {t(
              "Preparation stopped before search was ready. Try again, or open Advanced AI controls for details.",
            )}
          </p>
        )}
        {ready && active.quarantined > 0 && (
          <p role="status" className="text-sm text-warning">
            {t(
              "Some files could not be prepared. You can search the available files and review the rest in Advanced AI controls.",
            )}
          </p>
        )}
        {ready ? (
          <>
            <div>
              <h3 className="text-xl font-semibold">{t("AI Search is ready")}</h3>
              <p className="mt-2 max-w-prose text-sm text-muted-foreground">
                {t("Describe what you want to print. Try “a holder for my tools”.")}
              </p>
            </div>
            <div className="flex flex-wrap gap-3">
              <Button asChild>
                <Link to="/search?q=a%20holder%20for%20my%20tools">
                  {t("Try AI Search")}
                  <ArrowRight className="ml-2 h-4 w-4" aria-hidden />
                </Link>
              </Button>
              <Button variant="ghost" onClick={() => setChanging(true)}>
                {t("Change setup")}
              </Button>
            </div>
          </>
        ) : building ? (
          <>
            <h3 className="text-xl font-semibold">
              {t(building.phase === "ready" ? "Your search is prepared" : "Preparing your library")}
            </h3>
            <p className="max-w-prose text-sm text-muted-foreground">
              {t(
                "You can leave this page. Keyword search keeps working while preparation finishes.",
              )}
            </p>
            <progress
              className="h-2 w-full accent-primary"
              aria-label={t("Library preparation progress")}
              max={Math.max(1, building.eligible)}
              value={building.indexed}
            />
            <p role="status" className="text-sm tabular-nums">
              {t("{done} of {total} searchable entries prepared", {
                done: building.indexed,
                total: building.eligible,
              })}
            </p>
            {building.error_code && (
              <p role="alert" className="text-sm text-warning">
                {t("Some files need attention. Open Advanced AI controls to review them.")}
              </p>
            )}
            {building.version_token && (
              <div className="flex flex-wrap gap-2">
                {building.phase === "ready" && (
                  <Button loading={busy} onClick={() => change.mutate("activate")}>
                    {t("Use prepared search")}
                  </Button>
                )}
                <Button variant="outline" disabled={busy} onClick={() => change.mutate("cancel")}>
                  {t("Cancel preparation")}
                </Button>
              </div>
            )}
          </>
        ) : (
          <>
            <div>
              <h3 className="text-xl font-semibold">
                {t(path ? "Prepare search by meaning" : "Where should AI Search run?")}
              </h3>
              <p className="mt-2 max-w-prose text-sm text-muted-foreground">
                {t(
                  path
                    ? "PrintStash will prepare your library so you can find models by describing them."
                    : "Keep searches on your PrintStash machine, or use an AI server you already have.",
                )}
              </p>
            </div>
            {!path ? (
              <div className="divide-y divide-border rounded-md border border-border">
                {[
                  {
                    id: "local",
                    title: t("On this machine"),
                    help: t(
                      "Your library text stays here. Uses this machine’s memory and storage.",
                    ),
                    icon: HardDrive,
                  },
                  {
                    id: "server",
                    title: t("Another server"),
                    help: t("Connect an AI service. Library text will be sent to that server."),
                    icon: Server,
                  },
                ].map((option) => (
                  <div
                    key={option.id}
                    className="flex flex-col items-start gap-3 p-4 sm:flex-row sm:items-center sm:gap-4"
                  >
                    <option.icon
                      className="hidden h-5 w-5 shrink-0 text-muted-foreground sm:block"
                      aria-hidden
                    />
                    <div className="min-w-0 flex-1">
                      <p className="font-medium">{option.title}</p>
                      <p className="mt-1 text-sm text-muted-foreground">{option.help}</p>
                    </div>
                    <Button
                      variant="outline"
                      className="min-h-11"
                      onClick={() => {
                        setPath(option.id === "local" ? "local" : "server");
                        setMessage(null);
                      }}
                    >
                      {t(option.id === "local" ? "Use this machine" : "Connect another server")}
                    </Button>
                  </div>
                ))}
              </div>
            ) : (
              <>
                <div className="flex flex-wrap items-center justify-between gap-2 border-b pb-3">
                  <p className="text-sm font-medium">
                    {t(path === "local" ? "On this machine" : "Another server")}
                  </p>
                  <Button
                    variant="ghost"
                    disabled={busy || !!downloading}
                    onClick={() => {
                      setPath(null);
                      setMessage(null);
                    }}
                  >
                    {t("Change location")}
                  </Button>
                </div>
                {path === "local" && !local ? (
                  <div className="space-y-2">
                    <h4 className="font-medium">
                      {t("Local AI is not available on this installation")}
                    </h4>
                    <p className="max-w-prose text-sm text-muted-foreground">
                      {t(
                        "This installation needs a compatible local AI runtime and model. You can connect another server instead, or ask your administrator to install local AI support.",
                      )}
                    </p>
                    <Button variant="outline" onClick={() => setPath("server")}>
                      {t("Use another server")}
                    </Button>
                  </div>
                ) : path === "server" && (!remote || connecting) ? (
                  <>
                    <p className="max-w-prose text-sm text-muted-foreground">
                      {t(
                        "Enter the connection details supplied by your AI server. It must support text embeddings; a chat-only service cannot prepare search.",
                      )}
                    </p>
                    <InferenceEndpointForm
                      compact
                      onSaved={() => {
                        setConnecting(false);
                        onSaved();
                      }}
                    />
                  </>
                ) : (
                  <>
                    <div className="space-y-2">
                      <p className="font-medium break-words">
                        {path === "local" ? local?.key : remote?.model}
                      </p>
                      <p className="text-sm text-muted-foreground">
                        {path === "local"
                          ? local?.installed
                            ? t("Already downloaded. No download needed.")
                            : t("Download size: {size}", {
                                size: formatBytes(local?.size_bytes ?? 0),
                              })
                          : t("Library text and search queries will be sent to {host}.", {
                              host: remote?.host ?? "",
                            })}
                      </p>
                    </div>
                    {((path === "local" && choices.length > 1) || path === "server") && (
                      <details>
                        <summary className="cursor-pointer py-3 text-sm font-medium">
                          {t("Choose a different AI model")}
                        </summary>
                        <label className="block space-y-2 text-sm">
                          {t("AI model")}
                          <select
                            className="min-h-11 w-full rounded-md border border-input bg-background p-2"
                            value={path === "local" ? local?.id : remote?.id}
                            disabled={busy || !!downloading}
                            onChange={(event) => {
                              if (path === "local") setSelected(event.target.value);
                              else setEndpointId(Number(event.target.value));
                              setMessage(null);
                            }}
                          >
                            {path === "local"
                              ? choices.map((model) => (
                                  <option key={model.id} value={model.id}>
                                    {model.key} · {formatBytes(model.size_bytes)}
                                  </option>
                                ))
                              : servers.map((server) => (
                                  <option key={server.id} value={server.id}>
                                    {server.model} · {server.host}
                                  </option>
                                ))}
                          </select>
                        </label>
                        {path === "server" && (
                          <Button variant="ghost" onClick={() => setConnecting(true)}>
                            {t("Connect a new server")}
                          </Button>
                        )}
                      </details>
                    )}
                    {!enabled ? (
                      <>
                        <p className="max-w-prose text-sm text-muted-foreground">
                          {t(
                            path === "local"
                              ? "Continuing enables AI Search and local processing. Nothing is downloaded yet."
                              : "Continuing enables AI Search. Local processing and downloads keep their current settings.",
                          )}
                        </p>
                        <Button
                          loading={busy}
                          className="min-h-11"
                          onClick={() => change.mutate("enable")}
                        >
                          {t(
                            path === "local" ? "Enable local AI Search" : "Enable server AI Search",
                          )}
                        </Button>
                      </>
                    ) : downloading ? (
                      <>
                        <h4 className="font-medium">{t("Downloading an AI model")}</h4>
                        <progress
                          className="h-2 w-full accent-primary"
                          aria-label={t("AI model download progress")}
                          max={100}
                          value={downloading.progress ?? 0}
                        />
                        <p role="status" className="text-sm">
                          {formatBytes(downloading.processed)} / {formatBytes(downloading.total)}
                        </p>
                        <Button
                          variant="outline"
                          disabled={busy}
                          onClick={() => change.mutate("cancel")}
                        >
                          {t("Cancel download")}
                        </Button>
                      </>
                    ) : path === "local" && !local?.installed ? (
                      <>
                        {failedDownload && (
                          <p role="alert" className="text-sm text-warning">
                            {t("A model download failed. You can retry the download below.")}
                          </p>
                        )}
                        <p className="max-w-prose text-sm text-muted-foreground">
                          {t(
                            "This allows administrator-requested model downloads and downloads this model from its provider. Your library files are not uploaded.",
                          )}
                        </p>
                        <Button
                          loading={busy}
                          className="min-h-11"
                          onClick={() => change.mutate("download")}
                        >
                          {t("Allow download and continue")}
                        </Button>
                      </>
                    ) : (
                      <>
                        <p className="max-w-prose text-sm text-muted-foreground">
                          {t(
                            "We’ll check the space needed, then prepare your library. Search switches on automatically when it is ready.",
                          )}
                        </p>
                        <Button
                          loading={busy}
                          className="min-h-11"
                          onClick={() => change.mutate("prepare")}
                        >
                          {t("Prepare my library")}
                        </Button>
                      </>
                    )}
                  </>
                )}
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}
