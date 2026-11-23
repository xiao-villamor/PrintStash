import { type ReactNode } from "react";
import { useContentReveal } from "@/lib/use-content-reveal";
import { Skeleton } from "@/components/ui/skeleton";

/** The same quiet surface before browse data arrives and while an image decodes. */
export function LibraryPreviewPlaceholder() {
  return <div aria-hidden className="library-preview-placeholder h-full w-full" />;
}

/** Built from the card's own title and footer boxes, so a placeholder has the
 * height of the Model card (without a revision) that replaces it. */
export function LibraryCardDetailsPlaceholder() {
  return (
    <div aria-hidden className="flex flex-col bg-card">
      <div className="library-card-content library-card-standard">
        <h3 className="h-[1lh]">
          <Skeleton className="h-4 w-3/4 animate-none" />
        </h3>
      </div>
      <div className="library-card-summary-container">
        <div className="library-card-revision-summary">
          <div className="library-card-context flex items-center">
            <Skeleton className="h-3 w-1/4 animate-none" />
          </div>
          <div className="library-card-meta items-center">
            <Skeleton className="h-3 w-1/3 animate-none" />
            <Skeleton className="h-3 w-10 animate-none" />
          </div>
        </div>
      </div>
    </div>
  );
}

/** The actual card enters after it mounts, including cached destinations.
 * Thumbnail decoding has its own crossfade inside this stationary surface. */
export function LibraryCardFill({
  identity,
  animateOnMount,
  children,
  className = "library-card-fill-frame",
}: {
  className?: string;
  identity: string;
  animateOnMount: boolean;
  children: ReactNode;
}) {
  const frame = useContentReveal(identity, { animateOnMount });
  return (
    <div ref={frame} className={`${className} relative h-full`}>
      {children}
    </div>
  );
}
