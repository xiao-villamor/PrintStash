import { useState } from "react";
import { useMutation } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { createInferenceEndpoint } from "@/lib/api/search";
import { useI18n } from "@/lib/i18n";
import { toast } from "@/lib/toast";
import type { EndpointProposal, InferenceEndpoint } from "@/types/search";

export function InferenceEndpointForm({
  initial,
  onSaved,
  compact = false,
}: {
  initial?: InferenceEndpoint;
  compact?: boolean;
  onSaved: () => void;
}) {
  const { t } = useI18n();
  const [kind, setKind] = useState<"embedding" | "chat">(initial?.kind ?? "embedding");
  const [url, setUrl] = useState(initial?.base_url ?? "");
  const [model, setModel] = useState(initial?.model ?? "");
  const [revision, setRevision] = useState(initial?.revision ?? "configured-v1");
  const [repo, setRepo] = useState(initial?.model_repo ?? "");
  const [dimension, setDimension] = useState(initial?.native_dimension ?? 384);
  const [timeout, setTimeout] = useState(initial?.timeout_seconds ?? 15);
  const [apiKey, setApiKey] = useState<string | undefined>(undefined);
  const [replaceHeaders, setReplaceHeaders] = useState(false);
  const [headers, setHeaders] = useState<{ name: string; value: string }[]>([]);
  const [images, setImages] = useState(initial?.supports_images ?? false);
  const [responses, setResponses] = useState(initial?.prefer_responses ?? false);
  const save = useMutation({
    mutationFn: () => {
      const proposal: EndpointProposal = {
        kind,
        base_url: url,
        model,
        revision,
        model_repo: repo || null,
        ...(kind === "embedding"
          ? { native_dimension: dimension }
          : { supports_images: images, prefer_responses: responses }),
        timeout_seconds: timeout,
        max_input_characters: initial?.max_input_characters ?? 16384,
      };
      if (initial) proposal.inherit_credentials_from_id = initial.id;
      if (apiKey !== undefined) proposal.api_key = apiKey;
      if (replaceHeaders)
        proposal.headers = Object.fromEntries(headers.map((header) => [header.name, header.value]));
      return createInferenceEndpoint(proposal);
    },
    onSuccess: () => {
      setApiKey(undefined);
      setHeaders([]);
      setReplaceHeaders(false);
      toast.success(t("aiSearch.endpointReady"));
      onSaved();
    },
    onError: toast.error,
  });
  return (
    <form
      aria-label={t("aiSearch.endpointForm")}
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <p className="max-w-prose text-sm text-muted-foreground">{t("aiSearch.compatibleHelp")}</p>
      <div className="grid gap-3 sm:grid-cols-2">
        {!compact && (
          <>
            <label className="space-y-1 text-sm">
              {t("aiSearch.endpointKind")}
              <select
                className="block w-full rounded-md border border-input bg-background p-2"
                value={kind}
                disabled={!!initial}
                onChange={(event) => setKind(event.target.value === "chat" ? "chat" : "embedding")}
              >
                <option value="embedding">{t("aiSearch.embeddingEndpoint")}</option>
                <option value="chat">{t("aiSearch.chatEndpoint")}</option>
              </select>
            </label>
          </>
        )}

        <label className="space-y-1 text-sm">
          {t("aiSearch.endpointUrl")}
          <Input
            type="url"
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            required
            maxLength={2048}
            placeholder={t("aiSearch.endpointPlaceholder")}
          />
        </label>
        <label className="space-y-1 text-sm">
          {t("aiSearch.model")}
          <Input
            value={model}
            onChange={(event) => setModel(event.target.value)}
            required
            maxLength={128}
          />
        </label>

        {kind === "embedding" && (
          <label className="space-y-1 text-sm">
            {t(compact ? "Model output size (from your server)" : "aiSearch.nativeDimension")}
            <Input
              type="number"
              min={1}
              max={4096}
              value={dimension}
              required
              onChange={(event) => setDimension(Number(event.target.value))}
            />
          </label>
        )}
        <label className="space-y-1 text-sm">
          {t("aiSearch.apiKey")}
          <Input
            aria-label={t("aiSearch.apiKey")}
            type="password"
            autoComplete="new-password"
            value={apiKey ?? ""}
            onChange={(event) => setApiKey(event.target.value)}
            maxLength={8192}
          />
          {initial?.has_credentials && (
            <span className="block text-xs text-muted-foreground">
              {t("aiSearch.credentialsKept")}
            </span>
          )}
        </label>
      </div>
      <details open={compact ? undefined : true}>
        <summary className="cursor-pointer py-3 text-sm font-medium">{t("Server options")}</summary>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="space-y-1 text-sm">
            {t("aiSearch.revision")}
            <Input
              value={revision}
              onChange={(event) => setRevision(event.target.value)}
              required
              maxLength={128}
            />
          </label>
          <label className="space-y-1 text-sm">
            {t("aiSearch.repository")}
            <Input
              value={repo}
              onChange={(event) => setRepo(event.target.value)}
              placeholder={t("aiSearch.repoPlaceholder")}
            />
          </label>
          <label className="space-y-1 text-sm">
            {t("aiSearch.endpointTimeout")}
            <Input
              type="number"
              min={1}
              max={120}
              value={timeout}
              required
              onChange={(event) => setTimeout(Number(event.target.value))}
            />
          </label>
        </div>
      </details>
      {initial?.has_credentials && (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => {
            setApiKey("");
            setReplaceHeaders(true);
            setHeaders([]);
          }}
        >
          {t("aiSearch.clearCredentials")}
        </Button>
      )}
      <details>
        <summary className="cursor-pointer py-3 text-sm font-medium">
          {t("aiSearch.customHeaders")}
        </summary>
        {initial?.header_names.length ? (
          <p className="mt-2 text-xs text-muted-foreground">
            {t("aiSearch.savedHeaders", { names: initial.header_names.join(", ") })}
          </p>
        ) : null}
        <label className="mt-2 flex items-center gap-2 text-sm">
          <Checkbox
            ariaLabel={t("aiSearch.replaceHeaders")}
            checked={replaceHeaders}
            onChange={setReplaceHeaders}
          />
          {t("aiSearch.replaceHeaders")}
        </label>
        {replaceHeaders && (
          <div className="mt-2 space-y-2">
            {headers.map((header, index) => (
              <div key={index} className="flex flex-wrap gap-2">
                <Input
                  aria-label={t("aiSearch.headerName", { number: index + 1 })}
                  className="min-w-0 flex-1"
                  required
                  maxLength={128}
                  value={header.name}
                  onChange={(event) =>
                    setHeaders(
                      headers.map((item, i) =>
                        i === index ? { ...item, name: event.target.value } : item,
                      ),
                    )
                  }
                />
                <Input
                  aria-label={t("aiSearch.headerValue", { number: index + 1 })}
                  className="min-w-0 flex-1"
                  type="password"
                  autoComplete="new-password"
                  maxLength={8192}
                  value={header.value}
                  onChange={(event) =>
                    setHeaders(
                      headers.map((item, i) =>
                        i === index ? { ...item, value: event.target.value } : item,
                      ),
                    )
                  }
                />
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => setHeaders(headers.filter((_, i) => i !== index))}
                >
                  {t("aiSearch.remove")}
                </Button>
              </div>
            ))}
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={headers.length >= 16}
              onClick={() => setHeaders([...headers, { name: "", value: "" }])}
            >
              {t("aiSearch.addHeader")}
            </Button>
          </div>
        )}
      </details>
      {kind === "chat" && (
        <div className="space-y-2">
          <label className="flex items-center gap-2 text-sm">
            <Checkbox
              ariaLabel={t("aiSearch.acceptsImages")}
              checked={images}
              onChange={setImages}
            />
            {t("aiSearch.acceptsImages")}
          </label>
          <label className="flex items-center gap-2 text-sm">
            <Checkbox
              ariaLabel={t("aiSearch.responsesDialect")}
              checked={responses}
              onChange={setResponses}
            />
            {t("aiSearch.responsesDialect")}
          </label>
        </div>
      )}
      {url && (
        <p className="text-xs text-muted-foreground">{t("aiSearch.testDisclosure", { url })}</p>
      )}
      {save.isError && (
        <p role="alert" className="text-sm text-destructive">
          {t("aiSearch.endpointError")}
        </p>
      )}
      <Button type="submit" loading={save.isPending}>
        {t("aiSearch.testAndSave")}
      </Button>
    </form>
  );
}
