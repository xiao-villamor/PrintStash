import type { ReactNode } from "react";
import { Check } from "lucide-react";
import { AuthFrame } from "@/components/auth-frame";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";

const STEPS = ["stepAccount", "stepStorage", "stepAppearance", "stepReview"] as const;

export type SetupStep = 1 | 2 | 3 | 4;

export function SetupFrame({
  step,
  actions,
  children,
}: {
  step?: SetupStep;
  actions?: ReactNode;
  children: ReactNode;
}) {
  const { t } = useI18n();

  return (
    <AuthFrame
      layout="setup"
      cardClassName="max-w-2xl overflow-hidden rounded-lg"
      label={t("setup.welcome")}
    >
      {step && (
        <ol
          aria-label={t("setup.progress")}
          className="grid grid-cols-2 gap-1 border-b border-border p-3 sm:grid-cols-4 sm:gap-2 sm:px-5"
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
                  "flex min-w-0 items-center gap-1.5 rounded-md px-2 py-2 text-sm sm:gap-2",
                  current && "bg-accent font-semibold text-accent-foreground",
                  done && "text-foreground",
                  !current && !done && "text-muted-foreground",
                )}
              >
                <span
                  aria-hidden
                  className="flex size-5 shrink-0 items-center justify-center font-mono text-xs"
                >
                  {done ? <Check className="size-4" /> : position}
                </span>
                <span className="min-w-0 break-words">{t(`setup.${key}`)}</span>
              </li>
            );
          })}
        </ol>
      )}
      <div className="min-w-0 p-5 sm:p-7">{children}</div>
      {actions && (
        <div className="flex items-center justify-between gap-3 border-t border-border px-5 py-4 sm:px-7">
          {actions}
        </div>
      )}
    </AuthFrame>
  );
}
