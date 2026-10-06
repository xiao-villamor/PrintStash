/** Direct printer grants keep acknowledged roles while User selection stays local. */
import { test, expect } from "./helpers";

test.describe("printer access", () => {
  test("administers printer grants against authoritative state", async ({ page }) => {
    const stamp = Date.now();
    const username = `printer-access-user-${stamp}`;
    const name = `printer-access-${stamp}`;
    let userId: number | null = null;
    let printerId: number | null = null;
    try {
      const user = await page.request.post("/api/v1/admin/users", {
        data: { username, password: "FakeAccessPassword123" },
      });
      expect(user.status()).toBe(201);
      userId = (await user.json()).id;
      const printer = await page.request.post("/api/v1/printers", {
        data: {
          name,
          provider: "moonraker",
          moonraker_url: `http://127.0.0.1:${process.env.PLAYWRIGHT_MOCK_PRINTER_PORT ?? 7126}`,
        },
      });
      expect(printer.status()).toBe(201);
      printerId = (await printer.json()).id;
      const reads: string[] = [];
      page.on("request", (request) => {
        if (
          request.method() === "GET" &&
          new URL(request.url()).pathname === `/api/v1/printers/${printerId}/permissions`
        )
          reads.push(request.url());
      });
      await page.goto("/settings?section=access");
      const card = page.getByRole("group", { name: "Printer access" });
      await expect(card.getByRole("option", { name: username, exact: true })).toHaveCount(1);
      await card
        .getByRole("combobox", { name: "User", exact: true })
        .selectOption({ label: username });
      await card
        .getByRole("combobox", { name: "Printer", exact: true })
        .selectOption({ label: name });
      await card
        .getByRole("combobox", { name: "Role", exact: true })
        .selectOption({ label: "Print" });
      await card.getByRole("button", { name: "Save", exact: true }).click();
      await expect(card.getByText("print", { exact: true })).toBeVisible();
      const grants = await (
        await page.request.get(`/api/v1/printers/${printerId}/permissions`)
      ).json();
      expect(grants).toContainEqual(
        expect.objectContaining({ user_id: userId, printer_id: printerId, role: "print" }),
      );
      await card
        .getByRole("combobox", { name: "Role", exact: true })
        .selectOption({ label: "Control" });
      await card.getByRole("button", { name: "Save", exact: true }).click();
      await expect(card.getByText("control", { exact: true })).toBeVisible();
      await card.getByTitle("Remove printer access", { exact: true }).click();
      await expect(card.getByTitle("Remove printer access", { exact: true })).toHaveCount(0);
      const remaining = await (
        await page.request.get(`/api/v1/printers/${printerId}/permissions`)
      ).json();
      expect(remaining).not.toContainEqual(expect.objectContaining({ user_id: userId }));
      expect(reads).toHaveLength(1);
    } finally {
      if (printerId !== null)
        expect((await page.request.delete(`/api/v1/printers/${printerId}`)).status()).toBe(204);
      if (userId !== null)
        expect((await page.request.delete(`/api/v1/admin/users/${userId}`)).status()).toBe(204);
    }
  });
});
