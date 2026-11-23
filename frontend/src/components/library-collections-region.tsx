import { useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { useMediaQuery } from "@/lib/use-media-query";
import { cssTimeSeconds } from "@/lib/css-time";

/** Commit layout once; move the following content on the compositor while the row is clipped. */
export function LibraryCollectionsRegion({
  present,
  enabled,
  followingContent,
  children,
}: {
  present: boolean;
  enabled: boolean;
  followingContent: RefObject<HTMLDivElement | null>;
  children: ReactNode;
}) {
  const reduced = useMediaQuery("(prefers-reduced-motion: reduce)");
  const animate = enabled && !reduced;
  const row = useRef<HTMLDivElement>(null);
  const previous = useRef(present);
  const continuation = useRef<{ height: number; opacity: number } | null>(null);
  const [retained, setRetained] = useState(children);
  if (present && retained !== children) setRetained(children);
  if (!present && !animate && retained !== null) setRetained(null);

  useLayoutEffect(() => {
    const element = row.current;
    const content = followingContent.current;
    if (!element || !content) return;
    const fullHeight = element.getBoundingClientRect().height;
    const target = present ? fullHeight : 0;
    const before = previous.current;
    previous.current = present;
    const from = continuation.current ?? {
      height: before ? fullHeight : 0,
      opacity: before ? 1 : 0,
    };
    continuation.current = null;
    if (!animate || present === before) return;

    const tokens = getComputedStyle(element);
    const timing = {
      duration: cssTimeSeconds(tokens.getPropertyValue("--duration-library-enter")) * 1000,
      easing: tokens.getPropertyValue("--ease-library-enter").trim(),
      fill: "both" as const,
    };
    // Child grids measure their columns in layout effects. Hold the visible
    // position until those synchronous measurements settle, then start together
    // before the next paint. No network wait or arbitrary timeout is added.
    content.style.setProperty("transform", `translateY(${from.height - target}px)`);
    element.style.setProperty(
      "clip-path",
      `inset(0px 0px ${Math.max(0, fullHeight - from.height)}px 0px)`,
    );
    element.style.setProperty("opacity", String(from.opacity));
    const clearHold = () => {
      content.style.removeProperty("transform");
      element.style.removeProperty("clip-path");
      element.style.removeProperty("opacity");
    };
    let movement: Animation | null = null;
    let clipping: Animation | null = null;
    let fade: Animation | null = null;
    let settledHeight = target;
    let finished = false;
    const frame = requestAnimationFrame(() => {
      const height = element.getBoundingClientRect().height;
      settledHeight = present ? height : 0;
      movement = content.animate(
        [
          { transform: `translateY(${from.height - settledHeight}px)` },
          { transform: "translateY(0px)" },
        ],
        timing,
      );
      clipping = element.animate(
        [
          { clipPath: `inset(0px 0px ${Math.max(0, height - from.height)}px 0px)` },
          { clipPath: `inset(0px 0px ${present ? 0 : height}px 0px)` },
        ],
        timing,
      );
      fade = element.animate([{ opacity: from.opacity }, { opacity: present ? 1 : 0 }], timing);
      clearHold();
      movement.onfinish = () => {
        finished = true;
        movement?.cancel();
        clipping?.cancel();
        fade?.cancel();
        if (!present) setRetained(null);
      };
    });
    return () => {
      cancelAnimationFrame(frame);
      if (!finished) {
        continuation.current = movement
          ? {
              height:
                settledHeight + new DOMMatrixReadOnly(getComputedStyle(content).transform).m42,
              opacity: Number(getComputedStyle(element).opacity),
            }
          : from;
      }
      if (movement) movement.onfinish = null;
      movement?.cancel();
      clipping?.cancel();
      fade?.cancel();
      clearHold();
    };
  }, [present, animate, followingContent]);

  return (
    <div
      className="library-collections-region"
      data-open={present}
      inert={!present}
      aria-hidden={!present || undefined}
    >
      <div ref={row} className="library-collections-row flow-root">
        {present ? children : retained}
      </div>
    </div>
  );
}
