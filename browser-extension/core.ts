import type { ImportIntent } from "./import-status.ts";
import { sourcePublication, SOURCE_PROVIDERS } from "./source-providers.ts";
import { captureReviewUrl } from "./capture-result.ts";
import { vaultFetch } from "./vault-fetch.ts";
const SOURCE_RULES = [
  {
    source: "Printables",
    host: (hostname: string) => hostname === "printables.com" || hostname === "www.printables.com",
    path: /^\/(?:[^/]+\/)?(?:model|collections)\/\d+(?:[-/]|$)/,
  },
  {
    source: "MakerWorld",
    host: (hostname: string) => hostname === "makerworld.com" || hostname === "www.makerworld.com",
    path: /^\/(?:[^/]+\/)?models\/\d+(?:[-/]|$)/,
  },
  {
    source: "Thingiverse",
    host: (hostname: string) =>
      hostname === "thingiverse.com" || hostname === "www.thingiverse.com",
    path: /^\/(?:thing:\d+|things\/\d+)(?:[-/]|$)/,
  },
  {
    source: "Cults",
    host: (hostname: string) => hostname === "cults3d.com" || hostname === "www.cults3d.com",
    path: /^\/(?:[a-z]{2}\/)?3d-model\/[^/]+\/[^/]+(?:[-/]|$)/i,
  },
];

const DIRECT_FILE_PATH = /\.(?:zip|3mf|stl|obj|step|stp|gcode|g|gco|bgcode)$/i;

const CAPTURE_SOURCE_FIELD_LIMITS = {
  title: 512,
  description: 64 * 1024,
  instructions: 128 * 1024,
  creator_name: 512,
  creator_id: 255,
  creator_url: 2048,
  license_code: 255,
  license_url: 2048,
  license_text: 64 * 1024,
  attribution_text: 64 * 1024,
  published_at: 64,
  updated_at: 64,
};

import type { CaptureSourceDraft } from "./capture-adapter.ts";

const CAPTURE_SOURCE_PROVIDERS = new Set<string>(SOURCE_PROVIDERS.map((provider) => provider.code));

export const BROWSER_EXTENSION_SETUP_STORAGE_KEY = "printstash.browser-extension-setup:v1";

const BROWSER_EXTENSION_SETUP_MAX_AGE_MS = 10 * 60 * 1000;

