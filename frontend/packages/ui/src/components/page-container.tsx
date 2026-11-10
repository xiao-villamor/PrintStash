import type { ReactNode } from "react";

import { cn } from "../lib/utils";

export interface PageContainerProps {
  /** "prose" is a reading measure for long-form document views. */
  width?: "default" | "prose";
  className?: string;
  children: ReactNode;
}

/** The standard full-width page frame, with an optional reading measure. */
export function PageContainer({ width = "default", className, children }: PageContainerProps) {
  return (
    <div className="h-full overflow-y-auto [scrollbar-gutter:stable] bg-background px-4 py-6 pb-24 sm:px-6 md:pb-6">
      <div className={cn("w-full space-y-6", width === "prose" && "mx-auto max-w-4xl", className)}>
        {children}
      </div>
    </div>
  );
}
