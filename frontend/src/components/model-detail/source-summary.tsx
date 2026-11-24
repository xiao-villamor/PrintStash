import { useQuery } from "@tanstack/react-query";
import { provenanceOptions } from "@/features/library/provenance";
import { Link } from "@/lib/link";
import { useLocation } from "@tanstack/react-router";
import { useSearchParams } from "@/lib/navigation";
import { currentLocale, uiText } from "@/lib/locale";
import { ArrowRight, ExternalLink } from "lucide-react";
import { Button } from "@/components/ui/button";
import { safeHttpUrl } from "./source-url";
import { sourceProviderName } from "./presentation";
import { useUiLocale } from "@/lib/i18n";
import type { InboxManifest, ModelProvenanceRead } from "@/types";

export function CaptureSourceSummary({ manifest }: { manifest: InboxManifest }) {
  useUiLocale();
  if (manifest.schema_version !== 2 && manifest.schema_version !== 3) return null;
  const fields = manifest.source.fields;
  const license = fields.license_text ?? fields.license_code;
  return (
    <dl className="mt-3 text-xs text-muted-foreground">
      <dt>{uiText("Creator")}</dt>
      <dd>
        {fields.creator_name?.value ?? uiText("Not captured")}
        {fields.creator_name?.origin === "inferred" && ` · ${uiText("Inferred")}`}
      </dd>
      <dt>{uiText("Captured license")}</dt>
      <dd className="whitespace-pre-wrap wrap-anywhere">
        {license?.value ?? uiText("Not captured")}
        {license?.origin === "inferred" && ` · ${uiText("Inferred")}`}
      </dd>
    </dl>
  );
}

export function SourceLicenseFacts({
  fields,
}: {
  fields: ModelProvenanceRead["sources"][number]["fields"];
}) {
  useUiLocale();
  const license =
    fields.find((field) => field.field_name === "license_text") ??
    fields.find((field) => field.field_name === "license_code");
  return (
    <>
      <dt className="text-xs text-muted-foreground">{uiText("Captured license")}</dt>
      <dd className="whitespace-pre-wrap wrap-anywhere font-medium">
        {license?.captured_value ?? uiText("Not captured")}
        {license?.captured_origin === "inferred" && ` · ${uiText("Inferred")}`}
      </dd>
      {license?.user_override_set && (
        <>
          <dt className="text-xs text-muted-foreground">{uiText("Your correction")}</dt>
          <dd className="whitespace-pre-wrap wrap-anywhere font-medium">{license.user_value}</dd>
        </>
      )}
    </>
  );
}

export function ModelSourceSummary({
  modelId,
  sourceUrl = null,
}: {
  modelId: number;
  sourceUrl?: string | null;
}) {
  useUiLocale();
  const query = useQuery(provenanceOptions(modelId));
  const location = useLocation();
  const params = new URLSearchParams(useSearchParams());
  params.set("tab", "source");
  const sources = query.data?.sources ?? [];
  const legacyUrl = sourceUrl ? safeHttpUrl(sourceUrl) : null;
  const showLegacyLink =
    legacyUrl &&
    !sources.some(
      (source) => source.canonical_url && safeHttpUrl(source.canonical_url) === legacyUrl,
    );

  return (
    <section aria-label={uiText("Source and license")} className="min-w-0 space-y-3">
      <h2 className="text-base font-semibold">{uiText("Source and license")}</h2>
      {sources.length === 0 && query.isSuccess && (
        <p className="text-sm text-muted-foreground">
          {uiText("modelDetail.noCapturedPublication")}
        </p>
      )}
      {sources.map((source) => {
        const author = source.fields.find((field) => field.field_name === "creator_name");
        const publication = source.canonical_url ? safeHttpUrl(source.canonical_url) : null;
        return (
          <div key={source.id} className="space-y-2.5">
            <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-sm">
              {publication ? (
                <a
                  href={publication}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1.5 font-medium underline-offset-4 hover:underline"
                >
                  {sourceProviderName(source.provider)}
                  <ExternalLink className="size-3.5" aria-hidden />
                </a>
              ) : (
                <span className="font-medium">{sourceProviderName(source.provider)}</span>
              )}
              {author?.effective_value && (
                <span className="wrap-anywhere text-muted-foreground">
                  · {author.effective_value}
                  {author.effective_origin === "inferred" && ` · ${uiText("Inferred")}`}
                </span>
              )}
            </div>
            <dl className="grid grid-cols-[auto_minmax(0,1fr)] items-baseline gap-x-3 gap-y-1.5 text-sm">
              <SourceLicenseFacts fields={source.fields} />
              <dt className="text-xs text-muted-foreground">{uiText("Captured")}</dt>
              <dd>
                <time dateTime={source.first_captured_at}>
                  {new Date(source.first_captured_at).toLocaleDateString(currentLocale(), {
                    dateStyle: "medium",
                  })}
                </time>
              </dd>
            </dl>
          </div>
        );
      })}
      <div className="flex flex-wrap items-center gap-2">
        <Button asChild variant="outline" size="sm">
          <Link href={`/models/${modelId}?${params}`} state={location.state} replace>
            {uiText("modelDetail.sourceDetails")}
            <ArrowRight className="size-3.5" aria-hidden />
          </Link>
        </Button>
        {showLegacyLink && (
          <Button asChild variant="ghost" size="sm">
            <a href={legacyUrl} target="_blank" rel="noreferrer">
              {uiText("Source model")}
              <ExternalLink className="size-3.5" aria-hidden />
            </a>
          </Button>
        )}
      </div>
    </section>
  );
}
