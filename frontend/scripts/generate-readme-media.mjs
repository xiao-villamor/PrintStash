/* global document */

import { createHash } from "node:crypto";
import { spawn, spawnSync } from "node:child_process";
import { mkdir, readFile, rm } from "node:fs/promises";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "@playwright/test";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const frontendDir = resolve(scriptDir, "..");
const repoDir = resolve(frontendDir, "..");
const outputDir = join(repoDir, "screenshots");
const frameRoot = "/tmp/printstash-readme-frames";
const dataRoot = "/tmp/printstash-readme-data";
const apiPort = 8510;
const webPort = 3410;
const apiBase = `http://127.0.0.1:${apiPort}`;
const webBase = `http://127.0.0.1:${webPort}`;
const setupToken = "readme-media-setup-token";
const admin = { username: "demo", password: "printstash-demo" };

let token = "";

function start(command, args, options = {}) {
  return spawn(command, args, {
    stdio: ["ignore", "pipe", "pipe"],
    detached: true,
    ...options,
  });
}

function stop(child) {
  if (!child?.pid) return;
  try {
    process.kill(-child.pid, "SIGTERM");
  } catch {
    // Process already exited.
  }
}

async function waitFor(url, timeoutMs = 120_000) {
  const deadline = Date.now() + timeoutMs;
  let lastError;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.ok) return;
      lastError = new Error(`${response.status} ${await response.text()}`);
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolveWait) => setTimeout(resolveWait, 500));
  }
  throw new Error(`Timed out waiting for ${url}: ${lastError}`);
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers ?? {});
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (options.json !== undefined) headers.set("Content-Type", "application/json");
  const response = await fetch(`${apiBase}${path}`, {
    ...options,
    headers,
    body: options.json !== undefined ? JSON.stringify(options.json) : options.body,
  });
  if (!response.ok) {
    throw new Error(
      `${options.method ?? "GET"} ${path}: ${response.status} ${await response.text()}`,
    );
  }
  if (response.status === 204) return undefined;
  return response.json();
}

async function setup() {
  const status = await api("/api/v1/setup/status");
  if (!status.configured) {
    await api("/api/v1/setup", {
      method: "POST",
      json: { ...admin, setup_token: setupToken, storage_backend: "local" },
    });
  }
  const login = await api("/api/v1/auth/login", { method: "POST", json: admin });
  token = login.access_token;
  return api("/api/v1/auth/me");
}

async function pollJob(jobId) {
  const deadline = Date.now() + 180_000;
  while (Date.now() < deadline) {
    const job = await api(`/api/v1/ingest/jobs/${jobId}`);
    if (job.state === "completed") return job;
    if (job.state === "failed") throw new Error(`Ingest ${jobId} failed: ${job.error}`);
    await new Promise((resolveWait) => setTimeout(resolveWait, 400));
  }
  throw new Error(`Ingest ${jobId} timed out`);
}

async function upload(endpoint, filePath, fields) {
  const bytes = await readFile(filePath);
  const form = new FormData();
  form.set("file", new File([bytes], basename(filePath), { type: "application/octet-stream" }));
  for (const [key, value] of Object.entries(fields)) {
    if (value !== null && value !== undefined && value !== "") form.set(key, String(value));
  }
  const result = await api(endpoint, { method: "POST", body: form });
  return pollJob(result.job_id);
}

async function createModel({ name, mesh, gcode, collection, tags, description, sourceUrl }) {
  const meshPath = join(repoDir, "testdata", mesh);
  const meshBytes = await readFile(meshPath);
  const sourceHash = createHash("sha256").update(meshBytes).digest("hex");
  const meshJob = await upload("/api/v1/ingest/model", meshPath, {
    model_name: name,
    collection,
    tags: tags.join(","),
    source_hash: sourceHash,
  });
  if (gcode) {
    await upload("/api/v1/ingest/orca", join(repoDir, "testdata", gcode), {
      model_name: name,
      collection,
      tags: tags.join(","),
      source_hash: sourceHash,
    });
  }
  await api(`/api/v1/models/${meshJob.model_id}`, {
    method: "PATCH",
    json: { name, description, source_url: sourceUrl, collection, tags },
  });
  return meshJob.model_id;
}

