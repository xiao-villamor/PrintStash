/**
 * Notes and manuals living beside the models they belong to.
 *
 * Documents are the one part of the library that is edited in place rather than uploaded,
 * so the round trip — write, save, reload, read back — is the whole contract. The PDF
 * case is here because rendering one goes through pdf.js in the browser, which no unit
 * test can stand in for.
 */
import { readFile } from "node:fs/promises";
import { devices } from "@playwright/test";
import { test, expect, authBundleFor, authedContext, type Page } from "./helpers";
import { collapseNavigationSidebar, stlFor } from "./util";

// Collection documents (new in 0.8.0): markdown editor, collection README, and
// the pdf.js viewer. All real — every save/upload hits the backend DB + storage.

// Create a top-level collection (name == path for slug-safe names).
async function makeCollection(page: Page, name: string): Promise<void> {
  const receipt = await page.request.post("/api/v1/collections", { data: { name } });
  expect(receipt.ok(), await receipt.text()).toBe(true);
}

// Open Documents through the selected Collection's Library actions.
async function openDocsTab(page: Page, col: string): Promise<void> {
  await page.goto(`/?c=${col}&v=docs`);
  await expect(page.getByRole("heading", { name: "Documents", exact: true })).toBeVisible();
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
    await page.getByRole("button", { name: "Save", exact: true }).click();

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
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByRole("textbox", { name: "Markdown source" })).toBeHidden();
    await expect(page.getByText("Use the M3 bolts.", { exact: true })).toBeVisible();

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

test.describe("binary collection files", () => {
  for (const filename of ["calculations.xlsx", "resin.ctb"]) {
    test(`round-trips ${filename} through Documents`, async ({ page }) => {
      const col = `e2e-binary-${Date.now()}`;
      const bytes = Buffer.from([0, 255, 128, 1, 2, 3, 0, 13, 10]);
      await makeCollection(page, col);
      await openDocsTab(page, col);
      const chooserEvent = page.waitForEvent("filechooser");
      await page.getByRole("button", { name: "Upload file", exact: true }).click();
      const chooser = await chooserEvent;
      await chooser.setFiles({
        name: filename,
        mimeType: "application/octet-stream",
        buffer: bytes,
      });
      await expect(page).toHaveURL(/\/documents\/\d+$/);
      const documentUrl = page.url();
      try {
        await page.reload();
        await expect(page.getByText(`${filename} — use Download to open it.`)).toBeVisible();
        const downloadEvent = page.waitForEvent("download");
        await page.getByRole("button", { name: "Download", exact: true }).click();
        const download = await downloadEvent;
        expect(download.suggestedFilename()).toBe(filename);
        const path = await download.path();
        expect(path).not.toBeNull();
        expect(await readFile(path!)).toEqual(bytes);
        await openDocsTab(page, col);
        await expect(
          page.locator(`a[href="${new URL(documentUrl).pathname}"]`).first(),
        ).toBeVisible();
      } finally {
        const id = documentUrl.split("/").at(-1);
        expect((await page.request.delete(`/api/v1/documents/${id}`)).ok()).toBeTruthy();
      }
    });
  }
});

