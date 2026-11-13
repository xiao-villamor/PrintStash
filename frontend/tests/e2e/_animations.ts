/*
 * The CSS animations a page started, as they start.
 *
 * Library entrance classes only exist for the length of their entrance, so a
 * computed style read after the content appears races the entrance on a slow
 * runner. The `animationstart` event does not: it is recorded the moment the
 * entrance begins, whatever the runner's speed. Install before the navigation it
 * should cover.
 */
import type { Page } from "@playwright/test";

export interface AnimationStart {
  name: string;
  /** The animated element's class list. */
  target: string;
  /** Its parent's class list: entrance classes sit on the container. */
  container: string;
  text: string;
  duration: string;
}

declare global {
  interface Window {
    animationStarts: AnimationStart[];
  }
}

export async function recordAnimationStarts(page: Page): Promise<void> {
  await page.addInitScript(() => {
    window.animationStarts = [];
    document.addEventListener(
      "animationstart",
      (event) => {
        if (!(event.target instanceof HTMLElement)) return;
        window.animationStarts.push({
          name: event.animationName,
          target: event.target.className,
          container: event.target.parentElement?.className ?? "",
          text: event.target.textContent ?? "",
          duration: getComputedStyle(event.target).animationDuration,
        });
      },
      true,
    );
  });
}

export function animationStarts(page: Page): Promise<AnimationStart[]> {
  return page.evaluate(() => window.animationStarts);
}

/** Forget what has started so far, to watch only what an action starts next. */
export function forgetAnimationStarts(page: Page): Promise<void> {
  return page.evaluate(() => {
    window.animationStarts = [];
  });
}
