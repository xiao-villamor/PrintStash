import { useEffect, useState, type ReactNode } from "react";
import { Skeleton } from "@/components/ui/skeleton";
import { uiText } from "@/lib/locale";

const PLACEHOLDER_DELAY_MS = 200;

/** The placeholder stays in the same frame while the real content fills it.
 * Fast/cached reads never display a skeleton. Background reads retain content. */
export function DetailTabLoading({
  pending,
  variant,
  enabled = true,
  children,
}: {
  pending: boolean;
  variant: "source" | "history" | "similar";
  enabled?: boolean;
  children: ReactNode;
}) {
  const [showPlaceholder, setShowPlaceholder] = useState(false);
  useEffect(() => {
    if (!enabled || !pending) {
      setShowPlaceholder(false);
      return;
    }
    const timer = window.setTimeout(() => setShowPlaceholder(true), PLACEHOLDER_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [pending, enabled]);

  if (!enabled) return children;

  return (
    <div
      className="detail-tab-load"
      aria-busy={pending}
      data-pending={pending}
      data-variant={variant}
    >
      {pending && (
        <span role="status" className="sr-only">
          {uiText("Loading…")}
        </span>
      )}
      <div className="detail-tab-load-content">{children}</div>
      <div
        className="detail-tab-placeholder"
        data-visible={pending && showPlaceholder}
        aria-hidden="true"
      >
        {variant !== "similar" && (
          <div className="flex min-h-8 flex-wrap items-center justify-between gap-3">
            <Skeleton className="h-4 w-36" />
            <div className="flex gap-2">
              <Skeleton className="h-7 w-24" />
              {variant === "history" && <Skeleton className="h-7 w-32" />}
            </div>
          </div>
        )}
        {variant === "source" ? (
          <>
            <div className="space-y-3 border-b border-border py-4">
              <Skeleton className="h-5 w-28" />
              <Skeleton className="h-3 w-2/3" />
              <div className="grid grid-cols-2 gap-4 @xl/source:grid-cols-4">
                {[0, 1, 2, 3].map((item) => (
                  <div key={item} className="space-y-2">
                    <Skeleton className="h-3 w-16" />
                    <Skeleton className="h-4 w-20" />
                  </div>
                ))}
              </div>
            </div>
            <div className="space-y-3 py-4">
              <Skeleton className="h-4 w-24" />
              <Skeleton className="h-3 w-full" />
              <Skeleton className="h-3 w-3/4" />
            </div>
          </>
        ) : (
          <>
            {variant === "history" && <Skeleton className="mt-3 h-3 w-3/4" />}
            {[0, 1].map((item) => (
              <div
                key={item}
                className="flex items-start justify-between gap-4 border-b border-border py-4"
              >
                <div className="flex-1 space-y-3">
                  <Skeleton className="h-4 w-2/3" />
                  <Skeleton className="h-3 w-1/2" />
                </div>
                <Skeleton className="h-5 w-16" />
              </div>
            ))}
          </>
        )}
      </div>
    </div>
  );
}
