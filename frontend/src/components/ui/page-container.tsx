import { ReactNode } from "react";
import { cn } from "@/lib/utils";

/**
 * The standard page frame: the scroll container, its padding (including the
 * mobile bottom-nav clearance), and one canonical content width.
 *
 * Full-bleed surfaces — the vault grid, model detail, the share page — do not
 * use this; they own the whole viewport by design.
 */
export function PageContainer({
  width = "default",
  className,
  children,
}: {
  /** "prose" is a reading measure, for long-form document views. */
  width?: "default" | "prose";
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className="h-full overflow-y-auto [scrollbar-gutter:stable] bg-background px-4 py-6 pb-24 sm:px-6 lg:px-8 md:pb-6">
      <div
        className={cn(
          "mx-auto w-full space-y-6",
          width === "prose" ? "max-w-4xl" : "max-w-screen-2xl",
          className,
        )}
      >
        {children}
      </div>
    </div>
  );
}
