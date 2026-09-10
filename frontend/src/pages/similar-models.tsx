import { SimilarityQueue } from "@/components/similarity-queue";
import { PageContainer } from "@/components/ui/page-container";
import { PageHeader } from "@/components/ui/page-header";
import { useI18n } from "@/lib/i18n";

export default function SimilarModelsPage() {
  const { t } = useI18n();
  return (
    <PageContainer>
      <PageHeader title={t("similarity.title")} description={t("similarity.intro")} />
      <SimilarityQueue />
    </PageContainer>
  );
}
