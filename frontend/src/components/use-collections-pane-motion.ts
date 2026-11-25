import { useEffect, useLayoutEffect, useRef } from "react";
import { durationToken } from "@/lib/css-time";
const KEYFRAME_COUNT = 60;

/** A continuous ease into rest, with no spring response or oscillation.
 * A reversal carries its current velocity through a short, smooth turn.
 * Both docked panels (Collections and the Model inspector) use it. */
export function dockMotion(element: Element, from: number, velocity: number, target: number) {
  const duration = durationToken(element, "--duration-fast");
  const distance = target - from;
  const tangent = velocity * duration;
  const sample = (seconds: number) => {
    const t = Math.max(0, Math.min(1, seconds / duration));
    const quadratic = 3 * distance - 2 * tangent;
    const cubic = tangent - 2 * distance;
    const position = from + tangent * t + quadratic * t * t + cubic * t * t * t;
    return {
      position: Math.max(0, Math.min(1, position)),
      velocity:
        position < 0 || position > 1
          ? 0
          : (tangent + 2 * quadratic * t + 3 * cubic * t * t) / duration,
    };
  };
  const samples = Array.from({ length: KEYFRAME_COUNT + 1 }, (_, index) => {
    const time = (index / KEYFRAME_COUNT) * duration;
    return { time, position: sample(time).position };
  });
  return { samples, duration, sample };
}

/** Re-measure layouts after a dock width is committed. */
export const PANE_SETTLED_EVENT = "printstash:pane-settled";
/** Projects the sidebar's final width before its shell starts sliding. */
export const PANE_MOVING_EVENT = "printstash:pane-moving";

export class PaneMovingEvent extends Event {
  constructor(readonly targetSidebarWidth: number) {
    super(PANE_MOVING_EVENT);
  }
}

function setMoving(moving: boolean) {
  const root = document.documentElement;
  if (moving) root.dataset.paneMoving = "";
  else if ("paneMoving" in root.dataset) {
    delete root.dataset.paneMoving;
    window.dispatchEvent(new Event(PANE_SETTLED_EVENT));
  }
}

/** Move the shell edge with the workspace while the grid uses the sidebar's final width. */
export function useCollectionsPaneMotion(open: boolean) {
  const viewport = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const initialized = useRef(false);
  const velocity = useRef(0);

  useLayoutEffect(() => {
    const shell = viewport.current;
    const body = content.current;
    if (!shell || !body) return;
    if (!open && shell.contains(document.activeElement)) {
      document
        .querySelector<HTMLButtonElement>('.shell-topbar button[aria-controls="collections-pane"]')
        ?.focus({ preventScroll: true });
    }
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
    const desktop = window.matchMedia("(min-width: 768px)");
    let fullWidth = body.getBoundingClientRect().width;
    const fromWidth = shell.getBoundingClientRect().width;
    let widthAnimation: Animation | null = null;
    let trajectory: ReturnType<typeof dockMotion> | null = null;
    let settled = false;

    const finish = () => {
      settled = true;
      velocity.current = 0;
      shell.style.width = `${open ? fullWidth : 0}px`;
      body.style.visibility = open ? "visible" : "hidden";
      widthAnimation?.cancel();
      setMoving(false);
    };
    if (!initialized.current || reduced.matches || !desktop.matches || fullWidth === 0) {
      initialized.current = true;
      finish();
    } else {
      const target = open ? 1 : 0;
      const targetWidth = target * fullWidth;
      const motion = dockMotion(shell, fromWidth / fullWidth, velocity.current / fullWidth, target);
      trajectory = motion;
      if (Math.abs(fromWidth - targetWidth) < 0.5 && Math.abs(velocity.current) < 1) finish();
      else {
        body.style.visibility = "visible";
        setMoving(true);
        window.dispatchEvent(new PaneMovingEvent(targetWidth));
        const timing = {
          duration: motion.duration * 1000,
          easing: "linear",
          fill: "both",
        } satisfies KeyframeAnimationOptions;
        widthAnimation = shell.animate(
          motion.samples.map((sample) => ({
            offset: sample.time / motion.duration,
            width: `${Math.max(0, sample.position * fullWidth)}px`,
          })),
          timing,
        );
        widthAnimation.onfinish = finish;
      }
    }

    const resize = new ResizeObserver(() => {
      const nextWidth = body.getBoundingClientRect().width;
      if (Math.abs(nextWidth - fullWidth) < 0.5) return;
      fullWidth = nextWidth;
      finish();
    });
    resize.observe(body);
    const onPreference = () => {
      if (reduced.matches || !desktop.matches) finish();
    };
    reduced.addEventListener("change", onPreference);
    desktop.addEventListener("change", onPreference);
    return () => {
      resize.disconnect();
      reduced.removeEventListener("change", onPreference);
      desktop.removeEventListener("change", onPreference);
      if (widthAnimation && trajectory && !settled) {
        const time = Number(widthAnimation.currentTime);
        velocity.current =
          trajectory.sample(Number.isFinite(time) ? time / 1000 : 0).velocity * fullWidth;
        shell.style.width = `${shell.getBoundingClientRect().width}px`;
      }
      widthAnimation?.cancel();
    };
  }, [open]);
  // Clear the motion marker if the shell unmounts during a toggle.
  useEffect(() => () => setMoving(false), []);

  return { viewport, content };
}