function hostnameFromUnqualifiedVault(value: string) {
  const authority = value.split(/[/?#]/, 1)[0];
  if (authority.startsWith("[")) return authority.slice(1, authority.indexOf("]"));
  return authority.replace(/:\d+$/, "");
}

function isPrivateIpv4(hostname: string) {
  const octets = hostname.split(".").map(Number);
  if (
    octets.length !== 4 ||
    octets.some((octet) => !Number.isInteger(octet) || octet < 0 || octet > 255)
  ) {
    return false;
  }
  return (
    octets[0] === 10 ||
    octets[0] === 127 ||
    (octets[0] === 169 && octets[1] === 254) ||
    (octets[0] === 172 && octets[1] >= 16 && octets[1] <= 31) ||
    (octets[0] === 192 && octets[1] === 168) ||
    // Carrier-grade NAT space, which Tailscale and other overlay VPNs assign.
    (octets[0] === 100 && octets[1] >= 64 && octets[1] <= 127)
  );
}

/** Names home routers, NAS systems and private DNS give to machines on the LAN. */
const LAN_SUFFIXES = [
  ".localhost",
  ".local",
  ".lan",
  ".home",
  ".home.arpa",
  ".internal",
  ".intranet",
  ".localdomain",
];

function isLocalHostname(value: unknown) {
  const hostname = String(value ?? "")
    .trim()
    .replace(/^\[|\]$/g, "")
    .replace(/\.$/, "")
    .toLowerCase();
  if (!hostname) return false;
  if (
    hostname === "localhost" ||
    hostname === "::1" ||
    LAN_SUFFIXES.some((suffix) => hostname.endsWith(suffix))
  ) {
    return true;
  }
  if (isPrivateIpv4(hostname)) return true;
  if (hostname.includes(":")) {
    return hostname.startsWith("fc") || hostname.startsWith("fd") || /^fe[89ab]/.test(hostname);
  }
  return !hostname.includes(".");
}

export function isLocalVault(value: string) {
  try {
    return isLocalHostname(new URL(value).hostname);
  } catch {
    return false;
  }
}

export function normalizeVault(value: unknown): string {
  let raw = String(value ?? "").trim();
  if (!raw) throw new Error("Vault URL is required.");
  if (!/^[a-z][a-z\d+.-]*:\/\//i.test(raw)) {
    const hostname = hostnameFromUnqualifiedVault(raw);
    raw = `${isLocalHostname(hostname) ? "http" : "https"}://${raw}`;
  }
  let parsed;
  try {
    parsed = new URL(raw);
  } catch {
    throw new Error("Vault URL is invalid.");
  }
  if (!["http:", "https:"].includes(parsed.protocol)) {
    throw new Error("Vault URL must use HTTP or HTTPS.");
  }
  if (parsed.username || parsed.password) {
    throw new Error("Vault URL cannot contain credentials.");
  }
  parsed.search = "";
  parsed.hash = "";
  parsed.pathname = parsed.pathname.replace(/\/+$/, "");
  return parsed.toString().replace(/\/$/, "");
}

/**
 * The server addresses to try, in order, for what the user typed.
 *
 * A LAN name typed without a scheme is tried over HTTP, then HTTPS: home
 * servers serve either, and the user should not need to know which. A public
 * name always means HTTPS unless the user types `http://` themselves.
 */
export function vaultCandidates(value: unknown): string[] {
  const primary = normalizeVault(value);
  if (/^[a-z][a-z\d+.-]*:\/\//i.test(String(value).trim()) || !isLocalVault(primary)) {
    return [primary];
  }
  return [primary, primary.replace(/^http:/, "https:")];
}

/** Plain HTTP beyond the local network sends pairing codes and keys unencrypted. */
export function isPlainHttpToPublicHost(vault: string): boolean {
  return new URL(vault).protocol === "http:" && !isLocalVault(vault);
}

/** Why the server could not be used, with the address the user should open. */
export class VaultAccessError extends Error {
  constructor(
    readonly problem: "sign_in" | "unreachable",
    readonly url: string,
    message: string,
  ) {
    super(message);
    this.name = "VaultAccessError";
  }
}

function signInRequired(base: string) {
  return new VaultAccessError(
    "sign_in",
    base,
    `${new URL(base).host} shows a sign-in page in front of PrintStash. Open it in a tab, sign in, then connect again.`,
  );
}

export function parseBrowserExtensionSetup(
  value: unknown,
  activePageUrl: string,
  now = Date.now(),
) {
  if (typeof value !== "string" || !value) return null;
  let payload;
  try {
    payload = JSON.parse(value);
  } catch {
    return null;
  }
  if (
    !payload ||
    payload.version !== 1 ||
    typeof payload.vault !== "string" ||
    typeof payload.username !== "string" ||
    typeof payload.apiKey !== "string" ||
    typeof payload.expiresAt !== "number" ||
    payload.expiresAt <= now ||
    payload.expiresAt > now + BROWSER_EXTENSION_SETUP_MAX_AGE_MS
  ) {
    return null;
  }

  try {
    const vault = normalizeVault(payload.vault);
    const activePage = new URL(activePageUrl);
    if (new URL(vault).origin !== activePage.origin) return null;
    requireCredentials(payload.username, payload.apiKey);
    return {
      vault,
      username: payload.username.trim(),
      apiKey: payload.apiKey.trim(),
    };
  } catch {
    return null;
  }
}

export function classifyModelPage(value: string) {
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    return null;
  }
  if (!["http:", "https:"].includes(parsed.protocol)) return null;
  const publication = sourcePublication(value);
  if (publication) return publication.label;
  const hostname = parsed.hostname.toLowerCase();
  const rule = SOURCE_RULES.find(
    (candidate) => candidate.host(hostname) && candidate.path.test(parsed.pathname),
  );
  if (rule) return rule.source;
  return DIRECT_FILE_PATH.test(parsed.pathname) ? "Direct file" : null;
}

const TITLE_SEPARATOR = /\s+[|–—-]\s+/;
const SITE_BOILERPLATE = /\b(?:download|free|3d print(?:able)?|stl|model)\b/i;

/**
 * The model's name from a marketplace tab title, without the site's own suffix
 * ("3D BENCHY by Prusa Research | Download free STL model | Printables.com").
 * Trailing segments are dropped only while they name the source or are site
 * boilerplate, so a title that legitimately contains a separator is kept.
 */
export function modelTitleFromPageTitle(title: string, source: string): string {
  const segments = title.trim().split(TITLE_SEPARATOR);
  const siteName = source.toLowerCase().replace(/[^a-z0-9]/g, "");
  while (segments.length > 1) {
    const last = segments.at(-1) as string;
    const compact = last.toLowerCase().replace(/[^a-z0-9]/g, "");
    const namesSite = siteName.length > 0 && compact.startsWith(siteName);
    if (!namesSite && !SITE_BOILERPLATE.test(last)) break;
    segments.pop();
  }
  const separators = title.trim().match(new RegExp(TITLE_SEPARATOR, "g")) ?? [];
  return segments.reduce((kept, segment, index) =>
    index === 0 ? segment : `${kept}${separators[index - 1]}${segment}`,
  );
}

export function captureUrlForVault(value: string): string {
  const parsed = new URL(value);
  parsed.username = "";
  parsed.password = "";
  parsed.search = "";
  parsed.hash = "";
  return parsed.toString();
}

function boundedCaptureString(value: unknown, name: string, maximum: number): string {
  if (typeof value !== "string" || !value || value.length > maximum) {
    throw new Error(`Capture source ${name} is invalid.`);
  }
  // oxlint-disable no-control-regex -- reject untrusted control bytes before forwarding captures.
  if (
    /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/.test(value) ||
    /<\s*\/?\s*[a-z][^>]*>/i.test(value)
  ) {
    throw new Error(`Capture source ${name} is invalid.`);
  }
  // oxlint-enable no-control-regex
  return value;
}

function boundedCaptureTags(value: unknown): string[] {
  if (!Array.isArray(value) || value.length > 100) {
    throw new Error("Capture source tags are invalid.");
  }
  const normalized = value.map((tag) => boundedCaptureString(tag, "tag", 255).toLocaleLowerCase());
  if (new Set(normalized).size !== normalized.length)
    throw new Error("Capture source tags are invalid.");
  return normalized;
}

function validatedCaptureSource(value: CaptureSourceDraft | undefined, expectedProvider: string) {
  if (value == null) return null;
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("Capture source is invalid.");
  }
  const source = value;
  if (!CAPTURE_SOURCE_PROVIDERS.has(source.provider) || source.provider !== expectedProvider) {
    throw new Error("Capture source provider is invalid.");
  }
  if (typeof source.canonical_url !== "string") throw new Error("Capture source URL is invalid.");
  const canonicalUrl = captureUrlForVault(source.canonical_url);
  boundedCaptureString(canonicalUrl, "canonical URL", 2048);
  if (source.source_item_id !== null && source.source_item_id !== undefined) {
    boundedCaptureString(source.source_item_id, "item ID", 255);
  }
  if (source.source_revision !== null && source.source_revision !== undefined) {
    boundedCaptureString(source.source_revision, "revision", 256);
  }
  const adapterVersion = boundedCaptureString(source.adapter_version, "adapter version", 64);
  const tags = source.tags === undefined ? [] : boundedCaptureTags(source.tags);
  if (!source.fields || typeof source.fields !== "object" || Array.isArray(source.fields)) {
    throw new Error("Capture source fields are invalid.");
  }
  const fields: CaptureSourceDraft["fields"] = {};
  for (const [name, field] of Object.entries(source.fields)) {
    const maximum = (CAPTURE_SOURCE_FIELD_LIMITS as Record<string, number>)[name];
    if (!maximum || !field || typeof field !== "object" || Array.isArray(field)) {
      throw new Error("Capture source fields are invalid.");
    }
    const fieldValue = field as { value: unknown; origin: unknown };
    if (fieldValue.origin !== "confirmed" && fieldValue.origin !== "inferred") {
      throw new Error("Capture source field origin is invalid.");
    }
    fields[name] = {
      value: boundedCaptureString(fieldValue.value, name, maximum),
      origin: fieldValue.origin,
    };
  }
  return {
    provider: source.provider,
    canonical_url: canonicalUrl,
    source_item_id: source.source_item_id ?? null,
    source_revision: source.source_revision ?? null,
    adapter_version: adapterVersion,
    tags,
    fields,
  };
}

