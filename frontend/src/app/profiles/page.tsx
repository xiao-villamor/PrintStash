import { FilamentProfilesCard } from "@/components/filament-profiles-card";

export const metadata = {
  title: "Profiles — PrintStash",
};

export default function ProfilesPage() {
  return (
    <div className="h-full overflow-y-auto p-4 sm:p-6">
      <div className="w-full space-y-4 sm:space-y-6">
        <h2 className="text-xl font-semibold text-[var(--on-surface)]">Profiles</h2>
        <div className="max-w-6xl">
          <FilamentProfilesCard />
        </div>
      </div>
    </div>
  );
}
