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
  access: {
    role: "admin",
    can_view: true,
    can_print: true,
    can_control: true,
    can_admin: true,
  },
  notes: null,
  group: null,
  is_default: false,
  drain_mode: false,
  drain_reason: null,
  drain_updated_at: null,
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
let inboxCollectionId: number | null = null;
const state = {
  externalLibrariesEnabled: false,
  ingestJobQueued: false,
  thumbnailRebuildQueued: false,
  apiKeySequence: 0,
  inboxCaptured: false,
  inboxImported: false,
  sourceOverride: false,
  sourceCover: true,
  browserDeviceRevoked: false,
  s3LegacyCandidate: true,
  s3LegacyAdopted: false,
  // SAFETY: The mutable mock state is limited to the API's GC lifecycle values.
  gcPlanState: null as null | "preview" | "quarantined" | "aborted" | "completed",
};

export function resetMockApiState(): void {
  state.externalLibrariesEnabled = false;
  state.ingestJobQueued = false;
  state.thumbnailRebuildQueued = false;
  state.apiKeySequence = 0;
  state.inboxCaptured = false;
  state.inboxImported = false;
  inboxCollectionId = null;
  state.sourceOverride = false;
  state.sourceCover = true;
  state.browserDeviceRevoked = false;
  state.s3LegacyCandidate = true;
  state.s3LegacyAdopted = false;
  state.gcPlanState = null;
}

function gcPlan() {
  const planState = state.gcPlanState ?? "preview";
  return {
    id: 7,
    state: planState,
    digest: "a".repeat(64),
    resource_count: 1,
    candidate_pool_count: 1,
    key_count: 4,
    size_bytes: 1572864,
    quarantine_until: planState === "quarantined" ? "2099-06-11T00:24:22Z" : null,
    backup_id: planState === "quarantined" ? "backup-verified" : null,
    last_error: null,
    items: [],
  };
}

function inboxItem() {
  return {
    id: 41,
    owner_user_id: 1,
    source_kind: "url",
    source_url: "https://www.printables.com/model/41-capture-bracket",
    display_title: "Capture bracket",
    source_hostname: "www.printables.com",
    state: "review",
    manifest: {
      schema_version: 2,
      kind: "model_files",
      source: {
        provider: "printables",
        canonical_url: "https://www.printables.com/model/41-capture-bracket",
        source_item_id: "41",
        source_revision: "v2",
        adapter_version: "fixture-1",
        tags: [],
        fields: {},
      },
      files: [
        { id: "bracket-stl", name: "capture-bracket.stl", file_type: "stl", size: 1024 },
        { id: "bracket-3mf", name: "capture-bracket.3mf", file_type: "3mf", size: 2048 },
      ],
      selected_ids: ["bracket-stl", "bracket-3mf"],
    },
    target_collection_id: inboxCollectionId,
    requested_tags: ["fixture"],
    background_job_id: null,
    resulting_model_id: null,
    results: [],
    error_code: null,
    retryable: true,
    attempt_count: 0,
    created_at: now,
    updated_at: now,
    completed_at: null,
    completion: null,
  };
}

function importedInboxItem() {
  return {
    ...inboxItem(),
    state: "completed",
    resulting_model_id: 1,
    completion: "partial",
    completed_at: now,
    results: [
      {
        id: 1,
        source_selection_id: "bracket-stl",
        result_key: "one",
        original_filename: "capture-bracket.stl",
        state: "imported",
        model_id: 1,
        file_id: 1,
        provenance_source_id: 8,
        error_code: null,
        retryable: false,
        created_at: now,
        updated_at: now,
      },
      {
        id: 2,
        source_selection_id: "bracket-3mf",
        result_key: "two",
        original_filename: "capture-bracket.3mf",
        state: "failed",
        model_id: null,
        file_id: null,
        provenance_source_id: null,
        error_code: "download_failed",
        retryable: true,
        created_at: now,
        updated_at: now,
      },
    ],
  };
}