async function responseDetail(response: Response, fallback: string): Promise<string> {
  const body = await response.json().catch(() => ({}));
  if (typeof body.detail === "string") {
    const messages = {
      invalid_credentials: "The username or API key is incorrect.",
      not_authenticated: "The PrintStash connection expired. Reconnect and try again.",
      insufficient_scope: "This PrintStash user does not have import permission.",
      provide_password_or_api_key: "Enter a username and named API key.",
    };
    return messages[body.detail as keyof typeof messages] || body.detail;
  }
  return fallback;
}

export class PairingValidationError extends Error {
  constructor(
    message: string,
    readonly field: "code" | "name",
  ) {
    super(message);
  }
}

async function browserPairingClaimError(response: Response): Promise<Error> {
  const body = await response.json().catch(() => null);
  if (response.status === 400) {
    return new PairingValidationError(
      "That pairing code is invalid or expired. Create a new one in PrintStash.",
      "code",
    );
  }
  if (response.status === 409 && body?.detail === "browser_device_name_in_use") {
    return new PairingValidationError(
      "A browser with this name is already paired. Choose a different name.",
      "name",
    );
  }
  return new Error("PrintStash could not complete pairing. Try again.");
}

function requireCredentials(username: unknown, apiKey: unknown) {
  if (!String(username ?? "").trim() || !String(apiKey ?? "").trim()) {
    throw new Error("Username and named API key are required.");
  }
}

