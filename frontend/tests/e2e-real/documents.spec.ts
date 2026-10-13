/**
 * Notes and manuals living beside the models they belong to.
 *
 * Documents are the one part of the library that is edited in place rather than uploaded,
 * so the round trip — write, save, reload, read back — is the whole contract. The PDF
 * case is here because rendering one goes through pdf.js in the browser, which no unit
 * test can stand in for.
 */
import { devices } from "@playwright/test";
import { test, expect, authBundleFor, authedContext, type Page } from "./helpers";
import { createCollectionViaVault } from "./util";

// Collection documents (new in 0.8.0): markdown editor, collection README, and
// the pdf.js viewer. All real — every save/upload hits the backend DB + storage.

// Create a top-level collection (name == path for slug-safe names).
async function makeCollection(page: Page, name: string): Promise<void> {
  await createCollectionViaVault(page, name);
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
  await page.getByRole("tab", { name: "Documents" }).click();
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

test.describe("documents", () => {
  test("create, edit and preview a markdown document in a collection", async ({ page }) => {
    const col = `e2e-docs-${Date.now()}`;
    await makeCollection(page, col);
    await openDocsTab(page, col);

    // New markdown doc → editor (the name input is the lg/semibold header field;
    // the top-bar search is also an <input>, so scope by class).
    await page.getByRole("button", { name: "New document" }).click();
    await expect(page).toHaveURL(/\/documents\/new/);
    await page.locator("input.font-semibold").fill("Assembly guide");
    // The table is the reason this body is not just a heading: GFM tables are an
    // extension, so a renderer without it shows the pipes as literal text and a
    // build guide's parts list becomes unreadable.
    await page
      .getByPlaceholder(/Write markdown/)
      .fill(
        "# Step one\n\nGlue part A to part B.\n\n| Part | Material |\n| --- | --- |\n| A | PLA |\n| B | PETG |",
      );
    await page.getByRole("button", { name: "Save" }).click();

    // Saved → real row; the app keeps you in the editor. Switch to Preview to see
    // the rendered markdown.
    await expect(page).toHaveURL(/\/documents\/\d+$/);
    await page.getByRole("button", { name: "Preview" }).click();
    await expect(page.getByRole("heading", { name: "Step one" })).toBeVisible();
    await expect(page.getByText("Glue part A to part B.")).toBeVisible();
    await expect(page.getByRole("table")).toBeVisible();
    await expect(page.getByRole("columnheader", { name: "Part" })).toBeVisible();
    await expect(page.getByRole("columnheader", { name: "Material" })).toBeVisible();
    await expect(page.getByRole("cell", { name: "A", exact: true })).toBeVisible();
    await expect(page.getByRole("cell", { name: "PLA" })).toBeVisible();

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
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Printed parts" })).toBeVisible();

    // Survives a reload — proves it persisted, not just optimistic UI.
    await page.reload();
    await expect(page.getByRole("heading", { name: "Printed parts" })).toBeVisible();
  });
});

test.describe("PDF documents", () => {
  test.use({
    viewport: devices["Pixel 5"].viewport,
    userAgent: devices["Pixel 5"].userAgent,
    deviceScaleFactor: devices["Pixel 5"].deviceScaleFactor,
    isMobile: devices["Pixel 5"].isMobile,
    hasTouch: devices["Pixel 5"].hasTouch,
  });

  test("renders an uploaded PDF in the pdf.js viewer", async ({ page }) => {
    const col = `e2e-pdf-${Date.now()}`;
    const workerRequests: string[] = [];
    page.on("request", (request) => {
      if (request.url().includes("pdf.worker")) workerRequests.push(request.url());
    });
    await page.setViewportSize({ width: 1280, height: 720 });
    await makeCollection(page, col);
    await openDocsTab(page, col);
    await page.setViewportSize({ width: 393, height: 851 });

    await page
      .locator('input[accept=".pdf,.md,.markdown,.txt"]')
      .setInputFiles({ name: "manual.pdf", mimeType: "application/pdf", buffer: minimalPdf() });

    // Lands on the doc detail page with the themed pdf.js viewer.
    await expect(page).toHaveURL(/\/documents\/\d+$/);
    // Worker + render can take a moment; assert the page counter resolves.
    await expect(page.getByText("1 / 1")).toBeVisible({ timeout: 30_000 });
    expect(workerRequests).toHaveLength(1);
    expect(workerRequests[0]).toContain("?cache=pdfjs-worker-compat-v1");
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
});

// A competing writer, a missing acknowledgement, and revoked access all leave
// local edits intact. Only an explicit reviewed save can supersede newer data.
test.describe("conditional Document edits", () => {
  test("reviews a competing edit before saving the retained draft", async ({ page, context }) => {
    const name = `doc-conflict-${Date.now()}`;
    const created = await page.request.post("/api/v1/documents", {
      data: { name, collection_id: null, body: "Original notes" },
    });
    expect(created.ok()).toBeTruthy();
    const { id, edit_version: initialVersion } = await created.json();
    const other = await context.newPage();
    try {
      await page.goto(`/documents/${id}`);
      await other.goto(`/documents/${id}`);
      await page.getByRole("button", { name: "Edit", exact: true }).click();
      await other.getByRole("button", { name: "Edit", exact: true }).click();
      await page.getByPlaceholder(/Write markdown/).fill("First writer notes");
      await other.getByPlaceholder(/Write markdown/).fill("Retained local draft");
      await page.getByRole("button", { name: "Save", exact: true }).click();
      await expect(page.getByPlaceholder(/Write markdown/)).toHaveCount(0);
      await expect(page.getByText("First writer notes", { exact: true })).toBeVisible();

      await other.getByRole("button", { name: "Save", exact: true }).click();

      await expect(
        other
          .getByRole("region", { name: "Latest saved document" })
          .getByText("First writer notes", { exact: true }),
      ).toBeVisible();
      await expect(other.getByPlaceholder(/Write markdown/)).toHaveValue("Retained local draft");
      await expect(other.getByRole("button", { name: "Save", exact: true })).toBeDisabled();
      await other.getByRole("button", { name: "Use latest version as edit base" }).click();
      await other.getByRole("button", { name: "Save", exact: true }).click();
      await expect(other.getByPlaceholder(/Write markdown/)).toHaveCount(0);
      await expect(other.getByText("Retained local draft", { exact: true })).toBeVisible();
      const current = await page.request.get(`/api/v1/documents/${id}`);
      const saved = await current.json();
      expect(saved).toMatchObject({ body: "Retained local draft" });
      expect(saved.edit_version).toBeGreaterThan(initialVersion);
    } finally {
      await other.close();
      expect((await page.request.delete(`/api/v1/documents/${id}`)).ok()).toBeTruthy();
    }
  });

  test("confirms a committed save after its acknowledgement is lost", async ({ page }) => {
    const name = `doc-ack-${Date.now()}`;
    const created = await page.request.post("/api/v1/documents", {
      data: { name, collection_id: null, body: "Original notes" },
    });
    expect(created.ok()).toBeTruthy();
    const { id, edit_version: initialVersion } = await created.json();
    let writes = 0;
    try {
      await page.goto(`/documents/${id}`);
      await page.getByRole("button", { name: "Edit", exact: true }).click();
      await page.getByPlaceholder(/Write markdown/).fill("Committed without acknowledgement");
      await page.route(`**/api/v1/documents/${id}`, async (route) => {
        if (route.request().method() !== "PUT") {
          await route.continue();
          return;
        }
        writes += 1;
        const response = await route.fetch();
        expect(response.ok()).toBeTruthy();
        await route.abort("failed");
      });

      await page.getByRole("button", { name: "Save", exact: true }).click();

      await expect(page.getByText("Save confirmed from the current document.")).toBeVisible();
      await expect(page.getByPlaceholder(/Write markdown/)).toHaveCount(0);
      const current = await page.request.get(`/api/v1/documents/${id}`);
      const saved = await current.json();
      expect(saved).toMatchObject({ body: "Committed without acknowledgement" });
      expect(saved.edit_version).toBeGreaterThan(initialVersion);
      expect(writes).toBe(1);
    } finally {
      await page.unroute(`**/api/v1/documents/${id}`);
      expect((await page.request.delete(`/api/v1/documents/${id}`)).ok()).toBeTruthy();
    }
  });

  test("keeps a local draft after its collection grant is revoked", async ({ page, browser }) => {
    const stamp = Date.now();
    const username = `doc-editor-${stamp}`;
    const user = await page.request.post("/api/v1/admin/users", {
      data: { username, password: "userpass123" },
    });
    expect(user.ok()).toBeTruthy();
    const { id: userId } = await user.json();
    const collection = await page.request.post("/api/v1/collections", {
      data: { name: `doc-access-${stamp}` },
    });
    expect(collection.ok()).toBeTruthy();
    const { id: collectionId } = await collection.json();
    expect(
      (
        await page.request.put(`/api/v1/collections/${collectionId}/permissions/${userId}`, {
          data: { role: "edit" },
        })
      ).ok(),
    ).toBeTruthy();
    const created = await page.request.post("/api/v1/documents", {
      data: { name: `doc-revoked-${stamp}`, collection_id: collectionId, body: "Original notes" },
    });
    expect(created.ok()).toBeTruthy();
    const { id } = await created.json();
    const member = await authedContext(browser, await authBundleFor(username, "userpass123"));
    try {
      await member.page.goto(`/documents/${id}`);
      await member.page.getByRole("button", { name: "Edit", exact: true }).click();
      await member.page.getByPlaceholder(/Write markdown/).fill("My private draft");
      expect(
        (
          await page.request.delete(`/api/v1/collections/${collectionId}/permissions/${userId}`)
        ).ok(),
      ).toBeTruthy();

      await member.page.getByRole("button", { name: "Save", exact: true }).click();

      await expect(
        member.page.getByText("Document access changed. Your draft is kept here for copying."),
      ).toBeVisible();
      await expect(member.page.getByPlaceholder(/Write markdown/)).toHaveValue("My private draft");
      await expect(member.page.getByRole("button", { name: "Save", exact: true })).toBeDisabled();
      expect(await (await page.request.get(`/api/v1/documents/${id}`)).json()).toMatchObject({
        body: "Original notes",
        edit_version: 1,
      });
    } finally {
      await member.context.close();
      expect((await page.request.delete(`/api/v1/documents/${id}`)).ok()).toBeTruthy();
      expect((await page.request.delete(`/api/v1/collections/${collectionId}`)).ok()).toBeTruthy();
      expect((await page.request.delete(`/api/v1/admin/users/${userId}`)).ok()).toBeTruthy();
    }
  });
});
