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
    queryFn: () => listFamilyMembers(family.id, { limit: 5 }),
  });
  const canonical = useQuery({
    queryKey: ["models", family.canonical_model_id],
    enabled: family.canonical_model_id != null && family.canonical_model_id !== model.id,
    queryFn: () => getModel(family.canonical_model_id!),
  });
  const siblings =
    members.data?.items
      .filter((item) => item.model_id !== model.id && item.model_id !== family.canonical_model_id)
      .slice(0, 3) ?? [];
  const shownModelIds = new Set([model.id, ...siblings.map((item) => item.model_id)]);
  if (family.canonical_model_id != null) shownModelIds.add(family.canonical_model_id);
  const hasMore = members.data != null && members.data.total > shownModelIds.size;
  return (
    <section aria-label={t("families.family")} className="mt-2 space-y-1 break-words text-xs">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <Boxes className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
        <Link
          href={`/families/${family.id}`}
          className="max-w-full font-medium text-primary hover:underline"
        >
          {family.name}
        </Link>
        <span className="text-muted-foreground">{t(`families.role.${family.role}`)}</span>
        <Link href={`/families/${family.id}`} className="text-primary hover:underline">
          {t("families.manage")}
        </Link>
      </div>
      {family.canonical_model_id !== model.id && (
        <p className="text-muted-foreground">
          {family.canonical_model_id == null || canonical.isError ? (
            t("families.vacancy")
          ) : (
            <>
              {t("families.canonical")}:{" "}
              {canonical.data ? (
                <Link
                  href={`/models/${canonical.data.id}`}
                  className="text-primary hover:underline"
                >
                  {canonical.data.name}
                </Link>
              ) : (
                t("families.loading")
              )}
            </>
          )}
        </p>
      )}
      {members.isError ? (
        <div className="flex items-center gap-2">
          <span role="alert" className="text-destructive">
            {t("families.siblingsUnavailable")}
          </span>
          <Button size="xs" variant="ghost" onClick={() => void members.refetch()}>
            {t("families.retry")}
          </Button>
        </div>
      ) : siblings.length > 0 || hasMore ? (
        <div className="flex flex-wrap gap-x-2 gap-y-1">
          {siblings.length > 0 && (
            <span className="text-muted-foreground">{t("families.siblings")}:</span>
          )}
          {siblings.map((item) => (
            <Link
              key={item.id}
              href={`/models/${item.model_id}`}
              className="max-w-full text-primary hover:underline"
            >
              {item.model.name}
            </Link>
          ))}
          {hasMore && (
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
