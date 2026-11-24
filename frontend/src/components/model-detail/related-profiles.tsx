import { AlertCircle } from "lucide-react";
import { downloadAuthenticatedFile } from "@/lib/api/request";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { toast } from "@/lib/toast";
import { InspectorSection, PropertyList, PropertyRow, SoftCard } from "./inspector-section";
import { formatPlateEstimate } from "./format-plate-estimate";
import { sourceProviderName } from "./presentation";
import { useRelatedProfiles, type RelatedProfile } from "./use-related-profiles";

function ProfileCard({
  item,
  revision,
  differs,
}: {
  item: RelatedProfile;
  revision?: { number: number; time: number | null; weight: number | null };
  differs: boolean;
}) {
  const { profile } = item;
  const unknown = uiText("Not captured");
  const estimate =
    profile.origin === "provider"
      ? uiText("Estimate from {value1}", { value1: sourceProviderName(item.provider) })
      : uiText("Estimate included in the file");
  const time =
    profile.estimated_seconds == null
      ? unknown
      : formatPlateEstimate("estimated_time_s", profile.estimated_seconds);
  const filament =
    profile.filament_grams == null
      ? unknown
      : formatPlateEstimate("filament_weight_g", profile.filament_grams);
  return (
    <SoftCard>
      <div className="flex flex-wrap items-center gap-2.5 px-4 py-3">
        <span className="min-w-0 wrap-anywhere text-sm font-semibold">{profile.name}</span>
        <Badge variant="outline" className="text-xs">
          {estimate}
        </Badge>
        <Button
          size="sm"
          variant="outline"
          className="ms-auto"
          onClick={() =>
            void downloadAuthenticatedFile(
              `/api/v1/files/${item.file_id}/download`,
              item.filename,
            ).catch((error) => toast.error(error))
          }
        >
          {uiText("Download")}
        </Button>
      </div>
      <dl className="grid grid-cols-3 divide-x divide-border" aria-label={uiText("Profile facts")}>
        {(
          [
            [uiText("Time"), time],
            [uiText("Filament"), filament],
            [uiText("Printer"), profile.printer ?? unknown],
          ] as const
        ).map(([label, value]) => (
          <div key={label} className="min-w-0 space-y-0.5 px-4 py-2.5">
            <dt className="text-sm text-muted-foreground">{label}</dt>
            <dd className="wrap-anywhere text-[15px] font-medium tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>
      <PropertyList>
        <PropertyRow label={uiText("Material · plates")}>
          {profile.materials?.length ? profile.materials.join(", ") : unknown}
          {" · "}
          {profile.plate_count == null
            ? unknown
            : uiText("modelDetail.plateCount", { count: profile.plate_count })}
        </PropertyRow>
        <PropertyRow label={uiText("Creator")}>
          {profile.author ?? unknown}
          {" · "}
          {uiText("captured")}{" "}
          <time dateTime={item.captured_at}>{new Date(item.captured_at).toLocaleDateString()}</time>
        </PropertyRow>
      </PropertyList>
      <div className="flex items-start gap-2.5 bg-muted/40 px-4 py-2.5 text-sm leading-5">
        {differs && <AlertCircle className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden />}
        <p className="min-w-0">
          {differs && revision && (
            <>
              {uiText(
                "Differs from the Rev {value1} G-code estimate ({value2} · {value3}). The G-code estimate is the one PrintStash uses for printing and costing.",
                {
                  value1: String(revision.number),
                  value2: formatPlateEstimate("estimated_time_s", revision.time),
                  value3: formatPlateEstimate("filament_weight_g", revision.weight),
                },
              )}
            </>
          )}
          <span className={differs ? "block text-muted-foreground" : "text-muted-foreground"}>
            {uiText("Imported profile; not a measured or tested print result.")}
          </span>
        </p>
      </div>
    </SoftCard>
  );
}

export function RelatedProfiles({
  modelId,
  revisionEstimate,
  revisionNumber,
}: {
  modelId: number;
  revisionEstimate?: { time: number | null; weight: number | null };
  revisionNumber?: number;
}) {
  useUiLocale();
  const query = useRelatedProfiles(modelId);
  if (query.isPending || (!query.isError && !query.data?.items.length)) return null;
  return (
    <InspectorSection title={uiText("Profiles from this publication")}>
      {query.isError ? (
        <p className="px-1 text-sm text-muted-foreground">
          {uiText("Profile information could not be loaded.")}
        </p>
      ) : (
        <div className="space-y-3">
          {query.data.items.map((item) => {
            const differs =
              revisionEstimate !== undefined &&
              revisionNumber !== undefined &&
              item.model_id === modelId &&
              item.profile.origin === "provider" &&
              ((item.profile.estimated_seconds != null &&
                revisionEstimate.time !== null &&
                item.profile.estimated_seconds !== revisionEstimate.time) ||
                (item.profile.filament_grams != null &&
                  revisionEstimate.weight !== null &&
                  item.profile.filament_grams !== revisionEstimate.weight));
            return (
              <ProfileCard
                key={`${item.file_id}-${item.profile.id}`}
                item={item}
                differs={differs}
                revision={
                  revisionEstimate && revisionNumber !== undefined
                    ? { number: revisionNumber, ...revisionEstimate }
                    : undefined
                }
              />
            );
          })}
        </div>
      )}
      {query.data?.has_more && (
        <p className="px-1 text-xs text-muted-foreground">
          {uiText("More profiles may be available.")}
        </p>
      )}
    </InspectorSection>
  );
}
