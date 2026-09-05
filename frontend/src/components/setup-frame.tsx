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
    <main className="min-h-screen bg-background px-4 py-6 sm:px-8">
      <header className="mx-auto mb-6 flex max-w-5xl items-center justify-between">
        <div className="flex items-center gap-2 font-semibold">
          <BrandMark className="h-8 w-8" />
          PrintStash
        </div>
        <div className="flex gap-2">
          <LocaleToggle />
          <ThemeToggle />
        </div>
      </header>
      <Card className="mx-auto grid max-w-5xl overflow-hidden border-outline-variant lg:grid-cols-[16rem_1fr]">
        <aside className="border-b border-outline-variant bg-surface-container-low p-5 lg:border-b-0 lg:border-r lg:p-8">
          <h1 className="text-lg font-semibold">{t("setup.welcome")}</h1>
          <ol
            aria-label={t("setup.progress")}
            className="mt-5 grid grid-cols-3 gap-2 lg:grid-cols-1 lg:gap-4"
          >
            {(["account", "files", "start"] as const).map((key, index) => (
              <li
                key={key}
                aria-current={step === index + 1 ? "step" : undefined}
                className={cn(
                  "flex items-center gap-2 rounded-md p-2 text-xs sm:text-sm",
                  step === index + 1 ? "bg-accent text-accent-foreground" : "text-muted-foreground",
                )}
              >
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-border">
                  {step > index + 1 ? <Check className="h-4 w-4" aria-hidden /> : index + 1}
                </span>
                {t(`setup.${key}`)}
              </li>
            ))}
          </ol>
        </aside>
        <div className="min-w-0 p-6 sm:p-8 lg:p-10">{children}</div>
      </Card>
    </main>
  );
}