async function fetchVault(
  fetchImpl: typeof fetch,
  base: string,
  path: string,
  options: RequestInit = {},
) {
  try {
    return await fetchImpl(`${base}${path}`, options);
  } catch (error) {
    // A cancelled attempt is not an unreachable server.
    if (options.signal?.aborted || (error as { name?: unknown } | null)?.name === "AbortError")
      throw new DOMException("The operation was aborted.", "AbortError");
    throw await unreachableVault(fetchImpl, base);
  }
}

/** Explain a request that never got an HTTP response. */
async function unreachableVault(fetchImpl: typeof fetch, base: string) {
  // A sign-in proxy redirects to its own login host, which the extension has
  // no access to, so the followed request fails like a network error. The
  // redirect is still visible when it is not followed.
  try {
    const probe = await fetchImpl(`${base}/api/v1/health`, {
      redirect: "manual",
      cache: "no-store",
    });
    if (probe.type === "opaqueredirect") return signInRequired(base);
  } catch {
    // Unreachable either way; describe the address below.
  }
  const host = new URL(base).host;
  return new VaultAccessError(
    "unreachable",
    base,
    new URL(base).protocol === "https:"
      ? `Couldn't reach PrintStash at ${host}. Check the address and that the server is running. If it uses a self-signed certificate, open it in a tab and accept the certificate, then connect again.`
      : `Couldn't reach PrintStash at ${host}. Check the address and that PrintStash is running there.`,
  );
}

