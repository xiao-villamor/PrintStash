import { useLayoutEffect, useRef, useState } from "react";

import {
  PANE_SETTLED_EVENT,
  PaneMovingEvent,
  dockMotion,
} from "@/components/use-collections-pane-motion";
import { cssTimeSeconds } from "@/lib/css-time";

export const LIBRARY_INSPECTOR_MIN_WIDTH = 320;
export const LIBRARY_INSPECTOR_MAX_WIDTH = 800;

export function clampInspectorWidth(width: number, maxWidth: number) {
  return Math.max(LIBRARY_INSPECTOR_MIN_WIDTH, Math.min(maxWidth, Math.round(width)));
}

const POSITION_SETTLED = 0.001;
const VELOCITY_SETTLED = 0.03;

/** Compositor keyframes keep moving while the 3D preview initializes. Presence
 * follows completion, and reversals inherit the presentation and its velocity.
 * The panel shares the Collections pane's dock curve and duration. */
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

    const tokens = getComputedStyle(element);
    // The same curve and timing as the Collections pane: cards reflowing
    // beside either panel then move identically.
    const duration = cssTimeSeconds(tokens.getPropertyValue("--duration-slow"));
    const spring = dockMotion(position.current, velocity.current, target, duration);
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
    // The docked panel has already reserved (or released) its column. Cards
    // reflow through the same path as the Collections pane: one resting
    // layout, each card carried along this motion's own progress.
    const from = spring.samples[0]?.position ?? position.current;
    const span = target - from;
    window.dispatchEvent(
      new PaneMovingEvent({
        opening: open,
        contentDelta: 0,
        durationMs: spring.duration * 1000,
        progress: spring.samples.map((sample) =>
          Math.abs(span) < 1e-6 ? 1 : (sample.position - from) / span,
        ),
      }),
    );
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
