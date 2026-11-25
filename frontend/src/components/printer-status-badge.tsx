import {
  CircleAlert,
  CircleCheck,
  CirclePause,
  CirclePlay,
  CircleQuestionMark,
  WifiOff,
  type LucideIcon,
} from "lucide-react";
import { knownUiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import type { PrinterStatus } from "@/types";

const STATUS_ICON = {
  ready: CircleCheck,
  printing: CirclePlay,
  paused: CirclePause,
  error: CircleAlert,
  offline: WifiOff,
  unknown: CircleQuestionMark,
} satisfies Record<PrinterStatus, LucideIcon>;

/** A printer's status as a quiet neutral pill; only an error takes the destructive tone. */
export function PrinterStatusBadge({ status }: { status: PrinterStatus }) {
  useUiLocale();
  const Icon = STATUS_ICON[status];
  return (
    <span
      className={`inline-flex h-5.5 min-w-0 max-w-full items-center gap-1.5 rounded-full bg-foreground/10 pe-2.5 ps-2 text-xs font-medium capitalize ${status === "error" ? "text-destructive" : "text-foreground/80"}`}
    >
      <Icon aria-hidden="true" className="size-3 shrink-0" />
      <span className="truncate">{knownUiText(status)}</span>
    </span>
  );
}
