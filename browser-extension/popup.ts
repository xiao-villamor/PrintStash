import {
  readSourceGalleryInMainWorld,
  readPrintablesGallery,
  sourceGallery,
  downloadSourceImage,
  type SourceGalleryImage,
} from "./source-gallery.ts";
import { captureTransferFetch, progressLabel, type ByteProgress } from "./transfer-progress.ts";
import { collectionCombobox } from "./collection-combobox.ts";
import { captureDestinations, captureTags, selectedCaptureCover } from "./capture-preferences.ts";
import {
  requestCultsInMainWorld,
  cultsCaptureFromPage,
  downloadCultsCandidate,
  CULTS_DOWNLOAD_ORIGINS,
  type CultsPageResult,
} from "./cults-capture.ts";
import {
  readMyMiniFactoryInMainWorld,
  myMiniFactoryCaptureFromPage,
  downloadMyMiniFactoryCandidate,
  MYMINIFACTORY_DOWNLOAD_ORIGINS,
  type MyMiniFactoryPageResult,
} from "./myminifactory-capture.ts";
import {
  requestMakerOnlineInMainWorld,
  makerOnlineCaptureFromPage,
  downloadMakerOnlineCandidate,
  type MakerOnlinePageResult,
} from "./makeronline-capture.ts";
import { profileSummary } from "./print-profile.ts";
import {
  requestCrealityCloudInMainWorld,
  crealityCloudCaptureFromPage,
  downloadCrealityCloudCandidate,
  type CrealityCloudPageResult,
} from "./crealitycloud-capture.ts";
import { MarketplaceDownloadError } from "./marketplace-download.ts";
import {
  ThangsCaptureError,
  requestThangsInMainWorld,
  thangsCaptureFromPage,
  downloadThangsCandidate,
  type ThangsPageResult,
} from "./thangs-capture.ts";
import { type SourceProviderLabel } from "./source-providers.ts";
import { marketplaceOrigin } from "./marketplace-panel.ts";
import { isMarketplacePanelEnabled } from "./panel-registration.ts";
import { launcherButton, positionLauncher } from "./launcher.ts";
import {
  defaultLauncherPreferences,
  launcherPreferences,
  LAUNCHER_CORNERS,
  LAUNCHER_VARIANTS,
  type LauncherStorage,
} from "./launcher-preferences.ts";
import { POPUP_TABS, popupTabs, type PopupTab } from "./popup-tabs.ts";
import { captureSequentialFiles } from "./sequential-capture.ts";
import { captureReviewUrl } from "./capture-result.ts";
import { previousCaptures, captureHistoryLabel } from "./source-history.ts";
import {
  CaptureOperationOwner,
  CaptureRetiredError,
  runCaptureRequest,
  type CaptureOperation,
} from "./capture-operation.ts";
import {
  BROWSER_EXTENSION_SETUP_STORAGE_KEY,
  CaptureAuthenticationError,
  CaptureResponseError,
  PairingValidationError,
  captureUrlForVault,
  captureModelPage,
  claimBrowserPairing,
  classifyModelPage,
  isLocalVault,
  normalizeVault,
  parseBrowserExtensionSetup,
  verifyVaultReachability,
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
  captureDirectFile,
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
  thingiverseFailureMessage,
  thingiverseCaptureFromPage,
  validateThingiverseFiles,
  type ThingiverseFailureCode,
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

const extensionApi = chrome as BrowserExtensionApi & { storage: LauncherStorage };
const browser: BrowserProviderAdapter = createBrowserProviderAdapter(extensionApi);
const launcherStore = launcherPreferences(extensionApi.storage);
const panelMode = new URL(location.href).searchParams.get("panel") === "1";
document.documentElement.toggleAttribute("data-panel", panelMode);

type Page = { id?: number; title?: string; url?: string };
type Source = SourceProviderLabel | "Direct file" | null;
type Config = {
  vault: string;
  pairingCode?: string;
  username?: string;
  apiKey?: string;
  deviceCredential?: string;
  deviceName?: string;
};
type Profile = { username: string; is_superuser: boolean };
type ConnectionState = "checking" | "connected" | "error" | "disconnected";
type VisibleCaptureResult = {
  pageTitle: string;
  challengeDetected?: boolean;
  jsonLd: string[];
};

const vaultInput = requiredElement("#vault", HTMLInputElement);
const pairingMethod = requiredElement("#pairing-method", HTMLInputElement);
const apiKeyMethod = requiredElement("#api-key-method", HTMLInputElement);
const pairingFields = requiredElement("#pairing-fields", HTMLElement);
const apiKeyFields = requiredElement("#api-key-fields", HTMLElement);
const pairingCodeInput = requiredElement("#pairing-code", HTMLInputElement);
const deviceNameError = requiredElement("#device-name-error", HTMLElement);
const deviceNameInput = requiredElement("#device-name", HTMLInputElement);
const usernameInput = requiredElement("#username", HTMLInputElement);
const keyInput = requiredElement("#key", HTMLInputElement);
const runtimeMarker = requiredElement("#runtime-marker", HTMLElement);
const pageLabel = requiredElement("#page", HTMLElement);
const pageHost = requiredElement("#page-host", HTMLElement);
const unsupportedPage = requiredElement("#unsupported-page", HTMLElement);
const pageContext = requiredElement("#page-context", HTMLElement);
const sourceActions = requiredElement("#source-actions", HTMLElement);
const sourceLabel = requiredElement("#source", HTMLElement);
const statusLabel = requiredElement("#status", HTMLElement);
const statusTitle = requiredElement("#status-title", HTMLElement);
const statusTitleText = requiredElement("#status-title-text", HTMLElement);
const statusMessage = requiredElement("#status-message", HTMLElement);
const statusDetails = requiredElement("#status-details", HTMLElement);
const statusCode = requiredElement("#status-code", HTMLElement);
const preferenceFields = requiredElement("#capture-preferences", HTMLFieldSetElement);
const coverPicker = requiredElement("#cover-picker", HTMLElement);
const coverInput = requiredElement("#capture-cover", HTMLInputElement);
const coverFileName = requiredElement("#capture-cover-name", HTMLElement);
const coverError = requiredElement("#cover-error", HTMLElement);
const removeCover = requiredElement("#remove-cover", HTMLButtonElement);
const tagsInput = requiredElement("#capture-tags", HTMLInputElement);
const suggestionOperations = new CaptureOperationOwner();
const collection = collectionCombobox(
  requiredElement("#capture-collection", HTMLInputElement),
  requiredElement("#collection-options", HTMLElement),
  requiredElement("#collection-feedback", HTMLElement),
  searchCollections,
  () => suggestionOperations.retire(),
);
const captureButton = requiredElement("#capture", HTMLButtonElement);
const inboxButton = requiredElement("#open-inbox", HTMLButtonElement);
const importPanel = requiredElement("#import-panel", HTMLElement);
const importHint = requiredElement("#import-hint", HTMLElement);
const candidatePanel = requiredElement("#candidate-panel", HTMLFieldSetElement);
const candidateLegend = requiredElement("#candidate-panel legend", HTMLElement);
const candidateList = requiredElement("#candidate-list", HTMLElement);
const manualFilePanel = requiredElement("#manual-file-panel", HTMLFieldSetElement);
const manualFileInput = requiredElement("#manual-file", HTMLInputElement);
const manualFileName = requiredElement("#manual-file-name", HTMLElement);
const connectionStatus = requiredElement("#connection-status", HTMLElement);
const connectionTitle = requiredElement("#connection-title", HTMLElement);
const connectionDetail = requiredElement("#connection-detail", HTMLElement);
const connectionHostLabel = requiredElement("#connection-host", HTMLElement);
const connectionFeedback = requiredElement("#connection-feedback", HTMLElement);
const connectionPanel = requiredElement("#connection-panel", HTMLElement);
const connectionForm = requiredElement("#connection-form", HTMLFormElement);
const connectButton = requiredElement("#connect", HTMLButtonElement);
const editButton = requiredElement("#edit-connection", HTMLButtonElement);
const cancelButton = requiredElement("#cancel-edit", HTMLButtonElement);
const disconnectButton = requiredElement("#disconnect", HTMLButtonElement);
const captureSetup = requiredElement("#capture-setup", HTMLElement);
const captureEditorHint = requiredElement("#capture-editor-hint", HTMLElement);
const settingsConnection = requiredElement("#settings-connection", HTMLElement);
const connectionOverview = requiredElement("#connection-overview", HTMLElement);
const settingsServer = requiredElement("#settings-server", HTMLElement);
const settingsDevice = requiredElement("#settings-device", HTMLElement);
const settingsEdit = requiredElement("#settings-edit-connection", HTMLButtonElement);
const historyMessage = requiredElement("#history-message", HTMLElement);
const historyList = requiredElement("#history-list", HTMLElement);
const launcherEnabled = requiredElement("#launcher-enabled", HTMLInputElement);
const launcherPreview = requiredElement("#launcher-preview", HTMLElement);
const launcherSite = requiredElement("#launcher-site", HTMLButtonElement);
const launcherError = requiredElement("#launcher-settings-error", HTMLElement);
const apiSettingsButton = requiredElement("#open-api-settings", HTMLButtonElement);

runtimeMarker.textContent = `Version ${browser.runtime.getManifest().version}`;

const tabs = popupTabs(
  {
    capture: requiredElement("#tab-capture", HTMLButtonElement),
    history: requiredElement("#tab-history", HTMLButtonElement),
    settings: requiredElement("#tab-settings", HTMLButtonElement),
  },
  {
    capture: requiredElement("#view-capture", HTMLElement),
    history: requiredElement("#view-history", HTMLElement),
    settings: requiredElement("#view-settings", HTMLElement),
  },
  (tab, persist) => {
    activeTab = tab;
    if (persist) {
      tabTouched = true;
      void browser.storage.set({ popupTab: tab }).catch(() => {});
    }
    renderTabContent();
    if (tab === "history") void refreshCaptureHistory();
  },
);

let activeTab: PopupTab = "capture";
let tabTouched = false;
let tabBeforeEdit: PopupTab = "capture";
let historyRequest = 0;
let launcher = defaultLauncherPreferences();
let permittedSite: string | null = null;
let activePage: Page | null = null;
let activeSource: Source = null;
let connectionState = "checking";
let connectedConfig: Config | null = null;
let connectedProfile: Profile | null = null;
let accessToken: string | null = null;
let editingConnection = false;
// Only one connection transition owns permission cleanup or storage publication.
// Cancel retires verification; the lock remains until its cleanup finishes.
type ConnectionAttempt = { controller: AbortController; phase: "verifying" | "publishing" };
let connectionAttempt: ConnectionAttempt | null = null;
let connectionLocked = false;

function lockConnection(locked: boolean) {
  connectionLocked = locked;
  setConnectionFormBusy(locked);
  editButton.disabled = locked;
  disconnectButton.disabled = locked;
  cancelButton.disabled = locked;
}
let importBusy = false;
const captureOperations = new CaptureOperationOwner();
let pendingPrintablesCapture: BrowserCaptureMessage | null = null;
let pendingMakerWorldCapture: BrowserCaptureMessage | null = null;
let pendingThingiverseCapture: BrowserCaptureMessage | null = null;
let pendingMarketplaceCapture: BrowserCaptureMessage | null = null;
let pendingThingiverseLinks = new Map<string, string>();
let pendingGallery: SourceGalleryImage[] = [];
let galleryUnavailable = false;
let gallerySelection: HTMLFieldSetElement | null = null;
type ManualCapture =
  | { kind: "publication"; draft: BrowserCaptureMessage }
  | { kind: "direct"; url: string };
let pendingManualCapture: ManualCapture | null = null;

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
  | "thingiverse_links_failed"
  | "capture_download_failed"
  | "capture_download_timeout"
  | "capture_vault_slot_create_failed"
  | "capture_vault_slot_create_timeout"
  | "capture_vault_slot_upload_failed"
  | "capture_vault_slot_upload_timeout"
  | "capture_vault_finalize_failed"
  | "capture_vault_finalize_timeout"
  | "capture_failed";

type DiagnosticProvider = "Printables" | "MakerWorld" | "Thingiverse";
type DiagnosticFallbackCode =
  | PrintablesFailureCode
  | MakerWorldFailureCode
  | ThingiverseFailureCode;

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

function captureTimeoutMs(defaultMs = DEFAULT_CAPTURE_TIMEOUT_MS) {
  const configured = globalThis.__PRINTSTASH_CAPTURE_TIMEOUT_MS__;
  return typeof configured === "number" && Number.isFinite(configured) && configured > 0
    ? configured
    : defaultMs;
}

async function runCaptureStage<T>(
  operation: (signal: AbortSignal) => Promise<T>,
  timeoutCode: DiagnosticCode,
  failureCode: DiagnosticCode,
  safeMessage: string,
  provider?: DiagnosticProvider,
  fallbackCode?: DiagnosticFallbackCode,
  captureSignal?: AbortSignal,
  timeoutMs = captureTimeoutMs(),
): Promise<T> {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  const operationResult = runCaptureRequest([controller.signal, captureSignal], operation);
  try {
    return await new Promise<T>((resolve, reject) => {
      timer = setTimeout(() => {
        controller.abort();
        reject(new CaptureDiagnosticError(timeoutCode, safeMessage, provider, fallbackCode));
      }, timeoutMs);
      operationResult.then(resolve, reject);
    });
  } catch (error) {
    if (
      error instanceof CaptureDiagnosticError ||
      error instanceof CaptureRetiredError ||
      error instanceof CaptureAuthenticationError ||
      (error instanceof CaptureResponseError && error.diagnosticCode !== undefined)
    )
      throw error;
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
  if (
    error instanceof CaptureResponseError ||
    error instanceof ThangsCaptureError ||
    error instanceof MarketplaceDownloadError
  )
    return error.message;
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

function vaultStage(capture: CaptureOperation<Config>): CaptureStageRunner {
  return (stage: CaptureUploadStage, operation) => {
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
      undefined,
      undefined,
      capture.signal,
    );
  };
}

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
    statusTitleText.textContent = "";
    statusMessage.textContent = "";
    statusDetails.hidden = true;
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
  statusTitleText.textContent = presentation.title ?? "";
  statusTitle.hidden = kind !== "success";
  statusMessage.textContent = kind === "success" ? "" : presentation.message;
  statusMessage.hidden = kind === "success";
  statusDetails.hidden = !presentation.technicalCode;
  statusCode.textContent = presentation.technicalCode ?? "";
  if (kind) statusLabel.dataset.kind = kind;
  else delete statusLabel.dataset.kind;
}

function showDiagnosticStatus(error: unknown, fallbackMessage?: string) {
  const diagnostic = diagnosticStatus(error, fallbackMessage);
  showStatus(diagnostic.message, "error", diagnostic.diagnosticCode, diagnostic.providerCode);
}

function renderFileName(input: HTMLInputElement, display: HTMLElement) {
  const file = input.files?.[0];
  display.textContent = file ? file.name : "No file selected";
  display.title = file ? file.name : "";
}

function renderCover() {
  coverError.hidden = true;
  coverInput.removeAttribute("aria-invalid");
  try {
    selectedCaptureCover(coverInput.files?.[0]);
  } catch (error) {
    coverError.textContent = messageFrom(error);
    coverError.hidden = false;
    coverInput.setAttribute("aria-invalid", "true");
  }
  renderFileName(coverInput, coverFileName);
  const hasCover = Boolean(coverInput.files?.length);
  coverFileName.hidden = !hasCover;
  removeCover.hidden = !hasCover;
}

function retireCapture() {
  captureOperations.retire();
  collection.reset();
  tagsInput.value = "";
  coverInput.value = "";
  renderCover();
  importBusy = false;
  setButtonBusy(captureButton, false);
  clearCandidateSelection();
  clearManualFileSelection();
}

function recoverCaptureConnection() {
  retireCapture();
  clearInboxAction();
  accessToken = null;
  editingConnection = false;
  connectionPanel.hidden = false;
  cancelButton.hidden = true;
  renderConnection("error", { detail: "Reconnect PrintStash to continue importing." });
  showStatus();
}

function clearInboxAction() {
  inboxButton.hidden = true;
  delete inboxButton.dataset.url;
}

function clearCandidateSelection() {
  pendingGallery = [];
  galleryUnavailable = false;
  gallerySelection?.remove();
  gallerySelection = null;
  pendingMarketplaceCapture = null;
  pendingPrintablesCapture = null;
  pendingMakerWorldCapture = null;
  pendingThingiverseCapture = null;
  pendingThingiverseLinks.clear();
  candidatePanel.hidden = true;
  candidateList.replaceChildren();
  captureButton.textContent = "Send to PrintStash";
}

function clearManualFileSelection() {
  pendingManualCapture = null;
  manualFileInput.value = "";
  renderFileName(manualFileInput, manualFileName);
  manualFilePanel.hidden = true;
  captureButton.textContent = "Send to PrintStash";
}

function confirmCapture(url: string) {
  inboxButton.dataset.url = url;
  inboxButton.hidden = false;
  showStatus("Sent to Pending Imports", "success");
  renderImportAvailability();
  inboxButton.focus();
}

function renderManualFileSelection(draft: BrowserCaptureMessage) {
  offerManualFile({ kind: "publication", draft });
}

function offerManualFile(capture: ManualCapture) {
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

function selectedManualFile(sourceItemId: string | null): BrowserCaptureFile {
  const file = manualFileInput.files?.item(0);
  if (!file) throw new Error("Choose a downloaded model file before uploading.");
  return {
    id: stableCaptureFileId(sourceItemId, file.name),
    file,
    filename: file.name,
    mediaType: file.type || "application/octet-stream",
  };
}

function renderCandidateSelection(capture: BrowserCaptureMessage) {
  pendingMarketplaceCapture = null;
  if (capture.source.provider === "makerworld") {
    pendingMakerWorldCapture = capture;
    pendingPrintablesCapture = null;
    pendingThingiverseCapture = null;
  } else if (capture.source.provider === "thingiverse") {
    pendingThingiverseCapture = capture;
    pendingMakerWorldCapture = null;
    pendingPrintablesCapture = null;
  } else if (capture.source.provider === "printables") {
    pendingPrintablesCapture = capture;
    pendingMakerWorldCapture = null;
    pendingThingiverseCapture = null;
  }
  if (!["makerworld", "printables", "thingiverse"].includes(capture.source.provider)) {
    pendingMarketplaceCapture = capture;
    pendingMakerWorldCapture = null;
    pendingPrintablesCapture = null;
    pendingThingiverseCapture = null;
  }
  candidateLegend.textContent =
    capture.source.provider === "makerworld"
      ? "Select MakerWorld packages"
      : capture.source.provider === "thingiverse"
        ? "Select Thingiverse files"
        : `Select ${activeSource} files`;
  candidateList.replaceChildren();
  capture.candidates.forEach((candidate, index) => {
    const label = document.createElement("label");
    label.className = "candidate-option";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = capture.source.provider !== "makerworld";
    input.dataset.candidateIndex = String(index);
    const copy = document.createElement("span");
    copy.className = "candidate-copy";
    const filename = document.createElement("span");
    filename.className = "candidate-filename";
    filename.textContent = candidate.filename;
    filename.title = candidate.filename;
    copy.append(filename);
    if (candidate.profile) {
      const facts = document.createElement("small");
      facts.className = "candidate-facts";
      facts.textContent = profileSummary(candidate.profile);
      facts.title = facts.textContent;
      copy.append(facts);
    }
    label.append(input, copy);
    candidateList.append(label);
  });
  gallerySelection?.remove();
  gallerySelection = document.createElement("fieldset");
  const legend = document.createElement("legend");
  legend.textContent = `Publication gallery (${pendingGallery.length} images)`;
  legend.hidden = pendingGallery.length === 0;
  gallerySelection.append(legend);
  for (const [index, image] of pendingGallery.entries()) {
    const label = document.createElement("label");
    label.className = "candidate-option";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = true;
    input.dataset.imageId = image.id;
    label.append(input, `Image ${index + 1}${index === 0 ? " · default cover" : ""}`);
    gallerySelection.append(label);
  }
  const note = document.createElement("small");
  note.textContent = galleryUnavailable
    ? "Gallery unavailable. Reopen the page to retry, or add images later."
    : pendingGallery.length
      ? "GIFs are saved as still images. Change the cover in PrintStash."
      : "No gallery images. You can add a cover below.";
  gallerySelection.append(note);
  candidatePanel.after(gallerySelection);
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
  if (pairingMethod.checked) {
    const pairingCode = pairingCodeInput.value.trim();
    if (!pairingCode) throw new Error("Enter a pairing code.");
    if (!deviceNameInput.value.trim()) throw new Error("Enter a name for this browser.");
    return { vault, pairingCode, deviceName: deviceNameInput.value.trim() };
  }
  const username = usernameInput.value.trim();
  const apiKey = keyInput.value.trim();
  if (!username || !apiKey) throw new Error("Enter your username and named API key.");
  return { vault, username, apiKey };
}

function defaultBrowserName({ userAgent, platform }: Navigator) {
  const browserName = /Edg(?:A|iOS)?\//.test(userAgent)
    ? "Edge"
    : /Firefox|FxiOS/.test(userAgent)
      ? "Firefox"
      : /Chromium/.test(userAgent)
        ? "Chromium"
        : /Chrome|CriOS/.test(userAgent)
          ? "Chrome"
          : "Browser";
  const platformName = [
    [/CrOS/, "ChromeOS"],
    [/Android/, "Android"],
    [/iPhone|iPad|iPod/, "iOS"],
    [/Win/, "Windows"],
    [/Mac/, "macOS"],
    [/Linux/, "Linux"],
  ] as const;
  const os = platformName.find(([pattern]) => pattern.test(`${userAgent} ${platform}`))?.[1];
  return os ? `${browserName} on ${os}` : browserName;
}

const preferredBrowserName = defaultBrowserName(navigator);
deviceNameInput.value = preferredBrowserName;

function clearPairingError() {
  deviceNameError.hidden = true;
  deviceNameInput.removeAttribute("aria-invalid");
  pairingCodeInput.removeAttribute("aria-invalid");
}

function renderCredentialFields() {
  clearPairingError();
  pairingFields.hidden = apiKeyMethod.checked;
  apiKeyFields.hidden = pairingMethod.checked;
  connectButton.textContent = connectionActionLabel();
}

function connectionActionLabel() {
  if (editingConnection) return "Update connection";
  return pairingMethod.checked ? "Pair browser" : "Connect with API key";
}

function fillConnectionForm(config: Config | null) {
  vaultInput.value = config?.vault || "";
  deviceNameInput.value = config?.deviceName ?? preferredBrowserName;
  pairingCodeInput.value = "";
  usernameInput.value = config?.username || "";
  keyInput.value = config?.apiKey || "";
  const method = config?.username && config.apiKey ? apiKeyMethod : pairingMethod;
  method.checked = true;
  renderCredentialFields();
}

function permissionOrigin(config: Config) {
  const vault = new URL(config.vault);
  return isLocalVault(config.vault)
    ? `${vault.protocol}//${vault.hostname}/*`
    : `${vault.origin}/*`;
}

function hasBuiltInVaultPermission(config: Config) {
  const vault = new URL(config.vault);
  // These exact HTTP hosts are required by the extension manifest. Other
  // local addresses still use optional grants that Disconnect must remove.
  return vault.protocol === "http:" && ["localhost", "127.0.0.1", "[::1]"].includes(vault.hostname);
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
  const hasReceipt = Boolean(inboxButton.dataset.url);
  const canCapture = Boolean(readyConnection && activeSource && !hasReceipt);
  const needsDirectFile = pendingManualCapture?.kind === "direct";
  importPanel.hidden = !readyConnection || hasReceipt;
  pageContext.hidden = !canCapture;
  unsupportedPage.hidden = !readyConnection || Boolean(activeSource);
  preferenceFields.hidden = !canCapture || needsDirectFile;
  preferenceFields.disabled = importBusy || !canCapture;
  coverPicker.hidden = activeSource === "Direct file";
  captureButton.hidden = !canCapture;
  captureButton.disabled = importBusy || !canCapture;
  importHint.hidden = !importBusy && (!pendingManualCapture || needsDirectFile);
  if (importBusy) {
    importHint.textContent = panelMode
      ? "Sending to your review inbox… You can hide this panel; keep the source tab open."
      : "Sending to your review inbox… Keep this popup open until it finishes.";
  } else if (pendingManualCapture) {
    importHint.textContent = "Automatic download was unavailable.";
  }
}

function renderTabContent() {
  const showOverview = connectionState === "connected" && !editingConnection;
  const showSetupInCapture =
    activeTab === "capture" && connectionState !== "connected" && !editingConnection;
  const target = showSetupInCapture ? captureSetup : settingsConnection;
  if (connectionPanel.parentElement !== target) target.append(connectionPanel);
  connectionOverview.hidden = !showOverview;
  editButton.hidden = !showOverview || activeTab === "settings";
  captureEditorHint.hidden = !editingConnection;
  if (showOverview && connectedConfig) {
    settingsServer.textContent = connectedConfig.vault;
    settingsDevice.textContent = connectedConfig.deviceName ?? preferredBrowserName;
  }
  renderImportAvailability();
}

function renderConnection(
  state: ConnectionState,
  { config, profile, detail }: { config?: Config; profile?: Profile | null; detail?: string } = {},
) {
  connectionState = state;
  connectionStatus.dataset.state = state;

  if (state === "connected") {
    if (!config) throw new Error("Connected state requires a configuration.");
    connectionTitle.textContent = "Connected";
    const identity =
      profile?.username || (config.deviceCredential ? "Paired browser" : config.username);
    const role = profile ? ` · ${profile.is_superuser ? "Admin" : "Member"}` : "";
    connectionDetail.textContent = `${identity} · ${connectionHost(config)}${role}`;
  } else if (state === "checking") {
    connectionTitle.textContent = "Checking…";
    connectionDetail.textContent =
      detail || (config ? connectionHost(config) : "Reading saved settings");
  } else if (state === "error") {
    connectionTitle.textContent = "Connection problem";
    connectionDetail.textContent = detail || "Review the URL and credentials below.";
  } else {
    connectionTitle.textContent = "Not connected";
    connectionDetail.textContent = detail || "Connect a PrintStash server to start importing.";
  }
  connectionStatus.title = connectionDetail.textContent;
  connectionHostLabel.hidden = !config;
  connectionHostLabel.textContent = config ? connectionHost(config) : "";
  connectionHostLabel.title = connectionHostLabel.textContent;
  connectionFeedback.hidden = state !== "error";
  connectionFeedback.textContent = state === "error" ? connectionDetail.textContent : "";

  const showConnectedActions = state === "connected";
  disconnectButton.hidden = !showConnectedActions || !editingConnection;
  renderTabContent();
  if (activeTab === "history") void refreshCaptureHistory();
}

function renderActivePage() {
  const classified = activePage?.url ? classifyModelPage(activePage.url) : null;
  activeSource =
    classified === "Printables" ||
    classified === "MakerWorld" ||
    classified === "Thingiverse" ||
    classified === "Cults" ||
    classified === "MyMiniFactory" ||
    classified === "Thangs" ||
    classified === "Creality Cloud" ||
    classified === "MakerOnline" ||
    classified === "Direct file"
      ? classified
      : null;
  pageHost.hidden = true;
  if (activeSource === "Direct file" && activePage?.url) {
    const url = new URL(activePage.url);
    const filename = url.pathname.split("/").at(-1) ?? "";
    try {
      pageLabel.textContent = decodeURIComponent(filename);
    } catch {
      pageLabel.textContent = filename;
    }
    pageHost.textContent = url.host;
    pageHost.hidden = false;
  } else {
    pageLabel.textContent = activePage?.title || activePage?.url || "No active page";
  }
  pageLabel.title = pageLabel.textContent;
  sourceLabel.textContent = activeSource ? `${activeSource} · Supported` : "Unsupported source";
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
        ? "Permission to contact this PrintStash server was not granted."
        : "Reconnect this server to restore its browser permission.",
    );
  }
}

