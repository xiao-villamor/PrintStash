import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";

const now = "2026-06-04T00:24:22.000000";

const metadata = {
  slicer_name: "OrcaSlicer",
  slicer_version: "OrcaSlicer 2.3.1",
  printer_model: "Creality Ender-3 V3 SE",
  nozzle_diameter_mm: 0.4,
  layer_height_mm: 0.2,
  first_layer_height_mm: 0.24,
  infill_percent: 15,
  wall_loops: 3,
  top_shell_layers: 4,
  bottom_shell_layers: 3,
  support_material: false,
  nozzle_temperature_c: 215,
  bed_temperature_c: 60,
  estimated_time_s: 1812,
  filament_weight_g: 6.8,
  filament_length_mm: 2280,
  filament_cost: 0.14,
  material_type: "PLA",
  material_brand: "Generic PLA",
  bbox_x_mm: null,
  bbox_y_mm: null,
  bbox_z_mm: null,
  volume_mm3: null,
  triangle_count: null,
};

const model = {
  id: 1,
  name: "skadis_kitchen-roll_screw",
  slug: "skadis-kitchen-roll-screw",
  hash: "59b3ca0dd226918a7e65c4417a6c2ea2314f821b77bed988fa9eb7fec86d3f30",
  collection: "maraio",
  collection_id: 1,
  description: null,
  source_url: "https://www.printables.com/model/123-skadis-kitchen-roll-screw",
  effective_role: "admin",
  tags: ["tete"],
  thumbnail_url: "/api/v1/files/1/thumbnail",
  created_at: "2026-05-31T10:46:55.658492",
  updated_at: now,
  files: [
    {
      id: 1,
      model_id: 1,
      original_filename: "skadis_kitchen-roll_screw.stl",
      file_type: "stl",
      version: 1,
      gcode_revision_number: null,
      size_bytes: 1570684,
      sha256: "59b3ca0dd226918a7e65c4417a6c2ea2314f821b77bed988fa9eb7fec86d3f30",
      revision_label: null,
      revision_status: null,
      revision_notes: null,
      is_recommended: false,
      uploaded_at: "2026-05-31T10:46:55.705202",
      metadata: null,
    },
    {
      id: 2,
      model_id: 1,
      original_filename: "skadis_kitchen-roll_screw_PLA_30m12s.gcode",
      file_type: "gcode",
      version: 2,
      gcode_revision_number: 1,
      size_bytes: 3115403,
      sha256: "ae1f6b635c772c0267e9249cbff6fdcef505e336ac3bcf58a996d42b3547d1c4",
      revision_label: null,
      revision_status: "known_good",
      revision_notes: null,
      is_recommended: true,
      uploaded_at: "2026-05-31T10:46:56.705262",
      metadata,
    },
  ],
};

const printer = {
  id: 3,
  name: "ender",
  provider: "moonraker",
  moonraker_url: "http://moonraker.local:7125",
  has_api_key: true,
  bambu_host: null,
  bambu_serial: null,
  has_bambu_access_code: false,
  capabilities: {
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
  },
  notes: null,
  group: null,
  status: "ready",
  last_seen_at: now,
  last_error: null,
  created_at: "2026-05-31T18:51:39.627384",
  updated_at: now,
};

const filamentProfiles = [
  {
    id: 1,
    name: "Generic PLA",
    material_type: "PLA",
    material_brand: "Generic",
    cost_per_kg: 21,
    notes: null,
    created_at: now,
    updated_at: now,
  },
];

const printerProfiles = [
  {
    id: 1,
    name: "Creality Ender-3 V3 SE",
    printer_model: "Creality Ender-3 V3 SE",
    slicer_name: "OrcaSlicer",
    nozzle_diameter_mm: 0.4,
    notes: null,
    created_at: now,
    updated_at: now,
  },
];

