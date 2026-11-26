import type { ReactNode } from "react";
import { AuthPrintArt } from "@/components/auth-print-art";
import { BrandMark } from "@/components/brand-mark";
import { ThemeToggle } from "@/components/theme-toggle";
import { LocaleToggle } from "@/components/locale-toggle";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

/** Shared page chrome for sign-in and first-run setup. */
export function AuthFrame({
  layout,
  cardClassName,
  label,
  note,
  children,
}: {
  layout: "signin" | "setup";
  cardClassName?: string;
  /** Names the card region for assistive technology. */
  label?: string;
  note?: ReactNode;
  children: ReactNode;
}) {
  const setup = layout === "setup";

  return (
    <div
      className={cn(
        "auth-frame flex min-h-dvh flex-col bg-background",
        setup && "auth-frame-setup",
      )}
    >
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center justify-between gap-2 px-6 sm:h-16">
          <div className="flex min-w-0 items-center gap-2.5 text-foreground">
            <BrandMark className="size-6 shrink-0" />
            <span className="text-base font-semibold tracking-tight">PrintStash</span>
          </div>
          <div className="flex shrink-0 items-center gap-1">
            <LocaleToggle />
            <ThemeToggle />
          </div>
        </header>
        <main className="grid flex-1 place-items-center px-4 py-6 sm:px-6">
          <Card
            role={label ? "region" : undefined}
            aria-label={label}
            className={cn(
              "w-full min-w-0 rounded-lg border-border",
              cardClassName ?? "max-w-md p-6 sm:p-8",
            )}
          >
            {children}
            {note && (
              <p className="mt-5 border-t border-border pt-4 text-xs text-muted-foreground">
                {note}
              </p>
            )}
          </Card>
        </main>
      </div>
      <div className="auth-split-art dark hidden" aria-hidden="true">
        <AuthPrintArt animated={!setup} />
      </div>
    </div>
  );
}
