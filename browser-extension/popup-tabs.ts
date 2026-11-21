export const POPUP_TABS = ["capture", "history", "settings"] as const;
export type PopupTab = (typeof POPUP_TABS)[number];

export function popupTabs(
  buttons: Record<PopupTab, HTMLButtonElement>,
  panels: Record<PopupTab, HTMLElement>,
  changed: (tab: PopupTab, persist: boolean) => void,
) {
  function select(tab: PopupTab, persist = false) {
    for (const name of POPUP_TABS) {
      const selected = name === tab;
      buttons[name].setAttribute("aria-selected", String(selected));
      buttons[name].tabIndex = selected ? 0 : -1;
      panels[name].hidden = !selected;
    }
    changed(tab, persist);
  }

  for (const [index, name] of POPUP_TABS.entries()) {
    const button = buttons[name];
    button.addEventListener("click", () => select(name, true));
    button.addEventListener("keydown", (event) => {
      const destinations: Record<string, number | undefined> = {
        ArrowLeft: (index + POPUP_TABS.length - 1) % POPUP_TABS.length,
        ArrowRight: (index + 1) % POPUP_TABS.length,
        Home: 0,
        End: POPUP_TABS.length - 1,
      };
      if (!Object.hasOwn(destinations, event.key)) return;
      const tab = POPUP_TABS[destinations[event.key] ?? index];
      event.preventDefault();
      select(tab, true);
      buttons[tab].focus();
    });
  }

  return { select };
}
