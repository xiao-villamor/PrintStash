import { useEffect, useState } from "react";

/**
 * Keeps an entrance's motion value for the length of that entrance, then
 * releases it.
 *
 * Library entrance classes sit on containers whose children mount later: a
 * virtual row after the grid reflows, a card scrolled back into view. A class
 * left in place replays its keyframes on each of those mounts, so content the
 * reader has already seen slides or fades in again. Scoping the class to its
 * window means only the entrance itself moves.
 *
 * `entrance` identifies one entrance by identity: the state object a change
 * creates. `value` is what to apply during it, or `null` when nothing enters.
 */
export function useEntranceWindow<Entrance, T>(
  entrance: Entrance,
  value: T | null,
  ms: number,
): T | null {
  const [finished, setFinished] = useState<{ entrance: Entrance } | null>(null);
  useEffect(() => {
    if (value === null) return;
    // A cached destination can commit several layout passes before its first
    // paint. Don't consume its entrance window while that work is still hidden.
    let timer: number | undefined;
    let frame = window.requestAnimationFrame(() => {
      frame = window.requestAnimationFrame(() => {
        timer = window.setTimeout(() => setFinished({ entrance }), ms);
      });
    });
    return () => {
      window.cancelAnimationFrame(frame);
      window.clearTimeout(timer);
    };
  }, [entrance, value, ms]);
  return finished?.entrance === entrance ? null : value;
}
