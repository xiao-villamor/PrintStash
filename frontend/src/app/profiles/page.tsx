import { FilamentProfilesCard } from "@/components/filament-profiles-card";

export const metadata = {
  title: "Profiles — PrintStash",
};

export default function ProfilesPage() {
  return (
    <div className="h-full overflow-y-auto bg-background p-6 pb-24 md:pb-6">
      <div className="mx-auto w-full max-w-5xl space-y-6">
        <div>
          <h2 className="text-2xl font-bold text-foreground tracking-tight">Profiles</h2>
          <p className="text-sm text-muted-foreground">Filament and printer presets for cost tracking and slicer defaults</p>
        </div>
        <FilamentProfilesCard />
      </div>
    </div>
  );
}
