/*
 * Quick tag assignment for one Model.
 *
 * The dialog deliberately treats tag names case-insensitively: choosing
 * "FUNCTIONAL" must reuse the existing "Functional" taxonomy entry rather
 * than creating a duplicate that only differs by casing. Changes remain local
 * until Save so Cancel is a real escape hatch, while a refused write keeps the
 * user's pending choices visible for retry.
 */

import "@testing-library/jest-dom/vitest";
import { useState } from "react";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ModelTagsDialog } from "@/components/model-tags-dialog";
import { json, renderApp } from "@/test-support/render";
import type { ModelBatchResult, TagRead } from "@/types";

const model = { id: 1, name: "Cable guide", tags: ["Workshop"] };
const tags: TagRead[] = [
  { id: 1, name: "Workshop", slug: "workshop", model_count: 2 },
  { id: 2, name: "Functional", slug: "functional", model_count: 4 },
];
const success: ModelBatchResult = {
  succeeded_ids: [1],
  failed: [],
  succeeded_count: 1,
  failed_count: 0,
};

function renderDialog(route = json(success)) {
  const onSaved = vi.fn<(tags: string[]) => void>();
  const result = renderApp(
    <ModelTagsDialog
      model={model}
      suggestions={tags}
      open
      onClose={vi.fn<() => void>()}
      onSaved={onSaved}
    />,
    { routes: { "POST /api/v1/models/batch/tags": route } },
  );
  return { ...result, onSaved };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ModelTagsDialog", () => {
  it("assigns an existing tag", async () => {
    const user = userEvent.setup();
    const { requestsWithMethod } = renderDialog();

    await user.type(screen.getByLabelText("Search or create a tag"), "Func");
    await user.click(screen.getByRole("option", { name: /Functional/ }));
    await user.click(screen.getByRole("button", { name: "Save tags" }));

    await waitFor(() =>
      expect(JSON.parse(requestsWithMethod("POST")[0]?.body ?? "{}")).toMatchObject({
        model_ids: [1],
        add: ["Functional"],
        remove: [],
      }),
    );
  });

  it("creates a new tag through the assignment", async () => {
    const user = userEvent.setup();
    const { requestsWithMethod } = renderDialog();

    await user.type(screen.getByLabelText("Search or create a tag"), "Prototype");
    await user.click(screen.getByRole("option", { name: /Create tag/ }));
    await user.click(screen.getByRole("button", { name: "Save tags" }));

    await waitFor(() =>
      expect(JSON.parse(requestsWithMethod("POST")[0]?.body ?? "{}")).toMatchObject({
        add: ["Prototype"],
      }),
    );
  });

  it("removes an assigned tag", async () => {
    const user = userEvent.setup();
    const { requestsWithMethod } = renderDialog();

    await user.click(screen.getByRole("button", { name: "Remove Workshop" }));
    await user.click(screen.getByRole("button", { name: "Save tags" }));

    await waitFor(() =>
      expect(JSON.parse(requestsWithMethod("POST")[0]?.body ?? "{}")).toMatchObject({
        add: [],
        remove: ["Workshop"],
      }),
    );
  });

  it("keeps save disabled without changes", () => {
    renderDialog();

    expect(screen.getByRole("button", { name: "Save tags" })).toBeDisabled();
  });

  it("reuses the canonical name for a case-insensitive match", async () => {
    const user = userEvent.setup();
    renderDialog();

    await user.type(screen.getByLabelText("Search or create a tag"), "FUNCTIONAL");

    expect(screen.queryByRole("option", { name: /Create tag/ })).toBeNull();
    await user.keyboard("{Enter}");
    expect(screen.getByText("Functional")).toBeInTheDocument();
  });

  it("discards pending tags when cancelled", async () => {
    const user = userEvent.setup();

    function Harness() {
      const [open, setOpen] = useState(true);
      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>
            Open tags
          </button>
          <ModelTagsDialog
            model={model}
            suggestions={tags}
            open={open}
            onClose={() => setOpen(false)}
            onSaved={vi.fn<(tags: string[]) => void>()}
          />
        </>
      );
    }

    renderApp(<Harness />);
    await user.type(screen.getByLabelText("Search or create a tag"), "Prototype");
    await user.click(screen.getByRole("option", { name: /Create tag/ }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await user.click(screen.getByRole("button", { name: "Open tags" }));

    expect(screen.queryByText("Prototype")).toBeNull();
    expect(screen.getByText("Workshop")).toBeInTheDocument();
  });

  it("keeps pending choices after a server error", async () => {
    const user = userEvent.setup();
    renderDialog(json({ detail: "forbidden" }, 403));

    await user.type(screen.getByLabelText("Search or create a tag"), "Prototype");
    await user.click(screen.getByRole("option", { name: /Create tag/ }));
    await user.click(screen.getByRole("button", { name: "Save tags" }));

    expect(await screen.findByRole("dialog", { name: "Model tags" })).toBeInTheDocument();
    expect(screen.getByText("Prototype")).toBeInTheDocument();
  });
});
