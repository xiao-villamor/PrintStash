/**
 * Structured API error with server-detail extraction.
 *
 * Each instance carries:
 *   - status: HTTP status code (401, 409, 500, …)
 *   - code:   machine-readable detail string (e.g. "model_not_found")
 *   - detail: human-readable fallback (the raw body text if JSON parsing fails)
 *
 * Use ``parseApiError`` to convert a caught value into an ``ApiError``.
 */

export class ApiError extends Error {
  status: number;
  code: string;
  detail: string;

  constructor(status: number, code: string, detail: string) {
    super(`[${status}] ${code}`);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.detail = detail;
  }

  /** Is this a 401 / authentication error? */
  get isAuthError(): boolean {
    return this.status === 401;
  }

  /** Is the server saying the vault is not yet configured? */
  get isUnconfigured(): boolean {
    return this.status === 409 && this.code === "already_configured";
  }
}

/**
 * Parse a caught error value into an ApiError.
 * Handles the message format produced by ``handleResponse`` and ``expectOk``:
 * ``"HTTP <status>: <body>"`` where ``<body>`` is a JSON string from FastAPI.
 */
export function parseApiError(raw: unknown): ApiError {
  if (raw instanceof ApiError) return raw;

  if (
    raw instanceof TypeError ||
    (raw instanceof Error && raw.name === "AbortError")
  ) {
    return new ApiError(0, "network_unreachable", raw.message);
  }

  const message =
    raw instanceof Error
      ? raw.message
      : typeof raw === "string"
        ? raw
        : "Unknown error";

  const match = message.match(/^HTTP\s+(\d{3}):\s*([\s\S]+)$/);
  if (!match) {
    // No HTTP envelope. Background-job failures surface a bare server detail
    // code (e.g. "unsupported_file_type") with no status, so treat a
    // snake_case token as the code and let it map to friendly copy. Free-form
    // text (exception strings, etc.) stays "unknown".
    const code = /^[a-z][a-z0-9_]*$/.test(message) ? message : "unknown";
    return new ApiError(0, code, message);
  }

  const status = Number(match[1]);
  const body = match[2];

  try {
    const parsed = JSON.parse(body);
    const code = typeof parsed?.detail === "string" ? parsed.detail : String(status);
    return new ApiError(status, code, body);
  } catch {
    return new ApiError(status, String(status), body);
  }
}

/** Human-readable error messages keyed by server detail codes. */
const ERROR_MESSAGES: Record<string, string> = {
  // Auth
  invalid_api_key_or_token: "Authentication failed. Sign in again.",
  invalid_credentials: "Invalid username or password.",
  not_authenticated: "You must sign in to perform this action.",
  invalid_or_expired_token: "Your session has expired. Please sign in again.",
  // Models
  model_not_found: "This model no longer exists.",
  // Printers
  printer_not_found: "This printer no longer exists.",
  printer_offline: "The printer is offline.",
  // Ingest
  unsupported_file_type: "Unsupported file type.",
  file_too_large: "File exceeds the upload size limit.",
  upload_too_large: "File exceeds the upload size limit.",
  no_importable_files: "No importable 3D files were found.",
  no_entries_selected: "Select at least one file to import.",
  // URL import
  url_required: "Enter a URL to import from.",
  url_not_a_direct_file:
    "That link isn't a direct file. Paste a direct .stl/.3mf/.obj/.gcode or .zip download link.",
  url_scheme_not_allowed: "Only http(s) URLs can be imported.",
  url_host_missing: "That URL has no host.",
  url_dns_resolution_failed: "Couldn't resolve that host.",
  url_target_not_public: "That URL points to a private or local address.",
  url_too_many_redirects: "The URL redirected too many times.",
  url_redirect_without_location: "The server redirected without a destination.",
  download_too_large: "The download exceeds the size limit.",
  // Model-page resolution (Printables / MakerWorld / Thingiverse)
  printables_resolve_failed:
    "Couldn't find a download for that Printables page. Try a direct download link.",
  makerworld_resolve_failed:
    "Couldn't find a download for that MakerWorld page. Try a direct download link.",
  thingiverse_resolve_failed:
    "Couldn't find a download for that Thingiverse page. Try a direct download link.",
  printables_blocked:
    "Printables blocked the request. Try again later or use a direct download link.",
  makerworld_blocked:
    "MakerWorld blocked the request. Connect MakerWorld under Settings → Imports, or use a direct download link.",
  makerworld_login_required:
    "MakerWorld requires you to be logged in to download this model. Connect MakerWorld under Settings → Imports and try again.",
  // MakerWorld login (Settings → Imports)
  missing_credentials: "Enter your MakerWorld email and password.",
  invalid_code: "That verification code wasn't accepted. Try again.",
  missing_code: "Enter the verification code.",
  login_expired: "The login attempt expired. Start again.",
  login_failed: "MakerWorld login failed. Try again later.",
  network_error: "Couldn't reach MakerWorld. Check your connection and try again.",
  // Archive / ZIP import
  archive_invalid: "That file isn't a valid .zip archive.",
  archive_not_found: "This archive is no longer available — re-upload it.",
  archive_too_many_entries: "The archive has too many files.",
  archive_entry_too_large: "A file inside the archive is too large.",
  archive_too_large: "The archive's contents exceed the size limit.",
  archive_unsafe_entry: "The archive contains an unsafe file path.",
  // Collection import
  collection_import_failed:
    "None of the collection's models could be imported. Check that the source is reachable, and for MakerWorld that the account is connected under Settings → Imports.",
  collection_resolve_failed:
    "Couldn't read that collection. Check the URL, or try importing models individually.",
  makerworld_collection_resolve_failed:
    "Couldn't read that MakerWorld collection. Connect MakerWorld under Settings → Imports, or import models individually.",
  printables_collection_resolve_failed:
    "Couldn't read that Printables collection. Check the URL, or import models individually.",
  // Taxonomy
  collection_not_found: "Collection not found.",
  collection_not_empty: "Cannot delete: collection still has models assigned.",
  tag_not_found: "Tag not found.",
  // Setup
  already_configured: "This vault has already been set up.",
  users_already_exist: "A user account already exists in this vault.",
  data_dir_not_writable: "Cannot write to the data directory. Check filesystem permissions.",
  thumb_dir_not_writable: "Cannot write to the thumbnail directory. Check filesystem permissions.",
  // General
  duplicate_slug: "An item with that name already exists.",
  network_unreachable:
    "Couldn't reach the server. Check that PrintStash is running and try again.",
  unknown:
    "Something went wrong reaching the server. Check that PrintStash is running and try again.",
};

/** Return a user-presentable message for a given server detail code. */
export function getErrorMessage(code: string): string {
  const message = ERROR_MESSAGES[code];
  if (message) return message;
  const humanized = code.replace(/_/g, " ").trim();
  if (!humanized) return ERROR_MESSAGES.unknown;
  return `${humanized.charAt(0).toUpperCase()}${humanized.slice(1)}.`;
}

/** Return a user-presentable message for any caught error. */
export function userMessage(raw: unknown): string {
  const api = parseApiError(raw);
  return getErrorMessage(api.code);
}
