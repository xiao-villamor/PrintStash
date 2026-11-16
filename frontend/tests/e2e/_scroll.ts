import type { Page } from "@playwright/test";

/** The next page of a Library view loads as its main container is scrolled to the end. */
export async function scrollToEnd(page: Page): Promise<void> {
  // Scrolling an empty container reaches nothing: wait for the first page.
  await page.locator("main [data-library-entry]").first().waitFor();
  await page.getByRole("main").evaluate((main) => {
    main.scrollTop = main.scrollHeight;
  });
}

/** Locate a loaded virtual entry by traversing its real scroll surface. */
export async function scrollToLibraryEntry(
  page: Page,
  entry: import("@playwright/test").Locator,
): Promise<void> {
  const { expect } = await import("@playwright/test");
  await page.getByRole("main").evaluate((main) => {
    const list = main.querySelector<HTMLElement>("[data-library-list]");
    const scroll = list && list.scrollHeight > list.clientHeight ? list : main;
    scroll.scrollTop = 0;
  });
  await expect
    .poll(
      async () => {
        if (await entry.count()) return true;
        await page.getByRole("main").evaluate((main) => {
          const list = main.querySelector<HTMLElement>("[data-library-list]");
          const scroll = list && list.scrollHeight > list.clientHeight ? list : main;
          scroll.scrollTop += scroll.clientHeight / 2;
        });
        return false;
      },
      { intervals: [50, 100, 150] },
    )
    .toBe(true);
  await page.evaluate(() => document.fonts.ready);
  await entry.evaluate((node) => node.scrollIntoView({ block: "center", inline: "nearest" }));
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
}
