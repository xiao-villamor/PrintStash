import { useId, type ReactNode } from "react";
import { ChevronRight } from "lucide-react";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import type { FileRead } from "@/types";
import { revisionStatusLabel } from "./presentation";

/**
 * A small label above a soft card, with at most one action on the label line.
 * See DESIGN.md "Model detail".
 */
export function InspectorSection({
  title,
  actions,
  children,
}: {
  title: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
}) {
  const headingId = useId();
  return (
    <section aria-labelledby={headingId} className="min-w-0 space-y-1.5">
      <div className="flex min-h-7 items-center justify-between gap-3 ps-1">
        <h2 id={headingId} className="text-sm font-medium text-muted-foreground">
          {title}
        </h2>
        {actions}
      </div>
      {children}
    </section>
  );
}

/** A soft surface whose rows are divided by hairlines. */
export function SoftCard({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <Card
      className={cn(
        "min-w-0 divide-y divide-border overflow-hidden rounded-lg border border-border bg-card",
        className,
      )}
    >
      {children}
    </Card>
  );
}

/** Label/value rows inside a soft card. */
export function PropertyList({ children, label }: { children: ReactNode; label?: string }) {
  return (
    <dl aria-label={label} className="divide-y divide-border">
      {children}
    </dl>
  );
}

export function PropertyRow({
  label,
  icon,
  children,
}: {
  label: ReactNode;
  icon?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="grid min-h-10 min-w-0 grid-cols-[8.5rem_minmax(0,1fr)] items-center gap-3 px-4 py-2">
      <dt className="flex min-w-0 items-center gap-2 text-sm text-muted-foreground">
        {icon && (
          <span className="shrink-0 [&_svg]:size-4" aria-hidden>
            {icon}
          </span>
        )}
        <span className="min-w-0 truncate">{label}</span>
      </dt>
      <dd className="flex min-w-0 flex-wrap items-center gap-1.5 text-sm wrap-anywhere">
        {children}
      </dd>
    </div>
  );
}

/** A full-width row that opens another view in the app. */
export function CardLinkRow({ children }: { children: ReactNode }) {
  return (
    <span className="flex min-h-10 items-center justify-between gap-3 px-4 py-2 text-sm">
      <span className="min-w-0">{children}</span>
      <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden />
    </span>
  );
}

const STATUS_PILL = {
  known_good: "bg-success/10 text-success",
  needs_test: "bg-warning/10 text-warning",
  failed: "bg-destructive/10 text-destructive",
  archived: "bg-muted text-muted-foreground",
} satisfies Record<NonNullable<FileRead["revision_status"]>, string>;

/** A revision's manual status as a soft colored pill. */
export function StatusPill({ status }: { status: FileRead["revision_status"] }) {
  return (
    <span
      className={`inline-flex h-6 items-center gap-1.5 rounded-full px-2.5 text-xs font-medium ${status ? STATUS_PILL[status] : "bg-muted text-muted-foreground"}`}
    >
      <span aria-hidden className="size-1.5 rounded-full bg-current" />
      {revisionStatusLabel(status)}
    </span>
  );
}

/** A neutral pill for secondary facts such as "Recommended". */
export function NeutralPill({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex h-6 items-center rounded-full bg-muted px-2.5 text-xs font-medium text-muted-foreground">
      {children}
    </span>
  );
}
