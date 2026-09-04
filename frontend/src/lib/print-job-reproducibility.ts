import type {
  PrintJobIdentityRead,
  PrintJobReportedMetadataRead,
  PrintJobReproducibilityErrorRead,
  PrintJobReproducibilityRead,
  ReproducibilityLevel,
} from "@/types";

export interface PrintJobReproducibilityInput {
  source?: string | null;
  file_id?: number | null;
  remote_filename?: string | null;
  artifact_evidence?: string | null;
  artifact_capture_error?: string | null;
  artifact_capture_error_code?: string | null;
  artifact_capture_error_message?: string | null;
  reproducibility_level?: ReproducibilityLevel;
  toolpath_preview_url?: string | null;
  identity?: PrintJobIdentityRead;
  metadata?: PrintJobReportedMetadataRead;
  reproducibility?: PrintJobReproducibilityRead;
  download_url?: string | null;
  external_display_name?: string | null;
  external_task_id?: string | null;
  external_subtask_id?: string | null;
  external_project_id?: string | null;
  external_profile_id?: string | null;
  external_gcode_file?: string | null;
  external_plate_index?: number | null;
  external_current_layer?: number | null;
  external_total_layers?: number | null;
  external_nozzle_diameter?: number | null;
}

export interface ResolvedPrintJobReproducibility {
  level: ReproducibilityLevel;
  identity: PrintJobIdentityRead;
  metadata: PrintJobReportedMetadataRead;
  error: PrintJobReproducibilityErrorRead | null;
  downloadUrl: string | null;
  toolpathPreviewUrl: string | null;
}

const CAPTURE_ERROR_MESSAGES = {
  artifact_capture_failed: "The printer artifact could not be archived.",
  external_artifact_capture_disabled: "External artifact capture is disabled by configuration.",
  invalid_bambu_artifact_path: "The printer reported an invalid artifact path.",
  unsupported_bambu_artifact: "The printer artifact format is not supported for archiving.",
  bambu_ftps_path_invalid: "The printer reported an invalid artifact path.",
  bambu_ftps_authentication_failed: "The printer rejected artifact download authentication.",
  bambu_ftps_connection_reset: "The printer reset the artifact download connection.",
  bambu_ftps_local_error: "The local artifact staging operation failed.",
  bambu_ftps_not_found: "The printer could not find the reported artifact.",
  bambu_ftps_server_rejected: "The printer rejected the artifact download request.",
  bambu_ftps_size_mismatch: "The downloaded artifact size did not match the printer report.",
  bambu_ftps_timeout: "The printer artifact download timed out.",
  bambu_ftps_tls_error: "The printer artifact download failed its secure connection.",
  bambu_ftps_too_large: "The printer artifact is larger than the capture limit.",
  bambu_ftps_transport_error: "The printer artifact download encountered a transport error.",
  bambu_ftps_unknown_error: "The printer artifact download failed for an unknown reason.",
  bambu_ftps_unavailable: "The printer cache is unavailable.",
  bambu_artifact_too_large: "The printer artifact is larger than the capture limit.",
  bambu_download_size_mismatch: "The downloaded artifact size did not match the printer report.",
  download_failed: "The printer artifact download failed.",
} satisfies Record<string, string>;

function captureErrorMessage(code: string): string | undefined {
  return Object.entries(CAPTURE_ERROR_MESSAGES).find(([key]) => key === code)?.[1];
}

const ARCHIVED_ARTIFACT_EVIDENCE = new Set(["vault", "gcode_archived", "project_archived"]);

export const ARCHIVED_ARTIFACT_LABEL = "Archived artifact";
export const EXTERNAL_PRINT_EVIDENCE_LABEL = "External print evidence";

function hasIdentity(identity: PrintJobIdentityRead): boolean {
  return Object.values(identity).some((value) => value !== null && value !== "");
}

export function isArchivedPrintArtifact(evidence: string | null | undefined): boolean {
  return evidence !== null && evidence !== undefined
    ? ARCHIVED_ARTIFACT_EVIDENCE.has(evidence)
    : false;
}

