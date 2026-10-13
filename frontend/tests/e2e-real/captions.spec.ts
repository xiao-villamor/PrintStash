/** Real caption edits keep their captured version until explicit conflict review. */
import { test, expect } from "./helpers";
import { modelCard, uploadGcodeModel } from "./util";
import type { SubjectCaption } from "../../src/types/captions";

const API = `http://127.0.0.1:${process.env.PLAYWRIGHT_REAL_API_PORT ?? 8410}`;

test.describe("caption draft concurrency", () => {
  test("keeps a caption draft through a real conflicting edit", async ({ page }) => {
    const name = `e2e-caption-${Date.now()}`;
    await uploadGcodeModel(page, name);
    const target = await modelCard(page, name).getAttribute("href");
    if (!target) throw new Error("The uploaded Model requires its detail link");
    const id = new URL(target, page.url()).pathname.split("/").at(-1);
    const endpoint = `${API}/api/v1/subjects/model/${id}/caption`;
    const seeded = await page.request.patch(endpoint, {
      data: { action: "edit", text: "Original saved caption" },
    });
    expect(seeded.ok()).toBe(true);
    const initial: SubjectCaption = await seeded.json();
    expect(initial.version_token).not.toBeNull();
    await modelCard(page, name).click();
    await page.getByRole("button", { name: "Edit caption" }).click();
    const draft = page.getByRole("textbox", { name: "Caption text" });
    await draft.fill("My retained caption draft");
    const changed = await page.request.patch(endpoint, {
      data: {
        action: "edit",
        text: "Changed in another editor",
        version_token: initial.version_token,
      },
    });
    expect(changed.ok()).toBe(true);
    const current: SubjectCaption = await changed.json();
    expect(current.version_token).not.toBe(initial.version_token);
    await expect(draft).toHaveValue("My retained caption draft");
    const rejected = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === `/api/v1/subjects/model/${id}/caption` &&
        response.request().method() === "PATCH",
    );
    await page.getByRole("button", { name: "Save caption" }).click();
    const conflict = await rejected;
    expect(conflict.status()).toBe(409);
    expect(conflict.request().postDataJSON()).toEqual({
      action: "edit",
      text: "My retained caption draft",
      version_token: initial.version_token,
    });
    await expect(draft).toHaveValue("My retained caption draft");
    await expect(page.getByRole("button", { name: "Save caption" })).toBeDisabled();
    await page.getByRole("button", { name: "Review latest caption" }).click();
    const review = page.getByRole("dialog", { name: "Review latest caption" });
    await expect(review.getByText("Changed in another editor")).toBeVisible();
    await expect(review.getByText("My retained caption draft")).toBeVisible();
    await review.getByRole("button", { name: "Keep my draft against this version" }).click();
    const confirmed = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === `/api/v1/subjects/model/${id}/caption` &&
        response.request().method() === "PATCH",
    );
    await page.getByRole("button", { name: "Save caption" }).click();
    const saved = await confirmed;
    expect(saved.ok()).toBe(true);
    expect(saved.request().postDataJSON().version_token).toBe(current.version_token);
    const result: SubjectCaption = await (await page.request.get(endpoint)).json();
    expect(result.text).toBe("My retained caption draft");
    await expect(page.getByText("My retained caption draft", { exact: true })).toBeVisible();
  });
});
