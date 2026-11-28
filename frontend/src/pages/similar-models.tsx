import { useState } from "react";
import { SimilarityQueue } from "@/components/similarity-queue";
import {
  SimilaritySettingsPanel,
  type SimilaritySettingsView,
} from "@/components/similarity-settings-panel";
import { PageContainer } from "@/components/ui/page-container";
import { PageHeader } from "@/components/ui/page-header";
import { TabBar } from "@/components/ui/tabs";
import { useAuth } from "@/lib/auth-context";
import { useI18n } from "@/lib/i18n";

export default function SimilarModelsPage() {
  const { t } = useI18n();
  const { user } = useAuth();
  const [view, setView] = useState<SimilaritySettingsView>("scan");
  const activeView = user?.is_superuser ? view : "scan";
  return (
    <PageContainer>
      <PageHeader title={t("similarity.title")} description={t("similarity.intro")} />
      {user?.is_superuser && (
        <>
          <TabBar
            variant="framed"
            tabs={[
              { key: "scan", label: t("similarity.matchesTab") },
              { key: "settings", label: t("similarity.settingsTab") },
              { key: "history", label: t("similarity.history") },
            ]}
            active={activeView}
            onChange={setView}
            className="mb-4"
          />
          <div className={activeView === "scan" ? "mb-4" : undefined}>
            <SimilaritySettingsPanel view={activeView} onOpenSettings={() => setView("settings")} />
          </div>
        </>
      )}
      <div hidden={activeView !== "scan"}>
        <SimilarityQueue />
      </div>
    </PageContainer>
  );
}
