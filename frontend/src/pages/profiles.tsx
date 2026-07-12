import { FilamentProfilesCard } from "@/components/filament-profiles-card";
import { PageContainer } from "@/components/ui/page-container";
import { PageHeader } from "@/components/ui/page-header";

export default function ProfilesPage() {
  return (
    <PageContainer>
      <PageHeader
        title="Profiles"
        description="Filament and printer presets for cost tracking and slicer defaults"
      />
      <FilamentProfilesCard />
    </PageContainer>
  );
}
