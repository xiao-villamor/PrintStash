import { chromium } from "@playwright/test";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { createHash } from "node:crypto";
import {
  installObserver,
  observeInteraction,
  currentNavigationStart,
  openAvailableLibraryTree,
} from "./observer.mjs";
import { budgets, summarize, assertAcceptance } from "./budgets.mjs";
import { loadLibraryDocument } from "./navigation.mjs";

const [configPath, outputPath] = process.argv.slice(2);
if (!configPath || !outputPath)
  throw new Error("Usage: pnpm perf:accept CONFIG.json OUTPUT_DIRECTORY");
const source = await readFile(configPath, "utf8");
const config = JSON.parse(source);
const diagnostic = process.env.PERF_DIAGNOSTIC === "1";
const baseline = config.baseline === true;
if (
  !/^[a-f0-9]{40}$/.test(config.identity?.commit) ||
  !config.identity?.image ||
  !config.identity?.corpus
)
  throw new Error("Record full commit, image digest and corpus identity before measuring");
if (!diagnostic && config.identity.dirty !== false)
  throw new Error("Acceptance requires a clean source commit");
const username = process.env.PERF_USERNAME,
  password = process.env.PERF_PASSWORD;
if (!username || !password)
  throw new Error("Real login required: set PERF_USERNAME and PERF_PASSWORD");
