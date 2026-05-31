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

  const message =
    raw instanceof Error
      ? raw.message
      : typeof raw === "string"
        ? raw
        : "Unknown error";

  const match = message.match(/^HTTP\s+(\d{3}):\s*([\s\S]+)$/);
  if (!match) {
    return new ApiError(0, "unknown", message);
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
  invalid_api_key_or_token: "Authentication failed. Sign in or add a valid API key in Settings.",
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
  // Taxonomy
  category_not_found: "Category not found.",
  category_not_empty: "Cannot delete: category still has models assigned.",
  tag_not_found: "Tag not found.",
  // Setup
  already_configured: "This vault has already been set up.",
  users_already_exist: "A user account already exists in this vault.",
  data_dir_not_writable: "Cannot write to the data directory. Check filesystem permissions.",
  thumb_dir_not_writable: "Cannot write to the thumbnail directory. Check filesystem permissions.",
  // General
  duplicate_slug: "An item with that name already exists.",
};

/** Return a user-presentable message for a given server detail code. */
export function getErrorMessage(code: string): string {
  return ERROR_MESSAGES[code] ?? code.replace(/_/g, " ");
}

/** Return a user-presentable message for any caught error. */
export function userMessage(raw: unknown): string {
  const api = parseApiError(raw);
  return getErrorMessage(api.code);
}
