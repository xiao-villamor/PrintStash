/**
 * Builders for API-shaped objects — the frontend's arrange step.
 *
 * The counterpart to `backend/tests/factories/`, for the same reason but a
 * different failure. A backend row that encodes state wrongly is invisible to
 * the code under test; a frontend response object that is *missing a field* is
 * worse, because TypeScript stops helping the moment the literal is written
 * inline. `PrinterRead` has thirty-odd fields, most of them boilerplate `access`
 * and `capabilities` blocks, so two test files that each spell one out drift
 * apart — and when the wire type gains a field, every literal has to be found
 * and updated by hand.
 *
 * Each builder returns a complete, valid object and takes a shallow `Partial`,
 * so a test names only the fields its assertion depends on:
 *
 *     const printer = aPrinter({ status: "printing" });
 *
 * Nested blocks are **composed, not deep-merged** — they have their own
 * builders:
 *
 *     const printer = aPrinter({ access: printerAccess({ can_print: false }) });
 *
 * That is deliberate rather than a shortcut. A generic deep merge needs
 * `unknown` parameters and a runtime `typeof` walk, both of which this repo's
 * anti-slop lint rules forbid — they erase the contract a caller is relying on.
 * Composition keeps every override fully typed, and it reads better: the block
 * being customised is named, not inferred from nesting depth.
 *
 * Defaults describe the *ordinary* case — a reachable, fully-capable printer the
 * caller may operate — because that is what most tests need, and it makes an
 * interesting variant obvious at the call site.
 *
 * Timestamps are absolute ISO strings, never `Date.now()`: a clock-derived
 * fixture makes a snapshot differ between runs and a relative-time assertion
 * pass only on the day it was written.
 */

import type {
  CollectionRead,
  ModelListItem,
  PrinterAccess,
  PrinterCapabilities,
  PrinterRead,
  PrintJobRead,
  StorageUsageRead,
  TagRead,
  VaultStatsRead,
} from "@/types";

/** A fixed instant. Every builder's timestamps derive from this one. */
export const FROZEN_NOW = "2026-01-01T00:00:00Z";

/** Full access. Narrow it in a test that asserts a permission boundary. */
export function printerAccess(override?: Partial<PrinterAccess>): PrinterAccess {
  return {
    role: "admin",
    can_view: true,
    can_print: true,
    can_control: true,
    can_admin: true,
    ...override,
  };
}

/**
 * Every capability enabled, at `stable` support.
 *
 * The UI disables controls from this block, so a test asserting a control is
 * *absent* has to turn the matching capability off rather than trust a default —
 * and one asserting a control is present needs it on, which is why the default
 * is fully capable.
 */
export function printerCapabilities(override?: Partial<PrinterCapabilities>): PrinterCapabilities {
  return {
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
    ...override,
  };
}

/** A reachable Moonraker printer the caller may fully operate. */
export function aPrinter(override?: Partial<PrinterRead>): PrinterRead {
  return {
    id: 1,
    name: "Voron",
    provider: "moonraker",
    moonraker_url: "http://printer.invalid:7125",
    has_api_key: false,
    capabilities: printerCapabilities(),
    access: printerAccess(),
    notes: null,
    group: null,
    is_default: false,
    drain_mode: false,
    drain_reason: null,
    drain_updated_at: null,
    status: "ready",
    last_seen_at: FROZEN_NOW,
    last_error: null,
    created_at: FROZEN_NOW,
    updated_at: FROZEN_NOW,
    ...override,
  };
}

/**
 * A queued, vault-backed job for printer 1.
 *
 * Every required field is spelled out rather than cast, deliberately: an `as`
 * here would let the object drift out of shape the moment the wire type gains a
 * field, which is the drift this module exists to stop. If TypeScript complains
 * after a schema change, that complaint is the feature.
 */
export function aPrintJob(override?: Partial<PrintJobRead>): PrintJobRead {
  return {
    id: 1,
    printer_id: 1,
    file_id: 1,
    model_id: 1,
    remote_filename: "bracket.gcode",
    state: "queued",
    progress: 0,
    source: "vault",
    external_display_name: null,
    external_task_id: null,
    external_subtask_id: null,
    external_project_id: null,
    external_profile_id: null,
    external_gcode_file: null,
    external_plate_index: null,
    external_current_layer: null,
    external_total_layers: null,
    external_nozzle_diameter: null,
    // `vault` is the ordinary case: PrintStash owns the bytes. The other values
    // describe a job observed on the printer whose artifact could not be
    // captured, which is a different scenario a test asks for by name.
    artifact_evidence: "vault",
    artifact_capture_error: null,
    error: null,
    routing_strategy: "manual",
    queue_position: 0,
    provider_job_id: null,
    blocked_reason: null,
    dispatch_claimed_at: null,
    dispatch_attempts: 0,
    retryable: false,
    requested_by: null,
    spool_id: null,
    spool_name: null,
    started_at: null,
    finished_at: null,
    created_at: FROZEN_NOW,
    updated_at: FROZEN_NOW,
    ...override,
  };
}

/** A folder in the library, at the root, that the caller may administer. */
export function aCollection(override?: Partial<CollectionRead>): CollectionRead {
  return {
    id: 1,
    name: "Parts",
    slug: "parts",
    path: "parts",
    parent_id: null,
    model_count: 2,
    effective_role: "admin",
    ...override,
  };
}

export function aTag(override?: Partial<TagRead>): TagRead {
  return { id: 1, name: "functional", slug: "functional", model_count: 3, ...override };
}

export function storageUsage(override?: Partial<StorageUsageRead>): StorageUsageRead {
  return {
    backend: "local",
    prefix: null,
    bucket: null,
    object_count: 40,
    total_size_bytes: 1024,
    ok: true,
    error: null,
    ...override,
  };
}

/**
 * The library-wide totals the vault header and the settings overview render.
 *
 * `storage` is composed rather than deep-merged, like every other nested block
 * here: `vaultStats({ storage: storageUsage({ ok: false }) })`.
 */
export function vaultStats(override?: Partial<VaultStatsRead>): VaultStatsRead {
  return {
    model_count: 12,
    file_count: 40,
    source_file_count: 20,
    gcode_file_count: 20,
    collection_count: 3,
    tag_count: 5,
    printer_count: 1,
    indexed_size_bytes: 1024,
    storage: storageUsage(),
    ...override,
  };
}

/** One row of the model library listing, with nothing printed yet. */
export function aModelListItem(override?: Partial<ModelListItem>): ModelListItem {
  return {
    id: 1,
    name: "Bracket",
    slug: "bracket",
    collection: null,
    collection_id: null,
    source_url: null,
    effective_role: null,
    tags: [],
    thumbnail_url: null,
    file_count: 1,
    mesh_file_id: null,
    printer_presence: [],
    updated_at: FROZEN_NOW,
    print_summary: null,
    starred: false,
    ...override,
  };
}