test.describe("Model attachments", () => {
  test("attaches guides to one Model", async ({ page }, info) => {
    const name = "Assembly bracket";
    const seeded = await page.request.post("/api/v1/ingest/model", {
      multipart: {
        file: {
          name: "assembly-bracket.stl",
          mimeType: "model/stl",
          buffer: Buffer.from(stlFor(`bracket-${Date.now()}`)),
        },
        model_name: name,
      },
    });
    expect(seeded.status(), await seeded.text()).toBe(202);
    const jobPath = `/api/v1/jobs/${(await seeded.json()).job_id}`;
    await expect
      .poll(async () => (await (await page.request.get(jobPath)).json()).state)
      .toBe("completed");
    const { model_id: modelId } = await (await page.request.get(jobPath)).json();
    await page.goto(`/models/${modelId}`);
    await collapseNavigationSidebar(page);
    await page.getByRole("tab", { name: /^Files(?: \d+)?$/ }).click();
    const pane = page.getByRole("region", { name: /^Attachments/ });
    await expect(page.getByRole("heading", { name: /Source files/ })).toBeVisible();
    await expect(pane).toHaveCount(0);
    await page.getByRole("tab", { name: "Documentation", exact: true }).click();
    await expect(page).toHaveURL(/tab=documentation/);
    await expect(page.getByRole("tabpanel", { name: "Documentation", exact: true })).toBeVisible();
    const dropzone = pane.getByRole("button", { name: /Drop files here or click to upload/ });
    await expect(dropzone).toBeVisible();
    await dropzone.screenshot({ path: info.outputPath("attachments-dropzone.png") });
    const picker = page.waitForEvent("filechooser", { timeout: 10000 });
    await dropzone.click();
    const chooser = await picker;
    expect(chooser.isMultiple()).toBe(true);
    await chooser.setFiles([]);
    const addAttachment = pane.getByRole("button", { name: "Add attachment", exact: true });
    await addAttachment.click();
    const attachmentMenu = page.getByRole("menu").filter({
      has: page.getByRole("menuitem", { name: "Attach file", exact: true }),
    });
    await expect(attachmentMenu).toHaveAttribute("data-state", "open");
    await attachmentMenu.screenshot({
      animations: "disabled",
      path: info.outputPath("attachments-menu.png"),
    });
    await expect(attachmentMenu).not.toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
    await page.keyboard.press("Escape");
    await expect(addAttachment).toBeFocused();
    const modelUrl = page.url();

    const videoBytes = Buffer.from([0, 0, 0, 24, 102, 116, 121, 112]);
    const photo = Buffer.from(
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aEioAAAAASUVORK5CYII=",
      "base64",
    );
    const files = await page.evaluateHandle(
      ({ pdf, image }) => {
        const transfer = new DataTransfer();
        transfer.items.add(
          new File([new Uint8Array(pdf)], "assembly.pdf", { type: "application/pdf" }),
        );
        transfer.items.add(
          new File([new Uint8Array(image)], "reference.png", { type: "image/png" }),
        );
        return transfer;
      },
      { pdf: Array.from(minimalPdf()), image: Array.from(photo) },
    );
    await dropzone.dispatchEvent("dragenter", { dataTransfer: files });
    await expect(pane.getByText("Drop files to attach", { exact: true })).toBeVisible();
    await pane
      .getByRole("button", { name: /Drop files to attach/ })
      .dispatchEvent("dragover", { dataTransfer: files });
    await pane
      .getByRole("button", { name: /Drop files to attach/ })
      .dispatchEvent("drop", { dataTransfer: files });
    await files.dispose();
    await expect(pane.getByRole("link", { name: /assembly/ })).toBeVisible();
    await expect(pane.getByRole("link", { name: /reference/ })).toBeVisible();

    const selection = page.waitForEvent("filechooser");
    await addAttachment.click();
    await page.getByRole("menuitem", { name: "Attach file", exact: true }).click();
    await (
      await selection
    ).setFiles({ name: "build.mp4", mimeType: "video/mp4", buffer: videoBytes });
    await expect(pane.getByRole("link", { name: /build/ })).toBeVisible();

    await page.reload();
    await expect(pane.getByRole("link", { name: /assembly/ })).toBeVisible();
    await page.screenshot({ path: info.outputPath("model-files-populated.png") });
    await pane.getByRole("link", { name: /assembly/ }).click();
    await expect(page.getByText("1 / 1")).toBeVisible({ timeout: 30_000 });
    const reader = page.getByRole("dialog", { name: "Attachments" });
    await expect(page).toHaveURL(modelUrl);
    await reader
      .getByRole("combobox", { name: "Select attachment" })
      .selectOption({ label: "reference" });
    const image = reader.getByRole("img", { name: "reference", exact: true });
    await expect(image).toBeVisible();
    await expect(image).toHaveJSProperty("naturalWidth", 1);
    await reader
      .getByRole("combobox", { name: "Select attachment" })
      .selectOption({ label: "build" });
    await expect(page.getByText("build.mp4 — use Download to open it.")).toBeVisible();
    const downloading = page.waitForEvent("download");
    await reader.getByRole("button", { name: "Download", exact: true }).click();
    const download = await downloading;
    expect(download.suggestedFilename()).toBe("build.mp4");
    expect(await readFile(await download.path())).toEqual(videoBytes);

    await reader.getByRole("button", { name: "Close", exact: true }).click();
    await expect(reader).toBeHidden();
    await pane.getByRole("button", { name: "Delete document" }).first().click();
    const confirmation = page.getByRole("dialog", { name: "Delete document?" });
    await expect(confirmation.getByText(/"build" will be moved to trash/)).toBeVisible();
    await confirmation.getByRole("button", { name: "Delete", exact: true }).click();
    await expect(pane.getByRole("link", { name: /build/ })).toHaveCount(0);
    await page.reload();
    await expect(pane.getByRole("link", { name: /assembly/ })).toBeVisible();
    await expect(pane.getByRole("link", { name: /build/ })).toHaveCount(0);

    await pane.getByRole("button", { name: "Add attachment", exact: true }).click();
    await page.getByRole("menuitem", { name: "New document", exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`/documents/new\\?modelId=${modelId}`));
    await page.getByLabel("Document name").fill("Assembly notes");
    await page
      .getByPlaceholder(/Write markdown/)
      .fill("## Parts\n\n| Part | Count |\n| --- | --- |\n| Bolt | 4 |");
    const livePreview = page.getByRole("region", { name: "Live preview", exact: true });
    await expect(livePreview.getByRole("cell", { name: "Bolt" })).toBeVisible();
    await expect(page.getByRole("textbox", { name: "Markdown source" })).toBeVisible();
    await expect(page.getByText("Unsaved changes", { exact: true })).toBeVisible();
    await page.screenshot({ path: info.outputPath("editor-live-preview.png") });
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page).toHaveURL(/\/documents\/\d+$/);
    await page.getByRole("link", { name: "Back to model", exact: true }).click();
    await expect(page).toHaveURL(modelUrl);
    await pane.getByRole("link", { name: /Assembly notes/ }).click();
    await expect(reader.getByRole("cell", { name: "Bolt" })).toBeVisible();
    await reader.screenshot({ path: info.outputPath("model-attachment-reader.png") });

    // Capture the isolated print document before the native browser dialog.
    await page.evaluate(() => {
      window.print = () => {
        const sheet = document.querySelector(".document-print-sheet");
        document.body.dataset.printTitle = document.title;
        document.body.dataset.printBody = sheet?.textContent ?? "";
        window.dispatchEvent(new Event("afterprint"));
      };
    });
    await reader.getByRole("button", { name: "Export as PDF", exact: true }).click();
    await expect(page.locator("body")).toHaveAttribute("data-print-title", "Assembly notes");
    await expect(page.locator("body")).toHaveAttribute("data-print-body", /Parts.*Bolt/s);
    await reader.getByRole("link", { name: "Edit", exact: true }).click();
    await page.getByPlaceholder(/Write markdown/).fill("## Revised parts");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Revised parts" })).toBeVisible();
    await page.getByRole("link", { name: "Back to model", exact: true }).click();
    await page.reload();
    await pane.getByRole("link", { name: /Assembly notes/ }).click();
    await expect(reader.getByRole("heading", { name: "Revised parts" })).toBeVisible();
    await reader.getByRole("button", { name: "Close", exact: true }).click();

    await page.emulateMedia({ colorScheme: "dark" });
    const resize = page.getByRole("separator", { name: "Resize details panel" });
    await resize.press("Home");
    await expect(resize).toHaveAttribute("aria-valuenow", "400");
    const navigation = page.getByTestId("model-detail-sidebar").locator(".model-detail-tabs");
    await expect
      .poll(() =>
        navigation.evaluate((element) => {
          const bounds = element.getBoundingClientRect();
          const labels = Array.from(
            element.querySelectorAll('[role="tab"], [aria-haspopup="menu"]'),
            (button) => button.getBoundingClientRect(),
          );
          return {
            horizontalOverflow: element.scrollWidth > element.clientWidth,
            everyTabFits: labels.every(
              (tab) => tab.left >= bounds.left && tab.right <= bounds.right,
            ),
          };
        }),
      )
      .toEqual({ horizontalOverflow: false, everyTabFits: true });
    const add = pane.getByRole("button", { name: "Add attachment", exact: true });
    await expect(add).toBeVisible();
    await add.press("Enter");
    const menu = page
      .getByRole("menu")
      .filter({ has: page.getByRole("menuitem", { name: "Attach file" }) });
    await expect(menu.getByRole("menuitem", { name: "New document" })).toBeVisible();
    await page.screenshot({ path: info.outputPath("model-files-narrow-inspector.png") });
    await page.keyboard.press("Escape");
    await expect(menu).toBeHidden();
    await expect(add).toBeFocused();
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
      .getByLabel("Upload file", { exact: true })
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
