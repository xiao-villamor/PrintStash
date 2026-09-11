import { useRef, useState } from "react";
import { MoreHorizontal } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { DropdownMenu } from "@/components/ui/dropdown-menu";
import { useI18n } from "@/lib/i18n";
import { Link } from "@/lib/link";
import { filterValueText } from "@/lib/filter-labels";
import type { FamilyMemberItem } from "@/types/families";
import { ModelThumbnail } from "./model-picker";

export type MemberAction = "edit-member" | "canonical" | "detach";

export function FamilyMemberCard({
  member,
  selected,
  selectionFull,
  editable,
  onToggle,
  onAction,
}: {
  member: FamilyMemberItem;
  selected: boolean;
  selectionFull: boolean;
  editable: boolean;
  onToggle: () => void;
  onAction: (action: MemberAction) => void;
}) {
  const { t, locale } = useI18n();
  const [menu, setMenu] = useState(false);
  const menuTrigger = useRef<HTMLButtonElement>(null);
  const actions: MemberAction[] = ["edit-member"];
  if (member.role !== "canonical") actions.push("canonical");
  actions.push("detach");
  const metric = (label: string, value: string | number) => (
    <div className="min-w-0">
      <dt className="truncate text-xs text-muted-foreground">{label}</dt>
      <dd className="truncate text-sm font-medium tabular-nums">{value}</dd>
    </div>
  );
  return (
    <article
      aria-label={member.model.name}
      className={`min-w-0 rounded-md border p-3 ${selected ? "border-primary bg-accent/40" : "border-border bg-card"}`}
    >
      <header className="flex items-center gap-2.5">
        <Checkbox
          checked={selected}
          disabled={selectionFull && !selected}
          onChange={onToggle}
          ariaLabel={t("families.selectTwo", { name: member.model.name })}
        />
        <ModelThumbnail url={member.model.thumbnail_url} />
        <div className="min-w-0 flex-1">
          <Link
            href={`/models/${member.model_id}`}
            className="block truncate text-sm font-semibold hover:text-primary hover:underline"
          >
            {member.model.name}
          </Link>
          <p className="truncate text-xs text-muted-foreground">
            {t(`families.role.${member.role}`)} · {member.formats.join(" / ").toUpperCase() || "—"}
          </p>
        </div>
        {editable && (
          <DropdownMenu
            open={menu}
            onOpenChange={setMenu}
            contentClassName="rounded-md border border-border bg-card shadow-lg"
            trigger={
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                ref={menuTrigger}
                onClick={() => setMenu(!menu)}
                aria-expanded={menu}
                aria-haspopup="menu"
                data-menu-trigger
                aria-label={t("families.memberActions", { name: member.model.name })}
              >
                <MoreHorizontal className="h-4 w-4" aria-hidden />
              </Button>
            }
          >
            <div className="min-w-48 p-1">
              {actions.map((action) => (
                <button
                  key={action}
                  role="menuitem"
                  type="button"
                  className="block w-full rounded px-3 py-2 text-left text-sm hover:bg-accent focus-visible:bg-accent focus-visible:outline-none"
                  onClick={() => {
                    setMenu(false);
                    menuTrigger.current?.focus();
                    onAction(action);
                  }}
                >
                  {t(
                    action === "edit-member"
                      ? "families.memberEdit"
                      : action === "canonical"
                        ? "families.setCanonical"
                        : "families.detach",
                  )}
                </button>
              ))}
            </div>
          </DropdownMenu>
        )}
      </header>
      <dl className="mt-3 grid grid-cols-3 gap-x-3 gap-y-2">
        {metric(
          t("families.scale"),
          member.scale_factor === null
            ? t("families.unknown")
            : `${new Intl.NumberFormat(locale, { maximumFractionDigits: 4 }).format(member.scale_factor)}×`,
        )}
        {metric(t("families.sourceFiles"), member.source_file_count)}
        {metric(t("families.revisions"), member.gcode_revision_count)}
        {metric(t("families.knownGood"), member.known_good_count)}
        <div className="col-span-2">
          {metric(
            t("families.latestPrint"),
            member.latest_print_outcome
              ? filterValueText("print_outcome", member.latest_print_outcome)
              : t("families.noPrint"),
          )}
        </div>
      </dl>
      {member.relative_review_required && (
        <p className="mt-2 text-xs font-medium text-warning">{t("families.reviewRelative")}</p>
      )}
      {member.transformation_note && (
        <p className="mt-2 line-clamp-2 break-words text-xs leading-relaxed text-muted-foreground">
          {member.transformation_note}
        </p>
      )}
      {member.model.source_url && (
        <a
          href={member.model.source_url}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-2 inline-block text-xs text-primary hover:underline"
        >
          {t("families.source")}
        </a>
      )}
    </article>
  );
}
