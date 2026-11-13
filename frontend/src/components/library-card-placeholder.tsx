import { useLayoutEffect, useRef, type ReactNode, type RefObject } from "react";
import { Skeleton } from "@/components/ui/skeleton";

/** The same quiet surface before browse data arrives and while an image decodes. */
export function LibraryPreviewPlaceholder() {
  return <div aria-hidden className="library-preview-placeholder h-full w-full" />;
}

export function LibraryCardDetailsPlaceholder() {
  return (
    <div aria-hidden className="flex h-24 flex-col justify-between bg-card px-3.5 py-3">
      <Skeleton className="h-4 w-3/4 animate-none" />
      <div className="flex items-center justify-between border-t border-border pt-3">
        <Skeleton className="h-3 w-1/3 animate-none" />
        <Skeleton className="h-3 w-10 animate-none" />
      </div>
    </div>
  );
}

/** Keep the frame opaque; only reposition it when the destination layout changes. */
export function LibraryCardFill({
  filling,
  origins,
  index,
  children,
}: {
  filling: boolean;
  origins: RefObject<readonly DOMRect[]>;
  index: number;
  children: ReactNode;
}) {
  const frame = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const node = frame.current;
    const origin = origins.current[index];
    if (!filling || !node || !origin) return;
    // Sibling collection grids finish their layout effects before this frame.
    // Measuring earlier would animate toward their temporary one-column layout.
    const tick = requestAnimationFrame(() => {
      const destination = node.getBoundingClientRect();
      node.style.setProperty("--card-fill-x", `${origin.left - destination.left}px`);
      node.style.setProperty("--card-fill-y", `${origin.top - destination.top}px`);
      node.classList.add("library-card-fill-position");
    });
    return () => {
      cancelAnimationFrame(tick);
      node.classList.remove("library-card-fill-position");
      node.style.removeProperty("--card-fill-x");
      node.style.removeProperty("--card-fill-y");
    };
  }, [filling, origins, index]);
  return (
    <div ref={frame} className="relative h-full">
      {children}
      {filling && (
        <div
          aria-hidden
          className="library-card-fill pointer-events-none absolute inset-px flex flex-col overflow-hidden"
        >
          <div className="library-card-fill-preview shrink-0" />
          <div className="flex-1 bg-card">
            <LibraryCardDetailsPlaceholder />
          </div>
        </div>
      )}
    </div>
  );
}
