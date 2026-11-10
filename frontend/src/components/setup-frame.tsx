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
      <div className="w-full max-w-[560px]">
        <Card
          role="region"
          aria-label={t("setup.welcome")}
          className="w-full max-w-[560px] overflow-hidden border-border bg-card"
        >
          <header className="flex items-center justify-between gap-4 px-5 pt-6 sm:px-8 sm:pt-8">
            <div className="flex items-center gap-3">
              <div className="flex h-7 w-7 items-center justify-center rounded-brand bg-primary text-primary-foreground">
                <BrandMark className="h-5 w-5" />
              </div>
              <span className="text-[15px] font-semibold tracking-tight">PrintStash</span>
            </div>
            <div className="flex items-center gap-4">
              <LocaleToggle />
              <ThemeToggle />
            </div>
          </header>
          <h1 className="mt-6 px-5 text-[22px] font-semibold tracking-tight sm:px-8">
            {t("setup.welcome")}
          </h1>
          <ol aria-label={t("setup.progress")} className="mt-5 grid grid-cols-3 gap-2 px-5 sm:px-8">
            {(["stepAccount", "stepStorage", "stepReview"] as const).map((key, index) => (
              <li
                key={key}
                aria-current={step === index + 1 ? "step" : undefined}
                className={cn(
                  "flex items-center gap-2 border-t-2 pt-2.5 text-xs",
                  step >= index + 1
                    ? "border-primary font-medium text-foreground"
                    : "border-border text-muted-foreground",
                )}
              >
                <span
                  className={cn(
                    "flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-2xs",
                    step === index + 1
                      ? "bg-primary text-primary-foreground"
                      : "bg-muted text-muted-foreground",
                  )}
                >
                  {step > index + 1 ? <Check className="h-3 w-3" aria-hidden /> : index + 1}
                </span>
                {t(`setup.${key}`)}
              </li>
            ))}
          </ol>
          <div className="min-w-0 px-5 pb-6 pt-6 sm:px-8 sm:pb-8">{children}</div>
        </Card>
        <p className="mt-5 text-center text-xs text-muted-foreground">{t("auth.tagline")}</p>
      </div>
    </main>
  );
}