async function establishConnection(
  config: Config,
  { requestPermission, persist }: { requestPermission: boolean; persist: boolean },
) {
  if (connectionLocked) return false;
  const attempt: ConnectionAttempt = { controller: new AbortController(), phase: "verifying" };
  connectionAttempt = attempt;
  const signal = attempt.controller.signal;
  const fetchImpl: typeof fetch = async (input, options) => {
    signal.throwIfAborted();
    const result = await fetch(input, { ...options, signal });
    signal.throwIfAborted();
    return result;
  };
  const previous = connectedConfig
    ? { config: connectedConfig, profile: connectedProfile, token: accessToken }
    : null;
  const preservePrevious = Boolean(previous && editingConnection);
  retireCapture();
  renderConnection("checking", { config });
  lockConnection(true);
  cancelButton.disabled = !preservePrevious;
  showStatus();
  try {
    await ensureVaultPermission(config, requestPermission);
    signal.throwIfAborted();
    let verified;
    let normalized;
    if (config.pairingCode) {
      verified = await claimBrowserPairing({
        fetchImpl,
        vault: config.vault,
        code: config.pairingCode,
        name: config.deviceName,
      });
      normalized = {
        vault: verified.base,
        deviceCredential: verified.deviceCredential,
        deviceName: config.deviceName,
      };
    } else if (config.deviceCredential) {
      verified = await verifyVaultReachability({ vault: config.vault, fetchImpl });
      normalized = { ...config, vault: verified.base };
    } else {
      verified = await verifyVaultConnection({
        fetchImpl,
        vault: config.vault,
        username: config.username || "",
        apiKey: config.apiKey || "",
      });
      normalized = {
        vault: verified.base,
        username: config.username || "",
        apiKey: config.apiKey || "",
      };
    }

    // Parsing the response body is asynchronous too; retirement is checked
    // again at the publication boundary, after every verification response.
    signal.throwIfAborted();
    attempt.phase = "publishing";
    cancelButton.disabled = true;
    if (persist) {
      const credentials: Config = normalized;
      // One set publishes the vault and its credential kind together. Nulls
      // erase inactive secrets; initialization only accepts string credentials.
      await browser.storage.set({
        vault: credentials.vault,
        username: credentials.username ?? null,
        apiKey: credentials.apiKey ?? null,
        deviceCredential: credentials.deviceCredential ?? null,
        ...(credentials.deviceName || previous?.config.deviceName
          ? { deviceName: credentials.deviceName ?? null }
          : {}),
      });
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
    const wasEditing = editingConnection;
    editingConnection = false;
    connectionPanel.hidden = true;
    cancelButton.hidden = true;
    disconnectButton.hidden = true;
    fillConnectionForm(normalized);
    renderConnection("connected", { config: normalized, profile: verifiedConnection.user });
    if (wasEditing) tabs.select(tabBeforeEdit);
    return true;
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
    if (signal.aborted) return false;
    if (preservePrevious && previous) {
      connectedConfig = previous.config;
      connectedProfile = previous.profile;
      accessToken = previous.token;
      renderConnection("connected", { config: previous.config, profile: previous.profile });
    } else {
      connectedConfig = null;
      connectedProfile = null;
      accessToken = null;
      renderConnection(error instanceof PairingValidationError ? "disconnected" : "error", {
        detail: messageFrom(error),
      });
    }
    connectionPanel.hidden = false;
    throw error;
  } finally {
    connectionAttempt = null;
    lockConnection(false);
    connectButton.textContent = connectionActionLabel();
  }
}

async function ensureOriginPermission(origin: string) {
  if (await browser.permissions.contains({ origins: [origin] })) return;
  try {
    const granted = await browser.permissions.request({ origins: [origin] });
    if (granted) return;
  } catch {
    throw new MarketplaceDownloadError(
      "The browser could not request access to the download host. Click import again to grant access.",
    );
  }
  throw new MarketplaceDownloadError(
    "Permission to download the selected source file was not granted. Click import again to grant access.",
  );
}

async function ensureOriginPermissions(origins: string[]) {
  if (await browser.permissions.contains({ origins })) return;
  try {
    if (await browser.permissions.request({ origins })) return;
  } catch {
    throw new MarketplaceDownloadError(
      "The browser could not request file access. Click import again to grant access.",
    );
  }
  throw new MarketplaceDownloadError(
    "Permission to download the selected source files was not granted. Click import again to grant access.",
  );
}

async function ensurePrintablesFilePermissions(
  links: readonly string[],
  captureSignal: AbortSignal,
) {
  const origins = [...new Set(links.map((link) => `${new URL(link).origin}/*`))];
  const alreadyGranted = await runCaptureStage(
    () => browser.permissions.contains({ origins }),
    "capture_permission_contains_timeout",
    "capture_permission_contains_timeout",
    "Permission check for Printables files timed out. Attach downloaded files in Pending Imports.",
    "Printables",
    "cors_failure",
    captureSignal,
  );
  if (alreadyGranted) return;
  const granted = await runCaptureStage(
    () => browser.permissions.request({ origins }),
    "capture_permission_request_timeout",
    "capture_permission_request_timeout",
    "Permission request for Printables files timed out. Attach downloaded files in Pending Imports.",
    "Printables",
    "cors_failure",
    captureSignal,
    captureTimeoutMs(120_000),
  );
  if (!granted) {
    throw new CaptureDiagnosticError(
      "capture_permission_denied",
      "user_file_required: Permission to download the selected Printables files was not granted. Attach downloaded files in Pending Imports.",
      "Printables",
      "cors_failure",
    );
  }
}

async function ensureMetadataPermission(
  origin: string,
  provider: "Printables" | "MakerWorld",
  captureSignal: AbortSignal,
) {
  const origins = [origin];
  const alreadyGranted = await runCaptureStage(
    () => browser.permissions.contains({ origins }),
    "capture_permission_contains_timeout",
    "capture_permission_contains_timeout",
    `Permission check for ${provider} metadata timed out. Choose a downloaded ${provider} file to attach it in Pending Imports.`,
    provider,
    "cors_failure",
    captureSignal,
  );
  if (alreadyGranted) return;
  const granted = await runCaptureStage(
    () => browser.permissions.request({ origins }),
    "capture_permission_request_timeout",
    "capture_permission_request_timeout",
    `Permission request for ${provider} metadata timed out. Choose a downloaded ${provider} file to attach it in Pending Imports.`,
    provider,
    "cors_failure",
    captureSignal,
    captureTimeoutMs(120_000),
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
  progress?: ByteProgress,
): Promise<BrowserCaptureFile> {
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
  const file = await readBoundedPrintablesResponse(response, candidate.sizeBytes, signal, progress);
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
  captureSignal: AbortSignal,
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
    captureSignal,
  );
}

async function resolveMakerWorldLinks(
  capture: BrowserCaptureMessage,
  selected: readonly BrowserCaptureMessage["candidates"][number][],
  captureSignal: AbortSignal,
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
    captureSignal,
  );
}

