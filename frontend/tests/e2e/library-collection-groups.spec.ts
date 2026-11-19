/** Grouped child results keep the collection strip and persist an inline expansion. */
import { expect, test } from "@playwright/test";
import { aCollectionNode, aModelListItem } from "../../src/test-support/factories";
import { useMockApi } from "./_setup";

useMockApi();
test.use({ viewport: { width: 1920, height: 1080 } });

test("remembers the inline collection expansion", async ({ page }) => {
  const parent = aCollectionNode({
    id: 50,
    name: "Workshop",
    path: "workshop",
    child_count: 1,
    model_count: 9,
  });
  const child = aCollectionNode({
    id: 51,
    parent_id: parent.id,
    name: "Brackets",
    path: "workshop/brackets",
    model_count: 8,
  });
  await page.route("**/api/v1/collections/lookup?**", (route) =>
    route.fulfill({ json: { collection: parent, ancestors: [] } }),
  );
  await page.route("**/api/v1/collections/children?**", (route) =>
    route.fulfill({ json: { items: [child], next_cursor: null } }),
  );
  await page.route("**/api/v1/models/browse?**", (route) => {
    const grouped = new URL(route.request().url()).searchParams.get("collection") === child.path;
    const models = grouped
      ? Array.from({ length: 8 }, (_, index) =>
          aModelListItem({
            id: index + 2,
            name: `Bracket ${index + 1}`,
            collection: child.path,
            collection_id: child.id,
          }),
        )
      : [
          aModelListItem({
            id: 1,
            name: "Tool holder",
            collection: parent.path,
            collection_id: parent.id,
          }),
        ];
    return route.fulfill({
      json: {
        items: models.map((model) => ({ kind: "model", model })),
        total: models.length,
        next_cursor: null,
        browse_revision: "r1",
        authorization_revision: "a1",
      },
    });
  });

  await page.goto("/?c=workshop&cid=50&scope=here");
  const group = page.getByRole("heading", { name: "Brackets", exact: true }).locator("../..");
  await group.scrollIntoViewIfNeeded();
  await expect(group.getByRole("link", { name: "Open collection" })).toHaveAttribute(
    "href",
    /c=workshop%2Fbrackets/,
  );
  await expect(group.getByRole("link", { name: "Bracket 8", exact: true })).toHaveCount(0);
  await group.getByRole("button", { name: "Show all 8" }).click();
  await expect(group.getByRole("link", { name: "Bracket 8", exact: true })).toBeVisible();
  await page.reload();
  await group.scrollIntoViewIfNeeded();
  await expect(group.getByRole("button", { name: "Show fewer" })).toHaveAttribute(
    "aria-expanded",
    "true",
  );
  await page.goto("/?c=workshop&cid=50&scope=all");
  await expect(group).toHaveCount(0);
});

test("clamps the sidebar to the translated header width", async ({ page }) => {
  await page.goto("/");
  const sidebar = page.getByRole("complementary", { name: "Collections", exact: true });
  const handle = sidebar.getByRole("separator", { name: "Resize collections sidebar" });
  await handle.focus();
  await handle.press("Home");
  const title = sidebar.getByRole("heading", { name: "Collections", exact: true });
  const first = sidebar.getByRole("button", { name: "New collection", exact: true });
  const last = sidebar.getByRole("button", { name: "Collapse", exact: true });
  const pane = (await sidebar.boundingBox())!;
  const label = (await title.boundingBox())!;
  const start = (await first.boundingBox())!;
  const end = (await last.boundingBox())!;
  expect(start.x - (label.x + label.width)).toBeCloseTo(8);
  expect(end.x + end.width).toBeLessThan(pane.x + pane.width);
});
