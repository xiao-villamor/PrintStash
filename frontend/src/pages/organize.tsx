import { TaxonomyManager } from "@/components/taxonomy-manager";
import { PageContainer } from "@/components/ui/page-container";
import { PageHeader } from "@/components/ui/page-header";

export default function OrganizePage() {
  return (
    <PageContainer>
      <PageHeader title="Catalog" description="Collections and tags" />
      <TaxonomyManager />
    </PageContainer>
  );
}
