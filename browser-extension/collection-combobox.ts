import { CaptureRetiredError } from "./capture-operation.ts";
import type { CaptureDestination } from "./capture-preferences.ts";

const DEFAULT_DESTINATION = "Choose in Pending Imports";
const COLLECTION_SEARCH_DELAY_MS = 200;

export function collectionCombobox(
  input: HTMLInputElement,
  list: HTMLElement,
  feedback: HTMLElement,
  search: (query: string) => Promise<CaptureDestination[]>,
  retireSearch: () => void,
) {
  let defaultLabel = DEFAULT_DESTINATION;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let selected: CaptureDestination | null = null;
  let activeIndex = -1;
  let matches: Array<CaptureDestination | null> = [];

  function close() {
    clearTimeout(timer);
    retireSearch();
    matches = [];
    activeIndex = -1;
    list.replaceChildren();
    list.hidden = true;
    feedback.hidden = true;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
  }

  function choose(destination: CaptureDestination | null) {
    selected = destination;
    input.value = destination?.label ?? "";
    close();
  }

  function render(destinations: CaptureDestination[]) {
    matches = [null, ...destinations];
    list.replaceChildren(
      ...matches.map((destination, index) => {
        const option = document.createElement("li");
        option.id = `collection-option-${index}`;
        option.role = "option";
        option.ariaSelected = "false";
        option.textContent = destination?.label ?? defaultLabel;
        option.addEventListener("click", () => choose(destination));
        return option;
      }),
    );
    list.hidden = false;
    input.setAttribute("aria-expanded", "true");
  }

  function queueSearch(query: string) {
    close();
    // Keep the default usable while the server responds.
    render([]);
    timer = setTimeout(async () => {
      try {
        const destinations = await search(query);
        // The default may already be highlighted while the request is pending.
        activeIndex = -1;
        input.removeAttribute("aria-activedescendant");
        render(destinations);
        if (query && !destinations.length) {
          feedback.textContent = "No matching collections.";
          feedback.hidden = false;
        }
      } catch (error) {
        if (error instanceof CaptureRetiredError) return;
        feedback.textContent = error instanceof Error ? error.message : "Collection search failed.";
        feedback.hidden = false;
      }
    }, COLLECTION_SEARCH_DELAY_MS);
  }

  input.addEventListener("focus", () => queueSearch(selected ? "" : input.value));
  input.addEventListener("input", () => queueSearch(input.value));
  input.addEventListener("blur", () => choose(selected));
  // Keep focus on the combobox until the option's click commits the value.
  list.addEventListener("mousedown", (event) => event.preventDefault());

  input.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !list.hidden) {
      event.preventDefault();
      event.stopPropagation();
      choose(selected);
    } else if (event.key === "Enter" && activeIndex >= 0) {
      event.preventDefault();
      choose(matches[activeIndex]);
    } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (list.hidden) queueSearch(selected ? "" : input.value);
      const delta = event.key === "ArrowDown" ? 1 : -1;
      const index = activeIndex < 0 && delta < 0 ? matches.length - 1 : activeIndex + delta;
      activeIndex = Math.max(0, Math.min(index, matches.length - 1));
      const option = list.children[activeIndex];
      for (const child of list.children) child.ariaSelected = String(child === option);
      input.setAttribute("aria-activedescendant", option.id);
      option.scrollIntoView?.({ block: "nearest" });
    }
  });

  return {
    choose,
    setDefaultLabel(label: string) {
      defaultLabel = label;
      input.placeholder = label;
    },
    get destination() {
      return selected;
    },
    get id() {
      return selected?.id;
    },
    close() {
      choose(selected);
    },
    reset() {
      choose(null);
    },
  };
}
