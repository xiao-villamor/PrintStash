import { useLayoutEffect, useRef } from "react";
import { cssTimeSeconds } from "@/lib/css-time";

// Treat the last imperceptible tail as settled, even if onfinish is still queued.
const SETTLED_OPACITY = 0.99;

/** Quiet section handoff: no scale, slide, spring or outgoing snapshot. */
export function useContentReveal<T extends HTMLElement = HTMLDivElement>(
  identity: string,
  {
    enabled = true,
    scope,
    scroll = "preserve",
    animateOnEnable = false,
    animateOnMount = false,
    timing = "content",
  }: {
    enabled?: boolean;
    scope?: string;
    scroll?: "self" | "parent" | "preserve";
    animateOnEnable?: boolean;
    animateOnMount?: boolean;
    /** `press` is the quicker handoff for tool switches inside a stationary frame. */
    timing?: "content" | "press";
  } = {},
) {
  const content = useRef<T>(null);
  const previous = useRef({ identity: animateOnMount ? null : identity, scope, enabled });
  const continuation = useRef<number | null>(null);

  useLayoutEffect(() => {
    const changed = previous.current.identity !== identity;
    const sameScope = previous.current.scope === scope;
    const wasEnabled = previous.current.enabled;
    previous.current = { identity, scope, enabled };
    const element = content.current;
    if (!element) return;
    if (changed && scroll !== "preserve") {
      (scroll === "self" ? element : element.parentElement)?.scrollTo({
        top: 0,
        behavior: "instant",
      });
    }
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
    // StrictMode replays mount effects: resume the unfinished entrance even
    // when its identity is unchanged, rather than exposing an opaque first frame.
    if (
      (!changed && continuation.current === null) ||
      !enabled ||
      (!wasEnabled && !animateOnEnable) ||
      !sameScope ||
      reduced.matches
    ) {
      continuation.current = null;
      element.style.removeProperty("opacity");
      return;
    }
    const tokens = getComputedStyle(element);
    const from = continuation.current ?? Number(tokens.getPropertyValue("--opacity-content-enter"));
    // Keep the starting contrast visible for a paint. Cached destinations can
    // otherwise spend the whole entrance inside the same busy render commit.
    element.style.opacity = String(from);
    let animation: Animation | null = null;
    let frame: number;
    let finished = false;
    const finish = () => {
      finished = true;
      cancelAnimationFrame(frame);
      continuation.current = null;
      element.style.removeProperty("opacity");
      if (animation) animation.onfinish = null;
      animation?.cancel();
    };
    frame = requestAnimationFrame(() => {
      frame = requestAnimationFrame(() => {
        if (finished) return;
        animation = element.animate([{ opacity: from }, { opacity: 1 }], {
          duration:
            cssTimeSeconds(
              tokens.getPropertyValue(
                timing === "press" ? "--duration-press" : "--duration-library-enter",
              ),
            ) * 1000,
          easing: tokens
            .getPropertyValue(timing === "press" ? "--ease-out" : "--ease-library-enter")
            .trim(),
          fill: "both",
        });
        animation.onfinish = finish;
      });
    });
    const onPreference = () => {
      if (reduced.matches) finish();
    };
    reduced.addEventListener("change", onPreference);
    return () => {
      reduced.removeEventListener("change", onPreference);
      if (!finished) {
        const opacity = Number(getComputedStyle(element).opacity);
        continuation.current = opacity < SETTLED_OPACITY ? opacity : null;
        element.style.opacity = String(opacity);
      }
      cancelAnimationFrame(frame);
      if (animation) animation.onfinish = null;
      animation?.cancel();
    };
  }, [identity, enabled, scope, scroll, animateOnEnable, timing]);

  return content;
}
