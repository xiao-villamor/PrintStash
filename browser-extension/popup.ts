import {
  BROWSER_EXTENSION_SETUP_STORAGE_KEY,
  captureModelPage,
  claimBrowserPairing,
  classifyModelPage,
  isLocalVault,
  normalizeVault,
  parseBrowserExtensionSetup,
  verifyBrowserDevice,
  verifyVaultConnection,
} from "./core.ts";
import {
  buildBrowserCaptureMessage,
  JSON_LD_MAX_SCRIPT_BYTES,
  JSON_LD_MAX_SCRIPTS,
  JSON_LD_MAX_TOTAL_BYTES,
  stableCaptureFileId,
  type BrowserCaptureMessage,
} from "./capture-adapter.ts";
import {
  PRINTABLES_GRAPHQL_ENDPOINT,
  PRINTABLES_LINK_MUTATION,
  PRINTABLES_MAX_RESPONSE_BYTES,
  PRINTABLES_METADATA_ADAPTER_VERSION,
  PRINTABLES_METADATA_FIXTURE_VERSION,
  PRINTABLES_METADATA_QUERY,
  PRINTABLES_METADATA_PERMISSION_ORIGIN,
  printablesFailureMessage,
  readBoundedPrintablesResponse,
  requestPrintablesLinksInExtensionContext,
  requestPrintablesMetadataInExtensionContext,
  validatePrintablesMetadataDto,
  type PrintablesFailureCode,
  type PrintablesSelectedFile,
} from "./printables-capture.ts";
import {
  captureRichFiles,
  type BrowserCaptureFile,
  type CaptureStageRunner,
  type CaptureUploadStage,
} from "./capture-transport.ts";
import {
  MAKERWORLD_MAX_RESPONSE_BYTES,
  MAKERWORLD_METADATA_FIXTURE_VERSION,
  makerWorldCaptureFromMetadata,
  makerWorldFailureMessage,
  downloadMakerWorldCandidate,
  requestMakerWorldLinksInMainWorld,
  requestMakerWorldMetadataInMainWorld,
  validateMakerWorldMetadataDto,
  validateMakerWorldResolvedLinks,
  type MakerWorldFailureCode,
  type MakerWorldMetadataPageResult,
  type MakerWorldPackageFile,
} from "./makerworld-capture.ts";
import {
  THINGIVERSE_MAX_METADATA_RESPONSE_BYTES,
  downloadThingiverseCandidate,
  requestThingiverseFilesInMainWorld,
  thingiverseCaptureFromPage,
  validateThingiverseFiles,
  type ThingiverseFilesPageResult,
} from "./thingiverse-capture.ts";
import {
  createBrowserProviderAdapter,
  type BrowserExtensionApi,
  type BrowserProviderAdapter,
} from "./browser-provider-adapter.ts";
import { browserCaptureRoute } from "./capture-routing.ts";
import { buildStatusPresentation, type StatusKind } from "./status-presentation.ts";

declare const chrome: unknown;
function requiredElement<T extends Element>(selector: string, constructor: { new (): T }): T {
  const element = document.querySelector(selector);
  if (!(element instanceof constructor)) throw new Error(`Missing ${selector}`);
  return element;
}

const browser: BrowserProviderAdapter = createBrowserProviderAdapter(
  chrome as unknown as BrowserExtensionApi,
);
type Page = { id?: number; title?: string; url?: string };
type Source = "Printables" | "MakerWorld" | "Thingiverse" | "Cults" | "Direct file" | null;
type Config = {
  vault: string;
  pairingCode?: string;
  username?: string;
  apiKey?: string;
  deviceCredential?: string;
};
type Profile = { username: string; is_superuser: boolean };
type ConnectionState = "checking" | "connected" | "error" | "disconnected";
type VisibleCaptureResult = {
  pageTitle: string;
  challengeDetected?: boolean;
  jsonLd: string[];
};

const vaultInput = requiredElement("#vault", HTMLInputElement);
const pairingCodeInput = requiredElement("#pairing-code", HTMLInputElement);
const usernameInput = requiredElement("#username", HTMLInputElement);
const keyInput = requiredElement("#key", HTMLInputElement);
const runtimeMarker = requiredElement("#runtime-marker", HTMLElement);
const pageLabel = requiredElement("#page", HTMLElement);
const sourceLabel = requiredElement("#source", HTMLElement);
const statusLabel = requiredElement("#status", HTMLElement);
const statusTitle = requiredElement("#status-title", HTMLElement);
const statusMessage = requiredElement("#status-message", HTMLElement);
const statusDetails = requiredElement("#status-details", HTMLDetailsElement);
const statusCode = requiredElement("#status-code", HTMLElement);
const captureButton = requiredElement("#capture", HTMLButtonElement);
const inboxButton = requiredElement("#open-inbox", HTMLButtonElement);
const importPanel = requiredElement("#import-panel", HTMLElement);
const importHint = requiredElement("#import-hint", HTMLElement);
const candidatePanel = requiredElement("#candidate-panel", HTMLFieldSetElement);
const candidateLegend = requiredElement("#candidate-panel legend", HTMLElement);
const candidateList = requiredElement("#candidate-list", HTMLElement);
const manualFilePanel = requiredElement("#manual-file-panel", HTMLFieldSetElement);
const manualFileInput = requiredElement("#manual-file", HTMLInputElement);
const connectionStatus = requiredElement("#connection-status", HTMLElement);
const connectionTitle = requiredElement("#connection-title", HTMLElement);
const connectionDetail = requiredElement("#connection-detail", HTMLElement);
const connectionPanel = requiredElement("#connection-panel", HTMLElement);
const connectionForm = requiredElement("#connection-form", HTMLFormElement);
const connectButton = requiredElement("#connect", HTMLButtonElement);
const editButton = requiredElement("#edit-connection", HTMLButtonElement);
const cancelButton = requiredElement("#cancel-edit", HTMLButtonElement);
const disconnectButton = requiredElement("#disconnect", HTMLButtonElement);
const apiSettingsButton = requiredElement("#open-api-settings", HTMLButtonElement);

runtimeMarker.textContent = `Version ${browser.runtime.getManifest().version}`;

let activePage: Page | null = null;
let activeSource: Source = null;
let connectionState = "checking";
let connectedConfig: Config | null = null;
let connectedProfile: Profile | null = null;
let accessToken: string | null = null;
let editingConnection = false;
let importBusy = false;
let pendingPrintablesCapture: BrowserCaptureMessage | null = null;
let pendingMakerWorldCapture: BrowserCaptureMessage | null = null;
let pendingThingiverseCapture: BrowserCaptureMessage | null = null;
let pendingThingiverseLinks = new Map<string, string>();
let pendingManualCapture: BrowserCaptureMessage | null = null;

declare global {
  var __PRINTSTASH_CAPTURE_TIMEOUT_MS__: number | undefined;
}

