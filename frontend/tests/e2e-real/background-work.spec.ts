/**
 * Background work reaches an open page live, through the real backend.
 *
 * Derivatives (thumbnails, metadata) are produced by Jobs after an upload
 * commits, so a page that shows a Model must hear when one lands rather than
 * wait for a reload. This drives the whole path on the real engine: an
 * administrator regenerates every thumbnail from Settings → Background work
 * while another tab shows a Model; that tab follows the Model's channel on the
 * events socket and receives the derivative notice, and its current preview
 * stays visible while the replacement is produced.
 */
import { test, expect } from "./helpers";
import { modelCard, uploadModel } from "./util";

test.describe("background work", () => {
  test("@critical an open Model hears its thumbnail re-derived", async ({ page }) => {
    const name = `e2e-live-${Date.now()}`;
    // One events socket serves the whole tab, and the Task Center opens it
    // during the upload, so listen before the first navigation.
    const frames: string[] = [];
    const sent: string[] = [];
    page.on("websocket", (socket) => {
      if (!socket.url().includes("/api/v1/events/ws")) return;
      socket.on("framereceived", (frame) => frames.push(String(frame.payload)));
      socket.on("framesent", (frame) => sent.push(String(frame.payload)));
    });
    await uploadModel(page, name, { mesh: true, gcode: false });

    await modelCard(page, name).click();
    await expect(page).toHaveURL(/\/models\/\d+/);
    const modelId = Number(new URL(page.url()).pathname.split("/").at(-1));
    await expect.poll(() => sent).toContain(JSON.stringify({ subscribe: `model:${modelId}` }));

    const admin = await page.context().newPage();
    await admin.goto("/settings?section=work");
    await admin.getByText("Advanced controls").click();
    const thumbnails = admin
      .getByRole("listitem")
      .filter({ has: admin.getByText("thumbnail", { exact: true }) });
    await thumbnails.getByRole("button", { name: "Regenerate all" }).click();
    await admin.getByRole("dialog").getByRole("button", { name: "Regenerate" }).click();
    await expect(admin.getByText("Re-deriving every thumbnail")).toBeVisible();

    await expect
      .poll(
        () =>
          frames.some((payload) => {
            const notice: { type?: string; model_id?: number; kind?: string; state?: string } =
              JSON.parse(payload);
            return (
              notice.type === "derivative" &&
              notice.model_id === modelId &&
              notice.kind === "thumbnail" &&
              notice.state === "ready"
            );
          }),
        { timeout: 120_000 },
      )
      .toBe(true);
    // The page learned it without reloading, and still shows a preview.
    await expect(page.getByRole("status").filter({ hasText: "Preparing" })).toHaveCount(0);
    await admin.close();
  });
});
