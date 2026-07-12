import { TaxonomyManager } from "@/components/taxonomy-manager";
import { PageContainer } from "@/components/ui/page-container";

export default function OrganizePage() {
  return (
    <PageContainer>
      <div>
        <h2 className="text-2xl font-bold text-foreground tracking-tight">Catalog</h2>
        <p className="text-sm text-muted-foreground">Collections and tags</p>
      </div>
      <TaxonomyManager />
    </PageContainer>
  );
}