type DiagnosticCode =
  | "capture_permission_contains_timeout"
  | "capture_permission_request_timeout"
  | "capture_permission_denied"
  | "capture_visible_capture_failed"
  | "capture_visible_capture_timeout"
  | "printables_metadata_http"
  | "printables_metadata_timeout"
  | "makerworld_metadata_http"
  | "makerworld_metadata_timeout"
  | "capture_candidate_render_failed"
  | "printables_links_http"
  | "printables_links_timeout"
  | "makerworld_links_failed"
  | "makerworld_links_timeout"
  | "capture_download_failed"
  | "capture_download_timeout"
  | "capture_vault_slot_create_failed"
  | "capture_vault_slot_create_timeout"
  | "capture_vault_slot_upload_failed"
  | "capture_vault_slot_upload_timeout"
  | "capture_vault_finalize_failed"
  | "capture_vault_finalize_timeout"
  | "capture_failed";

type DiagnosticProvider = "Printables" | "MakerWorld";
type DiagnosticFallbackCode = PrintablesFailureCode | MakerWorldFailureCode;

class CaptureDiagnosticError extends Error {
  constructor(
    readonly diagnosticCode: DiagnosticCode,
    readonly safeMessage: string,
    readonly provider?: DiagnosticProvider,
    readonly fallbackCode?: DiagnosticFallbackCode,
    readonly fallbackJsonLd?: string[],
  ) {
    super(safeMessage);
    this.name = "CaptureDiagnosticError";
  }
}

const DEFAULT_CAPTURE_TIMEOUT_MS = 10_000;

function captureTimeoutMs() {
  const configured = globalThis.__PRINTSTASH_CAPTURE_TIMEOUT_MS__;
  return typeof configured === "number" && Number.isFinite(configured) && configured > 0
    ? configured
    : DEFAULT_CAPTURE_TIMEOUT_MS;
}

async function runCaptureStage<T>(
  operation: (signal: AbortSignal) => Promise<T>,
  timeoutCode: DiagnosticCode,
  failureCode: DiagnosticCode,
  safeMessage: string,
  provider?: DiagnosticProvider,
  fallbackCode?: DiagnosticFallbackCode,
): Promise<T> {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  const operationResult = Promise.resolve().then(() => operation(controller.signal));
  try {
    return await new Promise<T>((resolve, reject) => {
      timer = setTimeout(() => {
        controller.abort();
        reject(new CaptureDiagnosticError(timeoutCode, safeMessage, provider, fallbackCode));
      }, captureTimeoutMs());
      operationResult.then(resolve, reject);
    });
  } catch (error) {
    if (error instanceof CaptureDiagnosticError) throw error;
    const message = messageFrom(error);
    throw new CaptureDiagnosticError(
      failureCode,
      message.startsWith("user_file_required:") ? message : safeMessage,
      provider,
      fallbackCode,
    );
  } finally {
    if (timer !== undefined) clearTimeout(timer);
  }
}

function runCaptureSyncStage<T>(
  operation: () => T,
  diagnosticCode: DiagnosticCode,
  safeMessage: string,
): T {
  try {
    return operation();
  } catch {
    throw new CaptureDiagnosticError(diagnosticCode, safeMessage);
  }
}

function diagnosticStatus(error: unknown, fallbackMessage = "Capture could not be completed.") {
  if (error instanceof CaptureDiagnosticError) {
    return {
      message: error.safeMessage,
      diagnosticCode: error.diagnosticCode,
      providerCode: error.fallbackCode,
    };
  }
  return { message: fallbackMessage, diagnosticCode: "capture_failed" as const };
}

function safeCaptureMessage(error: unknown) {
  const message = messageFrom(error);
  if (message.startsWith("user_file_required:")) return message;
  if (message.startsWith("Choose a downloaded") || message.startsWith("Select at least one")) {
    return message;
  }
  if (
    message.includes("while creating upload slots") ||
    message.includes("while uploading") ||
    message.includes("while finalizing the capture") ||
    message.includes("capture upload")
  ) {
    return "PrintStash could not finish the selected file upload. Try again from Pending Imports.";
  }
  return "Capture could not be completed.";
}

const runVaultStage: CaptureStageRunner = (stage: CaptureUploadStage, operation) => {
  const timeoutCode: DiagnosticCode =
    stage === "slot_create"
      ? "capture_vault_slot_create_timeout"
      : stage === "slot_upload"
        ? "capture_vault_slot_upload_timeout"
        : "capture_vault_finalize_timeout";
  const failureCode: DiagnosticCode =
    stage === "slot_create"
      ? "capture_vault_slot_create_failed"
      : stage === "slot_upload"
        ? "capture_vault_slot_upload_failed"
        : "capture_vault_finalize_failed";
  return runCaptureStage(
    operation,
    timeoutCode,
    failureCode,
    "PrintStash could not finish the selected file upload. Try again from Pending Imports.",
  );
};

function messageFrom(error: unknown) {
  return error instanceof Error ? error.message : "Something went wrong.";
}

function showStatus(
  message = "",
  kind: StatusKind = "",
  diagnosticCode?: string,
  providerCode?: string,
) {
  if (!message) {
    statusLabel.hidden = true;
    statusTitle.textContent = "";
    statusMessage.textContent = "";
    statusDetails.hidden = true;
    statusDetails.open = false;
    statusCode.textContent = "";
    delete statusLabel.dataset.kind;
    return;
  }
  const presentation = buildStatusPresentation({
    message,
    kind,
    diagnosticCode,
    providerCode,
  });
  statusLabel.hidden = false;
  statusTitle.textContent = presentation.title ?? "";
  statusTitle.hidden = !presentation.title;
  statusMessage.textContent = presentation.message;
  statusDetails.hidden = !presentation.technicalCode;
  statusDetails.open = false;
  statusCode.textContent = presentation.technicalCode ?? "";
  if (kind) statusLabel.dataset.kind = kind;
  else delete statusLabel.dataset.kind;
}

function showDiagnosticStatus(error: unknown, fallbackMessage?: string) {
  const diagnostic = diagnosticStatus(error, fallbackMessage);
  showStatus(diagnostic.message, "error", diagnostic.diagnosticCode, diagnostic.providerCode);
}

function clearInboxAction() {
  inboxButton.hidden = true;
  delete inboxButton.dataset.url;
}

function clearCandidateSelection() {
  pendingPrintablesCapture = null;
  pendingMakerWorldCapture = null;
  pendingThingiverseCapture = null;
  pendingThingiverseLinks.clear();
  candidatePanel.hidden = true;
  candidateList.replaceChildren();
  captureButton.textContent = "Send to Pending Imports";
}

function clearManualFileSelection() {
  pendingManualCapture = null;
  manualFileInput.value = "";
  manualFilePanel.hidden = true;
  captureButton.textContent = "Send to Pending Imports";
}

function renderManualFileSelection(capture: BrowserCaptureMessage) {
  pendingPrintablesCapture = null;
  pendingMakerWorldCapture = null;
  pendingThingiverseCapture = null;
  pendingThingiverseLinks.clear();
  candidatePanel.hidden = true;
  candidateList.replaceChildren();
  pendingManualCapture = capture;
  manualFilePanel.hidden = false;
  captureButton.textContent = "Upload selected file";
}