async function addRevision(modelId, file, fields) {
  const bytes = await readFile(join(repoDir, "testdata", file));
  const form = new FormData();
  form.set("file", new File([bytes], basename(file), { type: "text/plain" }));
  for (const [key, value] of Object.entries(fields)) form.set(key, String(value));
  return api(`/api/v1/models/${modelId}/gcode-revisions`, { method: "POST", body: form });
}

async function seedData() {
  const collections = {};
  for (const name of ["Calibration Lab", "Workshop Tools", "Display Pieces", "Miniatures"]) {
    collections[name] = await api("/api/v1/collections", { method: "POST", json: { name } });
  }

  const models = {};
  const specs = [
    {
      key: "benchy",
      name: "3D Benchy",
      mesh: "benchy/3dbenchy.stl",
      gcode: "benchy/3dbenchy_PLA_1h12m.gcode",
      collection: collections["Calibration Lab"].path,
      tags: ["calibration", "PLA", "featured"],
      description:
        "Reliable calibration print used to validate cooling, bridging, and dimensional accuracy.",
      sourceUrl: "https://www.3dbenchy.com/",
    },
    {
      key: "cube",
      name: "Calibration Cube",
      mesh: "Calibration Cube.stl",
      gcode: "Calibration Cube_PLA_19m6s.gcode",
      collection: collections["Calibration Lab"].path,
      tags: ["calibration", "quick-print", "featured"],
      description: "20 mm dimensional calibration cube for fast printer checks.",
      sourceUrl: null,
    },
    {
      key: "spatula",
      name: "Workshop Spatula",
      mesh: "Spatula_Printables_IS.3mf",
      gcode: "Spatula_Printables_0.4n_0.15mm_PLA_MK4IS_MK3.9IS_27m.gcode",
      collection: collections["Workshop Tools"].path,
      tags: ["tool", "PLA", "featured"],
      description: "Compact bed scraper with a comfortable printed handle.",
      sourceUrl: "https://www.printables.com/",
    },
    {
      key: "star",
      name: "Modular Star Display",
      mesh: "Star_body [strait version].stl",
      gcode: "Star_body [strait version]_PLA_50m22s.gcode",
      collection: collections["Display Pieces"].path,
      tags: ["display", "modular", "featured"],
      description: "Modular display piece with separate body, stand, and detail parts.",
      sourceUrl: null,
    },
    {
      key: "dragon",
      name: "Articulated Crystal Dragon",
      mesh: "dragon_v2.3mf",
      gcode: null,
      collection: collections["Miniatures"].path,
      tags: ["articulated", "favorite", "featured"],
      description: "Print-in-place articulated dragon prepared as a reusable 3MF project.",
      sourceUrl: null,
    },
    {
      key: "mando",
      name: "Mandalorian Miniature",
      mesh: "bl-dnd-sitting-mando001.3mf",
      gcode: null,
      collection: collections["Miniatures"].path,
      tags: ["miniature", "display", "featured"],
      description: "Detailed tabletop miniature stored with its original project settings.",
      sourceUrl: null,
    },
    {
      key: "mario",
      name: "Mario Coin",
      mesh: "Mario_Coin.stl",
      gcode: "Mario_Coin_PLA_22m54s.gcode",
      collection: collections["Display Pieces"].path,
      tags: ["display", "PLA", "featured"],
      description: "Display coin with two tuned G-code revisions and recorded print outcome.",
      sourceUrl: null,
    },
  ];

  for (const spec of specs) {
    process.stdout.write(`Seeding ${spec.name}...\n`);
    models[spec.key] = await createModel(spec);
  }

  let mario = await api(`/api/v1/models/${models.mario}`);
  const firstRevision = mario.files.find((file) => file.file_type === "gcode");
  await api(`/api/v1/models/${models.mario}/files/${firstRevision.id}/revision`, {
    method: "PATCH",
    json: {
      revision_label: "Fast draft",
      revision_status: "needs_test",
      revision_notes: "Fast profile for fit checks; slight ringing on the outer wall.",
      is_recommended: false,
    },
  });
  mario = await addRevision(models.mario, "Mario_Coin_PLA_28m17s.gcode", {
    revision_label: "Final quality",
    revision_status: "known_good",
    revision_notes: "Clean top surface and sharper edge detail. Best result on the textured plate.",
    is_recommended: true,
  });

  for (const modelId of [models.mario, models.dragon, models.benchy]) {
    await api(`/api/v1/models/${modelId}/star`, { method: "PUT", json: {} });
  }
  await api("/api/v1/saved-views", {
    method: "POST",
    json: {
      name: "Ready to print",
      filters: {
        collection: null,
        direct: false,
        tag: ["PLA"],
        q: null,
        printer_id: null,
        printer_presence: null,
        favorites: false,
      },
    },
  });
  await api("/api/v1/saved-views", {
    method: "POST",
    json: {
      name: "Favorites",
      filters: {
        collection: null,
        direct: false,
        tag: [],
        q: null,
        printer_id: null,
        printer_presence: null,
        favorites: true,
      },
    },
  });

  return { models, collections, mario };
}

