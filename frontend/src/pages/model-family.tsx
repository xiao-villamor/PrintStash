import { useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { FamilyDetail } from "@/components/families/detail";
import { Button } from "@/components/ui/button";
import { PageContainer } from "@/components/ui/page-container";
import { getFamilyBySlug } from "@/lib/api/families";
import { ApiError, userMessage } from "@/lib/errors";
import { useI18n } from "@/lib/i18n";
import NotFound from "./not-found";

export default function ModelFamilyPage() {
  const { id } = useParams();
  const { t } = useI18n();
  const familyId = Number(id);
  const numeric = /^\d+$/.test(id ?? "");
  const resolved = useQuery({
    queryKey: ["families", "slug", id],
    enabled: !!id && !numeric,
    queryFn: () => getFamilyBySlug(id!),
  });
  if (!id || (numeric && (!Number.isSafeInteger(familyId) || familyId <= 0))) return <NotFound />;
  if (numeric) return <FamilyDetail key={familyId} id={familyId} />;
  if (resolved.data) return <FamilyDetail key={resolved.data.id} id={resolved.data.id} />;
  if (resolved.error instanceof ApiError && resolved.error.status === 404) return <NotFound />;
  return (
    <PageContainer>
      {resolved.isError ? (
        <div className="space-y-2">
          <p role="alert" className="text-sm text-destructive">
            {userMessage(resolved.error)}
          </p>
          <Button variant="outline" onClick={() => void resolved.refetch()}>
            {t("families.retry")}
          </Button>
        </div>
      ) : (
        <p role="status">{t("families.loading")}</p>
      )}
    </PageContainer>
  );
}
