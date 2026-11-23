import { MARKETPLACE_ORIGINS } from "./source-providers.ts";

export const LAUNCHER_VARIANTS = ["icon", "icon-and-label", "edge-tab"] as const;
export const LAUNCHER_CORNERS = ["top-left", "top-right", "bottom-left", "bottom-right"] as const;
export type LauncherVariant = (typeof LAUNCHER_VARIANTS)[number];
export type LauncherCorner = (typeof LAUNCHER_CORNERS)[number];

export interface LauncherPreferences {
  variant: LauncherVariant;
  corner: LauncherCorner;
  enabled: boolean;
  hiddenSites: ReadonlySet<string>;
}

interface StorageArea {
  get(keys: string[]): Promise<Record<string, unknown>>;
  set(values: Record<string, unknown>): Promise<void>;
  remove(keys: string[]): Promise<void>;
}

export interface LauncherStorage {
  local: StorageArea;
  sync?: StorageArea;
  onChanged: {
    addListener(listener: (changes: Record<string, unknown>, area: string) => void): void;
    removeListener(listener: (changes: Record<string, unknown>, area: string) => void): void;
  };
}

const variantKey = "launcher.variant";
const cornerKey = "launcher.corner";
const enabledKey = "launcher.enabled";
const sites = new Map(
  MARKETPLACE_ORIGINS.map((origin) => [origin, `launcher.hidden:${new URL(origin).hostname}`]),
);
const keys = [variantKey, cornerKey, enabledKey, ...sites.values()];

export function defaultLauncherPreferences(): LauncherPreferences {
  return { variant: "icon", corner: "bottom-right", enabled: true, hiddenSites: new Set() };
}

function readChoice<T>(stored: unknown, choices: readonly T[], initial: T): T {
  // Invalid and obsolete values reset to the declared default.
  return choices.find((choice) => choice === stored) ?? initial;
}

export function launcherPreferences(storage: LauncherStorage) {
  async function read(): Promise<LauncherPreferences> {
    const defaults = defaultLauncherPreferences();
    const [local, synced] = await Promise.all([
      storage.local.get(keys),
      storage.sync?.get(keys).catch(() => undefined),
    ]);
    const value = (key: string) => (local[key] === undefined ? synced?.[key] : local[key]);
    return {
      variant: readChoice(value(variantKey), LAUNCHER_VARIANTS, defaults.variant),
      corner: readChoice(value(cornerKey), LAUNCHER_CORNERS, defaults.corner),
      enabled: readChoice(value(enabledKey), [true, false], defaults.enabled),
      hiddenSites: new Set(
        [...sites].filter(([, key]) => value(key) === true).map(([origin]) => origin),
      ),
    };
  }

  async function set(key: string, value: LauncherVariant | LauncherCorner | boolean) {
    const sync = storage.sync;
    if (sync) {
      try {
        await sync.set({ [key]: value });
        await storage.local.remove([key]);
        return;
      } catch {
        // A local value takes precedence until the next successful sync write.
      }
    }
    await storage.local.set({ [key]: value });
  }

  return {
    read,
    setVariant(variant: LauncherVariant) {
      if (!LAUNCHER_VARIANTS.includes(variant)) throw new Error("Invalid launcher style.");
      return set(variantKey, variant);
    },
    setCorner(corner: LauncherCorner) {
      if (!LAUNCHER_CORNERS.includes(corner)) throw new Error("Invalid launcher position.");
      return set(cornerKey, corner);
    },
    setEnabled(enabled: boolean) {
      return set(enabledKey, enabled);
    },
    setSiteHidden(origin: string, hidden: boolean) {
      const key = sites.get(origin);
      if (!key) throw new Error("Unsupported launcher site.");
      return set(key, hidden);
    },
    subscribe(apply: (preferences: LauncherPreferences) => void, fail: () => void) {
      let revision = 0;
      let closed = false;
      function refresh() {
        const latest = ++revision;
        void read().then(
          (value) => {
            if (!closed && latest === revision) apply(value);
          },
          () => {
            if (!closed && latest === revision) fail();
          },
        );
      }
      const listener = (changes: Record<string, unknown>, area: string) => {
        if ((area === "local" || area === "sync") && keys.some((key) => key in changes)) {
          refresh();
        }
      };
      storage.onChanged.addListener(listener);
      refresh();
      return () => {
        closed = true;
        storage.onChanged.removeListener(listener);
      };
    },
  };
}

export type LauncherPreferencesOwner = ReturnType<typeof launcherPreferences>;