async function vaultLogin({
  fetchImpl,
  base,
  username,
  apiKey,
  signal,
}: {
  fetchImpl: typeof fetch;
  base: string;
  username?: string;
  apiKey?: string;
  signal?: AbortSignal;
}) {
  const login = await fetchVault(fetchImpl, base, "/api/v1/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    // PrintStash answers a login with its own session cookie. Accepting it
    // here would sign a PrintStash tab in this browser into the key's account.
    credentials: "omit",
    signal,
    body: JSON.stringify({
      username: String(username).trim(),
      api_key: String(apiKey).trim(),
      remember_me: false,
    }),
  });
  if (!login.ok) {
    throw new Error(await responseDetail(login, `PrintStash login returned ${login.status}.`));
  }
  const loginBody = await login.json().catch(() => null);
  if (!loginBody || typeof loginBody.access_token !== "string" || !loginBody.access_token) {
    throw new Error("PrintStash did not return an access token.");
  }
  return loginBody.access_token;
}

const HEALTH_PATH = "/api/v1/health";

/**
 * Where PrintStash actually answered. A reverse proxy may move the server to
 * HTTPS on the same host; a redirect to any other host is a sign-in page.
 */
function answeringBase(base: string, response: Response): string | null {
  if (!response.redirected || !response.url) return base;
  const final = new URL(response.url);
  if (final.hostname !== new URL(base).hostname || !final.pathname.endsWith(HEALTH_PATH)) {
    return null;
  }
  return `${final.origin}${final.pathname.slice(0, -HEALTH_PATH.length)}`;
}

async function verifyVaultHealth({ fetchImpl, base }: { fetchImpl: typeof fetch; base: string }) {
  const health = await fetchVault(fetchImpl, base, HEALTH_PATH, {
    headers: { Accept: "application/json" },
    credentials: "include",
    cache: "no-store",
  });
  const answered = answeringBase(base, health);
  if (answered === null) throw signInRequired(base);
  const healthBody = await health.json().catch(() => null);
  if (health.ok && healthBody?.status === "ok" && healthBody?.name === "PrintStash") {
    return {
      base: answered,
      ...(healthBody.capture_import_intent === true ? { importIntent: true as const } : {}),
    };
  }
  // Proxies refuse an unauthenticated API request instead of redirecting it.
  if ([401, 403, 407].includes(health.status) && healthBody === null) {
    throw signInRequired(base);
  }
  throw new Error("That URL is not a PrintStash server.");
}

export async function claimBrowserPairing({
  fetchImpl = vaultFetch,
  vault,
  code,
  name,
}: {
  fetchImpl?: typeof fetch;
  vault: string;
  code: string;
  name?: string;
}) {
  if (!String(code ?? "").trim()) throw new Error("Enter the pairing code from PrintStash.");
  const { base, ...capabilities } = await verifyVaultHealth({
    fetchImpl,
    base: normalizeVault(vault),
  });
  const claimed = await fetchVault(fetchImpl, base, "/api/v1/browser-pairings/claim", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({
      code: String(code).trim(),
      name: String(name || "Browser").trim() || "Browser",
    }),
  });
  if (!claimed.ok) {
    throw await browserPairingClaimError(claimed);
  }
  const payload = await claimed.json().catch(() => null);
  if (!payload || typeof payload.credential !== "string" || !payload.credential) {
    throw new Error("PrintStash did not return a browser credential.");
  }
  return {
    base,
    ...capabilities,
    deviceCredential: payload.credential,
    device: payload.device || null,
  };
}

/** Check public server reachability; paired credentials are authenticated when used for capture. */
export async function verifyVaultReachability({
  fetchImpl = vaultFetch,
  vault,
}: {
  fetchImpl?: typeof fetch;
  vault: string;
}) {
  return verifyVaultHealth({ fetchImpl, base: normalizeVault(vault) });
}