const now = "2026-07-14T10:30:00.000000";
const capabilities = {
  can_start: true,
  can_pause: true,
  can_resume: true,
  can_cancel: true,
  can_live_status: true,
  can_upload: true,
  can_list_files: true,
  can_send_gcode: true,
  can_measure_consumption: true,
  support_level: "stable",
  support_notes: [],
  unsupported_actions: [],
};
const printer = {
  id: 91,
  name: "Workshop Voron",
  provider: "moonraker",
  moonraker_url: "http://voron.local:7125",
  has_api_key: false,
  provider_variant: "generic",
  model_name: "Voron 2.4",
  detected_model: "Voron 2.4",
  capabilities,
  notes: "Primary workshop printer",
  group: "Workshop",
  status: "printing",
  last_seen_at: now,
  last_error: null,
  created_at: now,
  updated_at: now,
};

const snapshot = {
  print_stats: {
    state: "printing",
    filename: "Mario_Coin_PLA_28m17s.gcode",
    print_duration: 1020,
    total_duration: 1697,
    message: "Printing",
  },
  virtual_sdcard: { progress: 0.64, file_position: 640, file_size: 1000 },
  extruder: { temperature: 214.7, target: 215 },
  heater_bed: { temperature: 59.8, target: 60 },
  toolhead: { position: [114.2, 108.7, 12.4], homed_axes: "xyz" },
  webhooks: { state: "ready", state_message: "Printer is ready" },
};

const stats = {
  period: "90d",
  start_at: "2026-04-16T00:00:00Z",
  end_at: now,
  total_prints: 84,
  total_cost: 67.42,
  total_filament_g: 3280,
  avg_filament_g: 39.05,
  total_print_time_s: 462960,
  top_collections: [
    {
      collection_id: 1,
      name: "Calibration Lab",
      path: "calibration-lab",
      print_count: 28,
      total_cost: 11.8,
    },
    {
      collection_id: 2,
      name: "Workshop Tools",
      path: "workshop-tools",
      print_count: 21,
      total_cost: 18.2,
    },
    {
      collection_id: 3,
      name: "Display Pieces",
      path: "display-pieces",
      print_count: 19,
      total_cost: 23.1,
    },
    {
      collection_id: 4,
      name: "Miniatures",
      path: "miniatures",
      print_count: 16,
      total_cost: 14.32,
    },
  ],
  top_filaments: [
    {
      material_type: "PLA",
      material_brand: "Polymaker",
      print_count: 46,
      total_g: 1690,
      total_cost: 32.4,
    },
    {
      material_type: "PETG",
      material_brand: "Prusament",
      print_count: 22,
      total_g: 960,
      total_cost: 22.1,
    },
    {
      material_type: "ABS",
      material_brand: "eSUN",
      print_count: 16,
      total_g: 630,
      total_cost: 12.92,
    },
  ],
  top_models: [
    { model_id: 1, name: "3D Benchy", print_count: 15, total_g: 180 },
    { model_id: 7, name: "Mario Coin", print_count: 12, total_g: 240 },
    { model_id: 2, name: "Calibration Cube", print_count: 11, total_g: 88 },
    { model_id: 3, name: "Workshop Spatula", print_count: 8, total_g: 312 },
  ],
  top_printers: [
    { printer_id: 91, name: "Workshop Voron", print_count: 49, print_time_s: 288000 },
    { printer_id: 92, name: "Prusa MK4S", print_count: 23, print_time_s: 118800 },
    { printer_id: 93, name: "Bambu P1S", print_count: 12, print_time_s: 56160 },
  ],
  cost_over_time: [
    ["2026-04-20", 3.4, 180, 5],
    ["2026-04-27", 5.8, 270, 7],
    ["2026-05-04", 4.9, 240, 6],
    ["2026-05-11", 7.6, 360, 9],
    ["2026-05-18", 5.3, 260, 7],
    ["2026-05-25", 8.8, 420, 11],
    ["2026-06-01", 6.7, 330, 8],
    ["2026-06-08", 9.4, 455, 12],
    ["2026-06-15", 4.5, 225, 6],
    ["2026-06-22", 5.9, 290, 7],
    ["2026-06-29", 3.8, 190, 4],
    ["2026-07-06", 5.32, 240, 2],
  ].map(([bucket, cost, filament_g, print_count]) => ({ bucket, cost, filament_g, print_count })),
};

