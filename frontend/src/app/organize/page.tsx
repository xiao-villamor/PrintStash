import { TaxonomyManager } from "@/components/taxonomy-manager";

export const metadata = {
  title: "Catalog — PrintStash",
};

export default function OrganizePage() {
  return (
    <div className="h-full overflow-y-auto p-4 sm:p-6">
      <div className="w-full space-y-4 sm:space-y-6 lg:space-y-8">
        <h2 className="text-xl font-semibold text-[var(--on-surface)]">Catalog</h2>
        <TaxonomyManager />
      </div>
    </div>
  );
}
