/**
 * Seconds in a CSS <time> token. Production CSS is minified, which rewrites
 * `300ms` as `.3s`; reading either form as milliseconds made every motion
 * hook 1000 times too fast in builds (no animation at all).
 */
export function cssTimeSeconds(value: string): number {
  const match = /^\s*(-?(?:\d+\.?\d*|\.\d+))(ms|s)\s*$/i.exec(value);
  if (!match) throw new Error(`invalid_css_time:${value}`);
  const amount = Number(match[1]);
  return match[2]!.toLowerCase() === "ms" ? amount / 1000 : amount;
}

/** Reads a motion duration token (e.g. `--duration-fast`) from an element. */
export function durationToken(element: Element, name: `--${string}`): number {
  return cssTimeSeconds(getComputedStyle(element).getPropertyValue(name));
}
