import { useI18n } from "@/lib/i18n";
import { formatBytes } from "@/lib/format";
import type { AiSearchCatalog } from "@/lib/queries/search";
import type { SearchSettingsRead } from "@/types/search";

const searchSettingsLabels = [
  ["enabled", "aiSearch.enable"],
  ["local_models_enabled", "aiSearch.enableLocal"],
  ["download_enabled", "aiSearch.allowDownloads"],
  ["lexical_backend", "aiSearch.lexicalBackend"],
  ["lexical_weight", "aiSearch.lexicalWeight"],
  ["semantic_weight", "aiSearch.semanticWeight"],
  ["rrf_k", "aiSearch.rankSmoothing"],
  ["max_index_bytes", "aiSearch.indexBudget"],
  ["rollback_retention_hours", "aiSearch.retention"],
  ["query_timeout_seconds", "aiSearch.queryTimeout"],
  ["semantic_floor", "aiSearch.semanticFloor"],
  ["timezone", "aiSearch.instanceTimezone"],
  ["sparse_expansion_enabled", "aiSearch.sparseEnable"],
  ["sparse_model_id", "aiSearch.sparseModel"],
  ["chat_endpoint_id", "aiSearch.chatEndpoint"],
  ["send_rendered_images", "aiSearch.sendRenderedImages"],
  ["send_query_images", "aiSearch.sendQueryImages"],
  ["captions_enabled", "aiSearch.enableCaptions"],
  ["nl_filters_enabled", "aiSearch.enableNl"],
] as const;

/** Authorized saved values shown before an explicit revised settings command. */
export function SearchSettingsSnapshot({
  snapshot,
  catalog,
}: {
  snapshot: SearchSettingsRead;
  catalog: AiSearchCatalog;
}) {
  const { t } = useI18n();
  return (
    <dl className="grid gap-2 text-sm">
      {searchSettingsLabels.map(([key, label]) => {
        const value = snapshot.settings[key];
        const display =
          key === "chat_endpoint_id"
            ? (snapshot.endpoints.find((item) => item.id === value)?.model ?? t("aiSearch.none"))
            : key === "sparse_model_id"
              ? (catalog.models.data?.find((item) => item.id === value)?.key ??
                value ??
                t("aiSearch.none"))
              : key === "max_index_bytes"
                ? formatBytes(snapshot.settings.max_index_bytes)
                : value === true
                  ? t("aiSearch.reviewEnabled")
                  : value === false
                    ? t("aiSearch.reviewDisabled")
                    : value === null
                      ? t("aiSearch.none")
                      : String(value);
        return (
          <div key={key}>
            <dt className="font-medium">{t(label)}</dt>
            <dd>{display}</dd>
          </div>
        );
      })}
    </dl>
  );
}