function selectedManualFile(capture: BrowserCaptureMessage): BrowserCaptureFile {
  const file = manualFileInput.files?.item(0);
  if (!file) throw new Error("Choose a downloaded model file before uploading.");
  return {
    id: stableCaptureFileId(capture.source.source_item_id, file.name),
    file,
    filename: file.name,
    mediaType: file.type || "application/octet-stream",
  };
}

function renderCandidateSelection(capture: BrowserCaptureMessage) {
  if (capture.source.provider === "makerworld") {
    pendingMakerWorldCapture = capture;
    pendingPrintablesCapture = null;
    pendingThingiverseCapture = null;
  } else if (capture.source.provider === "thingiverse") {
    pendingThingiverseCapture = capture;
    pendingMakerWorldCapture = null;
    pendingPrintablesCapture = null;
  } else {
    pendingPrintablesCapture = capture;
    pendingMakerWorldCapture = null;
    pendingThingiverseCapture = null;
  }
  candidateLegend.textContent =
    capture.source.provider === "makerworld"
      ? "Select MakerWorld packages"
      : capture.source.provider === "thingiverse"
        ? "Select Thingiverse files"
        : "Select Printables files";
  candidateList.replaceChildren();
  capture.candidates.forEach((candidate, index) => {
    const label = document.createElement("label");
    label.className = "candidate-option";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = capture.source.provider !== "makerworld";
    input.dataset.candidateIndex = String(index);
    const filename = document.createElement("span");
    filename.textContent = candidate.filename;
    label.append(input, filename);
    candidateList.append(label);
  });
  candidatePanel.hidden = false;
  captureButton.textContent = "Confirm and upload selected files";
}

function selectedPrintablesCandidates(capture: BrowserCaptureMessage) {
  const indexes = [...candidateList.querySelectorAll<HTMLInputElement>("input:checked")].map(
    (input) => Number(input.dataset.candidateIndex),
  );
  return indexes.flatMap((index) => capture.candidates[index] ?? []);
}

function setButtonBusy(button: HTMLButtonElement, busy: boolean, busyLabel = "Working…") {
  if (busy) {
    button.dataset.label = button.textContent;
    button.textContent = busyLabel;
    button.setAttribute("aria-busy", "true");
  } else {
    button.textContent = button.dataset.label || button.textContent;
    button.removeAttribute("aria-busy");
    delete button.dataset.label;
  }
  button.disabled = busy;
}

function setConnectionFormBusy(busy: boolean) {
  connectionForm.toggleAttribute("aria-busy", busy);
  for (const control of connectionForm.elements)
    if (control instanceof HTMLInputElement || control instanceof HTMLButtonElement)
      control.disabled = busy;
  setButtonBusy(connectButton, busy, "Connecting…");
}

function configFromForm() {
  const vault = normalizeVault(vaultInput.value);
  const pairingCode = pairingCodeInput.value.trim();
  if (pairingCode) return { vault, pairingCode };
  const username = usernameInput.value.trim();
  const apiKey = keyInput.value.trim();
  if (!username || !apiKey)
    throw new Error("Enter a pairing code, or a username and named API key.");
  return { vault, username, apiKey };
}

function fillConnectionForm(config: Config | null) {
  vaultInput.value = config?.vault || "";
  pairingCodeInput.value = "";
  usernameInput.value = config?.username || "";
  keyInput.value = config?.apiKey || "";
}

function permissionOrigin(config: Config) {
  const vault = new URL(config.vault);
  return isLocalVault(config.vault)
    ? `${vault.protocol}//${vault.hostname}/*`
    : `${vault.origin}/*`;
}

function connectionHost(config: Config) {
  try {
    return new URL(config.vault).host;
  } catch {
    return config.vault;
  }
}

function renderImportAvailability() {
  const connected = connectionState === "connected" && connectedConfig;
  const readyConnection = connected && !editingConnection;
  importPanel.hidden = !readyConnection;
  captureButton.disabled = importBusy || !readyConnection || !activeSource;
  if (importBusy) {
    importHint.textContent = "Sending the model to your review inbox…";
  } else if (!connected) {
    importHint.textContent = "Connect PrintStash to enable importing.";
  } else if (!activeSource) {
    importHint.textContent = "Open a supported model page or direct model file first.";
  } else {
    importHint.textContent = `${activeSource} is ready. Files will remain reviewable before import.`;
  }
}

function renderConnection(
  state: ConnectionState,
  { config, profile, detail }: { config?: Config; profile?: Profile | null; detail?: string } = {},
) {
  connectionState = state;
  connectionStatus.dataset.state = state;

  if (state === "connected") {
    if (!config) throw new Error("Connected state requires a configuration.");
    const role = profile?.is_superuser ? "Admin" : "Member";
    connectionTitle.textContent = "Connected";
    connectionDetail.textContent = `${profile?.username || (config.deviceCredential ? "Paired browser" : config.username)} · ${connectionHost(config)} · ${role}`;
  } else if (state === "checking") {
    connectionTitle.textContent = "Checking connection…";
    connectionDetail.textContent =
      detail || (config ? connectionHost(config) : "Reading saved settings");
  } else if (state === "error") {
    connectionTitle.textContent = "Connection failed";
    connectionDetail.textContent = detail || "Review the URL and credentials below.";
  } else {
    connectionTitle.textContent = "Not connected";
    connectionDetail.textContent = detail || "Connect a PrintStash vault to start importing.";
  }

  const showConnectedActions = state === "connected";
  editButton.hidden = !showConnectedActions || editingConnection;
  disconnectButton.hidden = !showConnectedActions || !editingConnection;
  renderImportAvailability();
}

function renderActivePage() {
  const classified = activePage?.url ? classifyModelPage(activePage.url) : null;
  activeSource =
    classified === "Printables" ||
    classified === "MakerWorld" ||
    classified === "Thingiverse" ||
    classified === "Cults" ||
    classified === "Direct file"
      ? classified
      : null;
  pageLabel.textContent = activePage?.title || activePage?.url || "No active page";
  sourceLabel.textContent = activeSource
    ? activeSource === "Direct file"
      ? "Direct model file detected"
      : `${activeSource} page detected`
    : "Open a MakerWorld, Printables, Thingiverse, or Cults page, or a direct model file";
  sourceLabel.dataset.supported = activeSource ? "true" : "false";
  renderImportAvailability();
}

async function ensureVaultPermission(config: Config, requestPermission: boolean) {
  const origins = [permissionOrigin(config)];
  const granted = requestPermission
    ? await browser.permissions.request({ origins })
    : await browser.permissions.contains({ origins });
  if (!granted) {
    throw new Error(
      requestPermission
        ? "Permission to contact this PrintStash vault was not granted."
        : "Reconnect this vault to restore its browser permission.",
    );
  }
}