export async function verifyVaultConnection({
  fetchImpl = vaultFetch,
  vault,
  username,
  apiKey,
}: {
  fetchImpl?: typeof fetch;
  vault: string;
  username: string;
  apiKey: string;
}) {
  const requested = normalizeVault(vault);
  requireCredentials(username, apiKey);

  const { base, ...capabilities } = await verifyVaultHealth({ fetchImpl, base: requested });

  const accessToken = await vaultLogin({ fetchImpl, base, username, apiKey });
  const profile = await fetchVault(fetchImpl, base, "/api/v1/auth/me", {
    headers: {
      Accept: "application/json",
      Authorization: `Bearer ${accessToken}`,
    },
    credentials: "include",
    cache: "no-store",
  });
  if (!profile.ok) {
    throw new Error(
      await responseDetail(profile, `PrintStash profile check returned ${profile.status}.`),
    );
  }
  const user = await profile.json().catch(() => null);
  if (!user || typeof user.username !== "string" || typeof user.is_superuser !== "boolean") {
    throw new Error("PrintStash returned an invalid user profile.");
  }
  return { base, ...capabilities, accessToken, user };
}

/** A capture endpoint rejected the current browser or access credential. */
export class CaptureAuthenticationError extends Error {
  constructor() {
    super("The PrintStash connection expired. Reconnect and try again.");
    this.name = "CaptureAuthenticationError";
  }
}

export class CaptureResponseError extends Error {
  constructor(
    message: string,
    readonly diagnosticCode: string | undefined,
  ) {
    super(message);
  }

  static async from(response: Response, message: string) {
    const body = await response.json().catch(() => null);
    const detail: unknown = body?.detail;
    const code =
      typeof detail === "string" && /^[a-z][a-z0-9_]{0,127}$/.test(detail) ? detail : undefined;
    return new CaptureResponseError(
      code === "insufficient_scope"
        ? "This PrintStash user does not have import permission."
        : message,
      code,
    );
  }
}

export async function captureModelPage({
  fetchImpl = vaultFetch,
  vault,
  username,
  apiKey,
  deviceCredential,
  accessToken,
  pageUrl,
  title,
  captureSource,
  collectionId,
  onReady,
  importAgain,
  tags,
  signal,
}: {
  fetchImpl?: typeof fetch;
  vault: string;
  username?: string;
  apiKey?: string;
  deviceCredential?: string;
  accessToken?: string;
  pageUrl: string;
  title?: string;
  captureSource?: CaptureSourceDraft;
  collectionId?: number;
  onReady?: ImportIntent;
  importAgain?: boolean;
  tags?: string[];
  signal?: AbortSignal;
}) {
  const base = normalizeVault(vault);
  const source = classifyModelPage(pageUrl);
  if (!source) throw new Error("Open a supported model page or direct model file first.");
  if (source === "Printables" && captureSource !== undefined) {
    throw new Error(
      "user_file_required: Printables captures require a selected local file. Choose a downloaded Printables file before sending it to Pending Imports.",
    );
  }
  if (source === "MakerWorld") {
    throw new Error(
      "user_file_required: MakerWorld capture requires the active-tab package confirmation flow. Download the package normally, then attach it in Pending Imports.",
    );
  }
  const hasDeviceCredential = typeof deviceCredential === "string" && deviceCredential.length > 0;
  if (!hasDeviceCredential) requireCredentials(username, apiKey);
  const sourceUrl = captureUrlForVault(pageUrl);

  const token =
    accessToken ||
    deviceCredential ||
    (await vaultLogin({ fetchImpl, base, username, apiKey, signal }));

  const captured = await fetchVault(fetchImpl, base, "/api/v1/inbox", {
    method: "POST",
    signal,
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      url: sourceUrl,
      title: String(title ?? "").trim() || null,
      source_kind: "browser",
      ...(onReady ? { on_ready: onReady, import_again: importAgain === true } : {}),
      ...(collectionId !== undefined ? { collection_id: collectionId } : {}),
      ...(tags?.length ? { tags } : {}),
      ...(captureSource
        ? { capture_source: validatedCaptureSource(captureSource, source.toLocaleLowerCase()) }
        : {}),
    }),
  });
  if (captured.status === 401) throw new CaptureAuthenticationError();
  if (!captured.ok) {
    throw await CaptureResponseError.from(captured, "Capture could not be completed.");
  }
  const item = await captured.json();
  return { source, item, inboxUrl: captureReviewUrl(base, item) };
}