const output = resolve(outputPath);
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true });
const sessions = new Map();
const cohorts = [];
const measurement = {
  protocol: "browser-reload-immediate-mobile-tree-v3",
  authentication: "real-login-session-restored",
  node: process.version,
  files: Object.fromEntries(
    await Promise.all(
      ["accept.mjs", "observer.mjs", "budgets.mjs", "navigation.mjs"].map(async (name) => [
        name,
        createHash("sha256")
          .update(await readFile(new URL(name, import.meta.url)))
          .digest("hex"),
      ]),
    ),
  ),
};
const identity = {
  ...config.identity,
  configSha256: createHash("sha256").update(source).digest("hex"),
  browser: browser.version(),
  measurement,
  diagnostic,
  baseline,
};
async function prepared(scenario, device, locale, target = scenario.target) {
  const base = scenario.base;
  // Restore an authorized real-login session into otherwise empty contexts.
  // Each document still validates it through the real /auth/me endpoint.
  if (!sessions.has(base)) {
    const login = await fetch(`${base}/api/v1/auth/login`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    if (!login.ok) throw new Error(`Login failed: ${login.status}`);
    const { access_token } = await login.json();
    const response = await fetch(`${base}/api/v1/auth/me`, {
      headers: { Authorization: `Bearer ${access_token}` },
    });
    if (!response.ok) throw new Error(`Session validation failed: ${response.status}`);
    sessions.set(base, { token: access_token, user: await response.json() });
  }
  const session = sessions.get(base);
  const context = await browser.newContext({
    viewport: device === "mobile" ? { width: 390, height: 844 } : { width: 1440, height: 900 },
    isMobile: device === "mobile",
    hasTouch: device === "mobile",
    locale,
  });
  await context.addCookies([
    {
      name: "printstash_session",
      value: session.token,
      url: base,
      httpOnly: true,
      sameSite: "Strict",
    },
  ]);
  await context.addInitScript(
    `window.currentNavigationStart = ${currentNavigationStart.toString()}`,
  );
  await context.addInitScript(
    `window.openAvailableLibraryTree = ${openAvailableLibraryTree.toString()}`,
  );
  await context.addInitScript(installObserver, {
    user: session.user,
    locale,
    target,
  });
  return { context, page: await context.newPage(), base, device, locale };
}
async function openedTree(c) {
  if (c.device === "mobile")
    await c.page.waitForFunction(openAvailableLibraryTree, null, { polling: "raf" });
}

async function observed(c, kind) {
  try {
    await c.page.waitForFunction(
      (mobile) => {
        // Continue across a first-install document restart. Opening is an
        // immediate browser gesture, without the driver's actionability delay.
        if (mobile) window.openAvailableLibraryTree();
        return (
          window.libraryObservation.complete !== null || window.libraryObservation.errors.length
        );
      },
      c.device === "mobile",
      { timeout: 20000, polling: "raf" },
    );
    if (!baseline && kind !== "page")
      await c.page.waitForFunction(
        () => {
          const start = window.currentNavigationStart();
          return (
            start &&
            start.startTime >= window.libraryObservation.started &&
            performance.getEntriesByName(start.name.replace(/start$/, "complete")).length
          );
        },
        null,
        { timeout: 20000, polling: "raf" },
      );
    return c.page.evaluate(() => {
      const marks = performance.getEntriesByType("mark");
      const start = window.currentNavigationStart();
      const prefix = start?.name.replace(/start$/, "");
      const phases = Object.fromEntries(
        marks
          .filter((e) => prefix && e.name.startsWith(prefix))
          .map((e) => [
            e.name.slice(prefix.length),
            e.startTime - window.libraryObservation.started,
          ]),
      );
      return {
        ...window.libraryObservation,
        phases,
        worker: !!navigator.serviceWorker.controller,
        document: performance.getEntriesByType("navigation").map((entry) => entry.toJSON()),
        resources: performance
          .getEntriesByType("resource")
          .filter(
            (e) => e.startTime >= window.libraryObservation.started && !e.name.startsWith("blob:"),
          )
          .map((e) => ({
            path: new URL(e.name).pathname,
            start: e.startTime,
            end: e.responseEnd,
            ttfb: e.responseStart - e.requestStart,
            bytes: e.decodedBodySize,
            server: e.serverTiming.map((t) => ({ name: t.name, duration: t.duration })),
          })),
      };
    });
  } catch (cause) {
    const evidence = await c.page.evaluate(() => ({
      url: location.pathname + location.search,
      state: window.libraryObservation,
      marks: performance
        .getEntriesByType("mark")
        .map((e) => ({ name: e.name, start: e.startTime, detail: e.detail })),
    }));
    await writeFile(`${output}/failure-${kind}.json`, JSON.stringify(evidence, null, 2));
    await c.page.screenshot({ path: `${output}/failure-${kind}.png` });
    throw cause;
  }
}

async function reload(c, scenario, kind) {
  await loadLibraryDocument(c.page, c.base + scenario.target.url);
  return observed(c, kind);
}

async function begin(c, target) {
  await c.page.evaluate((target) => window.observeLibrary(target), target);
}
async function closeTree(c) {
  if (c.device === "mobile") {
    await c.page.keyboard.press("Escape");
    await c.page.getByRole("dialog", { name: /^(Filters|Filtros)$/ }).waitFor({ state: "hidden" });
  }
}
async function journey(c, scenario, kind) {
  const plan = scenario.journeys[kind];
  // An initial document visit prepares the source; collection transitions deliberately
  // start on published cards without waiting for the previous media downloads.
  await c.page.goto(c.base + plan.from.url, { waitUntil: "domcontentloaded" });
  await begin(c, plan.from);
  await c.page.waitForFunction(() => window.libraryObservation.content !== null, null, {
    polling: "raf",
  });
  if (kind !== "collection") await observed(c, "warm");
  if (kind === "collection") {
    await openedTree(c);
    const button = c.page
      .locator(c.device === "mobile" ? '[role="dialog"]' : "aside")
      .getByRole("button", { name: plan.button, exact: true });
    await button.scrollIntoViewIfNeeded();
    await button.evaluate(observeInteraction, { event: "click", target: plan.target });
    await button.click();
  } else if (kind === "back") {
    await closeTree(c);
    await c.page.locator(`main [data-library-entry="${plan.model}"]`).first().click();
    await c.page.waitForURL((url) => url.pathname === plan.model);
    await c.page.getByRole("heading", { name: plan.modelName, exact: true, level: 1 }).waitFor();
    await c.page.evaluate((target) => {
      window.observeLibrary(target);
      history.back();
    }, plan.target);
  } else if (kind === "page") {
    await closeTree(c);
    const button = c.page.getByRole("button", { name: /^(Load more|Cargar más)$/, exact: true });
    await button.scrollIntoViewIfNeeded();
    await button.evaluate(observeInteraction, { event: "click", target: plan.target });
    await button.click();
  } else if (kind === "search") {
    await closeTree(c);
    const input = c.page.locator("[data-model-search]");
    await input.focus();
    await input.evaluate(observeInteraction, { event: "input", target: plan.target });
    await input.fill(plan.target.query);
  }
  return observed(c, kind);
}
try {
  for (const scenario of config.scenarios.filter(
    (s) => !process.env.PERF_SCENARIO || s.name === process.env.PERF_SCENARIO,
  ))
    for (const device of ["desktop", "mobile"].filter(
      (d) => !process.env.PERF_DEVICE || d === process.env.PERF_DEVICE,
    ))
      for (const locale of ["en", "es"].filter(
        (l) => !process.env.PERF_LOCALE || l === process.env.PERF_LOCALE,
      )) {
        const rows = { warm: [], fresh: [] };
        const cohort = { scenario: scenario.name, device, locale, identity, rows };
        cohorts.push(cohort);
        const count = (kind) =>
          diagnostic ? Number(process.env.PERF_SAMPLES ?? 3) : budgets[kind].samples;
        const warm = await prepared(scenario, device, locale);
        try {
          await reload(warm, scenario, "warm");
          for (let i = 0; i < count("warm"); i++)
            rows.warm.push(await reload(warm, scenario, "warm"));
        } finally {
          await warm.context.close();
        }
        for (let i = 0; i < count("fresh"); i++) {
          const fresh = await prepared(scenario, device, locale);
          try {
            rows.fresh.push(await reload(fresh, scenario, "fresh"));
          } finally {
            await fresh.context.close();
          }
        }
        for (const kind of Object.keys(baseline ? {} : (scenario.journeys ?? {}))) {
          rows[kind] = [];
          for (let i = 0; i < count(kind); i++) {
            const c = await prepared(scenario, device, locale, scenario.journeys[kind].from);
            try {
              rows[kind].push(await journey(c, scenario, kind));
            } finally {
              await c.context.close();
            }
          }
        }
        const summary = Object.fromEntries(
          Object.entries(rows).map(([kind, samples]) => [
            kind,
            summarize(samples, kind, { diagnostic, baseline }),
          ]),
        );
        cohort.summary = summary;
        await writeFile(
          `${output}/${scenario.name}-${device}-${locale}.json`,
          JSON.stringify(cohort, null, 2),
        );
        console.log(JSON.stringify({ scenario: scenario.name, device, locale, summary }));
      }
  if (!diagnostic) assertAcceptance(cohorts, { baseline });
} finally {
  for (const cohort of cohorts)
    if (!cohort.summary)
      await writeFile(
        `${output}/${cohort.scenario}-${cohort.device}-${cohort.locale}-incomplete.json`,
        JSON.stringify(cohort, null, 2),
      );
  await writeFile(
    `${output}/index.json`,
    JSON.stringify({ identity, cohorts: cohorts.map(({ rows: _rows, ...rest }) => rest) }, null, 2),
  );
  await browser.close();
}
