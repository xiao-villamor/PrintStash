import { TaxonomyManager } from "@/components/taxonomy-manager";

export const metadata = {
  title: "Catalog — PrintStash",
};

export default function OrganizePage() {
  return (
    <div className="h-full overflow-y-auto bg-background p-6 pb-24 md:pb-6">
      <div className="w-full space-y-6">
        <div>
          <h2 className="text-2xl font-bold text-foreground tracking-tight">Catalog</h2>
          <p className="text-sm text-muted-foreground">Collections and tags</p>
        </div>
        <TaxonomyManager />
      </div>
    </div>
  );
}
