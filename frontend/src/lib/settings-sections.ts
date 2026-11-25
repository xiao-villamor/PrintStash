import type { MessageKey } from "@/lib/locale";

export const SETTINGS_SECTION_LABELS = {
  overview: "settings.overview",
  access: "settings.access",
  storage: "settings.storage",
  backup: "settings.backup",
  "remote-storage": "settings.remoteStorage",
  imports: "settings.imports",
  "ai-search": "aiSearch.settingsTitle",
  pricing: "Pricing",
  maintenance: "settings.maintenance",
  work: "settings.backgroundWork",
  libraries: "settings.libraries",
  notifications: "settings.notifications",
  sso: "settings.sso",
  spoolman: "settings.spoolman",
  design: "settings.design",
  previews: "settings.previews",
  gpu: "settings.gpu",
  trash: "settings.trash",
  about: "settings.about",
} as const satisfies Record<string, MessageKey>;

export type SettingsSection = keyof typeof SETTINGS_SECTION_LABELS;

export function settingsSection(value: string | null): SettingsSection {
  return (
    Object.keys(SETTINGS_SECTION_LABELS).find(
      (section): section is SettingsSection => section === value,
    ) ?? "overview"
  );
}