async function installExternalMocks(page, modelId, recommendedFileId) {
  await page.routeWebSocket(/\/api\/v1\/printers\/91\/ws/, (ws) => {
    setTimeout(() => ws.send(JSON.stringify({ type: "snapshot", data: snapshot })), 80);
  });
  await page.route("**/api/v1/models?**", async (route) => {
    const url = new URL(route.request().url());
    if (url.searchParams.getAll("tag").includes("featured")) {
      url.searchParams.delete("direct");
      return route.continue({ url: url.toString() });
    }
    return route.continue();
  });
  await page.route("**/api/v1/printers**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const json = (body, status = 200) =>
      route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (path === "/api/v1/printers/dashboard")
      return json({
        total_printers: 1,
        status_counts: { printing: 1 },
        active_jobs: 1,
        groups: [{ name: "Workshop", count: 1, status_counts: { printing: 1 } }],
      });
    if (path === "/api/v1/printers") return json([printer]);
    if (path === "/api/v1/printers/91/status") return json({ printer, snapshot });
    if (path === "/api/v1/printers/91/diagnostics")
      return json({
        printer_id: 91,
        provider: "moonraker",
        support_level: "stable",
        capabilities,
        unsupported_actions: [],
        notes: [],
        checks: [
          { name: "configuration", ok: true },
          { name: "provider_info", ok: true },
          { name: "live_status", ok: true },
        ],
        ok: true,
      });
    if (path === "/api/v1/printers/91/files")
      return json([
        {
          id: 901,
          printer_id: 91,
          printer_name: printer.name,
          file_id: recommendedFileId,
          model_id: modelId,
          model_name: "Mario Coin",
          original_filename: "Mario_Coin_PLA_28m17s.gcode",
          remote_filename: "Mario_Coin_PLA_28m17s.gcode",
          size_bytes: 2228224,
          sha256: "demo",
          matched_by: "sha256",
          modified_at: now,
          last_seen_at: now,
          missing_since: null,
          created_at: now,
          updated_at: now,
        },
      ]);
    if (path === "/api/v1/printers/91/jobs")
      return json([
        {
          id: 501,
          printer_id: 91,
          file_id: recommendedFileId,
          model_id: modelId,
          remote_filename: "Mario_Coin_PLA_28m17s.gcode",
          state: "printing",
          progress: 64,
          source: "vault",
          error: null,
          spool_id: null,
          spool_name: "Polymaker PLA — Orange",
          started_at: "2026-07-14T10:12:00Z",
          finished_at: null,
          created_at: "2026-07-14T10:12:00Z",
          updated_at: now,
        },
      ]);
    if (path === "/api/v1/printers/91/ws-ticket")
      return json({ ticket: "readme-demo", expires_in: 30 });
    if (path === "/api/v1/printers/91/send") return json({ job_id: 501, state: "uploading" }, 202);
    if (path === "/api/v1/printers/91") return json(printer);
    return route.continue();
  });
  await page.route("**/api/v1/models/stats/prints**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(stats) }),
  );
  await page.route(`**/api/v1/models/${modelId}/printer-files`, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([]) }),
  );
}

