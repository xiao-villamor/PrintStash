import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";

/** A quiet neutral pill with a small icon; only a failure takes the destructive tone. */
export function StatePill({
  icon: Icon,
  destructive = false,
  children,
}: {
  icon?: LucideIcon;
  destructive?: boolean;
  children: ReactNode;
}) {
  return (
    <span
      className={`inline-flex h-5.5 items-center gap-1.5 rounded-full bg-foreground/10 pe-2.5 text-xs font-medium ${Icon ? "ps-2" : "ps-2.5"} ${destructive ? "text-destructive" : "text-foreground/80"}`}
    >
      {Icon && <Icon aria-hidden="true" className="size-3 shrink-0" />}
      {children}
    </span>
  );
}
