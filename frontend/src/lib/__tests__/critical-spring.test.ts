/** Critical spring trajectories preserve continuity across interruptions without overshoot. */
import { describe, expect, it } from "vitest";
import { criticalSpring } from "../critical-spring";

describe("criticalSpring", () => {
  it("opens a resting panel without overshooting", () => {
    const motion = criticalSpring(0, 0, 1, 0.2);
    expect(motion.sample(0)).toEqual({ position: 0, velocity: 0 });
    expect(motion.samples.at(-1)?.position).toBe(1);
    expect(motion.duration).toBeGreaterThan(0);
    expect(motion.duration).toBeLessThan(0.4);
    for (let i = 1; i < motion.samples.length; i++) {
      expect(motion.samples[i].position).toBeGreaterThanOrEqual(motion.samples[i - 1].position);
      expect(motion.samples[i].position).toBeLessThanOrEqual(1);
    }
    expect(Math.abs(motion.sample(motion.duration).velocity)).toBeLessThan(0.03);
  });

  it("preserves motion continuity when the user reverses direction", () => {
    const opening = criticalSpring(0, 0, 1, 0.2);
    const current = opening.sample(0.04);
    const closing = criticalSpring(current.position, current.velocity, 0, 0.2);
    expect(closing.sample(0)).toEqual(current);
    expect(closing.samples.at(-1)?.position).toBe(0);
    expect(closing.sample(0.1).velocity).toBeLessThan(0);
  });

  it("settles immediately when the panel is already at rest", () => {
    const motion = criticalSpring(1, 0, 1, 0.2);
    expect(motion.duration).toBe(0);
    expect(motion.samples).toEqual([{ time: 0, position: 1 }]);
  });

  it("bounds the timeline even with an extreme incoming velocity", () => {
    const motion = criticalSpring(0, 1e9, 1, 0.2);
    expect(motion.duration).toBe(0.4);
    expect(motion.samples.at(-1)?.position).toBe(1);
    expect(motion.samples.every(({ position }) => Number.isFinite(position))).toBe(true);
  });
});
