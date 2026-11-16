import { useQuery } from "@tanstack/react-query";
import { provenanceOptions } from "@/features/library/provenance";
import { Link } from "@/lib/link";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import type { InboxManifest } from "@/types";

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

export function ModelSourceSummary({ modelId }: { modelId: number }) {
  useUiLocale();
  const query = useQuery(provenanceOptions(modelId));
  if (!query.data?.sources.length)
    return (
      <Link className="text-xs underline" href={`/models/${modelId}?tab=source`}>
        {uiText("Source and license")}
      </Link>
    );
  return (
    <div className="mt-3 space-y-2 text-xs">
      {query.data.sources.map((source) => {
        const author = source.fields.find((field) => field.field_name === "creator_name");
        const license =
          source.fields.find((field) => field.field_name === "license_text") ??
          source.fields.find((field) => field.field_name === "license_code");
        return (
          <div key={source.id}>
            <p>
              {source.provider} · {author?.effective_value ?? uiText("Not captured")}
              {author?.effective_origin === "inferred" && ` · ${uiText("Inferred")}`}
            </p>
            <p className="whitespace-pre-wrap wrap-anywhere">
              {uiText("Captured license")}: {license?.captured_value ?? uiText("Not captured")}
              {license?.captured_origin === "inferred" && ` · ${uiText("Inferred")}`}
            </p>
            {license?.user_override_set && (
              <p>
                {uiText("Your correction")}: {license.user_value}
              </p>
            )}
            <time dateTime={source.first_captured_at}>
              {new Date(source.first_captured_at).toLocaleDateString()}
            </time>
          </div>
        );
      })}
      <Link className="underline" href={`/models/${modelId}?tab=source`}>
        {uiText("Source and license")}
      </Link>
    </div>
  );
}
