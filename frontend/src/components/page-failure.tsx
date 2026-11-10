import { useId } from "react";
import {
  ArrowLeft,
  FileQuestion,
  LockKeyhole,
  RefreshCw,
  ServerCrash,
  WifiOff,
} from "lucide-react";
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
}: {
  kind: keyof typeof presentations;
  title?: string;
  description?: string;
  onRetry?: () => void;
  retrying?: boolean;
  reload?: boolean;
}) {
  useUiLocale();
  const heading = useId();
  const presentation = presentations[kind];
  const Icon = presentation.icon;
  return (
    <section
      role="alert"
      aria-labelledby={heading}
      className="flex min-h-full w-full items-center justify-center bg-background px-5 py-12 sm:px-8"
    >
      <div className="w-full max-w-xl overflow-hidden rounded-xl border border-border bg-card text-card-foreground shadow-sm">
        <div className="flex items-center justify-between border-b border-border px-6 py-3 text-xs text-muted-foreground">
          <span className="font-medium">PrintStash</span>
          {presentation.code && <span className="font-mono tabular-nums">{presentation.code}</span>}
        </div>
        <div className="px-6 py-9 sm:px-9 sm:py-11">
          <div
            aria-hidden="true"
            className="mb-6 flex h-12 w-12 items-center justify-center rounded-xl border border-border bg-muted/50 text-muted-foreground"
          >
            <Icon className="h-6 w-6" strokeWidth={1.5} />
          </div>
          <h1 id={heading} className="text-2xl font-semibold tracking-tight">
            {title ?? uiText(presentation.titleKey)}
          </h1>
          <p className="mt-3 max-w-md text-sm leading-relaxed text-muted-foreground">
            {description ?? uiText(presentation.descriptionKey)}
          </p>
          <div className="mt-7 flex flex-wrap items-center gap-3">
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
                <RefreshCw className="h-4 w-4" aria-hidden="true" />
                {uiText(reload ? "Reload page" : "Retry")}
              </Button>
            )}
            <Button
              asChild
              variant={onRetry || reload || kind === "authentication" ? "outline" : "default"}
            >
              <a href="/">
                <ArrowLeft className="h-4 w-4" aria-hidden="true" />
                {uiText("Back to vault")}
              </a>
            </Button>
          </div>
        </div>
      </div>
    </section>
  );
}
