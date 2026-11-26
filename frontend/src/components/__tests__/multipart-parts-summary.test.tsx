/** The picked models above the multipart browser: each is removable, and the count is plain text. */
import "@testing-library/jest-dom/vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { MultipartPartsSummary } from "@/components/multipart-parts-summary";
import { aModelListItem } from "@/test-support/factories";
import { renderApp } from "@/test-support/render";
import type { ModelListItem } from "@/types";

function setup(models: ModelListItem[], disabled = false) {
  const onRemove = vi.fn<(modelId: number) => void>();
  renderApp(
    <MultipartPartsSummary models={models} limit={100} disabled={disabled} onRemove={onRemove} />,
  );
  return { onRemove };
}

describe("MultipartPartsSummary", () => {
  it("prompts to pick models when none are chosen", () => {
    setup([]);
    expect(screen.getByRole("heading", { name: "Parts in this set" })).toBeVisible();
    expect(screen.getByText("0 of 100")).toBeVisible();
    expect(screen.getByText("No parts yet. Pick models below.")).toBeVisible();
  });

  it("lists each model with its folder, falling back to Vault only", () => {
    setup([
      aModelListItem({ id: 1, name: "Base", collection_label: "Workshop/Brackets" }),
      aModelListItem({ id: 2, name: "Lid", collection_label: null }),
    ]);
    expect(screen.getByText("2 of 100")).toBeVisible();
    expect(screen.getByText("Base")).toBeVisible();
    expect(screen.getByText("Workshop/Brackets")).toBeVisible();
    expect(screen.getByText("Lid")).toBeVisible();
    expect(screen.getByText("Vault only")).toBeVisible();
  });

  it("removes the model whose button was pressed", async () => {
    const { onRemove } = setup([
      aModelListItem({ id: 1, name: "Base" }),
      aModelListItem({ id: 2, name: "Lid" }),
    ]);
    await userEvent.click(screen.getByRole("button", { name: "Remove Lid" }));
    expect(onRemove).toHaveBeenCalledExactlyOnceWith(2);
  });

  it("locks the remove buttons while the set is being created", () => {
    setup([aModelListItem({ id: 1, name: "Base" })], true);
    expect(screen.getByRole("button", { name: "Remove Base" })).toBeDisabled();
  });
});