async function establishConnection(
  config: Config,
  { requestPermission, persist }: { requestPermission: boolean; persist: boolean },
) {
  const previous = connectedConfig
    ? { config: connectedConfig, profile: connectedProfile, token: accessToken }
    : null;
  const preservePrevious = Boolean(previous && editingConnection);
  renderConnection("checking", { config });
  setConnectionFormBusy(true);
  showStatus();
  try {
    await ensureVaultPermission(config, requestPermission);
    let verified;
    let normalized;
    if (config.pairingCode) {
      verified = await claimBrowserPairing({
        vault: config.vault,
        code: config.pairingCode,
        name: "Browser extension",
      });
      normalized = { vault: verified.base, deviceCredential: verified.deviceCredential };
      if (persist) {
        await browser.storage.set(normalized);
        await browser.storage.remove(["username", "apiKey"]);
      }
    } else if (config.deviceCredential) {
      verified = await verifyBrowserDevice(config);
      normalized = { vault: verified.base, deviceCredential: config.deviceCredential };
      if (persist) await browser.storage.set(normalized);
    } else {
      verified = await verifyVaultConnection({
        fetchImpl: fetch,
        vault: config.vault,
        username: config.username || "",
        apiKey: config.apiKey || "",
      });
      normalized = {
        vault: verified.base,
        username: config.username || "",
        apiKey: config.apiKey || "",
      };
      if (persist) await browser.storage.set(normalized);
    }

    if (previous && permissionOrigin(previous.config) !== permissionOrigin(normalized)) {
      await browser.permissions
        .remove({ origins: [permissionOrigin(previous.config)] })
        .catch(() => false);
    }

    connectedConfig = normalized;
    const verifiedConnection = verified as { user?: Profile; accessToken?: string };
    connectedProfile = verifiedConnection.user || null;
    accessToken = verifiedConnection.accessToken || null;
    if (previous && previous.config.vault !== normalized.vault) clearInboxAction();
    editingConnection = false;
    connectionPanel.hidden = true;
    cancelButton.hidden = true;
    disconnectButton.hidden = true;
    fillConnectionForm(normalized);
    renderConnection("connected", { config: normalized, profile: verifiedConnection.user });
  } catch (error) {
    let failedOrigin = null;
    let previousOrigin = null;
    try {
      failedOrigin = permissionOrigin(config);
      previousOrigin = previous ? permissionOrigin(previous.config) : null;
    } catch {
      // Invalid legacy settings have no origin permission to clean up.
    }
    if (requestPermission && failedOrigin && failedOrigin !== previousOrigin) {
      await browser.permissions.remove({ origins: [failedOrigin] }).catch(() => false);
    }
    if (preservePrevious && previous) {
      connectedConfig = previous.config;
      connectedProfile = previous.profile;
      accessToken = previous.token;
      renderConnection("connected", { config: previous.config, profile: previous.profile });
    } else {
      connectedConfig = null;
      connectedProfile = null;
      accessToken = null;
      renderConnection("error", { detail: messageFrom(error) });
    }
    connectionPanel.hidden = false;
    throw error;
  } finally {
    setConnectionFormBusy(false);
    connectButton.textContent = editingConnection ? "Update connection" : "Connect";
  }
}

async function ensureOriginPermission(origin: string) {
  const granted = await browser.permissions.request({ origins: [origin] });
  if (!granted) throw new Error("Permission to download the selected source file was not granted.");
}

async function ensureOriginPermissions(origins: string[]) {
  const granted = await browser.permissions.request({ origins });
  if (!granted)
    throw new Error("Permission to download the selected source files was not granted.");
}

async function ensureMetadataPermission(origin: string, provider: "Printables" | "MakerWorld") {
  const origins = [origin];
  const alreadyGranted = await runCaptureStage(
    () => browser.permissions.contains({ origins }),
    "capture_permission_contains_timeout",
    "capture_permission_contains_timeout",
    `Permission check for ${provider} metadata timed out. Choose a downloaded ${provider} file to attach it in Pending Imports.`,
    provider,
    "cors_failure",
  );
  if (alreadyGranted) return;
  const granted = await runCaptureStage(
    () => browser.permissions.request({ origins }),
    "capture_permission_request_timeout",
    "capture_permission_request_timeout",
    `Permission request for ${provider} metadata timed out. Choose a downloaded ${provider} file to attach it in Pending Imports.`,
    provider,
    "cors_failure",
  );
  if (!granted) {
    throw new CaptureDiagnosticError(
      "capture_permission_denied",
      `user_file_required: Permission to read public ${provider} metadata was not granted. Choose a downloaded ${provider} file to attach it in Pending Imports.`,
      provider,
      "cors_failure",
    );
  }
}

async function downloadPrintablesCandidate(
  candidate: BrowserCaptureMessage["candidates"][number],
  link: string,
  signal?: AbortSignal,
): Promise<BrowserCaptureFile> {
  const origin = `${new URL(link).origin}/*`;
  await ensureOriginPermission(origin);
  if (signal?.aborted) throw new DOMException("The operation was aborted.", "AbortError");
  const response = await fetch(link, {
    credentials: "omit",
    cache: "no-store",
    signal,
  });
  if (!response.ok) {
    throw new Error(
      "user_file_required: Printables could not provide the selected file. Attach it manually in Pending Imports.",
    );
  }
  if (response.url) {
    const redirected = new URL(response.url);
    const redirectedHost = redirected.hostname.toLowerCase();
    if (
      redirected.protocol !== "https:" ||
      (redirectedHost !== "printables.com" && !redirectedHost.endsWith(".printables.com"))
    ) {
      throw new Error(
        "user_file_required: Printables redirected the selected file to an unsafe host. Attach it manually in Pending Imports.",
      );
    }
  }
  const file = await readBoundedPrintablesResponse(response, candidate.sizeBytes, signal);
  return {
    id: candidate.id,
    file,
    filename: candidate.filename,
    mediaType: printablesMediaType(candidate.filename, candidate.fileType),
  };
}

function printablesMediaType(
  filename: string,
  fileType: BrowserCaptureMessage["candidates"][number]["fileType"],
): string {
  if (fileType === "gcode") return "text/plain";
  if (fileType === "stl") return "model/stl";
  if (fileType === "sla") return "application/octet-stream";
  if (/\.3mf$/i.test(filename)) return "model/3mf";
  if (/\.stl$/i.test(filename)) return "model/stl";
  if (/\.(?:gcode|gco|g|bgcode)$/i.test(filename)) return "text/plain";
  return "application/octet-stream";
}

async function resolvePrintablesLinks(
  capture: BrowserCaptureMessage,
  selected: readonly PrintablesSelectedFile[],
): Promise<Array<{ id: string; url: string }>> {
  if (!activePage?.id || !capture.source.source_item_id) {
    throw new Error(
      "user_file_required: The Printables tab is unavailable. Attach a downloaded file in Pending Imports.",
    );
  }
  const sourceItemId = capture.source.source_item_id;
  return runCaptureStage(
    async (signal) => {
      const result = await requestPrintablesLinksInExtensionContext({
        endpoint: PRINTABLES_GRAPHQL_ENDPOINT,
        query: PRINTABLES_LINK_MUTATION,
        sourceItemId,
        selected,
        maxResponseBytes: PRINTABLES_MAX_RESPONSE_BYTES,
        signal,
      });
      if (!result?.ok || !result.links) {
        throw new CaptureDiagnosticError(
          "printables_links_http",
          printablesFailureMessage(result?.code),
          "Printables",
          result?.code,
        );
      }
      return result.links;
    },
    "printables_links_timeout",
    "printables_links_http",
    "Printables could not resolve the selected files. Choose a downloaded Printables file to attach it in Pending Imports.",
    "Printables",
    "request_failed",
  );
}

