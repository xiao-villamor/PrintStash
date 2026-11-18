import type { ReactNode } from "react";
import { BrandMark } from "@/components/brand-mark";
import { ThemeToggle } from "@/components/theme-toggle";
import { LocaleToggle } from "@/components/locale-toggle";
import { Card } from "@/components/ui/card";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/** Shared page chrome for sign-in and first-run setup. */
export function AuthFrame({
  cardClassName,
  label,
  note,
  children,
}: {
  cardClassName?: string;
  /** Names the card region for assistive technology. */
  label?: string;
  note?: ReactNode;
  children: ReactNode;
}) {
  const { t } = useI18n();
  return (
    <div className="flex min-h-dvh flex-col bg-background">
      <header className="flex h-14 shrink-0 items-center justify-between gap-2 px-6">
        <div className="flex min-w-0 items-center gap-2.5 text-foreground">
          <BrandMark className="h-5 w-5 shrink-0" />
          <span className="text-[15px] font-semibold tracking-tight">PrintStash</span>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <LocaleToggle />
          <ThemeToggle />
        </div>
      </header>
      <main className="grid flex-1 place-items-center px-4 py-6">
        <Card
          role={label ? "region" : undefined}
          aria-label={label}
          className={cn("w-full min-w-0 border-border", cardClassName ?? "max-w-[380px] p-7")}
        >
          {children}
        </Card>
      </main>
      <footer className="flex flex-col gap-2 px-6 py-4 text-muted-foreground sm:min-h-14 sm:flex-row sm:items-center sm:justify-between sm:gap-4">
        <p className="font-mono text-2xs uppercase tracking-wider">{t("auth.tagline")}</p>
        {note && <p className="text-xs sm:text-right">{note}</p>}
      </footer>
    </div>
  );
}
