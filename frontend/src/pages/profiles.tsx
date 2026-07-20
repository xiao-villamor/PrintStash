import { FilamentProfilesCard } from "@/components/filament-profiles-card";
import { PageContainer } from "@/components/ui/page-container";
import { PageHeader } from "@/components/ui/page-header";
import { useI18n } from "@/lib/i18n";
import { translateUiText } from "@/components/ui/localized";

export default function ProfilesPage() {
  const { locale } = useI18n();
  return (
    <PageContainer>
      <PageHeader
        title={translateUiText(locale, "Profiles")}
        description={translateUiText(locale, "Filament and printer presets for cost tracking and slicer defaults")}
      />
      <FilamentProfilesCard />
    </PageContainer>
  );
}