function isRichProvider(source: Source): source is Exclude<Source, "Direct file" | null> {
  return (
    source === "Printables" ||
    source === "MakerWorld" ||
    source === "Thingiverse" ||
    source === "Cults" ||
    source === "MyMiniFactory" ||
    source === "Thangs" ||
    source === "Creality Cloud" ||
    source === "MakerOnline"
  );
}
async function readVisibleCapture(
  captureSignal: AbortSignal,
): Promise<BrowserCaptureMessage | null> {
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
      undefined,
      undefined,
      captureSignal,
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
        captureSignal,
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
      const fileResults = await runCaptureRequest([captureSignal], () =>
        browser.scripting.executeScript({
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
        }),
      );
      const fileResult = fileResults[0]?.result as ThingiverseFilesPageResult | undefined;
      if (!fileResult?.ok) {
        const failureCode = fileResult?.reason || fileResult?.code || "request_failed";
        throw new CaptureDiagnosticError(
          "thingiverse_links_failed",
          thingiverseFailureMessage(failureCode),
          "Thingiverse",
          failureCode,
          visible.jsonLd,
        );
      }
      let files: ReturnType<typeof validateThingiverseFiles>;
      try {
        files = validateThingiverseFiles(fileResult);
      } catch {
        throw new CaptureDiagnosticError(
          "thingiverse_links_failed",
          thingiverseFailureMessage("contract_changed"),
          "Thingiverse",
          "contract_changed",
          visible.jsonLd,
        );
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
      return thingiverseCapture;
    }
    if (provider === "Cults") {
      const publicationId = capture.source.source_item_id;
      if (!publicationId) return capture;
      const results = await browser.scripting.executeScript({
        target: { tabId },
        world: "MAIN",
        func: requestCultsInMainWorld,
        args: [{ publicationId, operation: "metadata" }],
      });
      const result = results[0]?.result as CultsPageResult | undefined;
      return result ? cultsCaptureFromPage(pageUrl, result) : capture;
    }
    if (provider === "MyMiniFactory") {
      const publicationId = capture.source.source_item_id;
      if (!publicationId) return capture;
      const results = await browser.scripting.executeScript({
        target: { tabId },
        world: "MAIN",
        func: readMyMiniFactoryInMainWorld,
        args: [{ publicationId }],
      });
      const result = results[0]?.result as MyMiniFactoryPageResult | undefined;
      return result ? myMiniFactoryCaptureFromPage(pageUrl, result) : capture;
    }
    if (provider === "MakerOnline") {
      const publicationId = capture.source.source_item_id;
      if (!publicationId) return capture;
      const results = await browser.scripting.executeScript({
        target: { tabId },
        world: "MAIN",
        func: requestMakerOnlineInMainWorld,
        args: [{ publicationId, operation: "metadata" }],
      });
      const result = results[0]?.result as MakerOnlinePageResult | undefined;
      return result ? makerOnlineCaptureFromPage(pageUrl, result) : capture;
    }
    if (provider === "Creality Cloud") {
      const publicationId = capture.source.source_item_id;
      if (!publicationId) return capture;
      const results = await browser.scripting.executeScript({
        target: { tabId },
        world: "MAIN",
        func: requestCrealityCloudInMainWorld,
        args: [{ publicationId, operation: "metadata" }],
      });
      const result = results[0]?.result as CrealityCloudPageResult | undefined;
      return result ? crealityCloudCaptureFromPage(pageUrl, result) : capture;
    }
    if (provider === "Thangs") {
      const modelId = capture.source.source_item_id;
      if (!modelId) return capture;
      const results = await browser.scripting.executeScript({
        target: { tabId },
        world: "MAIN",
        func: requestThangsInMainWorld,
        args: [{ modelId, operation: "metadata" }],
      });
      const result = results[0]?.result as ThangsPageResult | undefined;
      return result ? thangsCaptureFromPage(pageUrl, result) : capture;
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
      captureSignal,
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
    if (error instanceof CaptureDiagnosticError || error instanceof CaptureRetiredError)
      throw error;
    return fallbackVisibleCapture("request_failed");
  }
}