function provenance() {
  return {
    schema_version: 2,
    sources: [
      {
        id: 8,
        provider: "printables",
        source_item_id: "41",
        canonical_url: "https://www.printables.com/model/41-capture-bracket",
        source_revision: "v2",
        first_captured_at: now,
        last_checked_at: now,
        captures: [],
        fields: [
          {
            field_name: "description",
            captured_value:
              "New balloon-powered speedboat with an inflation adapter and twin nozzles for straight, long-lasting fun.",
            captured_origin: "confirmed",
            user_value: null,
            user_override_set: false,
            effective_value:
              "New balloon-powered speedboat with an inflation adapter and twin nozzles for straight, long-lasting fun.",
            effective_origin: "confirmed",
            captured_at: now,
            user_updated_at: null,
          },
          {
            field_name: "creator_name",
            captured_value: "Fixture maker",
            captured_origin: "confirmed",
            user_value: state.sourceOverride ? "Corrected maker" : null,
            user_override_set: state.sourceOverride,
            effective_value: state.sourceOverride ? "Corrected maker" : "Fixture maker",
            effective_origin: state.sourceOverride ? "user" : "confirmed",
            captured_at: now,
            user_updated_at: state.sourceOverride ? now : null,
          },
          {
            field_name: "license_text",
            captured_value: "CC BY 4.0",
            captured_origin: "confirmed",
            user_value: null,
            user_override_set: false,
            effective_value: "CC BY 4.0",
            effective_origin: "confirmed",
            captured_at: now,
            user_updated_at: null,
          },
          {
            field_name: "instructions",
            captured_value: "Print with supports.",
            captured_origin: "confirmed",
            user_value: null,
            user_override_set: false,
            effective_value: "Print with supports.",
            effective_origin: "confirmed",
            captured_at: now,
            user_updated_at: null,
          },
        ],
      },
    ],
  };
}

export function setExternalLibrariesEnabled(value: boolean): void {
  state.externalLibrariesEnabled = value;
}

function vaultConfig() {
  return {
    storage_backend: "local",
    storage_provider: "local",
    storage_provider_config: {
      provider: "local",
      data_dir: "/data/files",
      thumb_dir: "/data/thumbs",
      root: "vault-data",
    },
    storage_tier: "unguarded",
    storage_warnings: [],
    storage_unverified_acknowledged: false,
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
    model_thumbnail_width: 640,
  };
}

