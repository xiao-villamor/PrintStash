import { downloadAuthenticatedFile } from "@/lib/api/request";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import { Badge } from "@/components/ui/badge";
import { sourceProviderName } from "./presentation";
import { Button } from "@/components/ui/button";
import { useRelatedProfiles } from "./use-related-profiles";
import { ProfileFacts } from "./profile-facts";

export function RelatedProfiles({
  modelId,
  revisionEstimate,
}: {
  modelId: number;
  revisionEstimate?: { time: number | null; weight: number | null };
}) {
  useUiLocale();
  const query = useRelatedProfiles(modelId);
  if (query.isPending || (!query.isError && !query.data?.items.length)) return null;
  return (
    <section
      className="mt-4 space-y-3 border-t border-border pt-3"
      aria-label={uiText("Profiles from this publication")}
    >
      <h3 className="text-xs font-medium">{uiText("Profiles from this publication")}</h3>
      {query.isError ? (
        <p className="text-xs text-muted-foreground">
          {uiText("Profile information could not be loaded.")}
        </p>
      ) : (
        query.data.items.map((item) => {
          return (
            <article
              key={`${item.file_id}-${item.profile.id}`}
              className="space-y-2 border border-border p-3"
            >
              <Badge variant="outline">
                {uiText("Estimate from {value1}", { value1: sourceProviderName(item.provider) })}
              </Badge>
              {revisionEstimate &&
                item.model_id === modelId &&
                item.profile.origin === "provider" &&
                ((item.profile.estimated_seconds != null &&
                  revisionEstimate.time !== null &&
                  item.profile.estimated_seconds !== revisionEstimate.time) ||
                  (item.profile.filament_grams != null &&
                    revisionEstimate.weight !== null &&
                    item.profile.filament_grams !== revisionEstimate.weight)) && (
                  <p className="text-sm text-muted-foreground">
                    {uiText("Differs from the G-code estimate.")}
                  </p>
                )}
              <ProfileFacts profile={item.profile} />
              <p className="text-xs text-muted-foreground">
                {item.provider} ·{" "}
                <time dateTime={item.captured_at}>
                  {new Date(item.captured_at).toLocaleDateString()}
                </time>
              </p>
              <p className="text-sm wrap-anywhere">{item.filename}</p>
              <Button
                size="xs"
                variant="outline"
                onClick={() =>
                  downloadAuthenticatedFile(`/api/v1/files/${item.file_id}/download`, item.filename)
                }
              >
                {uiText("Download")}
              </Button>
            </article>
          );
        })
      )}
      {query.data?.has_more && (
        <p className="text-xs text-muted-foreground">{uiText("More profiles may be available.")}</p>
      )}
    </section>
  );
}
