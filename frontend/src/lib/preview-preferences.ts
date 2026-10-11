import { useEffect, useState } from "react";

export type PreviewQuality = "performance" | "balanced" | "detail";
export type MeshRendererPreference = "auto" | "webgpu" | "webgl";
export type ScreenshotScale = 1 | 2 | 3;

export interface PreviewPreferences {
  previewQuality: PreviewQuality;
  screenshotScale: ScreenshotScale;
  meshRenderer: MeshRendererPreference;
}

export const PREVIEW_PREFERENCES_STORAGE_KEY = "printstash.preview.preferences:v1";
export const PREVIEW_PREFERENCES_EVENT = "printstash:preview-preferences-changed";

// Declaring the event on WindowEventMap is what lets add/removeEventListener
// hand the listener a typed CustomEvent instead of a bare Event to assert on.
declare global {
  interface WindowEventMap {
    [PREVIEW_PREFERENCES_EVENT]: CustomEvent<PreviewPreferences>;
  }
}

export const DEFAULT_PREVIEW_PREFERENCES: PreviewPreferences = {
  previewQuality: "balanced",
  screenshotScale: 2,
  meshRenderer: "webgl",
};

const PREVIEW_PIXEL_RATIOS = {
  performance: 1,
  balanced: 1.5,
  detail: 2,
} satisfies Record<PreviewQuality, number>;

/**
 * One value `JSON.parse` can hand back from the preferences blob. Whatever an
 * older build (or a hand-edited devtools session) left in localStorage is JSON
 * and nothing more, so this is the honest input type for the field validators
 * below. `undefined` is a member because a stored blob may omit either key.
 */
type StoredJsonValue =
  | string
  | number
  | boolean
  | null
  | undefined
  | readonly StoredJsonValue[]
  | { readonly [key: string]: StoredJsonValue };

/** The unvalidated localStorage blob, before each field is decoded. */
interface StoredPreviewPreferences {
  readonly previewQuality?: StoredJsonValue;
  readonly screenshotScale?: StoredJsonValue;
  readonly meshRenderer?: StoredJsonValue;
}

const isBrowser = (): boolean => "window" in globalThis;

function isPreviewQuality(value: StoredJsonValue): value is PreviewQuality {
  return value === "performance" || value === "balanced" || value === "detail";
}

function isScreenshotScale(value: StoredJsonValue): value is ScreenshotScale {
  return value === 1 || value === 2 || value === 3;
}

// The current document retains a choice while optional persistence is unavailable.
let documentChoice: { value: PreviewPreferences; pending: boolean } | null = null;

export function readPreviewPreferences(): PreviewPreferences {
  if (!isBrowser()) return { ...DEFAULT_PREVIEW_PREFERENCES };
  if (documentChoice?.pending) return { ...documentChoice.value };
  let raw: string | null;
  try {
    raw = window.localStorage.getItem(PREVIEW_PREFERENCES_STORAGE_KEY);
  } catch {
    return { ...(documentChoice?.value ?? DEFAULT_PREVIEW_PREFERENCES) };
  }
  let value: PreviewPreferences;
  try {
    const stored: StoredPreviewPreferences = JSON.parse(raw ?? "{}");
    value = {
      previewQuality: isPreviewQuality(stored.previewQuality)
        ? stored.previewQuality
        : DEFAULT_PREVIEW_PREFERENCES.previewQuality,
      meshRenderer:
        stored.meshRenderer === "auto" ||
        stored.meshRenderer === "webgpu" ||
        stored.meshRenderer === "webgl"
          ? stored.meshRenderer
          : DEFAULT_PREVIEW_PREFERENCES.meshRenderer,
      screenshotScale: isScreenshotScale(stored.screenshotScale)
        ? stored.screenshotScale
        : DEFAULT_PREVIEW_PREFERENCES.screenshotScale,
    };
  } catch {
    value = DEFAULT_PREVIEW_PREFERENCES;
  }
  documentChoice = { value: { ...value }, pending: false };
  return { ...value };
}

export function writePreviewPreferences(preferences: PreviewPreferences): void {
  if (!isBrowser()) return;
  const raw = JSON.stringify(preferences);
  documentChoice = { value: { ...preferences }, pending: true };
  try {
    window.localStorage.setItem(PREVIEW_PREFERENCES_STORAGE_KEY, raw);
    documentChoice.pending = false;
  } catch {
    // A blocked/quota-limited store cannot prevent the same-tab renderer update.
  }
  window.dispatchEvent(
    new CustomEvent<PreviewPreferences>(PREVIEW_PREFERENCES_EVENT, {
      detail: { ...preferences },
    }),
  );
}

export function previewPixelRatio(quality: PreviewQuality): number {
  return PREVIEW_PIXEL_RATIOS[quality];
}

export function usePreviewPreferences(): PreviewPreferences {
  const [preferences, setPreferences] = useState(readPreviewPreferences);

  useEffect(() => {
    const refresh = () => setPreferences(readPreviewPreferences());
    const receive = (event: CustomEvent<PreviewPreferences>) => {
      setPreferences(event.detail ?? readPreviewPreferences());
    };
    window.addEventListener("storage", refresh);
    window.addEventListener(PREVIEW_PREFERENCES_EVENT, receive);
    return () => {
      window.removeEventListener("storage", refresh);
      window.removeEventListener(PREVIEW_PREFERENCES_EVENT, receive);
    };
  }, []);

  return preferences;
}