const printerDiagnostics = {
  printer_id: printer.id,
  provider: printer.provider,
  support_level: "stable",
  capabilities: {
    can_start: true,
    can_pause: true,
    can_resume: true,
    can_cancel: true,
    can_live_status: true,
    can_upload: true,
    can_list_files: true,
    can_send_gcode: true,
    can_measure_consumption: true,
  },
  unsupported_actions: [],
  notes: [],
  checks: [
    { name: "configuration", ok: true },
    { name: "provider_info", ok: true },
    { name: "live_status", ok: true },
  ],
  ok: true,
};

const modelList = [
  {
    id: model.id,
    name: model.name,
    slug: model.slug,
    collection: model.collection,
    collection_id: model.collection_id,
    source_url: model.source_url,
    effective_role: model.effective_role,
    tags: model.tags,
    thumbnail_url: model.thumbnail_url,
    file_count: model.files.length,
    printer_presence: [{ printer_id: printer.id, printer_name: printer.name, file_count: 1 }],
    updated_at: model.updated_at,
    mesh_file_id: 1,
    print_summary: null,
    recommended_revision_status: "known_good",
    recommended_revision_label: null,
  },
];

// Mutable server state a test can flip before navigating (workers: 1, serial).
const state = { externalLibrariesEnabled: false };

export function setExternalLibrariesEnabled(value: boolean): void {
  state.externalLibrariesEnabled = value;
}

function vaultConfig() {
  return {
    storage_backend: "local",
    data_dir: "/data/files",
    thumb_dir: "/data/thumbs",
    s3_bucket: "",
    s3_endpoint_url: "",
    s3_region: "",
    s3_access_key: "",
    s3_secret_key: "",
    has_s3_access_key: false,
    has_s3_secret_key: false,
    backup_retention_days: 30,
    trash_retention_days: 30,
    backup_s3_bucket: "",
    backup_s3_endpoint_url: "",
    backup_s3_region: "",
    backup_s3_access_key: "",
    backup_s3_secret_key: "",
    has_backup_s3_access_key: false,
    has_backup_s3_secret_key: false,
    has_backup_s3: false,
    auto_mark_known_good: true,
    external_libraries_enabled: state.externalLibrariesEnabled,
  };
}

const externalLibrary = {
  id: 1,
  name: "nas-main",
  root_path: "/mnt/nas/models",
  enabled: true,
  scan_interval_minutes: 60,
  scan_schedule: "0 * * * *",
  watch_mode: "auto",
  fs_kind: "network",
  watch_active: false,
  collection_mode: "mirror",
  target_collection_id: null,
  last_scanned_at: now,
  last_scan_status: "ok",
  last_scan_summary: {
    added: 3,
    updated: 0,
    removed: 0,
    skipped: 1,
    errors: [],
    error: null,
    aborted: false,
  },
};

const printerFiles = [
  {
    id: 1,
    printer_id: printer.id,
    printer_name: printer.name,
    file_id: 2,
    model_id: model.id,
    model_name: model.name,
    original_filename: "skadis_kitchen-roll_screw_PLA_30m12s.gcode",
    remote_filename: "skadis_kitchen-roll_screw_PLA_30m12s.gcode",
    size_bytes: 3115403,
    sha256: "ae1f6b635c772c0267e9249cbff6fdcef505e336ac3bcf58a996d42b3547d1c4",
    matched_by: "sha256",
    modified_at: now,
    last_seen_at: now,
    missing_since: null,
    created_at: now,
    updated_at: now,
  },
];

const snapshot = {
  print_stats: {
    state: "complete",
    filename: "skadis_kitchen-roll_screw_PLA_30m12s.gcode",
    print_duration: 3097,
    total_duration: 3400,
  },
  virtual_sdcard: { progress: 1, file_position: 100, file_size: 100 },
  extruder: { temperature: 170.8, target: 0 },
  heater_bed: { temperature: 58.6, target: 0 },
  toolhead: { position: [0, 0, 0], homed_axes: "xyz" },
  webhooks: { state: "ready", state_message: "Printer is ready" },
};

function sendJson(res: ServerResponse, body: unknown, status = 200): void {
  res.writeHead(status, {
    "content-type": "application/json",
    "access-control-allow-origin": "*",
  });
  res.end(JSON.stringify(body));
}

