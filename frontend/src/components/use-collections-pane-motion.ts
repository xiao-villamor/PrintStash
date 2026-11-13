import { useLayoutEffect, useRef } from "react";
const KEYFRAME_COUNT = 60;

/** A continuous ease into rest, with no spring response or oscillation.
 * A reversal carries its current velocity through a short, smooth turn. */
function dockMotion(from: number, velocity: number, target: number, duration: number) {
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

/** Only the shell column changes width; its contents retain their intrinsic
 * width and move inside a clip. Resizing remains direct, without spring lag. */
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
    };
    if (!initialized.current || reduced.matches || !desktop.matches || fullWidth === 0) {
      initialized.current = true;
      finish();
    } else {
      const duration =
        parseFloat(getComputedStyle(shell).getPropertyValue("--duration-slow")) / 1000;
      const motion = dockMotion(
        fromWidth / fullWidth,
        velocity.current / fullWidth,
        open ? 1 : 0,
        duration,
      );
      trajectory = motion;
      if (Math.abs(fromWidth - (open ? fullWidth : 0)) < 0.5 && Math.abs(velocity.current) < 1)
        finish();
      else {
        body.style.visibility = "visible";
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
        const time = widthAnimation.currentTime;
        velocity.current =
          trajectory.sample(typeof time === "number" ? time / 1000 : 0).velocity * fullWidth;
        shell.style.width = `${shell.getBoundingClientRect().width}px`;
      }
      widthAnimation?.cancel();
    };
  }, [open]);

  return { viewport, content };
}
