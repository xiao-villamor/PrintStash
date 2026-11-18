import type { ReactNode } from "react";
import { Check } from "lucide-react";
import { AuthFrame } from "@/components/auth-frame";
import { useI18n } from "@/lib/i18n";
import { useContentReveal } from "@/lib/use-content-reveal";
import { cn } from "@/lib/utils";

const STEPS = ["stepAccount", "stepStorage", "stepReview"] as const;

export function SetupFrame({
  step,
  actions,
  children,
}: {
  step: 1 | 2 | 3;
  actions: ReactNode;
  children: ReactNode;
}) {
  const { t } = useI18n();
  const content = useContentReveal(String(step), { scroll: "parent" });

  return (
    <AuthFrame cardClassName="setup-card" label={t("setup.welcome")}>
      <div className="min-w-0 border-b border-border p-4 md:border-b-0 md:border-r md:p-6">
        <h1 className="text-base font-semibold text-foreground">{t("setup.welcome")}</h1>
        <ol aria-label={t("setup.progress")} className="mt-4 grid grid-cols-3 gap-1 md:grid-cols-1">
          {STEPS.map((key, index) => {
            const position = index + 1;
            const current = step === position;
            const done = step > position;
            return (
              <li
                key={key}
                aria-current={current ? "step" : undefined}
                className={cn(
                  "flex min-w-0 items-center gap-1 px-1.5 py-2 text-[13px] md:gap-2 md:px-2.5",
                  current && "bg-accent font-medium text-accent-foreground",
                  done && "text-foreground",
                  !current && !done && "text-muted-foreground",
                )}
              >
                <span className="flex w-4 shrink-0 items-center justify-center font-mono text-xs">
                  {done ? <Check className="h-3.5 w-3.5" aria-hidden /> : position}
                </span>
                <span className="min-w-0 break-words">{t(`setup.${key}`)}</span>
              </li>
            );
          })}
        </ol>
      </div>
      <div className="flex min-h-0 min-w-0 flex-col">
        <div className="min-h-0 flex-1 md:overflow-y-auto md:[scrollbar-gutter:stable]">
          <div ref={content} className="p-4 md:p-6">
            {children}
          </div>
        </div>
        <div className="flex min-h-16 shrink-0 items-center justify-between gap-3 border-t border-border px-4 py-3 md:px-6">
          {actions}
        </div>
      </div>
    </AuthFrame>
  );
}
