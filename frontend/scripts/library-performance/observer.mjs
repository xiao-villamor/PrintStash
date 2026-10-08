/** Runs in the browser, with expected identities and media availability from the corpus. */
export function installObserver({ user, locale, target }) {
  localStorage.setItem("printstash.user", JSON.stringify(user));
  localStorage.setItem("printstash.locale", locale);
  sessionStorage.setItem("ps-filter-expanded", JSON.stringify(target.expanded));
  const clockKey = "printstash:performance-navigation-start";
  const startedAt = Number(sessionStorage.getItem(clockKey) ?? performance.timeOrigin);
  let stop = () => {};
  window.observeLibrary = (expected, started = performance.now()) => {
    stop();
    sessionStorage.setItem(clockKey, String(performance.timeOrigin + started));
    const state = (window.libraryObservation = {
      started,
      content: null,
      tree: null,
      media: null,
      complete: null,
      errors: [],
      visibleImages: 0,
      pendingDerivatives: 0,
      missingDerivatives: 0,
    });
    const decoded = new WeakSet();
    const decoding = new WeakSet();
    let consecutive = 0,
      frame = 0,
      alive = true;
    const visible = (element) => {
      const rect = element?.getBoundingClientRect();
      return (
        rect &&
        rect.width > 0 &&
        rect.height > 0 &&
        rect.top < innerHeight &&
        rect.bottom > 0 &&
        rect.left < innerWidth &&
        rect.right > 0
      );
    };
    const tick = () => {
      if (!alive) return;
      const main = document.querySelector("main");
      const url = new URL(location.href);
      const title = main?.querySelector("h1")?.textContent?.trim();
      const links = [...document.querySelectorAll("main [data-library-entry]")];
      const identities = [...new Set(links.map((a) => a.getAttribute("data-library-entry")))];
      const folders = [...document.querySelectorAll("main [data-collection-path]")];
      const content =
        url.pathname === "/" &&
        (url.searchParams.get("c") ?? null) === expected.collection &&
        (url.searchParams.get("q") ?? "") === (expected.query ?? "") &&
        title === expected.titles[locale] &&
        (expected.folders
          ? JSON.stringify(folders.map((e) => e.getAttribute("data-collection-path"))) ===
            JSON.stringify(expected.folders)
          : JSON.stringify(identities) === JSON.stringify(expected.entries.map((e) => e.path)));
      const tree =
        [...document.querySelectorAll("aside.bg-sidebar")].find(visible) ??
        [...document.querySelectorAll('[role="dialog"]')].find(
          (element) =>
            visible(element) &&
            element.getAttribute("aria-label") === (locale === "en" ? "Filters" : "Filtros"),
        );
      const branchNames = tree
        ? [...tree.querySelectorAll("button")].map(
            (b) => b.getAttribute("aria-label") ?? b.textContent?.trim(),
          )
        : [];
      const leafNames = tree
        ? [...tree.querySelectorAll('[role="button"][title]')].map((e) => e.title)
        : [];
      const branchButtons = expected.branches.map((name) =>
        [...(tree?.querySelectorAll("button") ?? [])].find(
          (button) => (button.getAttribute("aria-label") ?? button.textContent?.trim()) === name,
        ),
      );
      const treeReady =
        !!tree &&
        branchButtons.every((button) => {
          const bounds = button?.getBoundingClientRect();
          return (
            bounds &&
            bounds.width >= 40 &&
            bounds.height > 0 &&
            bounds.left >= 0 &&
            bounds.right <= innerWidth
          );
        }) &&
        expected.leaves.every((name) => leafNames.includes(name));
      if (
        content &&
        branchButtons.every(Boolean) &&
        branchButtons.some((button) => button.getBoundingClientRect().width < 40)
      )
        if (!state.errors.includes("unusable_tree_control"))
          state.errors.push("unusable_tree_control");
      state.debug = {
        title,
        collection: url.searchParams.get("c"),
        query: url.searchParams.get("q"),
        identities,
        branchNames,
        leafNames,
      };
      const search = document.querySelector("[data-model-search]");
      const filters = document.querySelector(
        `button[aria-label="${locale === "en" ? "Filters" : "Filtros"}"]`,
      );
      const controlsReady =
        visible(search) &&
        !search.disabled &&
        !search.readOnly &&
        (innerWidth >= 768 || (visible(filters) && !filters.disabled));
      const delta = () => performance.now() - started;
      if (content && state.content === null) state.content = delta();
      if (treeReady && state.tree === null) state.tree = delta();
      let media = content;
      state.visibleImages = 0;
      state.pendingDerivatives = 0;
      state.missingDerivatives = 0;
      if (content && !expected.folders)
        for (const entry of expected.entries) {
          const link = links.find((a) => a.getAttribute("data-library-entry") === entry.path);
          const card = link?.closest("article") ?? link;
          if (!visible(card)) continue;
          const container = card?.querySelector("[data-library-thumbnail]");
          if (!container) {
            media = false;
            continue;
          }
          if (!visible(container)) continue;
          if (entry.media === "pending" || entry.media === "missing") {
            if (container.getAttribute("data-library-thumbnail") !== "missing") media = false;
            if (entry.media === "pending") state.pendingDerivatives++;
            else state.missingDerivatives++;
            continue;
          }
          if (entry.media !== "available")
            throw new Error("Corpus requires explicit media availability");
          state.visibleImages++;
          if (container.getAttribute("data-library-thumbnail") === "failed") {
            if (!state.errors.includes(entry.path)) state.errors.push(entry.path);
          }
          const image = container.querySelector("img");
          if (!image || !image.complete || !image.naturalWidth) {
            media = false;
            continue;
          }
          if (!decoded.has(image) && !decoding.has(image)) {
            decoding.add(image);
            image.decode().then(
              () => {
                if (alive) decoded.add(image);
              },
              () => {
                if (alive) state.errors.push(entry.path);
              },
            );
          }
          if (!decoded.has(image)) media = false;
        }
      if (media && state.media === null) state.media = delta();
      if (content && treeReady && media && controlsReady && !state.errors.length) {
        consecutive++;
        if (consecutive >= 2) {
          state.complete = delta();
          return;
        }
      } else consecutive = 0;
      frame = requestAnimationFrame(tick);
    };
    stop = () => {
      alive = false;
      cancelAnimationFrame(frame);
    };
    frame = requestAnimationFrame(tick);
  };
  window.observeLibrary(target, startedAt - performance.timeOrigin);
}
