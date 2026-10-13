import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  semanticNeighborsOptions,
  similarityStatusOptions,
  type NeighborQuery,
} from "@/lib/queries/similarity";
import { useI18n } from "@/lib/i18n";
import { Link } from "@/lib/link";

/** Native semantic scores never offer the geometric confirmation actions. */
export function SimilaritySearch({ modelId }: { modelId?: number }) {
  const { t } = useI18n();
  const [text, setText] = useState("");
  const [submitted, setSubmitted] = useState<{
    subject: number | undefined;
    query: NeighborQuery;
  } | null>(null);
  const status = useQuery(similarityStatusOptions());
  const query = submitted?.subject === modelId ? (submitted?.query ?? null) : null;
  const result = useQuery(semanticNeighborsOptions(query));
  if (
    !status.data?.enabled ||
    status.data.embeddings_enabled === false ||
    !status.data.capabilities.local_embeddings ||
    (modelId === undefined && !status.data.capabilities["text_to_shape"])
  )
    return null;
  return (
    <Card className="mb-4 space-y-3 p-4">
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          const next: NeighborQuery =
            modelId === undefined ? { text: text.trim() } : { model_id: modelId };
          if (query && JSON.stringify(query) === JSON.stringify(next)) void result.refetch();
          else setSubmitted({ subject: modelId, query: next });
        }}
      >
        {modelId === undefined && (
          <label className="min-w-48 flex-1 space-y-1 text-sm">
            {t("similarity.semanticPrompt")}
            <Input
              value={text}
              maxLength={4096}
              required
              onChange={(event) => setText(event.target.value)}
            />
          </label>
        )}
        <Button
          type="submit"
          variant="outline"
          loading={query !== null && result.isFetching}
          disabled={modelId === undefined && !text.trim()}
        >
          {t(modelId === undefined ? "similarity.semanticSearch" : "similarity.semanticNeighbors")}
        </Button>
      </form>
      <p className="text-xs text-muted-foreground">{t("similarity.semanticHelp")}</p>
      {result.isError && (
        <p role="alert" className="text-sm text-destructive">
          {t("similarity.semanticError")}
          <Button variant="ghost" size="sm" onClick={() => void result.refetch()}>
            {t("similarity.retry")}
          </Button>
        </p>
      )}
      {result.data && (
        <div aria-live="polite" className="space-y-2">
          {result.data.items.length ? (
            <ul className="divide-y divide-border">
              {result.data.items.map(({ model, score }) => (
                <li key={model.id} className="flex items-center justify-between gap-3 py-2 text-sm">
                  <Link href={`/models/${model.id}`} className="text-primary hover:underline">
                    {model.name}
                  </Link>
                  <span
                    className="font-mono text-xs tabular-nums text-muted-foreground"
                    aria-label={t("similarity.semanticScore")}
                  >
                    {score.toFixed(3)}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">
              {t(
                result.data.index_state === "missing_model_vectors"
                  ? "similarity.semanticMissing"
                  : "similarity.semanticEmpty",
              )}
            </p>
          )}
          {result.data.truncated && (
            <p className="text-xs text-warning">{t("similarity.semanticTruncated")}</p>
          )}
        </div>
      )}
    </Card>
  );
}
