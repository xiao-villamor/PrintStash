import { useState } from "react";

/**
 * The count badge of a tree row. When its value changes it moves a few pixels
 * toward the new value (up when it grew, down when it shrank) and fades in; while
 * the value on screen belongs to filters that were just replaced, it is dimmed.
 * Starting from what is on screen keeps a refilter from reading as a reload.
 */
export function TreeCount({ value, stale = false }: { value: number; stale?: boolean }) {
  const [shown, setShown] = useState({ value, tick: 0, grew: true });
  if (shown.value !== value) {
    setShown({ value, tick: shown.tick + 1, grew: value > shown.value });
  }
  const motion = shown.tick === 0 ? "" : shown.grew ? "animate-count-up" : "animate-count-down";
  return (
    <span
      key={shown.tick}
      data-stale={stale || undefined}
      data-tree-count
      className={`block min-w-[18px] rounded bg-muted px-1 py-0.5 text-center text-2xs font-medium text-muted-foreground transition-opacity duration-press ease-out ${stale ? "opacity-50" : ""} ${motion}`}
    >
      {value}
    </span>
  );
}
