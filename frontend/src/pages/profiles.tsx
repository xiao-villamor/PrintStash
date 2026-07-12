import { FilamentProfilesCard } from "@/components/filament-profiles-card";
import { PageContainer } from "@/components/ui/page-container";

export default function ProfilesPage() {
  return (
    <PageContainer>
      <div>
        <h2 className="text-2xl font-bold text-foreground tracking-tight">Profiles</h2>
        <p className="text-sm text-muted-foreground">Filament and printer presets for cost tracking and slicer defaults</p>
      </div>
      <FilamentProfilesCard />
    </PageContainer>
  );
}
