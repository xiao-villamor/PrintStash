import { useLayoutEffect, useRef, useState } from "react";

import { criticalSpring } from "@/lib/critical-spring";

const POSITION_SETTLED = 0.001;
const VELOCITY_SETTLED = 0.03;

/** A compositor spring keeps moving while the 3D preview initializes. Presence
 * follows completion, and reversals inherit the presentation and its velocity. */
export function useInspectorMotion(open: boolean) {
  const panel = useRef<HTMLElement>(null);
  const position = useRef(1);
  const velocity = useRef(0);
  const [present, setPresent] = useState(open);
  if (open && !present) setPresent(true);

  useLayoutEffect(() => {
    const element = panel.current;
    if (!present || !element) return;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
    const target = open ? 0 : 1;
    let animation: Animation | null = null;
    let settled = false;
    const paint = () =>
      element.style.setProperty("--inspector-displacement", String(position.current));
    const settle = () => {
      settled = true;
      position.current = target;
      velocity.current = 0;
      paint();
      animation?.cancel();
      element.style.removeProperty("will-change");
      if (!open) setPresent(false);
    };
    const onPreference = () => {
      if (reduced.matches) settle();
    };
    if (
      reduced.matches ||
      (Math.abs(position.current - target) < POSITION_SETTLED &&
        Math.abs(velocity.current) < VELOCITY_SETTLED)
    ) {
      settle();
      return;
    }

    const tokens = getComputedStyle(element);
    const response = parseFloat(tokens.getPropertyValue("--duration-fast")) / 1000;
    const spring = criticalSpring(position.current, velocity.current, target, response);
    paint();
    element.style.willChange = "transform";
    animation = element.animate(
      spring.samples.map((sample) => ({
        offset: sample.time / spring.duration,
        transform: `translateX(calc((100% + var(--inspector-edge-gap)) * ${sample.position}))`,
      })),
      { duration: spring.duration * 1000, easing: "linear", fill: "both" },
    );
    animation.onfinish = settle;
    reduced.addEventListener("change", onPreference);
    return () => {
      reduced.removeEventListener("change", onPreference);
      if (animation && !settled) {
        // Read presentation before canceling; the analytical velocity comes from
        // the same spring timeline, not a new zero-velocity entrance.
        const currentTime = animation.currentTime;
        const elapsed = typeof currentTime === "number" ? currentTime / 1000 : 0;
        velocity.current = spring.sample(elapsed).velocity;
        const style = getComputedStyle(element);
        const distance =
          element.offsetWidth + parseFloat(style.getPropertyValue("--inspector-edge-gap"));
        position.current = new DOMMatrixReadOnly(style.transform).m41 / distance;
        paint();
      }
      animation?.cancel();
      element.style.removeProperty("will-change");
    };
  }, [open, present]);

  return { panel, present };
}
