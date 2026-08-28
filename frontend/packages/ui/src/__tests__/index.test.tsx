import { expect, it } from "vitest";

import * as ui from "../index";

it("exports the shared component and behavior surface", () => {
  expect(Object.keys(ui)).toEqual(
    expect.arrayContaining([
      "Badge",
      "Button",
      "Card",
      "Checkbox",
      "ConfirmModal",
      "Drawer",
      "DropdownMenu",
      "EmptyState",
      "Input",
      "Modal",
      "PageContainer",
      "PageHeader",
      "Separator",
      "Skeleton",
      "Spinner",
      "TabBar",
      "cn",
      "useComboboxNav",
      "useMediaQuery",
    ]),
  );
});
