import type { ReactNode } from "react";
import { BrandMark } from "@/components/brand-mark";
import { ThemeToggle } from "@/components/theme-toggle";
import { LocaleToggle } from "@/components/locale-toggle";
import { Card } from "@/components/ui/card";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/**
 * The one shell for the pages a visitor sees before the app: sign-in and
 * first-run setup. Both share the brand row, the language and theme controls,
 * the card padding and the footer, so the first two screens read as one product.
 * Only the width differs: sign-in is a single column of fields, setup carries a
 * storage chooser.
 */
export function AuthFrame({
  wide = false,
  label,
  children,
}: {
  wide?: boolean;
  /** Names the card region for assistive technology when the page has no landmark heading of its own. */
  label?: string;
  children: ReactNode;
}) {
  const { t } = useI18n();
  return (
    <main className="flex min-h-dvh flex-col items-center justify-center gap-5 bg-background px-4 py-6 sm:py-10">
      <Card
        role={label ? "region" : undefined}
        aria-label={label}
        className={cn(
          "w-full overflow-hidden border-border bg-card",
          wide ? "max-w-[560px]" : "max-w-[400px]",
        )}
      >
        <header className="flex items-center justify-between gap-4 px-6 pt-8 sm:px-8">
          <div className="flex items-center gap-2.5">
            <span className="flex h-7 w-7 items-center justify-center rounded-brand bg-primary text-primary-foreground">
              <BrandMark className="h-5 w-5" />
            </span>
            <span className="text-[15px] font-semibold tracking-tight text-foreground">
              PrintStash
            </span>
          </div>
          <div className="flex items-center gap-1">
            <LocaleToggle />
            <ThemeToggle />
          </div>
        </header>
        <div className="min-w-0 px-6 pb-8 pt-6 sm:px-8">{children}</div>
      </Card>
      <p className="font-mono text-2xs uppercase tracking-wider text-muted-foreground">
        {t("auth.tagline")}
      </p>
    </main>
  );
}