function sendPng(res: ServerResponse): void {
  const pixel = Buffer.from(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAFgwJ/lx2h4wAAAABJRU5ErkJggg==",
    "base64",
  );
  res.writeHead(200, {
    "content-type": "image/png",
    "access-control-allow-origin": "*",
  });
  res.end(pixel);
}

function drainRequest(req: IncomingMessage, done: () => void): void {
  req.resume();
  req.on("end", done);
}

function handle(req: IncomingMessage, res: ServerResponse): void {
  const url = new URL(req.url ?? "/", "http://127.0.0.1");
  if (req.method === "OPTIONS") {
    res.writeHead(204, {
      "access-control-allow-origin": "*",
      "access-control-allow-methods": "GET,POST,PATCH,DELETE,OPTIONS",
      "access-control-allow-headers": "*",
    });
    res.end();
    return;
  }

  if (url.pathname === "/api/v1/setup/status") {
    sendJson(res, { configured: true, has_users: true });
    return;
  }
  if (url.pathname === "/api/v1/auth/me") {
    sendJson(res, {
      id: 1,
      username: "tester",
      email: null,
      is_superuser: true,
      created_at: now,
      updated_at: now,
    });
    return;
  }
  if (url.pathname === "/api/v1/admin/users") {
    sendJson(res, [
      {
        id: 1,
        username: "tester",
        email: null,
        is_superuser: true,
        is_active: true,
        created_at: now,
        updated_at: now,
      },
    ]);
    return;
  }
  if (url.pathname === "/api/v1/collections") {
    sendJson(res, [
      {
        id: 1,
        name: "maraio",
        slug: "maraio",
        path: "maraio",
        parent_id: null,
        model_count: 1,
        effective_role: "admin",
      },
    ]);
    return;
  }
  if (url.pathname === "/api/v1/collections/1/permissions") {
    sendJson(res, []);
    return;
  }
  if (url.pathname === "/api/v1/tags") {
    sendJson(res, []);
    return;
  }
  if (url.pathname === "/api/v1/models/stats") {
    sendJson(res, {
      model_count: modelList.length,
      file_count: 1,
      source_file_count: 1,
      gcode_file_count: 0,
      collection_count: 1,
      tag_count: 0,
      printer_count: 1,
      indexed_size_bytes: 3115403,
      storage: {
        backend: "local",
        prefix: null,
        bucket: null,
        object_count: 1,
        total_size_bytes: 3115403,
        ok: true,
        error: null,
      },
    });
    return;
  }
  if (url.pathname === "/api/v1/models") {
    sendJson(res, modelList);
    return;
  }
  if (url.pathname === "/api/v1/models/1") {
    sendJson(res, model);
    return;
  }
  if (url.pathname === "/api/v1/models/1/printer-files") {
    sendJson(res, [
      {
        file_id: 2,
        printer_id: printer.id,
        printer_name: printer.name,
        remote_filename: "skadis_kitchen-roll_screw_PLA_30m12s.gcode",
        matched_by: "sha256",
        last_seen_at: now,
        missing_since: null,
      },
    ]);
    return;
  }
  if (url.pathname === "/api/v1/models/1/print-jobs") {
    sendJson(res, [
      {
        id: 1,
        printer_id: printer.id,
        printer_name: printer.name,
        file_id: 2,
        gcode_revision_number: 1,
        revision_label: null,
        state: "completed",
        material_type: "PLA",
        error: null,
        started_at: "2026-06-04T00:00:00.000000",
        finished_at: now,
        created_at: "2026-06-04T00:00:00.000000",
      },
    ]);
    return;
  }
  if (url.pathname === "/api/v1/filament-profiles") {
    sendJson(res, filamentProfiles);
    return;
  }
  if (url.pathname === "/api/v1/printer-profiles") {
    sendJson(res, printerProfiles);
    return;
  }
  if (url.pathname === "/api/v1/printers") {
    sendJson(res, [printer]);
    return;
  }
  if (url.pathname === "/api/v1/printers/3") {
    if (req.method === "PATCH") {
      drainRequest(req, () => sendJson(res, { ...printer, name: "Workshop printer" }));
      return;
    }
    sendJson(res, printer);
    return;
  }
  if (url.pathname === "/api/v1/printers/3/diagnostics") {
    sendJson(res, printerDiagnostics);
    return;
  }
  if (url.pathname === "/api/v1/printers/3/status") {
    sendJson(res, { printer, snapshot });
    return;
  }
  if (url.pathname === "/api/v1/printers/3/files") {
    sendJson(res, printerFiles);
    return;
  }
  if (url.pathname === "/api/v1/printers/3/jobs") {
    sendJson(res, [
      {
        id: 1,
        printer_id: printer.id,
        file_id: 2,
        model_id: model.id,
        remote_filename: "skadis_kitchen-roll_screw_PLA_30m12s.gcode",
        state: "completed",
        progress: 100,
        source: "vault",
        error: null,
        started_at: "2026-06-04T00:00:00.000000",
        finished_at: now,
        created_at: "2026-06-04T00:00:00.000000",
        updated_at: now,
      },
    ]);
    return;
  }
  if (req.method === "POST" && url.pathname === "/api/v1/ingest/orca") {
    drainRequest(req, () => {
      sendJson(res, { job_id: "gcode-job-1", state: "pending", message: "ingestion queued" }, 202);
    });
    return;
  }
  if (url.pathname === "/api/v1/ingest/jobs/gcode-job-1") {
    sendJson(res, {
      job_id: "gcode-job-1",
      state: "completed",
      model_id: model.id,
      file_id: 2,
      error: null,
      started_at: now,
      finished_at: now,
    });
    return;
  }
  if (url.pathname === "/api/v1/config") {
    if (req.method === "PUT") {
      drainRequest(req, () => sendJson(res, vaultConfig()));
      return;
    }
    sendJson(res, vaultConfig());
    return;
  }
  if (url.pathname === "/api/v1/libraries") {
    sendJson(res, state.externalLibrariesEnabled ? [externalLibrary] : []);
    return;
  }
  if (req.method === "POST" && url.pathname === "/api/v1/libraries/1/scan") {
    drainRequest(req, () => {
      sendJson(res, { job_id: "scan-job-1", state: "pending", message: "library scan queued" }, 202);
    });
    return;
  }
  if (url.pathname === "/api/v1/ingest/jobs/scan-job-1") {
    sendJson(res, {
      job_id: "scan-job-1",
      state: "completed",
      model_id: null,
      file_id: null,
      error: null,
      started_at: now,
      finished_at: now,
    });
    return;
  }
  if (url.pathname === "/api/v1/spoolman") {
    if (req.method === "PUT") {
      drainRequest(req, () =>
        sendJson(res, {
          enabled: false,
          base_url: null,
          has_api_key: false,
          write_enabled: true,
          connected: false,
          version: null,
          error: null,
          native_hook_detected: false,
        }),
      );
      return;
    }
    sendJson(res, {
      enabled: false,
      base_url: null,
      has_api_key: false,
      write_enabled: true,
      connected: false,
      version: null,
      error: null,
      native_hook_detected: false,
    });
    return;
  }
  if (url.pathname === "/api/v1/spoolman/spools") {
    sendJson(res, []);
    return;
  }
  if (req.method === "POST" && url.pathname === "/api/v1/spoolman/sync-filaments") {
    drainRequest(req, () =>
      sendJson(res, { created: 0, updated: 0, adopted: 0, unlinked: 0 }),
    );
    return;
  }
  if (url.pathname === "/api/v1/files/1/thumbnail") {
    sendPng(res);
    return;
  }
  if (url.pathname === "/api/v1/files/1/stl") {
    res.writeHead(200, {
      "content-type": "application/sla",
      "access-control-allow-origin": "*",
    });
    res.end("solid empty\nendsolid empty\n");
    return;
  }

  sendJson(res, { detail: "not_found", path: url.pathname }, 404);
}

export async function startMockApi(port: number): Promise<Server> {
  const server = createServer(handle);
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, "127.0.0.1", () => resolve());
  });
  return server;
}
