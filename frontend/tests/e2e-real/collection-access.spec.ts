/** Collection grants load for the selected folder, even in a nested tree. */
import { test, expect } from "./helpers";

test.describe("collection access", () => {
  test("grants access to a searched nested collection without loading every collection", async ({
    page,
  }) => {
    const stamp = Date.now();
    const parentName = `access-parent-${stamp}`;
    const childName = `access-child-${stamp}`;
    const username = `access-user-${stamp}`;
    let parentId: number | null = null;
    let childId: number | null = null;
    let userId: number | null = null;

    try {
      const parentResponse = await page.request.post("/api/v1/collections", {
        data: { name: parentName },
      });
      expect(parentResponse.status()).toBe(201);
      parentId = (await parentResponse.json()).id;
      const childResponse = await page.request.post("/api/v1/collections", {
        data: { name: childName, parent_id: parentId },
      });
      expect(childResponse.status()).toBe(201);
      childId = (await childResponse.json()).id;
      const userResponse = await page.request.post("/api/v1/admin/users", {
        data: { username, password: "accesspass123" },
      });
      expect(userResponse.status()).toBe(201);
      userId = (await userResponse.json()).id;

      const wholeTreeRequests: string[] = [];
      const permissionsReadIds: number[] = [];
      page.on("request", (request) => {
        const pathname = new URL(request.url()).pathname;
        if (pathname === "/api/v1/collections") wholeTreeRequests.push(request.url());
        const match = pathname.match(/^\/api\/v1\/collections\/(\d+)\/permissions$/);
        if (match && request.method() === "GET") permissionsReadIds.push(Number(match[1]));
      });
      await page.goto("/settings?section=access");
      const card = page.getByRole("group", { name: "Collection access" });
      await expect(card).toBeVisible();
      await card
        .getByRole("combobox")
        .filter({ has: page.getByRole("option", { name: "Select user" }) })
        .selectOption({ label: username });
      await expect(card.getByText("Select a collection to review grants.")).toBeVisible();
      expect(permissionsReadIds).toEqual([]);

      await card.getByRole("button", { name: "Select collection" }).click();
      const picker = card.getByRole("dialog");
      await picker.getByPlaceholder("Find destination").fill(childName);
      await picker
        .getByRole("option", { name: new RegExp(`^${parentName}/${childName} `) })
        .click();
      await expect(card.getByRole("button", { name: `${parentName}/${childName}` })).toBeVisible();
      await expect.poll(() => permissionsReadIds).toEqual([childId]);
      await card
        .getByRole("combobox")
        .filter({ has: page.getByRole("option", { name: "Admin", exact: true }) })
        .selectOption({ label: "Edit" });
      await card.getByRole("button", { name: "Grant" }).click();
      await expect(card.getByText("edit", { exact: true })).toBeVisible();

      const grants = await (
        await page.request.get(`/api/v1/collections/${childId}/permissions`)
      ).json();
      expect(grants).toContainEqual(expect.objectContaining({ user_id: userId, role: "edit" }));
      expect(wholeTreeRequests).toEqual([]);
      expect(permissionsReadIds.every((id) => id === childId)).toBe(true);
    } finally {
      if (userId !== null) {
        expect((await page.request.delete(`/api/v1/admin/users/${userId}`)).status()).toBe(204);
      }
      if (childId !== null) {
        expect((await page.request.delete(`/api/v1/collections/${childId}`)).status()).toBe(204);
      }
      if (parentId !== null) {
        expect((await page.request.delete(`/api/v1/collections/${parentId}`)).status()).toBe(204);
      }
    }
  });
});