function storageProviders() {
  return [
    {
      id: "local",
      label: "This machine",
      category: "this_machine",
      description: "Local filesystem directories.",
      expected_tier: "verified",
      expected_tier_note: "Verified on local filesystems with working hardlinks.",
      consequences: [],
      documentation_url: "/docs/storage-providers.md#local",
      available: true,
      selectable: true,
      support_level: "stable",
      fields: [
        {
          name: "data_dir",
          label: "Models directory",
          help: "Directory for model files",
          input_type: "path",
          required: true,
          secret: false,
        },
        {
          name: "thumb_dir",
          label: "Thumbnail directory",
          help: "Directory for generated images",
          input_type: "path",
          required: true,
          secret: false,
        },
        {
          name: "root",
          label: "Root",
          help: "Ownership namespace",
          input_type: "text",
          required: true,
          secret: false,
          default: "vault-data",
        },
      ],
    },
    {
      id: "sftp",
      label: "SFTP",
      category: "nas_sftp",
      description: "NAS storage over SSH File Transfer Protocol.",
      expected_tier: "guarded",
      expected_tier_note: "SFTP cannot prove conditional ownership.",
      consequences: [
        "Manual permanent deletion requires one-shot confirmation; scheduled storage purge is skipped.",
      ],
      documentation_url: "/docs/storage-providers.md#sftp",
      available: false,
      selectable: false,
      support_level: "beta",
      disabled_reason: "Requires the full image",
      fields: [
        {
          name: "host",
          label: "Host",
          help: "SFTP hostname",
          input_type: "text",
          required: true,
          secret: false,
        },
        {
          name: "host_key",
          label: "Host key",
          help: "OpenSSH known-host entry",
          input_type: "text",
          required: true,
          secret: false,
        },
      ],
    },
  ];
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
  binding_state: "bound",
  binding_reason: null,
  root_enrollable: false,
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

function sendJson<T>(res: ServerResponse, body: T, status = 200): void {
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
      "access-control-allow-methods": "GET,POST,PUT,PATCH,DELETE,OPTIONS",
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
  if (url.pathname === "/api/v1/auth/api-keys") {
    if (req.method === "POST") {
      drainRequest(req, () => {
        state.apiKeySequence += 1;
        sendJson(res, {
          id: state.apiKeySequence,
          name: "Browser extension",
          prefix: "psk_browser",
          created_at: now,
          last_used_at: null,
          api_key: "psk_browser_setup_secret",
        });
      });
      return;
    }
    sendJson(res, []);
    return;
  }
  if (url.pathname === "/api/v1/provider-connections") {
    sendJson(res, [
      { provider: "myminifactory", connected: false, updated_at: null },
      { provider: "cults", connected: false, updated_at: null },
    ]);
    return;
  }
  if (url.pathname === "/api/v1/browser-pairings") {
    if (req.method === "POST") {
      drainRequest(req, () =>
        sendJson(res, { code: "PAIR-1234", expires_at: "2026-06-04T00:34:22.000000" }, 201),
      );
      return;
    }
    sendJson(res, [
      {
        id: 9,
        name: "Fixture Firefox",
        created_at: now,
        last_used_at: null,
        revoked_at: state.browserDeviceRevoked ? now : null,
      },
    ]);
    return;
  }
  if (url.pathname === "/api/v1/browser-pairings/9") {
    if (req.method === "DELETE") {
      state.browserDeviceRevoked = true;
      res.writeHead(204, { "access-control-allow-origin": "*" });
      res.end();
      return;
    }
    if (req.method === "PATCH") {
      drainRequest(req, () =>
        sendJson(res, {
          id: 9,
          name: "Renamed fixture browser",
          created_at: now,
          last_used_at: null,
          revoked_at: null,
        }),
      );
      return;
    }
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
  if (url.pathname === "/api/v1/admin/gc") {
    if (req.method === "POST") {
      drainRequest(req, () => {
        state.gcPlanState = "preview";
        sendJson(res, gcPlan());
      });
      return;
    }
    sendJson(res, state.gcPlanState === null ? null : gcPlan());
    return;
  }
  if (url.pathname === "/api/v1/admin/gc/7/approve" && req.method === "POST") {
    drainRequest(req, () => {
      state.gcPlanState = "quarantined";
      sendJson(res, gcPlan());
    });
    return;
  }
  if (url.pathname === "/api/v1/admin/gc/7/abort" && req.method === "POST") {
    drainRequest(req, () => {
      state.gcPlanState = "aborted";
      sendJson(res, gcPlan());
    });
    return;
  }
  if (url.pathname === "/api/v1/admin/gc/7/finalize" && req.method === "POST") {
    drainRequest(req, () => {
      state.gcPlanState = "completed";
      sendJson(res, gcPlan());
    });
    return;
  }
  if (url.pathname === "/api/v1/collections") {
    if (req.method === "POST") {
      drainRequest(req, () => {
        inboxCollectionId = 42;
        sendJson(
          res,
          {
            id: 42,
            name: "Capture bracket",
            slug: "capture-bracket",
            path: "capture-bracket",
            parent_id: null,
            model_count: 0,
            effective_role: "admin",
          },
          201,
        );
      });
      return;
    }
    const collections = [
      {
        id: 1,
        name: "maraio",
        slug: "maraio",
        path: "maraio",
        parent_id: null,
        model_count: 1,
        effective_role: "admin",
      },
    ];
    if (inboxCollectionId !== null) {
      collections.push({
        id: 42,
        name: "Capture bracket",
        slug: "capture-bracket",
        path: "capture-bracket",
        parent_id: null,
        model_count: 0,
        effective_role: "admin",
      });
    }
    sendJson(res, collections);
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
  if (url.pathname === "/api/v1/saved-views") {
    sendJson(res, []);
    return;
  }
  if (url.pathname === "/api/v1/inbox") {
    if (req.method === "POST") {
      drainRequest(req, () => {
        state.inboxCaptured = true;
        state.inboxImported = false;
        sendJson(res, inboxItem(), 201);
      });
      return;
    }
    sendJson(
      res,
      state.inboxCaptured ? [state.inboxImported ? importedInboxItem() : inboxItem()] : [],
    );
    return;
  }
  if (url.pathname === "/api/v1/inbox/batch" && req.method === "POST") {
    drainRequest(req, () => {
      state.inboxCaptured = false;
      state.inboxImported = false;
      sendJson(res, []);
    });
    return;
  }
  if (url.pathname === "/api/v1/inbox/41") {
    if (req.method === "DELETE") {
      state.inboxCaptured = false;
      state.inboxImported = false;
      inboxCollectionId = null;
      res.writeHead(204, { "access-control-allow-origin": "*" });
      res.end();
      return;
    }
    if (req.method === "PATCH") {
      drainRequest(req, () => sendJson(res, inboxItem()));
      return;
    }
    sendJson(
      res,
      state.inboxCaptured
        ? state.inboxImported
          ? importedInboxItem()
          : inboxItem()
        : { detail: "not_found" },
      state.inboxCaptured ? 200 : 404,
    );
    return;
  }
  if (url.pathname === "/api/v1/inbox/41/import" && req.method === "POST") {
    if (inboxCollectionId === null) {
      drainRequest(req, () => sendJson(res, { detail: "collection_required" }, 409));
      return;
    }
    drainRequest(req, () => {
      state.inboxImported = true;
      sendJson(res, importedInboxItem());
    });
    return;
  }
  if (url.pathname === "/api/v1/inbox/41/retry" && req.method === "POST") {
    drainRequest(req, () => sendJson(res, importedInboxItem()));
    return;
  }
  if (url.pathname === "/api/v1/models/1/provenance") {
    sendJson(res, provenance());
    return;
  }
  if (url.pathname === "/api/v1/models/1/provenance/8" && req.method === "PATCH") {
    drainRequest(req, () => {
      state.sourceOverride = !state.sourceOverride;
      sendJson(res, provenance());
    });
    return;
  }
  if (url.pathname === "/api/v1/models/1/provenance/8/cover") {
    if (req.method === "GET") {
      if (!state.sourceCover) {
        sendJson(res, { detail: "source_cover_not_found" }, 404);
      } else {
        sendJson(res, {
          id: 1,
          provenance_source_id: 8,
          content_type: "image/webp",
          size_bytes: 68,
          updated_at: now,
        });
      }
      return;
    }
    if (req.method === "PUT") {
      drainRequest(req, () => {
        state.sourceCover = true;
        sendJson(res, {
          id: 1,
          provenance_source_id: 8,
          content_type: "image/webp",
          size_bytes: 68,
          updated_at: now,
        });
      });
      return;
    }
    if (req.method === "DELETE") {
      state.sourceCover = false;
      res.writeHead(204);
      res.end();
      return;
    }
  }
  if (url.pathname === "/api/v1/models/1/provenance/8/cover/content") {
    if (!state.sourceCover) {
      sendJson(res, { detail: "source_cover_not_found" }, 404);
    } else {
      sendPng(res);
    }
    return;
  }
  if (url.pathname === "/api/v1/models/trash") {
    sendJson(res, [
      {
        id: 91,
        name: "Retired bracket",
        slug: "retired-bracket",
        collection: null,
        tags: [],
        thumbnail_url: null,
        file_count: 2,
        size_bytes: 1572864,
        deleted_at: now,
        expires_at: now,
      },
    ]);
    return;
  }
  if (url.pathname === "/api/v1/models/trash/expired" && req.method === "DELETE") {
    sendJson(res, {
      purged_model_ids: [91],
      purged_count: 1,
      storage_completed: 1,
      storage_pending: 0,
      storage_blocked: 0,
      storage_cleanup_status: "completed",
    });
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
  if (url.pathname === "/api/v1/models/page") {
    sendJson(res, {
      items: modelList,
      next_cursor: null,
      total: modelList.length,
    });
    return;
  }
  if (url.pathname === "/api/v1/models/outliner") {
    sendJson(
      res,
      modelList.map(({ id, name, collection, collection_id }) => ({
        id,
        name,
        collection,
        collection_id,
      })),
    );
    return;
  }
  if (url.pathname === "/api/v1/models/facets") {
    sendJson(res, {
      file_type: [],
      material_type: [],
      slicer_name: [],
      printer_model: [],
      revision_status: [],
      print_outcome: [],
      storage: [],
      printed: [],
    });
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
        remote_filename: "subtask-9",
        source: "external",
        external_display_name: "Bambu project label",
        artifact_evidence: "project_archived",
        reproducibility_level: "exact",
        identity: {
          display_name: "Bambu project label",
          task_id: "task-42",
          subtask_id: "subtask-9",
          project_id: "project-7",
          profile_id: "profile-4",
          gcode_file: null,
          plate_index: 2,
        },
        metadata: { current_layer: 80, total_layers: 80, nozzle_diameter: 0.4 },
        reproducibility: {
          level: "exact",
          identity: {
            display_name: "Bambu project label",
            task_id: "task-42",
            subtask_id: "subtask-9",
            project_id: "project-7",
            profile_id: "profile-4",
            gcode_file: null,
            plate_index: 2,
          },
          metadata: { current_layer: 80, total_layers: 80, nozzle_diameter: 0.4 },
          error: null,
          download_url: "/api/v1/files/2/download",
          toolpath_preview_url: "/api/v1/files/2/toolpath-preview",
        },
        download_url: "/api/v1/files/2/download",
        toolpath_preview_url: "/api/v1/files/2/toolpath-preview",
        gcode_revision_number: 1,
        revision_label: null,
        state: "completed",
        material_type: "PLA",
        error: null,
        started_at: "2026-06-04T00:00:00.000000",
        finished_at: now,
        created_at: "2026-06-04T00:00:00.000000",
      },
      {
        id: 2,
        printer_id: printer.id,
        printer_name: printer.name,
        file_id: 2,
        remote_filename: "cache/partial.gcode",
        source: "external",
        external_display_name: "Partial Bambu plate",
        artifact_evidence: "capture_failed",
        reproducibility_level: "metadata",
        identity: {
          display_name: "Partial Bambu plate",
          task_id: "task-43",
          subtask_id: "subtask-10",
          project_id: "project-8",
          profile_id: "profile-5",
          gcode_file: "cache/partial.gcode",
          plate_index: 1,
        },
        metadata: { current_layer: 20, total_layers: 100, nozzle_diameter: 0.4 },
        reproducibility: {
          level: "metadata",
          identity: {
            display_name: "Partial Bambu plate",
            task_id: "task-43",
            subtask_id: "subtask-10",
            project_id: "project-8",
            profile_id: "profile-5",
            gcode_file: "cache/partial.gcode",
            plate_index: 1,
          },
          metadata: { current_layer: 20, total_layers: 100, nozzle_diameter: 0.4 },
          error: { code: "bambu_ftps_unavailable", message: "The printer cache is unavailable." },
          download_url: null,
        },
        download_url: null,
        gcode_revision_number: null,
        revision_label: null,
        state: "failed",
        material_type: "PLA",
        error: null,
        started_at: null,
        finished_at: now,
        created_at: "2026-06-03T00:00:00.000000",
      },
      {
        id: 3,
        printer_id: printer.id,
        printer_name: printer.name,
        file_id: 2,
        remote_filename: "cache/basic.gcode",
        source: "external",
        external_display_name: "Basic external print",
        artifact_evidence: "metadata_only",
        reproducibility_level: "basic",
        identity: {
          display_name: "Basic external print",
          task_id: null,
          subtask_id: null,
          project_id: null,
          profile_id: null,
          gcode_file: null,
          plate_index: null,
        },
        metadata: { current_layer: null, total_layers: null, nozzle_diameter: null },
        reproducibility: {
          level: "basic",
          identity: {
            display_name: "Basic external print",
            task_id: null,
            subtask_id: null,
            project_id: null,
            profile_id: null,
            gcode_file: null,
            plate_index: null,
          },
          metadata: { current_layer: null, total_layers: null, nozzle_diameter: null },
          error: null,
          download_url: null,
        },
        download_url: null,
        gcode_revision_number: null,
        revision_label: null,
        state: "completed",
        material_type: null,
        error: null,
        started_at: null,
        finished_at: now,
        created_at: "2026-06-02T00:00:00.000000",
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
  if (url.pathname === "/api/v1/printers/dashboard") {
    sendJson(res, {
      total_printers: 1,
      status_counts: { ready: 1 },
      active_jobs: 0,
      groups: [{ name: "__ungrouped", count: 1, status_counts: { ready: 1 } }],
    });
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
  if (url.pathname === "/api/v1/printers/3/config") {
    sendJson(res, {
      printer_id: printer.id,
      server_info: {},
      printer_info: {},
      moonraker_config: {},
      klipper_config: {},
    });
    return;
  }
  if (url.pathname === "/api/v1/printers/3/ws-ticket" && req.method === "POST") {
    drainRequest(req, () => sendJson(res, { ticket: "mock-ticket", expires_in: 30 }));
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
        external_display_name: null,
        artifact_evidence: "vault",
        reproducibility_level: "exact",
        identity: {
          display_name: null,
          task_id: null,
          subtask_id: null,
          project_id: null,
          profile_id: null,
          gcode_file: null,
          plate_index: null,
        },
        metadata: { current_layer: null, total_layers: null, nozzle_diameter: null },
        reproducibility: {
          level: "exact",
          identity: {
            display_name: null,
            task_id: null,
            subtask_id: null,
            project_id: null,
            profile_id: null,
            gcode_file: null,
            plate_index: null,
          },
          metadata: { current_layer: null, total_layers: null, nozzle_diameter: null },
          error: null,
          download_url: "/api/v1/files/2/download",
        },
        download_url: "/api/v1/files/2/download",
        error: null,
        started_at: "2026-06-04T00:00:00.000000",
        finished_at: now,
        created_at: "2026-06-04T00:00:00.000000",
        updated_at: now,
      },
      {
        id: 2,
        printer_id: printer.id,
        file_id: 2,
        model_id: model.id,
        remote_filename: "cache/partial.gcode",
        state: "failed",
        progress: 0.2,
        source: "external",
        external_display_name: "Partial Bambu plate",
        external_task_id: "task-43",
        external_subtask_id: "subtask-10",
        external_project_id: "project-8",
        external_profile_id: "profile-5",
        external_gcode_file: "cache/partial.gcode",
        external_plate_index: 1,
        external_current_layer: 20,
        external_total_layers: 100,
        external_nozzle_diameter: 0.4,
        artifact_evidence: "capture_failed",
        reproducibility_level: "metadata",
        identity: {
          display_name: "Partial Bambu plate",
          task_id: "task-43",
          subtask_id: "subtask-10",
          project_id: "project-8",
          profile_id: "profile-5",
          gcode_file: "cache/partial.gcode",
          plate_index: 1,
        },
        metadata: { current_layer: 20, total_layers: 100, nozzle_diameter: 0.4 },
        reproducibility: {
          level: "metadata",
          identity: {
            display_name: "Partial Bambu plate",
            task_id: "task-43",
            subtask_id: "subtask-10",
            project_id: "project-8",
            profile_id: "profile-5",
            gcode_file: "cache/partial.gcode",
            plate_index: 1,
          },
          metadata: { current_layer: 20, total_layers: 100, nozzle_diameter: 0.4 },
          error: { code: "bambu_ftps_unavailable", message: "The printer cache is unavailable." },
          download_url: null,
        },
        download_url: null,
        error: null,
        started_at: null,
        finished_at: now,
        created_at: "2026-06-03T00:00:00.000000",
        updated_at: now,
      },
      {
        id: 3,
        printer_id: printer.id,
        file_id: 2,
        model_id: model.id,
        remote_filename: "cache/basic.gcode",
        state: "completed",
        progress: 1,
        source: "external",
        external_display_name: "Basic external print",
        artifact_evidence: "metadata_only",
        reproducibility_level: "basic",
        identity: {
          display_name: "Basic external print",
          task_id: null,
          subtask_id: null,
          project_id: null,
          profile_id: null,
          gcode_file: null,
          plate_index: null,
        },
        metadata: { current_layer: null, total_layers: null, nozzle_diameter: null },
        reproducibility: {
          level: "basic",
          identity: {
            display_name: "Basic external print",
            task_id: null,
            subtask_id: null,
            project_id: null,
            profile_id: null,
            gcode_file: null,
            plate_index: null,
          },
          metadata: { current_layer: null, total_layers: null, nozzle_diameter: null },
          error: null,
          download_url: null,
        },
        download_url: null,
        error: null,
        started_at: null,
        finished_at: now,
        created_at: "2026-06-02T00:00:00.000000",
        updated_at: now,
      },
    ]);
    return;
  }
  if (url.pathname.startsWith("/api/v1/files/") && url.pathname.endsWith("/toolpath-preview")) {
    res.writeHead(200, { "Content-Type": "text/plain" });
    res.end("G90\nM82\nG1 Z0.2\nG1 X10 Y0 E0.1\nG1 X20 Y0 E0.2\n");
    return;
  }
  if (url.pathname.startsWith("/api/v1/files/") && url.pathname.endsWith("/download")) {
    res.writeHead(200, {
      "Content-Type": "application/octet-stream",
      "Content-Disposition": 'attachment; filename="benchy.gcode"',
    });
    res.end("G1 X1 Y1 E1\n");
    return;
  }
  if (req.method === "POST" && url.pathname === "/api/v1/ingest/orca") {
    drainRequest(req, () => {
      state.ingestJobQueued = true;
      sendJson(res, { job_id: "gcode-job-1", state: "pending", message: "ingestion queued" }, 202);
    });
    return;
  }
  if (req.method === "POST" && url.pathname === "/api/v1/files/thumbnails/rebuild") {
    drainRequest(req, () => {
      state.thumbnailRebuildQueued = true;
      sendJson(
        res,
        { job_id: "thumbnail-job-1", state: "pending", message: "thumbnail rebuild queued" },
        202,
      );
    });
    return;
  }
  if (url.pathname === "/api/v1/ingest/jobs") {
    sendJson(
      res,
      state.ingestJobQueued
        ? [
            {
              job_id: "gcode-job-1",
              state: "completed",
              stage: "completed",
              completion: "complete",
              progress: 100,
              processed: 1,
              total: 1,
              succeeded: 1,
              deduplicated: 0,
              skipped: 0,
              failed: 0,
              model_id: model.id,
              file_id: 2,
              error: null,
              retryable: false,
              thumbnail_status: "skipped",
              thumbnail_reason: "not_mesh",
              committed_at: now,
              started_at: now,
              finished_at: now,
              updated_at: now,
            },
          ]
        : [],
    );
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
  if (url.pathname === "/api/v1/backups/unowned-local" && req.method === "GET") {
    sendJson(res, []);
    return;
  }
  if (url.pathname === "/api/v1/backups/unowned-s3" && req.method === "GET") {
    sendJson(
      res,
      state.s3LegacyCandidate
        ? [
            {
              key: "nexus3d-backups/legacy-2025.tar.gz",
              prefix: "nexus3d-backups/",
              backup_id: "legacy-2025",
              created_at: "2025-01-01T00:00:00Z",
              size_bytes: 4096,
              file_count: 12,
              storage_backend: "s3",
              app_version: "0.12.1",
              location: "s3",
              source_ref: "s3-legacy-source",
              provider_ref: "provider-legacy-s3",
              namespace: "printstash-bucket/nexus3d-backups",
              candidate_kind: "unowned_archive",
              archive_sha256: "a".repeat(64),
            },
          ]
        : [],
    );
    return;
  }
  if (url.pathname === "/api/v1/backups" && req.method === "GET") {
    sendJson(res, []);
    return;
  }
  if (url.pathname === "/api/v1/backups/sources" && req.method === "GET") {
    sendJson(
      res,
      state.s3LegacyAdopted
        ? [
            {
              backup_id: "legacy-2025",
              created_at: "2025-01-01T00:00:00Z",
              size_bytes: 4096,
              file_count: 12,
              storage_backend: "s3",
              app_version: "0.12.1",
              location: "s3",
              source_ref: "s3-legacy-source",
              provider_ref: "provider-legacy-s3",
              namespace: "printstash-bucket/nexus3d-backups",
              key: "nexus3d-backups/legacy-2025.tar.gz",
              prefix: "nexus3d-backups/",
              archive_sha256: "a".repeat(64),
              canonical: true,
              precedence: 2,
            },
          ]
        : [],
    );
    return;
  }
  if (url.pathname === "/api/v1/backups/adopt-local" && req.method === "POST") {
    drainRequest(req, () => sendJson(res, { backup_id: "legacy-backup" }));
    return;
  }
  if (url.pathname === "/api/v1/backups/adopt-s3" && req.method === "POST") {
    state.s3LegacyCandidate = false;
    state.s3LegacyAdopted = true;
    drainRequest(req, () =>
      sendJson(res, {
        backup_id: "legacy-2025",
        source_ref: "s3-legacy-source",
        archive_sha256: "a".repeat(64),
      }),
    );
    return;
  }
  if (url.pathname === "/api/v1/config/storage-roots/enroll" && req.method === "POST") {
    drainRequest(req, () =>
      sendJson(res, { enrolled: true, role: "data", restart_required: true }),
    );
    return;
  }
  if (url.pathname === "/api/v1/health/details") {
    sendJson(res, {
      status: "ok",
      name: "PrintStash",
      version: "0.13.0",
      components: {
        database: { ok: true },
        storage: {
          ok: true,
          backend: "local",
          provider: "local",
          tier: "verified",
          warnings: [],
          diagnostics: { roots_ready: true, root_bindings: {} },
        },
      },
    });
    return;
  }
  if (url.pathname === "/api/v1/storage/providers") {
    sendJson(res, storageProviders());
    return;
  }
  if (url.pathname === "/api/v1/health/releases/latest") {
    sendJson(res, {
      status: "update_available",
      current_version: "0.10.0",
      latest_version: "0.10.1",
      update_available: true,
      release_url: "https://github.com/xiao-villamor/PrintStash/releases/tag/v0.10.1",
      published_at: "2026-07-14T10:00:00Z",
      checked_at: "2026-07-14T11:00:00Z",
    });
    return;
  }
  if (url.pathname === "/api/v1/libraries") {
    sendJson(res, state.externalLibrariesEnabled ? [externalLibrary] : []);
    return;
  }
  if (req.method === "POST" && url.pathname === "/api/v1/libraries/1/scan") {
    drainRequest(req, () => {
      sendJson(
        res,
        { job_id: "scan-job-1", state: "pending", message: "library scan queued" },
        202,
      );
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
    drainRequest(req, () => sendJson(res, { created: 0, updated: 0, adopted: 0, unlinked: 0 }));
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
    res.end(
      "solid triangle\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\nendloop\nendfacet\nendsolid triangle\n",
    );
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
