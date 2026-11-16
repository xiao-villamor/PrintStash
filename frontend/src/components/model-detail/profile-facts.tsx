import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import type { CaptureProfile } from "@/types/inbox";

export function ProfileFacts({ profile }: { profile: CaptureProfile }) {
  useUiLocale();
  const unknown = uiText("Not captured");
  return (
    <div className="space-y-1 text-xs font-normal text-muted-foreground">
      <p className="font-medium text-foreground">{profile.name}</p>
      <p>
        {profile.origin === "provider"
          ? uiText("Provider estimate")
          : uiText("Estimate included in the file")}
      </p>
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1">
        <dt>{uiText("Creator")}</dt>
        <dd className="wrap-anywhere">{profile.author ?? unknown}</dd>
        <dt>{uiText("Printer")}</dt>
        <dd>{profile.printer ?? unknown}</dd>
        <dt>{uiText("Time")}</dt>
        <dd>
          {profile.estimated_seconds == null
            ? unknown
            : `${Math.round(profile.estimated_seconds / 60)} min`}
        </dd>
        <dt>{uiText("Filament")}</dt>
        <dd>{profile.filament_grams == null ? unknown : `${profile.filament_grams} g`}</dd>
        <dt>{uiText("Material")}</dt>
        <dd>{profile.materials?.length ? profile.materials.join(", ") : unknown}</dd>
        <dt>{uiText("Plates")}</dt>
        <dd>{profile.plate_count ?? unknown}</dd>
      </dl>
      <p>{uiText("Imported profile; not a measured or tested print result.")}</p>
    </div>
  );
}