async function resolveMakerWorldLinks(
  capture: BrowserCaptureMessage,
  selected: readonly BrowserCaptureMessage["candidates"][number][],
): Promise<Array<{ id: string; url: string }>> {
  if (!activePage?.id || !capture.source.source_item_id || !activePage.url) {
    throw new Error(makerWorldFailureMessage("contract_changed"));
  }
  const origin = new URL(activePage.url).origin;
  const tabId = activePage.id;
  return runCaptureStage(
    async () => {
      const results = await browser.scripting.executeScript({
        target: { tabId },
        world: "MAIN",
        func: requestMakerWorldLinksInMainWorld,
        args: [
          {
            endpoint: `${origin}/api/v1/design-service/instance`,
            selectedIds: selected.map((candidate) => candidate.id),
            maxResponseBytes: MAKERWORLD_MAX_RESPONSE_BYTES,
          },
        ],
      });
      const result = results[0]?.result as
        | { ok: boolean; links?: Array<{ id: string; url: string }>; code?: MakerWorldFailureCode }
        | undefined;
      if (!result?.ok || !result.links) {
        throw new CaptureDiagnosticError(
          "makerworld_links_failed",
          makerWorldFailureMessage(result?.code),
          "MakerWorld",
          result?.code,
        );
      }
      const selectedFiles: MakerWorldPackageFile[] = selected.map((candidate) => ({
        id: candidate.id,
        filename: candidate.filename,
        fileType: "other",
        ...(candidate.sizeBytes === undefined ? {} : { sizeBytes: candidate.sizeBytes }),
      }));
      return validateMakerWorldResolvedLinks(selectedFiles, result.links);
    },
    "makerworld_links_timeout",
    "makerworld_links_failed",
    "MakerWorld could not resolve the selected package. Download it normally, then attach it in Pending Imports.",
    "MakerWorld",
    "request_failed",
  );
}

function isRichProvider(source: Source): source is Exclude<Source, "Direct file" | null> {
  return (
    source === "Printables" ||
    source === "MakerWorld" ||
    source === "Thingiverse" ||
    source === "Cults"
  );
}
async function readVisibleCapture(): Promise<BrowserCaptureMessage | null> {
  if (!activePage?.id || !activePage.url || !isRichProvider(activeSource)) return null;
  const tabId = activePage.id;
  const pageUrl = activePage.url;
  const pageTitle = activePage.title;
  const provider = activeSource;
  try {
    const results = await runCaptureStage(
      () =>
        browser.scripting.executeScript({
          target: { tabId },
          world: "MAIN",
          func: (limits: { maxScripts: number; maxScriptBytes: number; maxTotalBytes: number }) => {
            const pageTitle = document.title;
            const challengeDetected =
              /captcha|verify you are human|access denied/i.test(document.title) ||
              Boolean(document.querySelector('iframe[src*="challenge"], [class*="captcha"]'));
            const scripts = [...document.querySelectorAll('script[type="application/ld+json"]')];
            const safeResult = () => ({ pageTitle, challengeDetected, jsonLd: [] });
            if (scripts.length > limits.maxScripts) return safeResult();

            const encoder = new TextEncoder();
            const jsonLd: string[] = [];
            let totalBytes = 0;
            for (const script of scripts) {
              const text = script.textContent || "";
              const scriptBytes = encoder.encode(text).byteLength;
              if (
                scriptBytes > limits.maxScriptBytes ||
                totalBytes + scriptBytes > limits.maxTotalBytes
              ) {
                return safeResult();
              }
              totalBytes += scriptBytes;
              jsonLd.push(text);
            }
            return { pageTitle, challengeDetected, jsonLd };
          },
          args: [
            {
              maxScripts: JSON_LD_MAX_SCRIPTS,
              maxScriptBytes: JSON_LD_MAX_SCRIPT_BYTES,
              maxTotalBytes: JSON_LD_MAX_TOTAL_BYTES,
            },
          ],
        }),
      "capture_visible_capture_timeout",
      "capture_visible_capture_failed",
      "The active page could not be read. Choose a downloaded file to attach it in Pending Imports.",
    );
    const visible = results[0]?.result as VisibleCaptureResult | undefined;
    if (!visible || !Array.isArray(visible.jsonLd)) return fallbackVisibleCapture();
    const capture = buildBrowserCaptureMessage({
      provider,
      pageUrl,
      pageTitle: visible.pageTitle || activePage.title,
      jsonLd: visible.jsonLd,
    });
    if (provider === "MakerWorld") {
      if (visible.challengeDetected) return fallbackVisibleCapture("challenge", visible.jsonLd);
      const sourceItemId = capture.source.source_item_id;
      if (!sourceItemId) return fallbackVisibleCapture("contract_changed", visible.jsonLd);
      const pageOrigin = new URL(pageUrl).origin;
      return runCaptureStage(
        async () => {
          const metadataResults = await browser.scripting.executeScript({
            target: { tabId },
            world: "MAIN",
            func: requestMakerWorldMetadataInMainWorld,
            args: [
              {
                endpoint: `${pageOrigin}/api/v1/design-service/design/${encodeURIComponent(sourceItemId)}`,
                sourceItemId,
                fixtureVersion: MAKERWORLD_METADATA_FIXTURE_VERSION,
                maxResponseBytes: MAKERWORLD_MAX_RESPONSE_BYTES,
              },
            ],
          });
          const metadataResult = metadataResults[0]?.result as
            | MakerWorldMetadataPageResult
            | undefined;
          if (!metadataResult?.ok || !metadataResult.metadata) {
            throw new CaptureDiagnosticError(
              "makerworld_metadata_http",
              makerWorldFailureMessage(metadataResult?.code),
              "MakerWorld",
              metadataResult?.code,
              visible.jsonLd,
            );
          }
          try {
            const metadata = validateMakerWorldMetadataDto(metadataResult.metadata, sourceItemId);
            return makerWorldCaptureFromMetadata(metadata, pageUrl, visible.pageTitle || pageTitle);
          } catch {
            throw new CaptureDiagnosticError(
              "makerworld_metadata_http",
              makerWorldFailureMessage("contract_changed"),
              "MakerWorld",
              "contract_changed",
              visible.jsonLd,
            );
          }
        },
        "makerworld_metadata_timeout",
        "makerworld_metadata_http",
        "MakerWorld metadata could not be read. Download the package normally, then attach it in Pending Imports.",
        "MakerWorld",
        "request_failed",
      );
    }
    if (provider === "Thingiverse") {
      if (visible.challengeDetected) {
        return thingiverseCaptureFromPage({
          pageUrl,
          pageTitle: visible.pageTitle || pageTitle,
          jsonLd: visible.jsonLd,
          files: [],
        });
      }
      const sourceItemId = capture.source.source_item_id;
      if (!sourceItemId) return capture;
      const fileResults = await browser.scripting.executeScript({
        target: { tabId },
        world: "MAIN",
        func: requestThingiverseFilesInMainWorld,
        args: [
          {
            sourceItemId,
            endpoint: `${new URL(pageUrl).origin}/api/v2/things/${encodeURIComponent(sourceItemId)}/complete`,
            maxResponseBytes: THINGIVERSE_MAX_METADATA_RESPONSE_BYTES,
          },
        ],
      });
      const fileResult = fileResults[0]?.result as ThingiverseFilesPageResult | undefined;
      let files: ReturnType<typeof validateThingiverseFiles>;
      try {
        files = validateThingiverseFiles(fileResult);
      } catch {
        files = [];
      }
      pendingThingiverseLinks = new Map(
        files.map((file) => [`thingiverse:${sourceItemId}:file:${file.id}`, file.url]),
      );
      const thingiverseCapture = thingiverseCaptureFromPage({
        pageUrl,
        pageTitle: visible.pageTitle || pageTitle,
        jsonLd: visible.jsonLd,
        files,
      });
      return fileResult?.code === "challenge" && thingiverseCapture.state === "manual_file_required"
        ? {
            ...thingiverseCapture,
            message:
              "user_file_required: Thingiverse blocked access to its file list. Refresh this page and try again. If the files remain visible but PrintStash still cannot list them, download one and attach it in Pending Imports.",
          }
        : thingiverseCapture;
    }
    if (provider !== "Printables") return capture;
    if (visible.challengeDetected) {
      return {
        ...capture,
        state: "manual_file_required",
        message: printablesFailureMessage("challenge"),
        manual_file: {
          mapping: "user_selected_file",
          source_item_id: capture.source.source_item_id,
        },
      };
    }
    const sourceItemId = capture.source.source_item_id;
    if (!sourceItemId) return fallbackVisibleCapture("contract_changed");
    const metadata = await runCaptureStage(
      async (signal) => {
        const metadataResult = await requestPrintablesMetadataInExtensionContext({
          endpoint: PRINTABLES_GRAPHQL_ENDPOINT,
          query: PRINTABLES_METADATA_QUERY,
          sourceItemId,
          fixtureVersion: PRINTABLES_METADATA_FIXTURE_VERSION,
          maxResponseBytes: PRINTABLES_MAX_RESPONSE_BYTES,
          signal,
        });
        if (!metadataResult?.ok || !metadataResult.metadata) {
          throw new CaptureDiagnosticError(
            "printables_metadata_http",
            printablesFailureMessage(metadataResult?.code),
            "Printables",
            metadataResult?.code,
            visible.jsonLd,
          );
        }
        try {
          return validatePrintablesMetadataDto(metadataResult.metadata, sourceItemId);
        } catch {
          throw new CaptureDiagnosticError(
            "printables_metadata_http",
            printablesFailureMessage("contract_changed"),
            "Printables",
            "contract_changed",
            visible.jsonLd,
          );
        }
      },
      "printables_metadata_timeout",
      "printables_metadata_http",
      "Printables metadata could not be read. Choose a downloaded Printables file to attach it in Pending Imports.",
      "Printables",
      "request_failed",
    );
    const enriched = buildBrowserCaptureMessage({
      provider,
      pageUrl,
      pageTitle: visible.pageTitle || activePage.title,
      jsonLd: visible.jsonLd,
      sourceMetadata: metadata.source,
    });
    const candidates = metadata.files.map((file) => ({
      id: file.id,
      filename: file.filename,
      fileType: file.fileType,
      ...(file.sizeBytes === undefined ? {} : { sizeBytes: file.sizeBytes }),
    }));
    return {
      ...enriched,
      source: {
        ...enriched.source,
        adapter_version: PRINTABLES_METADATA_ADAPTER_VERSION,
      },
      state: candidates.length > 0 ? "ready" : "manual_file_required",
      candidates,
      ...(candidates.length > 0
        ? {}
        : {
            message: printablesFailureMessage("contract_changed"),
            manual_file: { mapping: "user_selected_file", source_item_id: sourceItemId },
          }),
    };
  } catch (error) {
    if (error instanceof CaptureDiagnosticError) throw error;
    return fallbackVisibleCapture("request_failed");
  }
}

