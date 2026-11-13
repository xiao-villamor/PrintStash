import { useLayoutEffect, useRef } from "react";

/** Quiet section handoff: no scale, slide, spring or outgoing snapshot. */
export function useContentReveal(
  identity: string,
  { enabled, scope }: { enabled: boolean; scope: string },
) {
  const content = useRef<HTMLDivElement>(null);
  const previous = useRef({ identity, scope, enabled });
  const continuation = useRef<number | null>(null);

  useLayoutEffect(() => {
    const changed = previous.current.identity !== identity;
    const sameScope = previous.current.scope === scope;
    const wasEnabled = previous.current.enabled;
    previous.current = { identity, scope, enabled };
    const element = content.current;
    if (!element) return;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
    if (!changed || !enabled || !wasEnabled || !sameScope || reduced.matches) {
      continuation.current = null;
      element.style.removeProperty("opacity");
      return;
    }
    const tokens = getComputedStyle(element);
    const from = continuation.current ?? Number(tokens.getPropertyValue("--opacity-content-enter"));
    const animation = element.animate([{ opacity: from }, { opacity: 1 }], {
      duration: parseFloat(tokens.getPropertyValue("--duration-library-enter")),
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
  }, [identity, enabled, scope]);

  return content;
}
