import { useLayoutEffect, useRef, useState } from "react";

import {
  PANE_SETTLED_EVENT,
  announcePaneMotion,
  dockMotion,
} from "@/components/use-collections-pane-motion";

export const LIBRARY_INSPECTOR_MIN_WIDTH = 320;
export const LIBRARY_INSPECTOR_MAX_WIDTH = 800;

export function clampInspectorWidth(width: number, maxWidth: number) {
  return Math.max(LIBRARY_INSPECTOR_MIN_WIDTH, Math.min(maxWidth, Math.round(width)));
}

const POSITION_SETTLED = 0.001;
const VELOCITY_SETTLED = 0.03;

/** Compositor keyframes keep moving while the 3D preview initializes. Presence
 * follows completion, and reversals inherit the presentation and its velocity.
 * Both docks share the same curve and duration. */
export function useInspectorMotion(open: boolean, onSettled?: () => void) {
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
    let announced = false;
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
      onSettled?.();
      if (announced) window.dispatchEvent(new Event(PANE_SETTLED_EVENT));
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

    const spring = dockMotion(element, position.current, velocity.current, target);
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
    // The inspector has already reserved or released its column. Carry cards
    // from their painted position along the inspector's progress.
    announcePaneMotion(spring, target, null);
    announced = true;
    reduced.addEventListener("change", onPreference);
    return () => {
      reduced.removeEventListener("change", onPreference);
      if (animation && !settled) {
        // Read presentation before canceling; the analytical velocity comes from
        // the same timeline, not a new zero-velocity entrance.
        // element.animate above uses the document timeline, whose time is ms.
        const elapsed = Number(animation.currentTime ?? 0) / 1000;
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
  }, [open, present, onSettled]);

  return { panel, present };
}
