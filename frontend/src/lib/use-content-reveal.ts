import { useLayoutEffect, useRef } from "react";
import { cssTimeSeconds } from "@/lib/css-time";

/** Quiet section handoff: no scale, slide, spring or outgoing snapshot. */
export function useContentReveal<T extends HTMLElement = HTMLDivElement>(
  identity: string,
  {
    enabled = true,
    scope,
    scroll = "preserve",
    animateOnEnable = false,
  }: {
    enabled?: boolean;
    scope?: string;
    scroll?: "self" | "parent" | "preserve";
    animateOnEnable?: boolean;
  } = {},
) {
  const content = useRef<T>(null);
  const previous = useRef({ identity, scope, enabled });
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
    if (
      !changed ||
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
    const animation = element.animate([{ opacity: from }, { opacity: 1 }], {
      duration: cssTimeSeconds(tokens.getPropertyValue("--duration-library-enter")) * 1000,
      easing: tokens.getPropertyValue("--ease-library-enter").trim(),
      fill: "both",
    });
    let finished = false;
    const finish = () => {
      finished = true;
      continuation.current = null;
      element.style.removeProperty("opacity");
      animation.cancel();
    };
    animation.onfinish = finish;
    const onPreference = () => {
      if (reduced.matches) finish();
    };
    reduced.addEventListener("change", onPreference);
    return () => {
      reduced.removeEventListener("change", onPreference);
      if (!finished) {
        continuation.current = Number(getComputedStyle(element).opacity);
        element.style.opacity = String(continuation.current);
      }
      animation.cancel();
    };
  }, [identity, enabled, scope, scroll, animateOnEnable]);

  return content;
}