function fallbackVisibleCapture(
  code?: PrintablesFailureCode | MakerWorldFailureCode | ThingiverseFailureCode,
  jsonLd: string[] = [],
) {
  if (
    !activePage?.url ||
    (activeSource !== "Printables" &&
      activeSource !== "MakerWorld" &&
      activeSource !== "Thingiverse")
  )
    return null;
  if (activeSource === "Thingiverse") {
    return thingiverseCaptureFromPage({
      pageUrl: activePage.url,
      pageTitle: activePage.title,
      jsonLd,
      files: [],
    });
  }
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

pairingMethod.addEventListener("change", renderCredentialFields);
apiKeyMethod.addEventListener("change", renderCredentialFields);
manualFileInput.addEventListener("change", () => {
  renderFileName(manualFileInput, manualFileName);
  renderImportAvailability();
});
coverInput.addEventListener("change", renderCover);
removeCover.addEventListener("click", () => {
  coverInput.value = "";
  renderCover();
  coverInput.focus();
});
deviceNameInput.addEventListener("input", clearPairingError);
pairingCodeInput.addEventListener("input", clearPairingError);

function showConnectionFeedback(message: string) {
  connectionFeedback.textContent = message;
  connectionFeedback.hidden = false;
}

connectionForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (connectionLocked) return;
  clearPairingError();
  connectionFeedback.hidden = true;
  let config: Config;
  try {
    config = configFromForm();
  } catch (error) {
    showConnectionFeedback(messageFrom(error));
    return;
  }
  try {
    if (await establishConnection(config, { requestPermission: true, persist: true })) {
      showStatus("Connection verified. This browser is ready to import.");
    }
  } catch (error) {
    if (error instanceof PairingValidationError) {
      const field = error.field === "name" ? deviceNameInput : pairingCodeInput;
      field.setAttribute("aria-invalid", "true");
      if (error.field === "name") {
        deviceNameError.textContent = error.message;
        deviceNameError.hidden = false;
      } else {
        showConnectionFeedback(error.message);
      }
      field.focus();
    } else if (connectionState === "connected") {
      showStatus(`Connection was not updated. ${messageFrom(error)}`, "error");
    }
  }
});

