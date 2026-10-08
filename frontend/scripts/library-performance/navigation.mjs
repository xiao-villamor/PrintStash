/** A warm document sample must be a browser reload, not a same-URL navigation. */
export async function loadLibraryDocument(page, url, { reload = false } = {}) {
  const target = new URL(url);
  const previous = new URL(page.url());
  if (previous.origin === target.origin)
    await page.evaluate(() => sessionStorage.removeItem("printstash:performance-navigation-start"));
  // The router may canonicalize query defaults; reload that current document.
  if (reload) await page.reload({ waitUntil: "domcontentloaded" });
  else await page.goto(target.href, { waitUntil: "domcontentloaded" });
}