async function settle(page) {
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.evaluate(() => document.fonts.ready);
  await page
    .addStyleTag({ content: ".tsqd-parent-container { display: none !important; }" })
    .catch(() => {});
  await page.waitForTimeout(700);
}

async function shot(page, filename) {
  await settle(page);
  await page.screenshot({ path: join(outputDir, filename), animations: "disabled" });
}

async function record(
  page,
  name,
  action,
  { interval = 170, frameDuration = Math.round(interval * 4.5), hold = 500 } = {},
) {
  const frameDir = join(frameRoot, name);
  await rm(frameDir, { recursive: true, force: true });
  await mkdir(frameDir, { recursive: true });
  await page
    .addStyleTag({ content: ".tsqd-parent-container { display: none !important; }" })
    .catch(() => {});
  let active = true;
  let frame = 0;
  const loop = (async () => {
    while (active) {
      await page.screenshot({ path: join(frameDir, `${String(frame++).padStart(4, "0")}.png`) });
      await page.waitForTimeout(interval);
    }
  })();
  try {
    await action();
    await page.waitForTimeout(hold);
  } finally {
    active = false;
    await loop.catch(() => {});
  }
  const python = resolve(repoDir, "backend/.venv/bin/python");
  const result = spawnSync(
    python,
    [
      join(scriptDir, "build-readme-gif.py"),
      frameDir,
      join(outputDir, `${name}.gif`),
      "--duration",
      String(frameDuration),
    ],
    { stdio: "inherit" },
  );
  if (result.status !== 0) throw new Error(`GIF build failed for ${name}`);
}

async function openModel(page, modelId) {
  await page.goto(`${webBase}/models/${modelId}`);
  await page.getByRole("heading", { name: "Mario Coin" }).waitFor();
  await settle(page);
}

async function captureMedia(page, seed) {
  const modelId = seed.models.mario;
  const currentModel = await api(`/api/v1/models/${modelId}`);
  const recommended = currentModel.files.find(
    (file) => file.file_type === "gcode" && file.is_recommended,
  );
  await installExternalMocks(page, modelId, recommended.id);

  await page.goto(`${webBase}/?tag=featured`);
  await page.getByRole("heading", { name: "All Models" }).waitFor();
  await page.addStyleTag({ content: "main [data-collection-path] { display: none !important; }" });
  await page.locator('a[href^="/models/"]').filter({ hasText: "Mario Coin" }).waitFor();
  await shot(page, "01-vault-overview.png");

  await openModel(page, modelId);
  await shot(page, "02-model-detail.png");

  await page.getByTitle("G-code toolpath preview").click();
  await page
    .getByText(/G-code toolpath/i)
    .first()
    .waitFor();
  await page.waitForTimeout(1800);
  await shot(page, "03-gcode-viewer.png");

  await openModel(page, modelId);
  await page.getByRole("separator", { name: "Resize details panel" }).press("End");
  await page.getByRole("tab", { name: /Revisions/ }).click();
  await page.getByRole("heading", { name: "Compare Artifacts" }).scrollIntoViewIfNeeded();
  await shot(page, "04-artifact-compare.png");

  await page.goto(`${webBase}/printers/91`);
  await page.getByRole("heading", { name: "Workshop Voron" }).waitFor();
  await shot(page, "05-printer-live.png");

  await page.goto(`${webBase}/statistics`);
  await page.getByRole("heading", { name: "Statistics" }).waitFor();
  await page.getByRole("button", { name: "90 days" }).click();
  await shot(page, "06-statistics.png");

  await page.goto(`${webBase}/?tag=featured`);
  await page.getByRole("heading", { name: "All Models" }).waitFor();
  await page.addStyleTag({ content: "main [data-collection-path] { display: none !important; }" });
  await page.locator('a[href^="/models/"]').filter({ hasText: "Mario Coin" }).waitFor();
  await settle(page);
  await record(
    page,
    "00-demo-v010",
    async () => {
      const search = page.getByPlaceholder(/Search PrintStash/i);
      await search.fill("Mario");
      await page.waitForTimeout(700);
      await page.locator('a[href^="/models/"]').filter({ hasText: "Mario Coin" }).click();
      await page.getByRole("heading", { name: "Mario Coin" }).waitFor();
      await page.waitForTimeout(700);
      await page.getByTitle("G-code toolpath preview").click();
      await page.waitForTimeout(1500);
      await page.getByRole("tab", { name: "Overview" }).click();
      await page.getByRole("button", { name: "Send to printer" }).first().click();
      await page.getByRole("dialog", { name: "Send to printer" }).waitFor();
      await page.getByLabel("Select Workshop Voron").check();
      await page.getByLabel("Start print immediately").check();
      await page.waitForTimeout(900);
    },
    { interval: 180, hold: 800 },
  );

  await page.goto(`${webBase}/?tag=featured`);
  await page.getByRole("heading", { name: "All Models" }).waitFor();
  await page.addStyleTag({ content: "main [data-collection-path] { display: none !important; }" });
  await page.locator('a[href^="/models/"]').filter({ hasText: "Mario Coin" }).waitFor();
  await record(
    page,
    "07-organize-library",
    async () => {
      await page.getByRole("button", { name: "Select", exact: true }).click();
      await page.getByRole("checkbox", { name: "Select Mario Coin" }).click();
      await page.getByRole("checkbox", { name: "Select Mandalorian Miniature" }).click();
      await page.getByRole("checkbox", { name: "Select Calibration Cube" }).click();
      await page.waitForTimeout(600);
      await page.locator("div.fixed.bottom-4").getByRole("button", { name: "Tag" }).click();
      const dialog = page.getByRole("dialog", { name: /Tag .* models/ });
      await dialog.getByPlaceholder("Search or create — press Enter").first().fill("README-ready");
      await dialog.getByPlaceholder("Search or create — press Enter").first().press("Enter");
      await page.waitForTimeout(500);
      await dialog.getByRole("button", { name: "Apply" }).click();
      await page.waitForTimeout(900);
    },
    { interval: 170, hold: 650 },
  );

  await openModel(page, modelId);
  await record(
    page,
    "08-revision-compare",
    async () => {
      await page.getByRole("tab", { name: /Revisions/ }).click();
      await page.waitForTimeout(800);
      const panel = page.getByRole("heading", { name: "Compare Artifacts" });
      await panel.scrollIntoViewIfNeeded();
      await page.waitForTimeout(1100);
    },
    { interval: 170, hold: 900 },
  );
}