function openConnectionEditor() {
  if (connectionLocked) return;
  tabBeforeEdit = activeTab;
  editingConnection = true;
  collection.close();
  clearInboxAction();
  connectionPanel.hidden = false;
  editButton.hidden = true;
  cancelButton.hidden = false;
  disconnectButton.hidden = false;
  connectButton.textContent = connectionActionLabel();
  tabs.select("settings");
  showStatus();
  vaultInput.focus();
}

editButton.addEventListener("click", openConnectionEditor);
settingsEdit.addEventListener("click", openConnectionEditor);

cancelButton.addEventListener("click", () => {
  if (connectionLocked && connectionAttempt?.phase !== "verifying") return;
  connectionAttempt?.controller.abort();
  editingConnection = false;
  fillConnectionForm(connectedConfig);
  connectionPanel.hidden = Boolean(connectedConfig);
  cancelButton.hidden = true;
  disconnectButton.hidden = true;
  renderConnection("connected", {
    config: connectedConfig ?? undefined,
    profile: connectedProfile,
  });
  tabs.select(tabBeforeEdit);
  showStatus();
});

disconnectButton.addEventListener("click", async () => {
  if (connectionLocked) return;
  lockConnection(true);
  const previous = connectedConfig;
  retireCapture();
  const builtInPermission = previous ? hasBuiltInVaultPermission(previous) : false;
  try {
    await browser.storage.remove(["apiKey", "username", "deviceCredential", "deviceName"]);
  } catch (error) {
    showStatus(`Couldn't remove the stored browser credential. ${messageFrom(error)}`, "error");
    lockConnection(false);
    return;
  }

  let permissionStillGranted = false;
  if (previous && !builtInPermission) {
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
  fillConnectionForm({ vault: vaultInput.value });
  clearInboxAction();
  clearCandidateSelection();
  clearManualFileSelection();
  connectionPanel.hidden = false;
  cancelButton.hidden = true;
  disconnectButton.hidden = true;
  renderConnection("disconnected");
  lockConnection(false);
  showStatus(
    permissionStillGranted
      ? "Disconnected and removed the stored browser credential, but Chrome kept the server permission. Remove it from the extension's site access settings."
      : builtInPermission
        ? "Disconnected. The stored browser credential was removed; built-in loopback access contains no credentials."
        : "Disconnected. The stored browser credential and server permission were removed from this browser.",
    permissionStillGranted ? "error" : "",
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

async function searchCollections(query: string) {
  const authorization = accessToken || connectedConfig?.deviceCredential;
  if (importBusy || !connectedConfig || !authorization) throw new CaptureRetiredError();
  const operation = suggestionOperations.begin(connectedConfig, authorization);
  try {
    return await operation.run((signal) =>
      captureDestinations({ vault: operation.config.vault, authorization, query, signal }),
    );
  } catch (error) {
    if (!suggestionOperations.isCurrent(operation)) throw new CaptureRetiredError();
    if (error instanceof CaptureAuthenticationError) {
      recoverCaptureConnection();
      throw new CaptureRetiredError();
    }
    throw error;
  } finally {
    suggestionOperations.finish(operation);
  }
}

captureButton.addEventListener("click", async () => {
  if (!connectedConfig || connectionState !== "connected") {
    showStatus("Connect PrintStash before importing.", "error");
    return;
  }
  if (captureButton.disabled) return;
  if (!coverError.hidden) {
    coverInput.focus();
    return;
  }
  const config = { ...connectedConfig };
  const authorization = accessToken || config.deviceCredential;
  if (!authorization) {
    showStatus("The browser connection expired. Connect PrintStash again.", "error");
    return;
  }
  collection.close();
  const capture = captureOperations.begin(config, authorization);
  const runStage = vaultStage(capture);
  importBusy = true;
  setButtonBusy(captureButton, true, "Sending…");
  renderImportAvailability();
  showStatus();
  try {
    const transferFetch = captureTransferFetch((loaded, total) => {
      capture.assertCurrent();
      showStatus(
        `Transferring to PrintStash: ${progressLabel(loaded, total)}${total !== null && loaded === total ? " · awaiting confirmation" : ""}`,
      );
    });
    const preferences = {
      collectionId: collection.id,
      tags: captureTags(tagsInput.value),
      cover: selectedCaptureCover(coverInput.files?.[0]),
    };
    const selectedImages = pendingGallery.filter(
      (image) =>
        gallerySelection?.querySelector<HTMLInputElement>(`input[data-image-id="${image.id}"]`)
          ?.checked,
    );
    const downloadOrigins = [
      ...new Set(selectedImages.map((image) => `${new URL(image.url).origin}/*`)),
    ];
    if (pendingMarketplaceCapture?.source.provider === "cults")
      downloadOrigins.push(...CULTS_DOWNLOAD_ORIGINS);
    if (pendingMarketplaceCapture?.source.provider === "myminifactory")
      downloadOrigins.push(...MYMINIFACTORY_DOWNLOAD_ORIGINS);
    if (pendingMarketplaceCapture?.source.provider === "makeronline")
      downloadOrigins.push("https://acop-prod-private.s3-accelerate.amazonaws.com/*");
    if (downloadOrigins.length) await ensureOriginPermissions([...new Set(downloadOrigins)]);
    capture.assertCurrent();
    // Ask while the import click still carries user activation, before provider verification.
    if (pendingMarketplaceCapture?.source.provider === "cults") {
      await ensureOriginPermissions(CULTS_DOWNLOAD_ORIGINS);
      capture.assertCurrent();
    }
    if (pendingMarketplaceCapture?.source.provider === "myminifactory") {
      await ensureOriginPermissions(MYMINIFACTORY_DOWNLOAD_ORIGINS);
      capture.assertCurrent();
    }
    if (pendingMarketplaceCapture?.source.provider === "makeronline") {
      await ensureOriginPermission("https://acop-prod-private.s3-accelerate.amazonaws.com/*");
      capture.assertCurrent();
    }
    if (activeSource === "Printables" && !pendingPrintablesCapture && !pendingManualCapture) {
      try {
        await ensureMetadataPermission(
          PRINTABLES_METADATA_PERMISSION_ORIGIN,
          "Printables",
          capture.signal,
        );
      } catch (error) {
        capture.assertCurrent();
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
    capture.assertCurrent();
    if (pendingManualCapture) {
      const manual = pendingManualCapture;
      const title = pageLabel.textContent;
      const receipt = await capture.run((signal) =>
        manual.kind === "direct"
          ? captureDirectFile({
              vault: capture.config.vault,
              authorization,
              sourceUrl: manual.url,
              title,
              file: selectedManualFile(null),
              fetchImpl: transferFetch,
              signal,
            })
          : captureRichFiles({
              ...preferences,
              fetchImpl: transferFetch,
              vault: capture.config.vault,
              authorization,
              sourceUrl: manual.draft.source.canonical_url,
              title,
              captureSource: manual.draft.source,
              files: [selectedManualFile(manual.draft.source.source_item_id)],
              runStage,
            }),
      );
      clearManualFileSelection();
      confirmCapture(captureReviewUrl(capture.config.vault, receipt));
      return;
    }
    const draft =
      pendingPrintablesCapture ??
      pendingMakerWorldCapture ??
      pendingThingiverseCapture ??
      pendingMarketplaceCapture;
    if (draft) {
      const selected = selectedPrintablesCandidates(draft);
      if (selected.length === 0) {
        showStatus("Select at least one file to upload.", "error");
        return;
      }
      const receipt = await capture.run((signal) =>
        captureSequentialFiles({
          ...preferences,
          fetchImpl: transferFetch,
          vault: capture.config.vault,
          authorization,
          source: draft.source,
          title: activePage?.title,
          candidates: selected,
          gallery: selectedImages.map((image) => ({
            id: image.id,
            filename: image.filename,
            download: (signal) => downloadSourceImage(image, signal),
          })),
          storage: browser.storage,
          signal,
          progress: (message) => {
            capture.assertCurrent();
            showStatus(message);
          },
          download: async (candidate, signal) => {
            const progress: ByteProgress = (loaded, total) => {
              capture.assertCurrent();
              showStatus(`Downloading ${candidate.filename}: ${progressLabel(loaded, total)}`);
            };
            if (draft.source.provider === "printables") {
              const links = await resolvePrintablesLinks(draft, [candidate], capture.signal);
              const link = links.find((link) => link.id === candidate.id)?.url;
              if (!link) throw new Error("Printables link mapping changed.");
              await ensurePrintablesFilePermissions([link], capture.signal);
              return downloadPrintablesCandidate(candidate, link, signal, progress);
            }
            if (draft.source.provider === "makerworld") {
              const links = await resolveMakerWorldLinks(draft, [candidate], capture.signal);
              const link = links.find((link) => link.id === candidate.id)?.url;
              if (!link) throw new Error("MakerWorld link mapping changed.");
              return downloadMakerWorldCandidate({
                candidate: { ...candidate, fileType: "other" },
                link,
                totalBefore: 0,
                ensureOriginPermission,
                signal,
                progress,
              });
            }
            if (draft.source.provider === "cults") {
              if (!activePage?.id || !draft.source.source_item_id)
                throw new MarketplaceDownloadError("Reopen the Cults publication to continue.");
              const results = await browser.scripting.executeScript({
                target: { tabId: activePage.id },
                world: "MAIN",
                func: requestCultsInMainWorld,
                args: [{ publicationId: draft.source.source_item_id, operation: "download" }],
              });
              const result = results[0]?.result as CultsPageResult | undefined;
              if (!result)
                throw new MarketplaceDownloadError("Cults download could not be checked.");
              return downloadCultsCandidate({ candidate, result, signal, progress });
            }
            if (draft.source.provider === "myminifactory") {
              if (!activePage?.id || !draft.source.source_item_id)
                throw new MarketplaceDownloadError(
                  "Reopen the MyMiniFactory publication to continue.",
                );
              const results = await browser.scripting.executeScript({
                target: { tabId: activePage.id },
                world: "MAIN",
                func: readMyMiniFactoryInMainWorld,
                args: [{ publicationId: draft.source.source_item_id }],
              });
              const result = results[0]?.result as MyMiniFactoryPageResult | undefined;
              if (!result)
                throw new MarketplaceDownloadError(
                  "MyMiniFactory file selection could not be checked.",
                );
              return downloadMyMiniFactoryCandidate({
                candidate,
                result,
                ensureOriginPermissions,
                signal,
                progress,
              });
            }
            if (draft.source.provider === "makeronline") {
              if (!activePage?.id || !draft.source.source_item_id)
                throw new MarketplaceDownloadError(
                  "Reopen the MakerOnline model page to continue.",
                );
              showStatus(
                "Requesting the MakerOnline download. If verification appears, hide this panel and complete it on the source page; the transfer will continue.",
              );
              const results = await browser.scripting.executeScript({
                target: { tabId: activePage.id },
                world: "MAIN",
                func: requestMakerOnlineInMainWorld,
                args: [
                  {
                    publicationId: draft.source.source_item_id,
                    operation: "download",
                    profileId: candidate.id,
                  },
                ],
              });
              const result = results[0]?.result as MakerOnlinePageResult | undefined;
              if (!result)
                throw new MarketplaceDownloadError(
                  "MakerOnline did not return a download response.",
                );
              return downloadMakerOnlineCandidate({
                candidate,
                result,
                ensureOriginPermission,
                signal,
                progress,
              });
            }
            if (draft.source.provider === "crealitycloud") {
              if (!activePage?.id || !draft.source.source_item_id)
                throw new MarketplaceDownloadError(
                  "Reopen the Creality Cloud model page to continue.",
                );
              const results = await browser.scripting.executeScript({
                target: { tabId: activePage.id },
                world: "MAIN",
                func: requestCrealityCloudInMainWorld,
                args: [
                  {
                    publicationId: draft.source.source_item_id,
                    operation: "download",
                    profileId: candidate.id,
                  },
                ],
              });
              const result = results[0]?.result as CrealityCloudPageResult | undefined;
              if (!result)
                throw new MarketplaceDownloadError(
                  "Creality Cloud did not return a download response.",
                );
              return downloadCrealityCloudCandidate({
                candidate,
                result,
                ensureOriginPermission,
                signal,
                progress,
              });
            }
            if (draft.source.provider === "thangs") {
              if (!activePage?.id || !draft.source.source_item_id)
                throw new Error("Reopen the Thangs model page to continue.");
              const results = await browser.scripting.executeScript({
                target: { tabId: activePage.id },
                world: "MAIN",
                func: requestThangsInMainWorld,
                args: [{ modelId: draft.source.source_item_id, operation: "download" }],
              });
              const result = results[0]?.result as ThangsPageResult | undefined;
              if (!result) throw new Error("Thangs did not return a download response.");
              return downloadThangsCandidate({
                candidate,
                result,
                ensureOriginPermission,
                signal,
                progress,
              });
            }
            const link = pendingThingiverseLinks.get(candidate.id);
            if (!link) throw new Error("Thingiverse file mapping changed.");
            return downloadThingiverseCandidate({
              candidate,
              link,
              ensureOriginPermissions,
              signal,
              progress,
            });
          },
        }),
      );
      clearCandidateSelection();
      confirmCapture(captureReviewUrl(capture.config.vault, receipt));
      return;
    }
    const visibleCapture =
      (await capture.run((signal) => readVisibleCapture(signal))) ?? fallbackVisibleCapture();
    capture.assertCurrent();
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
      pendingGallery = [];
      galleryUnavailable = false;
      if (activePage?.id) {
        const tabId = activePage.id;
        try {
          if (visibleCapture.source.provider === "printables") {
            pendingGallery = await capture.run((signal) =>
              readPrintablesGallery(visibleCapture.source.canonical_url, signal),
            );
          } else {
            const results = await capture.run(() =>
              browser.scripting.executeScript({
                target: { tabId },
                world: "MAIN",
                func: readSourceGalleryInMainWorld,
                args: [visibleCapture.source.provider],
              }),
            );
            pendingGallery = await sourceGallery(
              visibleCapture.source.canonical_url,
              results[0]?.result ?? [],
            );
          }
        } catch {
          capture.assertCurrent();
          galleryUnavailable = true;
        }
        capture.assertCurrent();
      }
      runCaptureSyncStage(
        () => renderCandidateSelection(visibleCapture),
        "capture_candidate_render_failed",
        "The provider files could not be shown. Choose a downloaded file to attach it in Pending Imports.",
      );
      return;
    }
    const pageUrl = activePage?.url;
    if (!pageUrl) throw new Error("The active tab has no capture URL.");
    const result = await capture.run((signal) =>
      captureModelPage({
        ...capture.config,
        accessToken: capture.authorization,
        signal,
        pageUrl,
        title: pageLabel.textContent,
        collectionId: preferences.collectionId,
        tags: preferences.tags,
        captureSource: visibleCapture?.source,
      }),
    );
    confirmCapture(result.inboxUrl);
  } catch (error) {
    if (!captureOperations.isCurrent(capture) || error instanceof CaptureRetiredError) return;
    if (error instanceof CaptureAuthenticationError) {
      recoverCaptureConnection();
      return;
    }
    if (error instanceof CaptureResponseError) {
      if (error.diagnosticCode === "url_target_not_public" && !pendingManualCapture) {
        const fallback = fallbackVisibleCapture();
        if (fallback) {
          renderManualFileSelection(fallback);
        } else if (activeSource === "Direct file" && activePage?.url) {
          offerManualFile({ kind: "direct", url: captureUrlForVault(activePage.url) });
        }
        showStatus(
          "PrintStash can't download from local or private addresses. Download the file and attach it instead.",
          "error",
          error.diagnosticCode,
        );
      } else {
        showStatus(safeCaptureMessage(error), "error", error.diagnosticCode ?? "capture_failed");
      }
      return;
    }
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
      recoverCaptureConnection();
    } else {
      showStatus(safeCaptureMessage(error), "error", "capture_failed");
    }
  } finally {
    if (captureOperations.finish(capture)) {
      importBusy = false;
      setButtonBusy(captureButton, false);
      if (pendingPrintablesCapture || pendingMakerWorldCapture || pendingThingiverseCapture) {
        captureButton.textContent = "Confirm and upload selected files";
      } else if (pendingManualCapture) {
        captureButton.textContent = "Upload selected file";
      }
      renderImportAvailability();
    }
  }
});

inboxButton.addEventListener("click", () => {
  if (inboxButton.dataset.url) browser.tabs.create({ url: inboxButton.dataset.url });
});

const enablePanel = document.createElement("button");
enablePanel.type = "button";
enablePanel.textContent = "Enable panel on this site";
enablePanel.className = "text-button text-action";
enablePanel.hidden = true;
sourceActions.append(enablePanel);
async function refreshCaptureHistory() {
  const request = ++historyRequest;
  const config = connectedConfig;
  const authorization = accessToken || config?.deviceCredential;
  const url = activePage?.url;
  historyList.replaceChildren();
  historyList.hidden = true;
  historyMessage.hidden = false;
  if (!config || !authorization) {
    historyMessage.textContent = "Connect to PrintStash to see previous captures.";
    return;
  }
  if (!url || !activeSource || !marketplaceOrigin(url)) {
    historyMessage.textContent = "No capture history for this page.";
    return;
  }
  historyMessage.textContent = "Loading previous captures…";
  try {
    const items = await previousCaptures(config.vault, authorization, url);
    if (historyRequest !== request) return;
    if (!items.length) {
      historyMessage.textContent = "No previous captures of this page.";
      return;
    }
    historyMessage.hidden = true;
    historyList.hidden = false;
    for (const item of items) {
      const row = document.createElement("li");
      const facts = document.createElement("div");
      const identifier = document.createElement("strong");
      identifier.textContent = `#${item.id}`;
      const state = document.createElement("span");
      state.className = "history-state";
      state.textContent = captureHistoryLabel(item);
      const open = document.createElement("button");
      open.type = "button";
      open.className = "text-button";
      open.textContent = "Open";
      open.setAttribute("aria-label", `Open capture #${item.id}`);
      open.addEventListener("click", () =>
        browser.tabs.create({ url: captureReviewUrl(config.vault, item) }),
      );
      facts.append(identifier, state);
      row.append(facts, open);
      historyList.append(row);
    }
  } catch {
    if (historyRequest === request) historyMessage.textContent = "Capture history is unavailable.";
  }
}

function pageMarketplace() {
  return activeSource && activePage?.url ? marketplaceOrigin(activePage.url) : null;
}

const preview = launcherButton("/icon.svg");
launcherPreview.append(preview);
const variantFields = LAUNCHER_VARIANTS.map((value) =>
  requiredElement(`[name="launcher-variant"][value="${value}"]`, HTMLInputElement),
);
const cornerFields = LAUNCHER_CORNERS.map((value) =>
  requiredElement(`[name="launcher-corner"][value="${value}"]`, HTMLInputElement),
);

function renderLauncherPreferences() {
  launcherEnabled.checked = launcher.enabled;
  variantFields.forEach((input) => (input.checked = input.value === launcher.variant));
  cornerFields.forEach((input) => (input.checked = input.value === launcher.corner));
  positionLauncher(launcherPreview, launcher.corner, launcher.variant);

  const origin = pageMarketplace();
  const siteHidden = Boolean(origin && launcher.hiddenSites.has(origin));
  launcherSite.hidden = !origin || (!siteHidden && permittedSite !== origin);
  launcherSite.textContent = siteHidden ? "Show on this site" : "Hide on this site";
  enablePanel.hidden = panelMode || !origin || permittedSite === origin;
}

function showLauncherError() {
  launcherError.textContent = "Launcher settings could not be saved. Try again.";
  launcherError.hidden = false;
  renderLauncherPreferences();
}

function updateLauncher(write: Promise<void>) {
  launcherError.hidden = true;
  void write.catch(showLauncherError);
}

variantFields.forEach((input, index) =>
  input.addEventListener("change", () =>
    updateLauncher(launcherStore.setVariant(LAUNCHER_VARIANTS[index])),
  ),
);
cornerFields.forEach((input, index) =>
  input.addEventListener("change", () =>
    updateLauncher(launcherStore.setCorner(LAUNCHER_CORNERS[index])),
  ),
);
launcherEnabled.addEventListener("change", () =>
  updateLauncher(launcherStore.setEnabled(launcherEnabled.checked)),
);
launcherSite.addEventListener("click", () => {
  const origin = pageMarketplace();
  if (origin)
    updateLauncher(launcherStore.setSiteHidden(origin, !launcher.hiddenSites.has(origin)));
});

const stopLauncherUpdates = launcherStore.subscribe((value) => {
  launcher = value;
  renderLauncherPreferences();
}, showLauncherError);
window.addEventListener(
  "pagehide",
  (event) => {
    if (!event.persisted) stopLauncherUpdates();
  },
  { once: true },
);

enablePanel.addEventListener("click", async () => {
  const origin = activePage?.url ? marketplaceOrigin(activePage.url) : null;
  if (!origin) return;
  const message = { type: "printstash-enable-panel", origin };
  // The native permission prompt can close the toolbar popup. Hand off the
  // activation before requesting permission so the background can finish it.
  const activation = browser.runtime.sendMessage(message).catch(() => false);
  if (!(await browser.permissions.request({ origins: [origin] }))) return;
  const enabled =
    (await activation) || (await browser.runtime.sendMessage(message).catch(() => false));
  if (enabled) {
    permittedSite = origin;
    renderLauncherPreferences();
  }
  showStatus(
    enabled
      ? "Panel enabled. Open PrintStash on the page; hiding it keeps transfers running. Reloading the tab requires recovery."
      : "The panel could not be enabled.",
    enabled ? "" : "error",
  );
});

async function captureTabs(): Promise<Page[]> {
  if (!panelMode) return browser.tabs.query({ active: true, currentWindow: true });
  const tab = await browser.runtime.sendMessage({ type: "printstash-panel-context" });
  if (
    typeof tab !== "object" ||
    tab === null ||
    !("id" in tab) ||
    typeof tab.id !== "number" ||
    !("url" in tab) ||
    typeof tab.url !== "string" ||
    !marketplaceOrigin(tab.url)
  )
    return [];
  return [
    {
      id: tab.id,
      url: tab.url,
      title: "title" in tab && typeof tab.title === "string" ? tab.title : undefined,
    },
  ];
}

async function initialize() {
  lockConnection(true);
  const [stored, openTabs] = await Promise.all([
    browser.storage.get([
      "vault",
      "username",
      "apiKey",
      "deviceCredential",
      "deviceName",
      "popupTab",
    ]),
    captureTabs(),
  ]);
  activePage = openTabs[0] || null;
  renderActivePage();
  if (!tabTouched) {
    const saved = POPUP_TABS.find((tab) => tab === stored.popupTab);
    tabs.select(activeSource ? "capture" : (saved ?? "capture"));
  }
  const origin = pageMarketplace();
  if (
    origin &&
    (panelMode || (await isMarketplacePanelEnabled(browser.storage, origin).catch(() => false)))
  ) {
    permittedSite = origin;
  }
  renderLauncherPreferences();
  const prepared = await takePreparedSetup(activePage);
  if (prepared) {
    fillConnectionForm(prepared);
    connectionPanel.hidden = false;
    renderConnection("disconnected", {
      detail: "Setup received from this PrintStash tab.",
    });
    const origins = [permissionOrigin(prepared)];
    const alreadyAllowed = await browser.permissions.contains({ origins }).catch(() => false);
    lockConnection(false);
    connectButton.textContent = "Finish setup";
    if (alreadyAllowed) {
      try {
        await establishConnection(prepared, { requestPermission: false, persist: true });
        showStatus("Extension setup completed and connection verified.");
      } catch {
        showStatus();
      }
    } else {
      showStatus("Setup received. Choose Finish setup to approve access to this server.");
    }
    return;
  }
  const storedConfig: Config = {
    vault: typeof stored.vault === "string" ? stored.vault : "",
    ...(typeof stored.deviceName === "string" ? { deviceName: stored.deviceName } : {}),
    ...(typeof stored.username === "string" ? { username: stored.username } : {}),
    ...(typeof stored.apiKey === "string" ? { apiKey: stored.apiKey } : {}),
    ...(typeof stored.deviceCredential === "string"
      ? { deviceCredential: stored.deviceCredential }
      : {}),
  };
  fillConnectionForm(storedConfig);
  lockConnection(false);

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
  lockConnection(false);
  connectionPanel.hidden = false;
  renderConnection("error", { detail: messageFrom(error) });
});

// Escape hides the embedded panel; the transfer owner remains mounted.
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape" || !panelMode) return;
  const origin = activePage?.url ? new URL(activePage.url).origin : null;
  if (origin) window.parent.postMessage({ type: "printstash-hide-panel" }, origin);
});
