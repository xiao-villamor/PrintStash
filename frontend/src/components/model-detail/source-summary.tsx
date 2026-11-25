import { useQuery } from "@tanstack/react-query";
import { provenanceOptions } from "@/features/library/provenance";
import { Link } from "@/lib/link";
import { useLocation } from "@tanstack/react-router";
import { useSearchParams } from "@/lib/navigation";
import { currentLocale, uiText } from "@/lib/locale";
import { CalendarDays, ExternalLink, Globe, Scale, User } from "lucide-react";
import {
  CardLinkRow,
  InspectorSection,
  PropertyList,
  PropertyRow,
  SoftCard,
} from "./inspector-section";
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
      <dt className="text-sm text-muted-foreground">{uiText("Captured license")}</dt>
      <dd className="whitespace-pre-wrap wrap-anywhere font-medium">
        {license?.captured_value ?? uiText("Not captured")}
        {license?.captured_origin === "inferred" && ` · ${uiText("Inferred")}`}
      </dd>
      {license?.user_override_set && (
        <>
          <dt className="text-sm text-muted-foreground">{uiText("Your correction")}</dt>
          <dd className="whitespace-pre-wrap wrap-anywhere font-medium">{license.user_value}</dd>
        </>
      )}
    </>
  );
}

export function ModelSourceSummary({
  modelId,
  sourceUrl = null,
  onAddSource,
}: {
  modelId: number;
  sourceUrl?: string | null;
  /** Opens the editor that can record a source link; absent for read-only viewers. */
  onAddSource?: () => void;
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
    <InspectorSection title={uiText("Source and license")}>
      <SoftCard>
        {sources.length === 0 && (query.isSuccess || showLegacyLink) && (
          <PropertyList>
            <PropertyRow label={uiText("Source")} icon={<Globe />}>
              {showLegacyLink ? (
                <ExternalSourceLink href={legacyUrl} label={uiText("Source model")} />
              ) : onAddSource ? (
                <Button
                  type="button"
                  variant="ghost"
                  size="xs"
                  className="-my-1 -me-2 h-auto min-h-7 px-2 text-sm"
                  onClick={onAddSource}
                >
                  {uiText("modelDetail.addSourceLink")}
                </Button>
              ) : (
                <span className="font-normal text-muted-foreground">
                  {uiText("modelDetail.noCapturedPublication")}
                </span>
              )}
            </PropertyRow>
            <PropertyRow label={uiText("License")} icon={<Scale />}>
              {uiText("Not recorded")}
            </PropertyRow>
          </PropertyList>
        )}
        {sources.map((source) => {
          const author = source.fields.find((field) => field.field_name === "creator_name");
          const publication = source.canonical_url ? safeHttpUrl(source.canonical_url) : null;
          const license =
            source.fields.find((field) => field.field_name === "license_text") ??
            source.fields.find((field) => field.field_name === "license_code");
          const licenseUrl = safeHttpUrl(
            source.fields.find((field) => field.field_name === "license_url")?.effective_value ??
              "",
          );
          return (
            <PropertyList key={source.id}>
              <PropertyRow label={uiText("Source")} icon={<Globe />}>
                {publication ? (
                  <ExternalSourceLink
                    href={publication}
                    label={sourceProviderName(source.provider)}
                  />
                ) : (
                  sourceProviderName(source.provider)
                )}
              </PropertyRow>
              {author?.effective_value && (
                <PropertyRow label={uiText("Creator")} icon={<User />}>
                  {author.effective_value}
                  {author.effective_origin === "inferred" && (
                    <span className="font-normal text-muted-foreground">
                      {` · ${uiText("Inferred")}`}
                    </span>
                  )}
                </PropertyRow>
              )}
              <PropertyRow label={uiText("License")} icon={<Scale />}>
                {licenseUrl ? (
                  <ExternalSourceLink
                    href={licenseUrl}
                    label={license?.effective_value || uiText("modelDetail.licenseTerms")}
                  />
                ) : (
                  <span className="whitespace-pre-wrap">
                    {license?.effective_value || uiText("Not captured")}
                  </span>
                )}
                {license?.user_override_set && (
                  <span className="block text-sm text-muted-foreground">
                    {uiText("Captured: {value1}", { value1: license.captured_value })}
                  </span>
                )}
                {license?.effective_origin === "inferred" && (
                  <span className="font-normal text-muted-foreground">
                    {` · ${uiText("Inferred")}`}
                  </span>
                )}
              </PropertyRow>
              <PropertyRow label={uiText("Captured")} icon={<CalendarDays />}>
                <time dateTime={source.first_captured_at}>
                  {new Date(source.first_captured_at).toLocaleDateString(currentLocale(), {
                    dateStyle: "medium",
                  })}
                </time>
              </PropertyRow>
            </PropertyList>
          );
        })}
        {sources.length > 0 && showLegacyLink && (
          <div className="px-4 py-2.5 text-sm">
            <ExternalSourceLink href={legacyUrl} label={uiText("Source model")} />
          </div>
        )}
        {sources.length > 0 && (
          <Link
            href={`/models/${modelId}?${params}`}
            state={location.state}
            replace
            className="block hover:bg-muted/50 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring"
          >
            <CardLinkRow>{uiText("modelDetail.sourceDetails")}</CardLinkRow>
          </Link>
        )}
      </SoftCard>
    </InspectorSection>
  );
}

function ExternalSourceLink({ href, label }: { href: string; label: string }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="inline-flex min-w-0 items-center gap-1 rounded-sm text-primary underline decoration-primary/40 underline-offset-4 hover:decoration-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
    >
      <span className="min-w-0 wrap-anywhere">{label}</span>
      <ExternalLink className="size-3.5 shrink-0" aria-hidden />
      <span className="sr-only">{uiText("Opens in a new tab")}</span>
    </a>
  );
}
