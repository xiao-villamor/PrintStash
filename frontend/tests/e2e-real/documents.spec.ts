import { test, expect, type Page } from "./helpers";

// Collection documents (new in 0.8.0): markdown editor, collection README, and
// the pdf.js viewer. All real — every save/upload hits the backend DB + storage.

// Create a top-level collection (name == path for slug-safe names).
async function makeCollection(page: Page, name: string): Promise<void> {
  await page.goto("/organize");
  const input = page.getByPlaceholder("New collection...");
  await input.fill(name);
  await input.press("Enter");
  await expect(page.getByRole("button", { name: `Delete ${name}` })).toBeVisible();
}

// Land on a collection's Documents tab with the collection actually *selected*.
// The README "Add a description" button only renders once the collection row has
// loaded, so it's our signal that `collectionId` is set before we create/upload
// (otherwise the new doc would land at root, not in the collection).
async function openDocsTab(page: Page, col: string): Promise<void> {
  await page.goto(`/?c=${col}`);
  await expect(
    page.getByRole("button", { name: /Add a description for this collection/ }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Documents" }).click();
  await expect(page.getByText("No documents here yet.")).toBeVisible();
}

// A fully-valid single-page PDF (correct xref offsets) so pdf.js loads it cleanly.
function minimalPdf(): Buffer {
  const objs = [
    "<</Type/Catalog/Pages 2 0 R>>",
    "<</Type/Pages/Kids[3 0 R]/Count 1>>",
    "<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 300]>>",
  ];
  let body = "%PDF-1.4\n";
  const offsets: number[] = [];
  objs.forEach((o, i) => {
    offsets.push(body.length);
    body += `${i + 1} 0 obj\n${o}\nendobj\n`;
  });
  const xrefStart = body.length;
  body += `xref\n0 ${objs.length + 1}\n0000000000 65535 f \n`;
  offsets.forEach((off) => {
    body += `${String(off).padStart(10, "0")} 00000 n \n`;
  });
  body += `trailer\n<</Size ${objs.length + 1}/Root 1 0 R>>\nstartxref\n${xrefStart}\n%%EOF`;
  return Buffer.from(body, "latin1");
}

test("create, edit and preview a markdown document in a collection", async ({ page }) => {
  const col = `e2e-docs-${Date.now()}`;
  await makeCollection(page, col);
  await openDocsTab(page, col);

  // New markdown doc → editor (the name input is the lg/semibold header field;
  // the top-bar search is also an <input>, so scope by class).
  await page.getByRole("button", { name: "New document" }).click();
  await expect(page).toHaveURL(/\/documents\/new/);
  await page.locator("input.font-semibold").fill("Assembly guide");
  await page.getByPlaceholder(/Write markdown/).fill("# Step one\n\nGlue part A to part B.");
  await page.getByRole("button", { name: "Save" }).click();

  // Saved → real row; the app keeps you in the editor. Switch to Preview to see
  // the rendered markdown.
  await expect(page).toHaveURL(/\/documents\/\d+$/);
  await page.getByRole("button", { name: "Preview" }).click();
  await expect(page.getByRole("heading", { name: "Step one" })).toBeVisible();
  await expect(page.getByText("Glue part A to part B.")).toBeVisible();

  // Edit an existing doc → Save returns to preview automatically.
  await page.getByRole("button", { name: "Edit" }).click();
  await page.getByPlaceholder(/Write markdown/).fill("# Step one\n\nUse the M3 bolts.");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Use the M3 bolts.")).toBeVisible();

  // It shows up as a card back on the Documents tab.
  await page.goto(`/?c=${col}&v=docs`);
  const card = page.getByText("Assembly guide");
  await expect(card).toBeVisible();

  // Cleanup: delete the doc through the shared confirmation dialog.
  await card.hover();
  await page.getByTitle("Delete document").click();
  await page.getByRole("dialog").getByRole("button", { name: "Delete" }).click();
  await expect(page.getByText("No documents here yet.")).toBeVisible();
});

test("edit a collection README and have it persist", async ({ page }) => {
  const col = `e2e-readme-${Date.now()}`;
  await makeCollection(page, col);

  await page.goto(`/?c=${col}`);
  await page.getByRole("button", { name: /Add a description for this collection/ }).click();
  await page
    .getByPlaceholder(/short description of this collection/i)
    .fill("## Printed parts\n\nDownload, slice, print.");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("heading", { name: "Printed parts" })).toBeVisible();

  // Survives a reload — proves it persisted, not just optimistic UI.
  await page.reload();
  await expect(page.getByRole("heading", { name: "Printed parts" })).toBeVisible();
});

test("upload a PDF and render it in the pdf.js viewer", async ({ page }) => {
  const col = `e2e-pdf-${Date.now()}`;
  await makeCollection(page, col);
  await openDocsTab(page, col);

  await page
    .locator('input[accept=".pdf,.md,.markdown,.txt"]')
    .setInputFiles({ name: "manual.pdf", mimeType: "application/pdf", buffer: minimalPdf() });

  // Lands on the doc detail page with the themed pdf.js viewer.
  await expect(page).toHaveURL(/\/documents\/\d+$/);
  // Worker + render can take a moment; assert the page counter resolves.
  await expect(page.getByText("1 / 1")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTitle("Zoom in")).toBeVisible();
  await expect(page.getByRole("button", { name: "Download" })).toBeVisible();

  // Cleanup: back to the Documents tab and delete the only doc there.
  await page.goto(`/?c=${col}&v=docs`);
  const docCard = page.locator('a[href^="/documents/"]').first();
  await expect(docCard).toBeVisible();
  await docCard.hover();
  await page.getByTitle("Delete document").click();
  await page.getByRole("dialog").getByRole("button", { name: "Delete" }).click();
  await expect(page.getByText("No documents here yet.")).toBeVisible();
});