async function main() {
  await mkdir(outputDir, { recursive: true });
  await rm(frameRoot, { recursive: true, force: true });
  const backend = start("bash", ["tests/e2e-real/scripts/start-backend.sh"], {
    cwd: frontendDir,
    env: {
      ...process.env,
      PLAYWRIGHT_REAL_API_PORT: String(apiPort),
      PLAYWRIGHT_REAL_DATA_DIR: dataRoot,
      VAULT_SETUP_TOKEN: setupToken,
    },
  });
  const vite = start(
    "pnpm",
    ["exec", "vite", "--port", String(webPort), "--strictPort", "--host", "127.0.0.1"],
    {
      cwd: frontendDir,
      env: { ...process.env, VITE_API_URL: apiBase },
    },
  );
  backend.stderr.on("data", (chunk) => process.stderr.write(`[backend] ${chunk}`));
  vite.stderr.on("data", (chunk) => process.stderr.write(`[vite] ${chunk}`));

  let browser;
  try {
    await waitFor(`${apiBase}/api/v1/health`);
    await waitFor(webBase);
    const user = await setup();
    const seed = await seedData();
    browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({
      viewport: { width: 1440, height: 900 },
      colorScheme: "dark",
      deviceScaleFactor: 1,
    });
    await context.addCookies([
      {
        name: "printstash_session",
        value: token,
        url: apiBase,
        httpOnly: true,
        sameSite: "Strict",
      },
    ]);
    await context.addInitScript(
      ({ storedUser }) => {
        localStorage.setItem("printstash.user", storedUser);
        localStorage.setItem("printstash.theme", "dark");
        const style = document.createElement("style");
        style.textContent = ".tsqd-parent-container { display: none !important; }";
        document.documentElement.appendChild(style);
      },
      { storedUser: JSON.stringify(user) },
    );
    const page = await context.newPage();
    await captureMedia(page, seed);
    await context.close();
  } finally {
    if (browser) await browser.close();
    stop(vite);
    stop(backend);
    await rm(frameRoot, { recursive: true, force: true });
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