function fallbackVisibleCapture(
  code?: PrintablesFailureCode | MakerWorldFailureCode,
  jsonLd: string[] = [],
) {
  if (!activePage?.url || (activeSource !== "Printables" && activeSource !== "MakerWorld"))
    return null;
  const capture = buildBrowserCaptureMessage({
    provider: activeSource,
    pageUrl: activePage.url,
    pageTitle: activePage.title,
    jsonLd,
  });
  return {
    ...capture,
    state: "manual_file_required" as const,
    message:
      activeSource === "MakerWorld"
        ? makerWorldFailureMessage(code as MakerWorldFailureCode | undefined)
        : printablesFailureMessage(code as PrintablesFailureCode | undefined),
    manual_file: {
      mapping: "user_selected_file" as const,
      source_item_id: capture.source.source_item_id,
    },
  };
}

async function takePreparedSetup(page: Page) {
  if (
    page.id === undefined ||
    !page.url ||
    !Number.isInteger(page.id) ||
    !/^https?:/i.test(page.url)
  )
    return null;
  const tabId = page.id;
  const pageUrl = page.url;
  try {
    const results = await browser.scripting.executeScript({
      target: { tabId },
      world: "MAIN",
      func: (storageKey) => {
        const value = window.sessionStorage.getItem(storageKey);
        if (value) window.sessionStorage.removeItem(storageKey);
        return value;
      },
      args: [BROWSER_EXTENSION_SETUP_STORAGE_KEY],
    });
    return parseBrowserExtensionSetup(results[0]?.result, pageUrl);
  } catch {
    return null;
  }
}

connectionForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const config = configFromForm();
    await establishConnection(config, { requestPermission: true, persist: true });
    showStatus("Connection verified. This browser is ready to import.", "success");
  } catch (error) {
    if (connectionState === "connected") {
      showStatus(`Connection was not updated. ${messageFrom(error)}`, "error");
    } else {
      showStatus();
    }
  }
});

editButton.addEventListener("click", () => {
  editingConnection = true;
  connectionPanel.hidden = false;
  editButton.hidden = true;
  cancelButton.hidden = false;
  disconnectButton.hidden = false;
  connectButton.textContent = "Update connection";
  renderImportAvailability();
  showStatus();
  vaultInput.focus();
});

cancelButton.addEventListener("click", () => {
  editingConnection = false;
  fillConnectionForm(connectedConfig);
  connectionPanel.hidden = Boolean(connectedConfig);
  cancelButton.hidden = true;
  disconnectButton.hidden = true;
  connectButton.textContent = "Connect";
  renderConnection("connected", {
    config: connectedConfig ?? undefined,
    profile: connectedProfile,
  });
  showStatus();
});

