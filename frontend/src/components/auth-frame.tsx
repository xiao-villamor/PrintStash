import type { ReactNode } from "react";
import { AuthPrintArt } from "@/components/auth-print-art";
import { BrandMark } from "@/components/brand-mark";
import { ThemeToggle } from "@/components/theme-toggle";
import { LocaleToggle } from "@/components/locale-toggle";
import { Card } from "@/components/ui/card";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/** Shared page chrome for sign-in and first-run setup. */
export function AuthFrame({
  layout = "centered",
  cardClassName,
  label,
  note,
  children,
}: {
  layout?: "centered" | "split";
  cardClassName?: string;
  /** Names the card region for assistive technology. */
  label?: string;
  note?: ReactNode;
  children: ReactNode;
}) {
  const { t } = useI18n();
  const split = layout === "split";
  const content = (
    <>
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
      <main className={cn("grid flex-1 place-items-center px-4 py-6", split && "auth-split-main")}>
        <Card
          role={label ? "region" : undefined}
          aria-label={label}
          className={cn(
            "w-full min-w-0 border-border",
            cardClassName ?? "max-w-[380px] p-7",
            split && "auth-split-form",
          )}
        >
          {children}
        </Card>
      </main>
      <footer className="flex flex-col gap-2 px-6 py-4 text-muted-foreground sm:min-h-14 sm:flex-row sm:items-center sm:justify-between sm:gap-4">
        <p
          className={cn(
            "font-mono text-2xs uppercase tracking-wider",
            split && "auth-split-tagline",
          )}
        >
          {t("auth.tagline")}
        </p>
        {note && <p className="text-xs sm:text-right">{note}</p>}
      </footer>
    </>
  );
  if (!split) {
    return <div className="flex min-h-dvh flex-col bg-background">{content}</div>;
  }
  return (
    <div className="auth-frame-split flex min-h-dvh flex-col bg-background">
      <div className="auth-split-workspace flex min-w-0 flex-1 flex-col">{content}</div>
      <div className="auth-split-art hidden border-l border-border" aria-hidden="true">
        <AuthPrintArt />
      </div>
    </div>
  );
}
