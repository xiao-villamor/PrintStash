import type { ReactNode } from "react";
import { Check } from "lucide-react";
import { AuthFrame } from "@/components/auth-frame";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";

const STEPS = ["stepAccount", "stepStorage", "stepReview"] as const;

/**
 * First-run setup inside the shared sign-in shell. The welcome heading greets the
 * visitor once, on the first step; later steps keep it for assistive technology
 * only, so each step's own title is the first thing a sighted visitor reads.
 */
export function SetupFrame({ step, children }: { step: 1 | 2 | 3; children: ReactNode }) {
  const { t } = useI18n();
  return (
    <AuthFrame wide label={t("setup.welcome")}>
      <h1
        className={
          step === 1 ? "text-[22px] font-semibold tracking-tight text-foreground" : "sr-only"
        }
      >
        {t("setup.welcome")}
      </h1>
      <ol
        aria-label={t("setup.progress")}
        className={cn("grid grid-cols-3 gap-2", step === 1 ? "mt-5" : "mt-0")}
      >
        {STEPS.map((key, index) => {
          const position = index + 1;
          const current = step === position;
          const done = step > position;
          return (
            <li
              key={key}
              aria-current={current ? "step" : undefined}
              className={cn(
                "flex items-center gap-2 border-t-2 pt-2.5 text-[13px]",
                current && "border-primary font-medium text-foreground",
                done && "border-primary text-on-surface-variant",
                !current && !done && "border-border text-muted-foreground",
              )}
            >
              <span
                className={cn(
                  "flex h-5 w-5 shrink-0 items-center justify-center font-mono text-2xs",
                  current && "bg-primary text-primary-foreground",
                  done && "bg-primary/10 text-primary",
                  !current && !done && "bg-muted text-muted-foreground",
                )}
              >
                {done ? <Check className="h-3 w-3" aria-hidden /> : position}
              </span>
              {t(`setup.${key}`)}
            </li>
          );
        })}
      </ol>
      <div className="pt-6">{children}</div>
    </AuthFrame>
  );
}
