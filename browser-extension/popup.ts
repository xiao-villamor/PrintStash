import { setPanelVaultNetwork, vaultFetch } from "./vault-fetch.ts";
import { workerVaultNetwork } from "./vault-worker-transport.ts";
import { defaultCaptureSelection, type CaptureSelection } from "./capture-selection.ts";
import {
  canResumeCapture,
  captureAccount,
  captureProgress,
  captureRecoveryMessage,
  type ImportIntent,
} from "./import-status.ts";
import {
  readSourceGalleryInMainWorld,
  readPrintablesGallery,
  sourceGallery,
  downloadSourceImage,
  type SourceGalleryImage,
} from "./source-gallery.ts";
import { galleryPicker } from "./gallery-picker.ts";
import { captureTransferFetch, progressLabel, type ByteProgress } from "./transfer-progress.ts";
import { collectionCombobox } from "./collection-combobox.ts";
import {
  captureDestinations,
  capturePreferences,
  captureTags,
  selectedCaptureCover,
} from "./capture-preferences.ts";
import {
  requestCultsInMainWorld,
  cultsCaptureFromPage,
  downloadCultsCandidate,
  type CultsPageResult,
} from "./cults-capture.ts";
import {
  readMyMiniFactoryInMainWorld,
  myMiniFactoryCaptureFromPage,
  downloadMyMiniFactoryCandidate,
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
import {
  MARKETPLACE_ORIGINS,
  sourcePublication,
  type SourceProviderLabel,
} from "./source-providers.ts";
import { ALL_MARKETPLACE_ACCESS_ORIGINS, marketplaceAccessOrigins } from "./marketplace-access.ts";
import { marketplaceOrigin } from "./marketplace-panel.ts";
import { isMarketplacePanelEnabled, requestPanelsOnGrant } from "./panel-registration.ts";
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
  modelTitleFromPageTitle,
  normalizeVault,
  isPlainHttpToPublicHost,
  vaultCandidates,
  VaultAccessError,
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
  MakerWorldSignInError,
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
import {
  clearConnectionDraft,
  holdPendingConnection,
  PENDING_CONNECTION_MS,
  readConnectionDraft,
  saveConnectionDraft,
  takePendingConnection,
  type PendingConnection,
} from "./connection-draft.ts";
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
/** Memory-only storage shared by this extension's pages, never content scripts. */
const sessionStore = (
  extensionApi.storage as unknown as {
    session: Parameters<typeof holdPendingConnection>[0];
  }
).session;
const importPreferences = capturePreferences(browser.storage);
const panelMode = new URL(location.href).searchParams.get("panel") === "1";
document.documentElement.toggleAttribute("data-panel", panelMode);
if (panelMode) {
  // A marketplace can upgrade all HTTP requests from its embedded documents.
  // An extension worker loaded from its own script has a separate policy.
  setPanelVaultNetwork(
    workerVaultNetwork(
      () => new Worker(new URL("./vault-network-worker.ts", import.meta.url), { type: "module" }),
    ),
  );
}

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
const reviewBeforeImporting = requiredElement("#review-before-importing", HTMLInputElement);
const importAgainButton = requiredElement("#import-again", HTMLButtonElement);
const inboxButton = requiredElement("#open-inbox", HTMLButtonElement);
const importPanel = requiredElement("#import-panel", HTMLElement);
const importHint = requiredElement("#import-hint", HTMLElement);
const captureContent = requiredElement("#capture-content", HTMLElement);
const fileSummary = requiredElement("#file-summary", HTMLElement);
const fileCount = requiredElement("#file-count", HTMLElement);
const changeFiles = requiredElement("#change-files", HTMLButtonElement);
const selectionMessage = requiredElement("#selection-message", HTMLElement);
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
const openServerButton = requiredElement("#open-server", HTMLButtonElement);
const plainHttp = requiredElement("#plain-http", HTMLElement);
const plainHttpDetail = requiredElement("#plain-http-detail", HTMLElement);
const plainHttpConfirm = requiredElement("#plain-http-confirm", HTMLInputElement);
const marketplaceAccessRow = requiredElement("#marketplace-access-row", HTMLElement);
const marketplaceAccess = requiredElement("#marketplace-access", HTMLInputElement);
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

const captureView = requiredElement("#view-capture", HTMLElement);
const tabs = panelMode
  ? null
  : popupTabs(
      {
        capture: requiredElement("#tab-capture", HTMLButtonElement),
        history: requiredElement("#tab-history", HTMLButtonElement),
        settings: requiredElement("#tab-settings", HTMLButtonElement),
      },
      {
        capture: captureView,
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

if (panelMode) {
  requiredElement(".popup-tabs", HTMLDivElement).remove();
  captureView.removeAttribute("role");
  captureView.removeAttribute("aria-labelledby");
}

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
let importAccount: number | null = null;
let importIntentSupported = false;
let importAgain = false;
let resumingImport = false;
let checkingImportContext = false;
let importContext: AbortController | null = null;
let importBusy = false;
const captureOperations = new CaptureOperationOwner();
let pendingPrintablesCapture: BrowserCaptureMessage | null = null;
let pendingMakerWorldCapture: BrowserCaptureMessage | null = null;
let pendingThingiverseCapture: BrowserCaptureMessage | null = null;
let pendingMarketplaceCapture: BrowserCaptureMessage | null = null;
let pendingThingiverseLinks = new Map<string, string>();
let gallerySelection: ReturnType<typeof galleryPicker> | null = null;
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
  importContext?.abort();
  checkingImportContext = false;
  importAccount = null;
  importAgain = false;
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
  importAgainButton.hidden = true;
  delete inboxButton.dataset.url;
}

function clearCandidateSelection() {
  gallerySelection?.element.remove();
  gallerySelection = null;
  fileCount.textContent = "Files";
  setFilePickerVisible(false);
  selectionMessage.hidden = true;
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

function showCaptureOutcome(
  message: string,
  url: string,
  action: string,
  kind: StatusKind = "success",
) {
  inboxButton.textContent = action;
  inboxButton.dataset.url = url;
  inboxButton.hidden = false;
  showStatus(message, kind);
  if (kind === "success") statusTitleText.textContent = message;
  renderImportAvailability();
  inboxButton.focus();
}

async function confirmCapture(
  url: string,
  capture: CaptureOperation<Config>,
  onReady: ImportIntent | undefined,
) {
  if (onReady === "import") {
    const itemId = Number(new URL(url).pathname.split("/").at(-1));
    showStatus("Importing into PrintStash…");
    const deadline = Date.now() + 90_000;
    while (Date.now() < deadline) {
      let item;
      try {
        item = await capture.run((signal) =>
          captureProgress(capture.config.vault, capture.authorization, itemId, signal),
        );
      } catch (error) {
        if (error instanceof CaptureRetiredError || error instanceof CaptureAuthenticationError)
          throw error;
        break;
      }
      if (item.state === "completed" && item.completion !== "partial") {
        const model = item.resulting_model_id;
        showCaptureOutcome(
          item.collection_name ? `Imported to ${item.collection_name}` : "Imported",
          Number.isSafeInteger(model) && Number(model) > 0
            ? `${capture.config.vault}/models/${model}`
            : url,
          "Open in PrintStash",
        );
        return;
      }
      if (
        item.state === "review" ||
        item.state === "failed" ||
        item.state === "completed" ||
        item.state === "dismissed"
      ) {
        const reason = captureRecoveryMessage(
          item.review_reason ?? item.error_code,
          "Check this capture in Pending Imports.",
        );
        showCaptureOutcome(`Needs review: ${reason}`, url, "Review", "error");
        return;
      }
      await capture.run(() => new Promise((resolve) => setTimeout(resolve, 650)));
    }
    showCaptureOutcome(
      "Import started. Open PrintStash to check its progress.",
      url,
      "Open in PrintStash",
      "",
    );
    return;
  }
  showCaptureOutcome("Sent to Pending Imports", url, "Review in PrintStash");
}

async function primeImportContext(config: Config, authorization: string) {
  const controller = new AbortController();
  importContext?.abort();
  importContext = controller;
  checkingImportContext = true;
  resumingImport = false;
  renderImportAvailability();
  try {
    const account = await captureAccount(config.vault, authorization, controller.signal);
    const defaults = await importPreferences.defaults(config.vault, account);
    if (controller.signal.aborted) return;
    importAccount = account;
    collection.choose(defaults.collection);
    tagsInput.value = defaults.tags.join(", ");
    if (!activePage?.url || !activeSource) return;
    const items = await previousCaptures(config.vault, authorization, activePage.url);
    if (controller.signal.aborted) return;
    const previous = items.find((item) => item.state !== "failed" && item.state !== "dismissed");
    if (previous) {
      if (
        previous.state === "captured" &&
        (await canResumeCapture(config.vault, authorization, previous.id, controller.signal))
      ) {
        if (!controller.signal.aborted) resumingImport = true;
        return;
      }
      if (controller.signal.aborted) return;
      const url = previous.resulting_model_id
        ? `${config.vault}/models/${previous.resulting_model_id}`
        : captureReviewUrl(config.vault, previous);
      showCaptureOutcome("Already in PrintStash", url, "Open");
      importAgainButton.hidden = false;
    }
  } catch {
    // The server still checks for an existing Pending Import at creation.
  } finally {
    if (importContext === controller) {
      checkingImportContext = false;
      renderImportAvailability();
    }
  }
}

importAgainButton.addEventListener("click", () => {
  importAgain = true;
  clearInboxAction();
  showStatus();
  renderImportAvailability();
  captureButton.focus();
  captureButton.click();
});

reviewBeforeImporting.addEventListener("change", () => {
  void importPreferences.setReviewBeforeImporting(reviewBeforeImporting.checked);
  renderImportAvailability();
});

function renderManualFileSelection(draft: BrowserCaptureMessage) {
  offerManualFile({ kind: "publication", draft });
}

function offerManualFile(capture: ManualCapture) {
  clearCandidateSelection();
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

function setFilePickerVisible(visible: boolean) {
  candidatePanel.hidden = !visible;
  changeFiles.setAttribute("aria-expanded", String(visible));
  changeFiles.textContent = visible ? "Done" : "Change";
}

function renderCandidateSelection(
  capture: BrowserCaptureMessage,
  defaults: CaptureSelection,
  images: SourceGalleryImage[] | null,
  showPicker: boolean,
) {
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
  const selected = new Set(
    defaults.kind === "selected" ? defaults.files.map((file) => file.id) : [],
  );
  candidateList.onchange = renderImportAvailability;
  capture.candidates.forEach((candidate, index) => {
    const label = document.createElement("label");
    label.className = "candidate-option";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = selected.has(candidate.id);
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
  selectionMessage.hidden = defaults.kind !== "ambiguous";
  if (defaults.kind === "ambiguous") selectionMessage.textContent = defaults.message;
  setFilePickerVisible(showPicker || defaults.kind !== "selected");
  if (images === null || images.length > 0) {
    gallerySelection = galleryPicker(images);
    candidatePanel.after(gallerySelection.element);
  }
  captureButton.textContent = "Confirm and upload selected files";
  renderImportAvailability();
}

function pendingDraft() {
  return (
    pendingPrintablesCapture ??
    pendingMakerWorldCapture ??
    pendingThingiverseCapture ??
    pendingMarketplaceCapture
  );
}

function selectedCandidates(capture: BrowserCaptureMessage) {
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
  const draft = pendingDraft();
  const count = draft ? selectedCandidates(draft).length : 0;
  const packageSource = activeSource === "MakerWorld" || activeSource === "Creality Cloud";
  const unit = packageSource ? "package" : "file";
  fileSummary.hidden = !canCapture || !draft;
  fileCount.textContent = draft ? `Files: ${count} ${unit}${count === 1 ? "" : "s"}` : "Files";
  changeFiles.disabled = importBusy || checkingImportContext;
  candidatePanel.disabled = importBusy;
  if (gallerySelection) gallerySelection.element.disabled = importBusy;
  if (count > 0) selectionMessage.hidden = true;
  captureButton.disabled =
    importBusy ||
    checkingImportContext ||
    !canCapture ||
    Boolean(draft && !count && importIntentSupported);
  if (!importBusy && importIntentSupported) {
    const legend = preferenceFields.querySelector("legend");
    if (legend) {
      legend.textContent = reviewBeforeImporting.checked
        ? "Suggestions for review"
        : "Import options";
    }
    captureButton.textContent = pendingManualCapture
      ? "Upload selected file"
      : resumingImport
        ? "Resume import"
        : !draft && isRichProvider(activeSource)
          ? "Review files and images"
          : reviewBeforeImporting.checked
            ? "Send to Pending Imports"
            : count > 1
              ? `Import ${count} files`
              : "Import";
  }
  importHint.hidden = !importBusy && (!pendingManualCapture || needsDirectFile);
  if (importBusy) {
    importHint.textContent = panelMode
      ? "You can hide this panel. Keep the source tab open."
      : "Keep this popup open until it finishes.";
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
  editButton.hidden = panelMode || !showOverview || activeTab === "settings";
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
    pageLabel.textContent = activePage?.title
      ? modelTitleFromPageTitle(activePage.title, activeSource ?? "")
      : activePage?.url || "No active page";
  }
  pageLabel.title = pageLabel.textContent;
  sourceLabel.textContent = activeSource ? `${activeSource} · Supported` : "Unsupported source";
  sourceLabel.dataset.supported = activeSource ? "true" : "false";
  renderImportAvailability();
}

async function ensureVaultPermission(origins: string[], requestPermission: boolean) {
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

/** The first address where the server answers; a pairing code is claimed only there. */
async function answeringCandidate(candidates: string[], fetchImpl: typeof fetch) {
  let failure: unknown = null;
  for (const vault of candidates) {
    try {
      return (await verifyVaultReachability({ vault, fetchImpl })).base;
    } catch (error) {
      if (!(error instanceof VaultAccessError && error.problem === "unreachable")) throw error;
      failure = error;
    }
  }
  throw failure;
}

/** Ask the background to add the page button once marketplace access arrives. */
function requestMarketplacePanels() {
  void requestPanelsOnGrant(sessionStore, MARKETPLACE_ORIGINS).catch(() => {});
}

async function establishConnection(
  config: Config,
  {
    requestPermission,
    persist,
    candidates = [config.vault],
    marketplaces = false,
    resumed = false,
  }: {
    requestPermission: boolean;
    persist: boolean;
    /** Addresses to try in order for what the user typed. */
    candidates?: string[];
    /** Grant every supported marketplace in the same browser prompt. */
    marketplaces?: boolean;
    /** Finishing an attempt whose popup Chrome closed for the prompt. */
    resumed?: boolean;
  },
) {
  if (connectionLocked) return false;
  const attempt: ConnectionAttempt = { controller: new AbortController(), phase: "verifying" };
  connectionAttempt = attempt;
  const signal = attempt.controller.signal;
  const fetchImpl: typeof fetch = async (input, options) => {
    signal.throwIfAborted();
    const result = await vaultFetch(input, { ...options, signal });
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
  renderOpenServer(null);
  const candidateOrigins = [...new Set(candidates.map((vault) => permissionOrigin({ vault })))];
  const ownsPermissions = requestPermission || resumed;
  let held: Promise<void> = Promise.resolve();
  try {
    if (requestPermission) {
      if (marketplaces) requestMarketplacePanels();
      // Not awaited yet: the prompt must open within this click's activation.
      held = holdPendingConnection(sessionStore, {
        ...config,
        candidates,
        expiresAt: Date.now() + PENDING_CONNECTION_MS,
      }).catch(() => {});
    }
    // One prompt: every address to try, plus the marketplaces when chosen.
    try {
      await ensureVaultPermission(
        requestPermission && marketplaces
          ? [...candidateOrigins, ...ALL_MARKETPLACE_ACCESS_ORIGINS]
          : candidateOrigins,
        requestPermission,
      );
    } finally {
      // The popup survived the prompt, so nothing needs finishing later.
      if (requestPermission) {
        await held;
        await takePendingConnection(sessionStore).catch(() => null);
      }
    }
    signal.throwIfAborted();
    if (candidates.length > 1) {
      config = { ...config, vault: await answeringCandidate(candidates, fetchImpl) };
      signal.throwIfAborted();
    }
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
      await clearConnectionDraft(browser.storage).catch(() => {});
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
    // The other address was asked only to find where the server answers.
    const unused = candidateOrigins.filter(
      (origin) =>
        origin !== permissionOrigin(normalized) &&
        (!previous || origin !== permissionOrigin(previous.config)),
    );
    if (ownsPermissions && unused.length)
      await browser.permissions.remove({ origins: unused }).catch(() => false);

    connectedConfig = normalized;
    const verifiedConnection = verified as {
      user?: Profile;
      accessToken?: string;
      importIntent?: true;
    };
    importIntentSupported = verifiedConnection.importIntent === true;
    collection.setDefaultLabel(
      importIntentSupported ? "New collection from title" : "Choose in Pending Imports",
    );
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
    if (wasEditing) tabs?.select(tabBeforeEdit);
    const captureAuthorization = accessToken ?? normalized.deviceCredential;
    if (importIntentSupported && captureAuthorization)
      void primeImportContext(normalized, captureAuthorization);
    return true;
  } catch (error) {
    let previousOrigin = null;
    try {
      previousOrigin = previous ? permissionOrigin(previous.config) : null;
    } catch {
      // Invalid legacy settings have no origin permission to clean up.
    }
    const failedOrigins = candidateOrigins.filter((origin) => origin !== previousOrigin);
    // A sign-in page or an unaccepted certificate is fixed in a tab and then
    // retried; keep that server's grant so the retry needs no second prompt.
    if (ownsPermissions && failedOrigins.length && !(error instanceof VaultAccessError)) {
      await browser.permissions.remove({ origins: failedOrigins }).catch(() => false);
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
      renderOpenServer(error);
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
  const missing = (
    await Promise.all(
      origins.map(async (origin) =>
        (await browser.permissions.contains({ origins: [origin] })) ? null : origin,
      ),
    )
  ).filter((origin) => origin !== null);
  if (!missing.length) return;
  try {
    if (await browser.permissions.request({ origins: missing })) return;
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
// The popup closes while the user copies a pairing code; keep what they typed.
function rememberConnectionDraft() {
  if (connectionState === "connected" && !editingConnection) return;
  void saveConnectionDraft(browser.storage, {
    vault: vaultInput.value,
    method: apiKeyMethod.checked ? "api-key" : "pairing",
    deviceName: deviceNameInput.value,
    username: usernameInput.value,
  }).catch(() => {});
}
for (const control of [vaultInput, deviceNameInput, usernameInput, pairingMethod, apiKeyMethod])
  control.addEventListener(
    control instanceof HTMLInputElement && control.type === "radio" ? "change" : "input",
    rememberConnectionDraft,
  );
pairingCodeInput.addEventListener("input", clearPairingError);
vaultInput.addEventListener("input", () => {
  plainHttp.hidden = true;
  plainHttpConfirm.checked = false;
});

let openServerUrl: string | null = null;
/** Offer the page the user must open: a sign-in page or a certificate to accept. */
function renderOpenServer(error: unknown) {
  openServerUrl = error instanceof VaultAccessError ? error.url : null;
  openServerButton.hidden = openServerUrl === null;
  openServerButton.textContent =
    error instanceof VaultAccessError && error.problem === "sign_in"
      ? "Open sign-in page"
      : "Open server in a tab";
}
openServerButton.addEventListener("click", () => {
  if (openServerUrl) void browser.tabs.create({ url: openServerUrl });
});

/** Finish a connection whose popup Chrome closed while asking for access. */
async function resumePendingConnection(pending: PendingConnection) {
  const { candidates, expiresAt: _expiresAt, ...config } = pending;
  const origins = [...new Set(candidates.map((vault) => permissionOrigin({ vault })))];
  if (!(await browser.permissions.contains({ origins }).catch(() => false))) return false;
  vaultInput.value = config.vault;
  if (config.deviceName) deviceNameInput.value = config.deviceName;
  connectionPanel.hidden = false;
  lockConnection(false);
  try {
    if (
      await establishConnection(config, {
        requestPermission: false,
        persist: true,
        candidates,
        resumed: true,
      })
    )
      showStatus("Access allowed. This browser is connected and ready to import.");
  } catch {
    // establishConnection shows why the connection failed.
  }
  return true;
}

/** The marketplace switch disappears once every marketplace is granted. */
async function renderMarketplaceAccess() {
  const granted = await browser.permissions
    .contains({ origins: [...ALL_MARKETPLACE_ACCESS_ORIGINS] })
    .catch(() => false);
  marketplaceAccessRow.hidden = granted;
}

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
  let candidates: string[];
  try {
    config = configFromForm();
    candidates = vaultCandidates(vaultInput.value);
  } catch (error) {
    showConnectionFeedback(messageFrom(error));
    return;
  }
  if (isPlainHttpToPublicHost(config.vault) && !plainHttpConfirm.checked) {
    plainHttpDetail.textContent = `${new URL(config.vault).host} is not on your local network, and HTTP sends the pairing code and this browser's key unencrypted. Use https:// if the server has a certificate.`;
    plainHttp.hidden = false;
    plainHttpConfirm.focus();
    return;
  }
  try {
    if (
      await establishConnection(config, {
        requestPermission: true,
        persist: true,
        candidates,
        marketplaces: !marketplaceAccessRow.hidden && marketplaceAccess.checked,
      })
    ) {
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
  tabs?.select("settings");
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
  tabs?.select(tabBeforeEdit);
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

async function rememberCaptureDefaults(config: Config, tags: string[]) {
  if (importAccount === null) return;
  await importPreferences
    .saveDefaults(config.vault, importAccount, { collection: collection.destination, tags })
    .catch(() => {});
}

captureButton.addEventListener("click", () => void runCapture());
changeFiles.addEventListener("click", () => {
  const open = candidatePanel.hidden === true;
  setFilePickerVisible(open);
  if (open) candidatePanel.querySelector("input")?.focus();
});

async function runCapture() {
  if (!connectedConfig || connectionState !== "connected") {
    showStatus("Connect PrintStash before importing.", "error");
    return;
  }
  if (importBusy || checkingImportContext || captureButton.disabled) return;
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
  let focusReview = false;
  importBusy = true;
  const discovering = isRichProvider(activeSource) && !pendingDraft() && !pendingManualCapture;
  setButtonBusy(captureButton, true, discovering ? "Finding files and images…" : "Sending…");
  renderImportAvailability();
  showStatus();
  try {
    const transferProgress = (loaded: number, total: number | null) => {
      capture.assertCurrent();
      showStatus(
        `Transferring to PrintStash: ${progressLabel(loaded, total)}${total !== null && loaded === total ? " · awaiting confirmation" : ""}`,
      );
    };
    const transferFetch = captureTransferFetch(transferProgress);
    const onReady: ImportIntent | undefined = importIntentSupported
      ? reviewBeforeImporting.checked
        ? "review"
        : "import"
      : undefined;
    const preferences = {
      onReady,
      importAgain,
      collectionId: collection.id,
      tags: captureTags(tagsInput.value),
      cover: selectedCaptureCover(coverInput.files?.[0]),
    };
    // Ask for every host this marketplace can use while the import click can
    // still open Chrome's prompt. A prompt shown after a lookup can retire the
    // toolbar popup or arrive after the click's activation has expired.
    const provider = activePage?.url ? sourcePublication(activePage.url)?.code : undefined;
    if (provider && !pendingManualCapture)
      await ensureOriginPermissions(marketplaceAccessOrigins(provider));
    capture.assertCurrent();
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
      await rememberCaptureDefaults(capture.config, preferences.tags);
      await confirmCapture(
        captureReviewUrl(capture.config.vault, receipt),
        capture,
        manual.kind === "direct" ? undefined : onReady,
      );
      return;
    }
    async function uploadSelection(draft: BrowserCaptureMessage) {
      const selectedImages = gallerySelection?.selectedImages() ?? [];
      const imageOrigins = [
        ...new Set(selectedImages.map((image) => `${new URL(image.url).origin}/*`)),
      ];
      capture.assertCurrent();
      const selected = selectedCandidates(draft);
      if (selected.length === 0) {
        showStatus("Select at least one file to upload.", "error");
        return;
      }
      // Chrome can close a toolbar popup when it opens a native access prompt.
      // Obtain access before creating a capture session with durable slots.
      const printablesLinks =
        draft.source.provider === "printables" && onReady === "import"
          ? await resolvePrintablesLinks(draft, selected, capture.signal)
          : null;
      if (printablesLinks) {
        await ensurePrintablesFilePermissions(
          printablesLinks.map((link) => link.url),
          capture.signal,
        );
      }
      const makerWorldLinks =
        draft.source.provider === "makerworld" && onReady
          ? await resolveMakerWorldLinks(draft, selected, capture.signal)
          : null;
      if (makerWorldLinks) {
        await ensureOriginPermissions([
          ...new Set([
            ...imageOrigins,
            ...makerWorldLinks.map((link) => `${new URL(link.url).origin}/*`),
          ]),
        ]);
      } else if (imageOrigins.length) {
        await ensureOriginPermissions(imageOrigins);
      }
      const receipt = await capture.run((signal) =>
        captureSequentialFiles({
          uploadProgress: transferProgress,
          ...preferences,
          fetchImpl: transferFetch,
          vault: capture.config.vault,
          authorization: capture.authorization,
          source: draft.source,
          title: draft.source.fields.title?.value.slice(0, 255) || activePage?.title,
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
              const links =
                printablesLinks ??
                (await resolvePrintablesLinks(draft, [candidate], capture.signal));
              const link = links.find((link) => link.id === candidate.id)?.url;
              if (!link) throw new Error("Printables link mapping changed.");
              if (!printablesLinks) await ensurePrintablesFilePermissions([link], capture.signal);
              return downloadPrintablesCandidate(candidate, link, signal, progress);
            }
            if (draft.source.provider === "makerworld") {
              const links =
                makerWorldLinks ??
                (await resolveMakerWorldLinks(draft, [candidate], capture.signal));
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
      await rememberCaptureDefaults(capture.config, preferences.tags);
      await confirmCapture(captureReviewUrl(capture.config.vault, receipt), capture, onReady);
    }
    const draft = pendingDraft();
    if (draft) {
      await uploadSelection(draft);
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
      let images: SourceGalleryImage[] | null = [];
      if (activePage?.id) {
        const tabId = activePage.id;
        try {
          if (visibleCapture.source.provider === "printables") {
            images = await capture.run((signal) =>
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
            images = await sourceGallery(
              visibleCapture.source.canonical_url,
              results[0]?.result ?? [],
            );
          }
        } catch {
          capture.assertCurrent();
          images = null;
        }
        capture.assertCurrent();
      }
      if (!activePage?.url) throw new Error("The active tab has no capture URL.");
      const defaults = defaultCaptureSelection(visibleCapture, activePage.url);
      runCaptureSyncStage(
        () => renderCandidateSelection(visibleCapture, defaults, images, !onReady),
        "capture_candidate_render_failed",
        "The provider files could not be shown. Choose a downloaded file to attach it in Pending Imports.",
      );
      focusReview = true;
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
        onReady,
        importAgain,
        captureSource: visibleCapture?.source,
      }),
    );
    await rememberCaptureDefaults(capture.config, preferences.tags);
    await confirmCapture(result.inboxUrl, capture, onReady);
  } catch (error) {
    if (!captureOperations.isCurrent(capture) || error instanceof CaptureRetiredError) return;
    if (error instanceof CaptureAuthenticationError) {
      recoverCaptureConnection();
      return;
    }
    if (error instanceof CaptureResponseError) {
      if (error.diagnosticCode === "capture_already_pending") {
        await primeImportContext(capture.config, capture.authorization);
        if (inboxButton.dataset.url) return;
      }
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
        showStatus(
          captureRecoveryMessage(error.diagnosticCode, safeCaptureMessage(error)),
          "error",
          error.diagnosticCode ?? "capture_failed",
        );
      }
      return;
    }
    if (error instanceof MakerWorldSignInError) {
      showStatus(error.message, "error");
      return;
    }
    if (error instanceof CaptureDiagnosticError) {
      if (error.provider === "MakerWorld" && error.fallbackCode === "auth_required") {
        showStatus(makerWorldFailureMessage("auth_required"), "error");
        return;
      }
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
      if (focusReview) {
        const firstChoice = candidatePanel.hidden
          ? gallerySelection?.element.querySelector("input")
          : candidatePanel.querySelector("input");
        (firstChoice ?? captureButton).focus();
      }
    }
  }
}

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
  const requested = { origins: [origin] };
  const allowed = await browser.permissions.contains(requested).catch(() => false);
  if (!allowed && !(await browser.permissions.request(requested).catch(() => false))) return;
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

function pageTitleIsUrl(title: string | undefined, url: string) {
  return !title?.trim() || title === url || title === url.replace(/^https?:\/\/(?:www\.)?/, "");
}

async function resolvePageTitle(page: Page) {
  const { id, url, title } = page;
  if (id === undefined || !url || !marketplaceOrigin(url) || !pageTitleIsUrl(title, url)) return;
  try {
    const [{ result } = {}] = await browser.scripting.executeScript({
      target: { tabId: id },
      func: () =>
        document.querySelector<HTMLMetaElement>('meta[property="og:title"]')?.content.trim() ||
        document.querySelector("main h1")?.textContent?.trim() ||
        document.title.trim(),
    });
    if (typeof result === "string" && !pageTitleIsUrl(result, url)) {
      page.title = result;
      renderActivePage();
    }
  } catch {
    // Tab permissions or the source document may have changed while opening PrintStash.
  }
}

function measurePanelHeight(page: Page | null) {
  if (!panelMode || !page?.url) return;
  const origin = new URL(page.url).origin;
  const observer = new ResizeObserver(() => {
    const height = Math.ceil(
      Math.max(document.body.scrollHeight, document.body.getBoundingClientRect().height) +
        Math.max(0, captureContent.scrollHeight - captureContent.clientHeight),
    );
    if (height > 0) window.parent.postMessage({ type: "printstash-panel-height", height }, origin);
  });
  observer.observe(document.body);
  observer.observe(captureContent);
  observer.observe(importPanel);
}

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
  reviewBeforeImporting.checked = await importPreferences
    .reviewBeforeImporting()
    .catch(() => false);
  activePage = openTabs[0] || null;
  renderActivePage();
  measurePanelHeight(activePage);
  if (activePage) void resolvePageTitle(activePage);
  if (tabs && !tabTouched) {
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
  await renderMarketplaceAccess();
  const pending = await takePendingConnection(sessionStore).catch(() => null);
  if (pending && (await resumePendingConnection(pending))) return;
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
  if (!storedConfig.deviceCredential && !storedConfig.apiKey) {
    const draft = await readConnectionDraft(browser.storage).catch(() => null);
    if (draft) {
      vaultInput.value = draft.vault;
      if (draft.deviceName) deviceNameInput.value = draft.deviceName;
      usernameInput.value = draft.username;
      (draft.method === "api-key" ? apiKeyMethod : pairingMethod).checked = true;
      renderCredentialFields();
    }
  }
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
