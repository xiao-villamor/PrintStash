import type { ReactNode } from "react";
import { Check } from "lucide-react";
import { BrandMark } from "@/components/brand-mark";
import { ThemeToggle } from "@/components/theme-toggle";
import { LocaleToggle } from "@/components/locale-toggle";
import { Card } from "@/components/ui/card";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";

export function SetupFrame({ step, children }: { step: 1 | 2 | 3; children: ReactNode }) {
  const { t } = useI18n();
  return (
    <main className="flex min-h-dvh items-center justify-center bg-background px-4 py-6 sm:py-10">
      <Card
        role="region"
        aria-label={t("setup.welcome")}
        className="w-full max-w-2xl overflow-hidden border-outline-variant bg-surface-container-low"
      >
        <header className="flex items-center justify-between gap-4 px-5 py-5 sm:px-8">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary text-primary-foreground">
              <BrandMark className="h-7 w-7" />
            </div>
            <span className="text-base font-semibold tracking-tight">PrintStash</span>
            <h1 className="sr-only">{t("setup.welcome")}</h1>
          </div>
          <div className="flex items-center gap-4">
            <LocaleToggle />
            <ThemeToggle />
          </div>
        </header>
        <ol
          aria-label={t("setup.progress")}
          className="grid grid-cols-3 gap-2 border-y border-outline-variant bg-muted/30 px-5 py-3 sm:px-8"
        >
          {(["account", "files", "start"] as const).map((key, index) => (
            <li
              key={key}
              aria-current={step === index + 1 ? "step" : undefined}
              className={cn(
                "flex items-center justify-center gap-2 text-xs",
                step === index + 1 ? "font-semibold text-foreground" : "text-muted-foreground",
              )}
            >
              <span
                className={cn(
                  "flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-2xs",
                  step === index + 1
                    ? "bg-accent text-accent-foreground"
                    : "bg-muted text-muted-foreground",
                )}
              >
                {step > index + 1 ? <Check className="h-3 w-3" aria-hidden /> : index + 1}
              </span>
              {t(`setup.${key}`)}
            </li>
          ))}
        </ol>
        <div className="min-w-0 p-5 sm:p-8">{children}</div>
      </Card>
    </main>
  );
}
