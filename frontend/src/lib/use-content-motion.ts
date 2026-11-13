import { useLayoutEffect, useRef } from "react";
import { criticalSpring } from "./critical-spring";

/** Section changes share one opaque depth settle. The frame never participates,
 * and interrupted changes inherit the current presentation and velocity. */
export function useContentMotion<T extends HTMLElement = HTMLDivElement>(
  identity: string,
  {
    enabled = true,
    scroll = "preserve",
    scope,
    animateOnEnable = false,
  }: {
    enabled?: boolean;
    /** Changes to the surrounding surface reset motion instead of replaying it. */
    scope?: string;
    animateOnEnable?: boolean;
    scroll?: "self" | "parent" | "preserve";
  } = {},
) {
  const content = useRef<T>(null);
  const previous = useRef({ identity, enabled, scope });
  const progress = useRef(0);
  const velocity = useRef(0);
  const interrupted = useRef(false);

  useLayoutEffect(() => {
    const changed = previous.current.identity !== identity;
    const wasEnabled = previous.current.enabled;
    const sameScope = previous.current.scope === scope;
    previous.current = { identity, enabled, scope };
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
      interrupted.current = false;
      progress.current = 0;
      velocity.current = 0;
      element.style.removeProperty("transform");
      return;
    }
    const tokens = getComputedStyle(element);
    const initialScale = Number(tokens.getPropertyValue("--scale-content-enter"));
    const response = parseFloat(tokens.getPropertyValue("--duration-fast")) / 1000;
    if (!interrupted.current) {
      progress.current = 0;
      velocity.current = 0;
    }
    const spring = criticalSpring(progress.current, velocity.current, 1, response);
    if (spring.duration === 0) {
      interrupted.current = false;
      progress.current = 0;
      velocity.current = 0;
      element.style.removeProperty("transform");
      return;
    }
    let settled = false;
    element.style.willChange = "transform";
    const animation = element.animate(
      spring.samples.map((sample) => ({
        offset: sample.time / spring.duration,
        transform: `scale(${initialScale + (1 - initialScale) * sample.position})`,
      })),
      { duration: spring.duration * 1000, easing: "linear", fill: "both" },
    );
    const finish = () => {
      settled = true;
      interrupted.current = false;
      progress.current = 0;
      velocity.current = 0;
      element.style.removeProperty("transform");
      element.style.removeProperty("will-change");
      animation.cancel();
    };
    animation.onfinish = finish;
    const onPreference = () => {
      if (reduced.matches) finish();
    };
    reduced.addEventListener("change", onPreference);
    return () => {
      reduced.removeEventListener("change", onPreference);
      if (!settled) {
        const scale = new DOMMatrixReadOnly(getComputedStyle(element).transform).a;
        const time = animation.currentTime;
        progress.current = (scale - initialScale) / (1 - initialScale);
        velocity.current = spring.sample(typeof time === "number" ? time / 1000 : 0).velocity;
        interrupted.current = true;
        element.style.transform = `scale(${scale})`;
      }
      animation.cancel();
      element.style.removeProperty("will-change");
    };
  }, [identity, enabled, scroll, scope, animateOnEnable]);

  return content;
}
