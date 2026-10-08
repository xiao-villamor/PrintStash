/** Reference-host acceptance. CI exercises correctness, never shared-runner wall time. */
export const budgets = Object.freeze({
  warm: { median: 500, p95: 800, samples: 30 },
  fresh: { median: 1000, p95: 1500, samples: 20 },
  collection: { median: 400, p95: 800, samples: 20 },
  back: { median: 250, p95: 500, samples: 20 },
  page: { median: 300, p95: 500, samples: 20 },
  search: { median: 650, p95: 1000, samples: 20 },
});
export function summarize(samples, kind, { diagnostic = false, baseline = false } = {}) {
  const budget = budgets[kind];
  if (!budget) throw new Error(`Unknown journey: ${kind}`);
  if (!samples.length || (!diagnostic && samples.length !== budget.samples))
    throw new Error(`${kind}: expected ${budget.samples} samples, received ${samples.length}`);
  if (baseline && samples.some((sample) => sample.errors.length || sample.complete === null))
    return {
      n: samples.length,
      failed: samples.filter((sample) => sample.errors.length || sample.complete === null).length,
      median: null,
      p95: null,
      accepted: false,
    };
  for (const sample of samples) {
    for (const field of ["content", "tree", "media", "complete"])
      if (!Number.isFinite(sample[field]) || sample[field] < 0)
        throw new Error(`${kind}: missing/invalid ${field}`);
    if (sample.errors.length) throw new Error(`${kind}: failed sample`);
    if (sample.complete < Math.max(sample.content, sample.tree, sample.media))
      throw new Error(`${kind}: completion preceded its prerequisites`);
    if (!baseline && kind !== "page") {
      for (const phase of [
        "start",
        "session-validated",
        "cards",
        "tree",
        "media",
        "usable",
        "complete",
      ])
        if (!Number.isFinite(sample.phases?.[phase]))
          throw new Error(`${kind}: missing phase ${phase}`);
      if (sample.phases.start < 0) throw new Error(`${kind}: stale navigation phases`);
      if (
        sample.phases.complete <
        Math.max(sample.phases.cards, sample.phases.tree, sample.phases.media)
      )
        throw new Error(`${kind}: application completed prematurely`);
    }
  }
  const values = samples.map((s) => s.complete).toSorted((a, b) => a - b);
  const median =
    (values[Math.floor((values.length - 1) / 2)] + values[Math.floor(values.length / 2)]) / 2;
  const p95 = values[Math.ceil(values.length * 0.95) - 1];
  return { n: values.length, median, p95, accepted: median <= budget.median && p95 <= budget.p95 };
}
export function assertAcceptance(cohorts, { baseline = false } = {}) {
  const names = new Set();
  const failures = [];
  for (const cohort of cohorts) {
    const key = `${cohort.scenario}/${cohort.device}/${cohort.locale}`;
    if (names.has(key)) throw new Error(`Duplicate cohort ${key}`);
    names.add(key);
    for (const kind of cohort.scenario === "dense" && !baseline
      ? Object.keys(budgets)
      : ["warm", "fresh"]) {
      const result = summarize(cohort.rows[kind] ?? [], kind, { baseline });
      if (!result.accepted && !baseline)
        failures.push(`${key}/${kind}: ${result.median.toFixed(1)} / ${result.p95.toFixed(1)} ms`);
    }
  }
  for (const scenario of ["distributed", "dense", "deep"])
    for (const device of ["desktop", "mobile"])
      for (const locale of ["en", "es"])
        if (!names.has(`${scenario}/${device}/${locale}`))
          throw new Error(`Missing cohort ${scenario}/${device}/${locale}`);
  if (failures.length) throw new Error(`Performance budgets exceeded:\n${failures.join("\n")}`);
}
