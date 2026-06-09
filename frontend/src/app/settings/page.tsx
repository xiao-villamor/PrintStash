import { SettingsPanel } from "@/components/settings-panel";

export const metadata = {
  title: "Settings — PrintStash",
};

export default function SettingsPage() {
  return (
    <div className="h-full overflow-y-auto bg-background p-6 pb-24 md:pb-6">
      <SettingsPanel />
    </div>
  );
}
