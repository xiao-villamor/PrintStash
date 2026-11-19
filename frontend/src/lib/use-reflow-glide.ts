import { useLayoutEffect, useState } from "react";
import { cssTimeSeconds } from "@/lib/css-time";

interface Point {
  left: number;
  top: number;
}

const running = new WeakMap<Element, Animation>();

/** The translation an in-flight glide currently paints on an element. */
function paintedOffset(element: Element): Point {
  if (!running.has(element)) return { left: 0, top: 0 };
  const matrix = new DOMMatrixReadOnly(getComputedStyle(element).transform);
  return { left: matrix.m41, top: matrix.m42 };
}

/** Painted and resting positions of keyed items near the container's visible box. */
function measure(container: HTMLElement, attribute: string) {
  const bounds = container.getBoundingClientRect();
  const margin = bounds.height;
  const items = new Map<string, { element: HTMLElement; painted: Point; rest: Point }>();
  for (const element of container.querySelectorAll<HTMLElement>(`[${attribute}]`)) {
    const key = element.getAttribute(attribute);
    if (key === null) throw new Error(`reflow_glide_missing_key:${attribute}`);
    const rect = element.getBoundingClientRect();
    if (rect.bottom < bounds.top - margin || rect.top > bounds.bottom + margin) continue;
    const offset = paintedOffset(element);
    items.set(key, {
      element,
      painted: { left: rect.left, top: rect.top },
      rest: { left: rect.left - offset.left, top: rect.top - offset.top },
    });
  }
  return items;
}

/**
 * When `layout` changes (for example, the docked inspector reserving its
 * column), keyed items commit at their new grid positions and then translate
 * from where they were painted. The grid may settle over several commits
 * (column measurement follows the width change), so every frame of a short
 * window compares resting positions and glides each item that moved again.
 * Only position animates; sizes commit at once so text and previews never
 * scale. Interruptions start from the painted position, items without a
 * previous position appear in place, and reduced motion stays immediate.
 */
export function useReflowGlide(
  container: HTMLElement | null,
  layout: string,
  attribute = "data-library-key",
) {
  const [captured, setCaptured] = useState<{
    layout: string;
    rest: ReadonlyMap<string, Point> | null;
  }>({ layout, rest: null });
  // Render runs before the commit, while the DOM still shows the previous
  // layout: the only moment the starting positions are observable.
  if (captured.layout !== layout) {
    const rest = new Map<string, Point>();
    if (container)
      for (const [key, item] of measure(container, attribute)) rest.set(key, item.rest);
    setCaptured({ layout, rest });
  }

  useLayoutEffect(() => {
    const initial = captured.rest;
    if (!initial || initial.size === 0 || !container) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const tokens = getComputedStyle(container);
    const duration = cssTimeSeconds(tokens.getPropertyValue("--duration-slow")) * 1000;
    const easing = tokens.getPropertyValue("--ease-out").trim();
    const known = new Map(initial);
    const started = performance.now();
    let frame = 0;
    const check = () => {
      for (const [key, item] of measure(container, attribute)) {
        const previous = known.get(key);
        known.set(key, item.rest);
        if (!previous) continue;
        if (
          Math.abs(previous.left - item.rest.left) < 1 &&
          Math.abs(previous.top - item.rest.top) < 1
        )
          continue;
        // The last painted frame showed the previous box plus the glide's
        // current translation; continue from there toward the new box.
        const offset = {
          left: item.painted.left - item.rest.left,
          top: item.painted.top - item.rest.top,
        };
        const start = {
          left: previous.left + offset.left - item.rest.left,
          top: previous.top + offset.top - item.rest.top,
        };
        running.get(item.element)?.cancel();
        const animation = item.element.animate(
          [
            { transform: `translate(${start.left}px, ${start.top}px)` },
            { transform: "translate(0, 0)" },
          ],
          { duration, easing, composite: "add" },
        );
        running.set(item.element, animation);
        animation.onfinish = () => {
          if (running.get(item.element) === animation) running.delete(item.element);
        };
      }
      if (performance.now() - started < duration) frame = requestAnimationFrame(check);
    };
    check();
    return () => cancelAnimationFrame(frame);
  }, [container, captured, attribute]);
}