function legacyIdentity(job: PrintJobReproducibilityInput): PrintJobIdentityRead {
  return {
    display_name: job.external_display_name ?? null,
    task_id: job.external_task_id ?? null,
    subtask_id: job.external_subtask_id ?? null,
    project_id: job.external_project_id ?? null,
    profile_id: job.external_profile_id ?? null,
    gcode_file: job.external_gcode_file ?? null,
    plate_index: job.external_plate_index ?? null,
  };
}

function legacyMetadata(job: PrintJobReproducibilityInput): PrintJobReportedMetadataRead {
  return {
    current_layer: job.external_current_layer ?? null,
    total_layers: job.external_total_layers ?? null,
    nozzle_diameter: job.external_nozzle_diameter ?? null,
  };
}

function legacyError(job: PrintJobReproducibilityInput): PrintJobReproducibilityErrorRead | null {
  const rawError = job.artifact_capture_error?.trim() || null;
  const explicitCode = job.artifact_capture_error_code?.trim() || null;
  const explicitMessage = job.artifact_capture_error_message?.trim() || null;
  if (!rawError && !explicitCode && !explicitMessage) return null;

  const resolvedCode = explicitCode ?? rawError ?? "artifact_capture_failed";
  const mappedMessage = captureErrorMessage(resolvedCode);
  const rawMessage = rawError && rawError !== resolvedCode ? rawError : null;
  const message =
    explicitMessage && explicitMessage !== resolvedCode
      ? explicitMessage
      : (rawMessage ?? mappedMessage ?? "The printer artifact could not be archived.");

  return {
    code: resolvedCode,
    message,
  };
}

function normalizeContractError(
  error: PrintJobReproducibilityErrorRead | null | undefined,
): PrintJobReproducibilityErrorRead | null {
  if (!error) return null;
  const mappedMessage = captureErrorMessage(error.code);
  if (mappedMessage && error.message.trim() === error.code) {
    return { ...error, message: mappedMessage };
  }
  return error;
}

function basename(path: string | null | undefined): string | null {
  if (!path) return null;
  const leaf = path.replaceAll("\\", "/").split("/").pop();
  return leaf || null;
}

/** Use the printer's reported artifact path, never its transport/cache name. */
export function printJobArtifactLabel(job: PrintJobReproducibilityInput): string {
  const reportedName = basename(resolvePrintJobReproducibility(job).identity.gcode_file);
  if (reportedName) return reportedName;
  if (isArchivedPrintArtifact(job.artifact_evidence)) {
    if (job.source !== "external") return basename(job.remote_filename) ?? ARCHIVED_ARTIFACT_LABEL;
    return ARCHIVED_ARTIFACT_LABEL;
  }
  return EXTERNAL_PRINT_EVIDENCE_LABEL;
}

/** Normalize the additive contract while keeping old server responses readable. */
export function resolvePrintJobReproducibility(
  job: PrintJobReproducibilityInput,
): ResolvedPrintJobReproducibility {
  const contract = job.reproducibility;
  const identity = contract?.identity ?? job.identity ?? legacyIdentity(job);
  const metadata = contract?.metadata ?? job.metadata ?? legacyMetadata(job);
  const level =
    contract?.level ??
    job.reproducibility_level ??
    (isArchivedPrintArtifact(job.artifact_evidence)
      ? "exact"
      : hasIdentity(identity)
        ? "metadata"
        : "basic");
  const error = contract ? normalizeContractError(contract.error) : legacyError(job);
  const toolpathPreviewUrl =
    contract?.toolpath_preview_url !== undefined
      ? contract.toolpath_preview_url
      : job.toolpath_preview_url;

  return {
    level,
    identity,
    metadata,
    error,
    // A download is meaningful only for an exact record with an archived
    // artifact. In particular, do not make a stale/legacy URL look like a
    // downloadable external artifact after a failed capture.
    downloadUrl:
      level === "exact" && isArchivedPrintArtifact(job.artifact_evidence)
        ? (contract?.download_url ?? job.download_url ?? null)
        : null,
    toolpathPreviewUrl:
      job.artifact_evidence === "project_archived" ? (toolpathPreviewUrl ?? null) : null,
  };
}
