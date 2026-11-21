import type { LauncherCorner, LauncherVariant } from "./launcher-preferences.ts";

export function launcherButton(iconUrl: string) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "launcher";
  button.setAttribute("aria-label", "Import to PrintStash");

  const mark = document.createElement("img");
  mark.className = "launcher-mark";
  mark.src = iconUrl;
  mark.alt = "";
  mark.width = 26;
  mark.height = 26;

  const close = document.createElement("span");
  close.className = "launcher-close";
  close.textContent = "×";
  close.setAttribute("aria-hidden", "true");

  const label = document.createElement("span");
  label.className = "launcher-label";
  label.textContent = "Import";
  label.setAttribute("aria-hidden", "true");

  const hint = document.createElement("span");
  hint.className = "launcher-hint";
  hint.textContent = "Import to PrintStash";
  hint.setAttribute("aria-hidden", "true");

  button.append(mark, close, label, hint);
  return button;
}

export function positionLauncher(
  element: HTMLElement,
  corner: LauncherCorner,
  variant: LauncherVariant,
) {
  element.dataset.corner = corner;
  element.dataset.variant = variant;
}
