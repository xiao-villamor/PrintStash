import { cn } from "@/lib/utils";
import { useQuery } from "@tanstack/react-query";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { inputClasses } from "@/components/ui/input";
import { getFamily } from "@/lib/api/families";
import { useI18n } from "@/lib/i18n";
import type { FamilyBrowseMode, VariantRole } from "@/types/families";
import { MEMBER_ROLES } from "@/types/families";

export type FamilyFilterKey = "browse" | "family_id" | "family_role" | "in_family";

export function FamilyFilters({
  mode,
  familyId,
  role,
  membership,
  onChange,
}: {
  mode: FamilyBrowseMode;
  familyId?: number;
  role?: VariantRole;
  membership?: boolean;
  onChange: (key: FamilyFilterKey, value: string) => void;
}) {
  const { t } = useI18n();
  const family = useQuery({
    queryKey: ["families", familyId],
    enabled: familyId !== undefined,
    queryFn: () => getFamily(familyId!),
  });
  return (
    <div className="flex flex-wrap items-center gap-2">
      <select
        aria-label={t("families.browseMode")}
        className={cn(inputClasses, "h-9 w-auto text-xs")}
        value={mode}
        onChange={(event) => onChange("browse", event.target.value)}
      >
        <option value="models">{t("families.browseModels")}</option>
        <option value="families_collapsed">{t("families.browseFamilies")}</option>
      </select>
      <select
        aria-label={t("families.inFamily")}
        className={cn(inputClasses, "h-9 w-auto text-xs")}
        value={membership === undefined ? "" : membership ? "yes" : "no"}
        onChange={(event) => onChange("in_family", event.target.value)}
      >
        <option value="">{t("families.anyMembership")}</option>
        <option value="yes">{t("families.grouped")}</option>
        <option value="no">{t("families.ungrouped")}</option>
      </select>
      <select
        aria-label={t("families.role")}
        className={cn(inputClasses, "h-9 w-auto text-xs")}
        value={role ?? ""}
        onChange={(event) => onChange("family_role", event.target.value)}
      >
        <option value="">{t("families.allRoles")}</option>
        {(["canonical", ...MEMBER_ROLES] as const).map((item) => (
          <option key={item} value={item}>
            {t(`families.role.${item}`)}
          </option>
        ))}
      </select>
      {familyId !== undefined && (
        <Button
          size="xs"
          variant="secondary"
          onClick={() => onChange("family_id", "")}
          aria-label={t("families.clearFilter")}
        >
          <span className="max-w-48 truncate">{family.data?.name ?? t("families.family")}</span>
          <X className="h-3 w-3" aria-hidden />
        </Button>
      )}
    </div>
  );
}
