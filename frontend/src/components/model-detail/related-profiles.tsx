import { useQuery } from "@tanstack/react-query";
import { getJson, downloadAuthenticatedFile } from "@/lib/api/request";
import { Link } from "@/lib/link";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import { queryKeys } from "@/lib/query-client";
import { Button } from "@/components/ui/button";
import type { CaptureProfile } from "@/types/inbox";
import { ProfileFacts } from "./profile-facts";

export interface RelatedProfile {
  model_id: number;
  model_name: string;
  file_id: number;
  filename: string;
  provider: string;
  captured_at: string;
  profile: CaptureProfile;
}
export function RelatedProfiles({ modelId }: { modelId: number }) {
  useUiLocale();
  const query = useQuery({
    queryKey: [...queryKeys.model(modelId), "related-profiles"],
    queryFn: ({ signal }) =>
      getJson<{ items: RelatedProfile[]; has_more: boolean }>(
        `/api/v1/models/${modelId}/related-profiles`,
        { signal },
      ),
    retry: false,
  });
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
        query.data.items.map((item) => (
          <article
            key={`${item.file_id}-${item.profile.id}`}
            className="space-y-2 border border-border p-3"
          >
            <ProfileFacts profile={item.profile} />
            <p className="text-xs text-muted-foreground">
              {item.provider} ·{" "}
              <time dateTime={item.captured_at}>
                {new Date(item.captured_at).toLocaleDateString()}
              </time>
            </p>
            <Link
              className="block text-xs underline wrap-anywhere"
              href={`/models/${item.model_id}?tab=files&artifact=${item.file_id}#artifact-${item.file_id}`}
            >
              {uiText("Open profile file")}: {item.filename}
            </Link>
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
        ))
      )}
      {query.data?.has_more && (
        <p className="text-xs text-muted-foreground">{uiText("More profiles may be available.")}</p>
      )}
    </section>
  );
}