disconnectButton.addEventListener("click", async () => {
  const previous = connectedConfig;
  const loopbackPermission = previous ? isLocalVault(previous.vault) : false;
  try {
    await browser.storage.remove(["apiKey", "username", "deviceCredential"]);
  } catch (error) {
    showStatus(`Couldn't remove the stored browser credential. ${messageFrom(error)}`, "error");
    return;
  }

  let permissionStillGranted = false;
  if (previous && !loopbackPermission) {
    const origins = [permissionOrigin(previous)];
    try {
      await browser.permissions.remove({ origins });
      permissionStillGranted = await browser.permissions.contains({ origins });
    } catch {
      permissionStillGranted = true;
    }
  }
  connectedConfig = null;
  connectedProfile = null;
  accessToken = null;
  editingConnection = false;
  keyInput.value = "";
  clearInboxAction();
  clearCandidateSelection();
  clearManualFileSelection();
  connectionPanel.hidden = false;
  cancelButton.hidden = true;
  disconnectButton.hidden = true;
  connectButton.textContent = "Connect";
  renderConnection("disconnected");
  showStatus(
    permissionStillGranted
      ? "Disconnected and removed the stored browser credential, but Chrome kept the vault permission. Remove it from the extension's site access settings."
      : loopbackPermission
        ? "Disconnected. The stored browser credential was removed; built-in loopback access contains no credentials."
        : "Disconnected. The stored browser credential and vault permission were removed from this browser.",
    permissionStillGranted ? "error" : "success",
  );
});

apiSettingsButton.addEventListener("click", () => {
  try {
    const base = normalizeVault(vaultInput.value);
    browser.tabs.create({ url: `${base}/settings?section=imports` });
  } catch (error) {
    showStatus(messageFrom(error), "error");
  }
});

captureButton.addEventListener("click", async () => {
  if (!connectedConfig || connectionState !== "connected") {
    showStatus("Connect PrintStash before importing.", "error");
    return;
  }
  if (activeSource === "Printables" && !pendingPrintablesCapture && !pendingManualCapture) {
    try {
      await ensureMetadataPermission(PRINTABLES_METADATA_PERMISSION_ORIGIN, "Printables");
    } catch (error) {
      const fallback = fallbackVisibleCapture("cors_failure");
      if (fallback) {
        renderManualFileSelection(fallback);
        showDiagnosticStatus(error);
      } else {
        showDiagnosticStatus(error);
      }
      return;
    }
  }
  importBusy = true;
  setButtonBusy(captureButton, true, "Sending…");
  renderImportAvailability();
  showStatus();
  try {
    if (pendingManualCapture) {
      const authorization = accessToken || connectedConfig.deviceCredential;
      if (!authorization)
        throw new Error("The browser connection expired. Connect PrintStash again.");
      const file = selectedManualFile(pendingManualCapture);
      await captureRichFiles({
        vault: connectedConfig.vault,
        authorization,
        sourceUrl: pendingManualCapture.source.canonical_url,
        title: activePage?.title,
        captureSource: pendingManualCapture.source,
        files: [file],
        runStage: runVaultStage,
      });
      const inboxUrl = `${normalizeVault(connectedConfig.vault)}/inbox`;
      clearManualFileSelection();
      inboxButton.hidden = false;
      inboxButton.dataset.url = inboxUrl;
      showStatus("Selected file and source metadata sent to Pending Imports.", "success");
      return;
    }
    if (pendingPrintablesCapture) {
      const selected = selectedPrintablesCandidates(pendingPrintablesCapture);
      if (selected.length === 0) {
        showStatus("Select at least one Printables file to upload.", "error");
        return;
      }
      const authorization = accessToken || connectedConfig.deviceCredential;
      if (!authorization)
        throw new Error("The browser connection expired. Connect PrintStash again.");
      const links = await resolvePrintablesLinks(pendingPrintablesCapture, selected);
      const linksById = new Map(links.map((link) => [link.id, link.url]));
      const files = await Promise.all(
        selected.map((candidate) =>
          runCaptureStage(
            async (signal) => {
              const link = linksById.get(candidate.id);
              if (!link) throw new Error("Printables link mapping changed.");
              return downloadPrintablesCandidate(candidate, link, signal);
            },
            "capture_download_timeout",
            "capture_download_failed",
            "The selected Printables file could not be downloaded. Attach it manually in Pending Imports.",
            "Printables",
            "request_failed",
          ),
        ),
      );
      await captureRichFiles({
        vault: connectedConfig.vault,
        authorization,
        sourceUrl: pendingPrintablesCapture.source.canonical_url,
        title: activePage?.title,
        captureSource: pendingPrintablesCapture.source,
        files,
        runStage: runVaultStage,
      });
      const inboxUrl = `${normalizeVault(connectedConfig.vault)}/inbox`;
      clearCandidateSelection();
      inboxButton.hidden = false;
      inboxButton.dataset.url = inboxUrl;
      showStatus("Selected Printables files sent to Pending Imports.", "success");
      return;
    }
    if (pendingMakerWorldCapture) {
      const selected = selectedPrintablesCandidates(pendingMakerWorldCapture);
      if (selected.length === 0) {
        showStatus("Select at least one MakerWorld package to upload.", "error");
        return;
      }
      const authorization = accessToken || connectedConfig.deviceCredential;
      if (!authorization)
        throw new Error("The browser connection expired. Connect PrintStash again.");
      const links = await resolveMakerWorldLinks(pendingMakerWorldCapture, selected);
      const linksById = new Map(links.map((link) => [link.id, link.url]));
      const files: BrowserCaptureFile[] = [];
      let totalBytes = 0;
      for (const candidate of selected) {
        const link = linksById.get(candidate.id);
        if (!link) throw new Error("MakerWorld link mapping changed.");
        const file = await runCaptureStage(
          (signal) =>
            downloadMakerWorldCandidate({
              candidate: {
                id: candidate.id,
                filename: candidate.filename,
                fileType: "other",
                ...(candidate.sizeBytes === undefined ? {} : { sizeBytes: candidate.sizeBytes }),
              },
              link,
              totalBefore: totalBytes,
              ensureOriginPermission,
              signal,
            }),
          "capture_download_timeout",
          "capture_download_failed",
          "The selected MakerWorld package could not be downloaded. Attach it manually in Pending Imports.",
          "MakerWorld",
          "request_failed",
        );
        totalBytes += file.file.size;
        files.push(file);
      }
      await captureRichFiles({
        vault: connectedConfig.vault,
        authorization,
        sourceUrl: pendingMakerWorldCapture.source.canonical_url,
        title: activePage?.title,
        captureSource: pendingMakerWorldCapture.source,
        files,
        runStage: runVaultStage,
      });
      const inboxUrl = `${normalizeVault(connectedConfig.vault)}/inbox`;
      clearCandidateSelection();
      inboxButton.hidden = false;
      inboxButton.dataset.url = inboxUrl;
      showStatus("Selected MakerWorld packages sent to Pending Imports.", "success");
      return;
    }
    if (pendingThingiverseCapture) {
      const selected = selectedPrintablesCandidates(pendingThingiverseCapture);
      if (selected.length === 0) {
        showStatus("Select at least one Thingiverse file to upload.", "error");
        return;
      }
      const authorization = accessToken || connectedConfig.deviceCredential;
      if (!authorization)
        throw new Error("The browser connection expired. Connect PrintStash again.");
      const files: BrowserCaptureFile[] = [];
      for (const candidate of selected) {
        const link = pendingThingiverseLinks.get(candidate.id);
        if (!link) throw new Error("Thingiverse file mapping changed.");
        files.push(
          await runCaptureStage(
            (signal) =>
              downloadThingiverseCandidate({
                candidate,
                link,
                ensureOriginPermissions,
                signal,
              }),
            "capture_download_timeout",
            "capture_download_failed",
            "The selected Thingiverse file could not be downloaded. Attach it manually in Pending Imports.",
          ),
        );
      }
      await captureRichFiles({
        vault: connectedConfig.vault,
        authorization,
        sourceUrl: pendingThingiverseCapture.source.canonical_url,
        title: activePage?.title,
        captureSource: pendingThingiverseCapture.source,
        files,
        runStage: runVaultStage,
      });
      const inboxUrl = `${normalizeVault(connectedConfig.vault)}/inbox`;
      clearCandidateSelection();
      inboxButton.hidden = false;
      inboxButton.dataset.url = inboxUrl;
      showStatus("Selected Thingiverse files sent to Pending Imports.", "success");
      return;
    }
    const visibleCapture = (await readVisibleCapture()) ?? fallbackVisibleCapture();
    const captureRoute = browserCaptureRoute(visibleCapture);
    if (captureRoute === "manual_file") {
      if (!visibleCapture) throw new Error("The active tab has no normalized capture source.");
      renderManualFileSelection(visibleCapture);
      showStatus(
        visibleCapture.message ||
          "Choose a downloaded model file to attach it to this metadata draft.",
      );
      return;
    }
    if (captureRoute === "candidate_confirmation") {
      if (!visibleCapture) throw new Error("The active tab has no normalized capture source.");
      runCaptureSyncStage(
        () => renderCandidateSelection(visibleCapture),
        "capture_candidate_render_failed",
        "The provider files could not be shown. Choose a downloaded file to attach it in Pending Imports.",
      );
      showStatus(
        visibleCapture.source.provider === "makerworld"
          ? "Select MakerWorld packages, then confirm the upload."
          : visibleCapture.source.provider === "thingiverse"
            ? "Select Thingiverse files, then confirm the upload."
            : "Review the selected Printables files, then confirm the upload.",
      );
      return;
    }
    const pageUrl = activePage?.url;
    if (!pageUrl) throw new Error("The active tab has no capture URL.");
    const result = await captureModelPage({
      ...connectedConfig,
      accessToken: accessToken ?? undefined,
      pageUrl,
      title: activePage?.title ?? undefined,
      captureSource: visibleCapture?.source,
    });
    inboxButton.hidden = false;
    inboxButton.dataset.url = result.inboxUrl;
    showStatus(`Model from ${result.source} sent to Pending Imports.`, "success");
  } catch (error) {
    if (error instanceof CaptureDiagnosticError) {
      const fallback =
        pendingPrintablesCapture ||
        pendingMakerWorldCapture ||
        pendingThingiverseCapture ||
        (error.provider ? fallbackVisibleCapture(error.fallbackCode, error.fallbackJsonLd) : null);
      if (fallback) renderManualFileSelection(fallback);
      showDiagnosticStatus(error);
      return;
    }
    const message = messageFrom(error);
    if (
      (pendingPrintablesCapture || pendingMakerWorldCapture || pendingThingiverseCapture) &&
      (message.startsWith("user_file_required") ||
        message.startsWith("Printables") ||
        message.startsWith("MakerWorld") ||
        message.startsWith("Thingiverse"))
    ) {
      const capture =
        pendingPrintablesCapture || pendingMakerWorldCapture || pendingThingiverseCapture;
      if (!capture) throw new Error("Missing pending capture.");
      renderManualFileSelection(capture);
      showStatus(
        message.startsWith("user_file_required")
          ? safeCaptureMessage(error)
          : capture.source.provider === "makerworld"
            ? makerWorldFailureMessage("contract_changed")
            : capture.source.provider === "thingiverse"
              ? "Choose a downloaded Thingiverse file to attach it in Pending Imports."
              : printablesFailureMessage("contract_changed"),
        "error",
      );
      return;
    }
    const connectionLost = [
      "Couldn't reach PrintStash",
      "connection expired",
      "username or API key is incorrect",
      "browser connection is no longer valid",
    ].some((marker) => message.includes(marker));
    if (connectionLost) {
      accessToken = null;
      editingConnection = false;
      connectionPanel.hidden = false;
      cancelButton.hidden = true;
      renderConnection("error", { detail: "Reconnect PrintStash to continue importing." });
      showStatus();
    } else {
      showStatus(safeCaptureMessage(error), "error", "capture_failed");
    }
  } finally {
    importBusy = false;
    setButtonBusy(captureButton, false);
    if (pendingPrintablesCapture || pendingMakerWorldCapture || pendingThingiverseCapture) {
      captureButton.textContent = "Confirm and upload selected files";
    } else if (pendingManualCapture) {
      captureButton.textContent = "Upload selected file";
    }
    renderImportAvailability();
  }
});

