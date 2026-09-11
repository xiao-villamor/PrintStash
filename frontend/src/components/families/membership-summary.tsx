import { useQuery } from "@tanstack/react-query";
import { Boxes } from "lucide-react";
import { Button } from "@/components/ui/button";
import { getModel } from "@/lib/api/models";
import { listFamilyMembers } from "@/lib/api/families";
import { useI18n } from "@/lib/i18n";
import { Link } from "@/lib/link";
import type { ModelFamilySummary } from "@/types/families";
import type { ModelRead } from "@/types/models";

export function FamilyMembershipSummary({
  family,
  model,
}: {
  family: ModelFamilySummary;
  model: ModelRead;
}) {
  const { t } = useI18n();
  const members = useQuery({
    queryKey: ["families", family.id, "overview"],
    queryFn: () => listFamilyMembers(family.id, { limit: 4 }),
  });
  const canonical = useQuery({
    queryKey: ["models", family.canonical_model_id],
    enabled: family.canonical_model_id != null && family.canonical_model_id !== model.id,
    queryFn: () => getModel(family.canonical_model_id!),
  });
  const canonicalModel = family.canonical_model_id === model.id ? model : canonical.data;
  const siblings = members.data?.items.filter((item) => item.model_id !== model.id) ?? [];
  return (
    <section aria-label={t("families.family")} className="mt-2 space-y-1 text-xs">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <Boxes className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
        <Link href={`/families/${family.id}`} className="font-medium text-primary hover:underline">
          {family.name}
        </Link>
        <span className="text-muted-foreground">{t(`families.role.${family.role}`)}</span>
        <Link href={`/families/${family.id}`} className="text-primary hover:underline">
          {t("families.manage")}
        </Link>
      </div>
      <p className="text-muted-foreground">
        {family.canonical_model_id == null || canonical.isError ? (
          t("families.vacancy")
        ) : (
          <>
            {t("families.canonical")}:{" "}
            {canonicalModel ? (
              <Link href={`/models/${canonicalModel.id}`} className="text-primary hover:underline">
                {canonicalModel.name}
              </Link>
            ) : (
              t("families.loading")
            )}
          </>
        )}
      </p>
      {members.isError ? (
        <div className="flex items-center gap-2">
          <span role="alert" className="text-destructive">
            {t("families.siblingsUnavailable")}
          </span>
          <Button size="xs" variant="ghost" onClick={() => void members.refetch()}>
            {t("families.retry")}
          </Button>
        </div>
      ) : siblings.length > 0 ? (
        <div className="flex flex-wrap gap-x-2 gap-y-1">
          <span className="text-muted-foreground">{t("families.siblings")}:</span>
          {siblings.map((item) => (
            <Link
              key={item.id}
              href={`/models/${item.model_id}`}
              className="text-primary hover:underline"
            >
              {item.model.name}
            </Link>
          ))}
          {members.data && members.data.total > members.data.items.length && (
            <Link href={`/families/${family.id}`} className="text-primary hover:underline">
              {t("families.viewMembers")}
            </Link>
          )}
        </div>
      ) : (
        <p className="text-muted-foreground">
          {t("families.memberCount", { count: family.member_count })}
        </p>
      )}
    </section>
  );
}
