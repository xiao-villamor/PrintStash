const POSITION_SETTLED = 0.001;
const VELOCITY_SETTLED = 0.03;
const SAMPLES_PER_RESPONSE = 60;

/** Normalized, critically damped motion shared by the workbench's side panels.
 * Sampled keyframes run on the browser timeline; sample() preserves velocity
 * when a user reverses an in-flight transition. */
export function criticalSpring(from: number, velocity: number, target: number, response: number) {
  const omega = (2 * Math.PI) / response;
  const sample = (time: number) => {
    const displacement = from - target;
    const coefficient = velocity + omega * displacement;
    const decay = Math.exp(-omega * time);
    return {
      position: target + (displacement + coefficient * time) * decay,
      velocity: (velocity - omega * coefficient * time) * decay,
    };
  };
  const samples = [];
  for (let step = 0; step <= SAMPLES_PER_RESPONSE * 2; step += 1) {
    const time = (step * response) / SAMPLES_PER_RESPONSE;
    const value = sample(time);
    samples.push({ time, position: value.position });
    if (
      Math.abs(value.position - target) < POSITION_SETTLED &&
      Math.abs(value.velocity) < VELOCITY_SETTLED
    )
      break;
  }
  const duration = samples[samples.length - 1].time;
  samples[samples.length - 1].position = target;
  return { samples, duration, sample };
}