inboxButton.addEventListener("click", () => {
  if (inboxButton.dataset.url) browser.tabs.create({ url: inboxButton.dataset.url });
});

async function initialize() {
  const [stored, tabs] = await Promise.all([
    browser.storage.get(["vault", "username", "apiKey", "deviceCredential"]),
    browser.tabs.query({ active: true, currentWindow: true }),
  ]);
  activePage = tabs[0] || null;
  renderActivePage();
  const prepared = await takePreparedSetup(activePage);
  if (prepared) {
    fillConnectionForm(prepared);
    connectionPanel.hidden = false;
    renderConnection("disconnected", {
      detail: "Setup received from this PrintStash tab.",
    });
    connectButton.textContent = "Finish setup";
    const origins = [permissionOrigin(prepared)];
    const alreadyAllowed = await browser.permissions.contains({ origins }).catch(() => false);
    if (alreadyAllowed) {
      try {
        await establishConnection(prepared, { requestPermission: false, persist: true });
        showStatus("Extension setup completed and connection verified.", "success");
      } catch {
        showStatus();
      }
    } else {
      showStatus("Setup received. Choose Finish setup to approve access to this vault.", "success");
    }
    return;
  }
  const storedConfig: Config = {
    vault: typeof stored.vault === "string" ? stored.vault : "",
    ...(typeof stored.username === "string" ? { username: stored.username } : {}),
    ...(typeof stored.apiKey === "string" ? { apiKey: stored.apiKey } : {}),
    ...(typeof stored.deviceCredential === "string"
      ? { deviceCredential: stored.deviceCredential }
      : {}),
  };
  fillConnectionForm(storedConfig);

  if (
    !storedConfig.vault ||
    (!storedConfig.deviceCredential && (!storedConfig.username || !storedConfig.apiKey))
  ) {
    connectionPanel.hidden = false;
    renderConnection("disconnected");
    return;
  }

  connectionPanel.hidden = true;
  try {
    await establishConnection(storedConfig, { requestPermission: false, persist: false });
  } catch {}
}

initialize().catch((error) => {
  connectionPanel.hidden = false;
  renderConnection("error", { detail: messageFrom(error) });
  showStatus(messageFrom(error), "error");
});
