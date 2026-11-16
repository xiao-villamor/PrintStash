import { useId } from "react";
import { FileQuestion, LockKeyhole, ServerCrash, WifiOff } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useUiLocale } from "@/lib/i18n";
import { uiText } from "@/lib/locale";

const presentations = {
  "not-found": {
    code: "404",
    icon: FileQuestion,
    titleKey: "Page not found",
    descriptionKey: "This page doesn’t exist. It may have moved or been deleted.",
  },
  forbidden: {
    code: "403",
    icon: LockKeyhole,
    titleKey: "Access restricted",
    descriptionKey:
      "Your account does not have access to this page. Ask the owner to check your permissions.",
  },
  authentication: {
    code: "401",
    icon: LockKeyhole,
    titleKey: "Sign in required",
    descriptionKey: "Sign in to your PrintStash account to continue.",
  },
  offline: {
    code: null,
    icon: WifiOff,
    titleKey: "Unable to connect",
    descriptionKey: "Couldn't reach the server. Check that PrintStash is running and try again.",
  },
  unavailable: {
    code: null,
    icon: ServerCrash,
    titleKey: "Page unavailable",
    descriptionKey: "Something went wrong in this browser tab. Reload the page and try again.",
  },
} as const;

/** Eager, router-independent recovery also works after a route chunk fails. */
export function PageFailure({
  kind,
  title,
  description,
  onRetry,
  retrying = false,
  reload = false,
  fullScreen = false,
}: {
  kind: keyof typeof presentations;
  title?: string;
  description?: string;
  onRetry?: () => void;
  retrying?: boolean;
  reload?: boolean;
  fullScreen?: boolean;
}) {
  useUiLocale();
  const heading = useId();
  const presentation = presentations[kind];
  const Icon = presentation.icon;
  return (
    <section
      role="alert"
      aria-labelledby={heading}
      className={`flex w-full flex-col overflow-y-auto bg-background text-foreground ${fullScreen ? "min-h-dvh" : "h-full min-h-full"}`}
    >
      <div className="flex h-14 shrink-0 items-center justify-between border-b border-border px-6 sm:px-8">
        <a
          href="/"
          className="text-sm font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          PrintStash
        </a>
        {presentation.code && (
          <span className="font-mono text-xs tabular-nums text-muted-foreground">
            {presentation.code}
          </span>
        )}
      </div>
      <div className="flex flex-1 items-center justify-center px-6 py-12 sm:px-8">
        <div className="w-full max-w-lg text-center">
          <div
            aria-hidden="true"
            className="mx-auto mb-6 flex h-16 w-16 items-center justify-center bg-muted text-muted-foreground"
          >
            <Icon className="h-7 w-7" strokeWidth={1.5} />
          </div>
          <h1
            id={heading}
            className="text-balance text-3xl font-semibold tracking-tight sm:text-4xl"
          >
            {title ?? uiText(presentation.titleKey)}
          </h1>
          <p className="mx-auto mt-4 max-w-md text-pretty text-sm leading-relaxed text-muted-foreground sm:text-base">
            {description ?? uiText(presentation.descriptionKey)}
          </p>
          <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
            {kind === "authentication" && (
              <Button asChild>
                <a href="/login">{uiText("Sign in")}</a>
              </Button>
            )}
            {(onRetry || reload) && (
              <Button
                onClick={reload ? () => window.location.reload() : onRetry}
                loading={retrying}
              >
                {uiText(reload ? "Reload page" : "Retry")}
              </Button>
            )}
            <Button
              asChild
              variant={onRetry || reload || kind === "authentication" ? "outline" : "default"}
            >
              <a href="/">{uiText("Back to vault")}</a>
            </Button>
          </div>
        </div>
      </div>
    </section>
  );
}
