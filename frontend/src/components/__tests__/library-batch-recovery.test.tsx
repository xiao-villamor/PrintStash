/** Interrupted commands retain their original uncertainty while confirmed-only undo runs. */
import "@testing-library/jest-dom/vitest";
import { useState } from "react";
import { act, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { LibraryBatchRecovery } from "../library-batch-recovery";
import { moveLibraryModels, type LibraryEditReceipt } from "@/features/library/batch-edits";
import { aModelListItem, anEditingBase } from "@/test-support/factories";
import { json, renderApp } from "@/test-support/render";
import { clearLogin } from "@/lib/auth-store";

const models = Array.from({ length: 501 }, (_, index) =>
  aModelListItem({ id: index + 1, name: `Model ${index + 1}`, collection: "original" }),
);
const ids = models.slice(0, 500).map((model) => model.id);
const acknowledgement = {
  succeeded_ids: ids,
  succeeded_count: ids.length,
  succeeded_versions: Object.fromEntries(ids.map((id) => [id, anEditingBase({ edit_version: 9 })])),
  failed: [],
  failed_count: 0,
};
const undone = {
  ...acknowledgement,
  succeeded_versions: Object.fromEntries(
    ids.map((id) => [id, anEditingBase({ edit_version: 12 })]),
  ),
};

function Review({ show = true }: { show?: boolean }) {
  const [receipt, setReceipt] = useState<LibraryEditReceipt | null>(null);
  const [changed, setChanged] = useState(false);
  return (
    <>
      <button onClick={() => void moveLibraryModels(models, "destination").then(setReceipt)}>
        Start
      </button>
      <output aria-label="Displayed result">{changed ? "refreshed" : "original"}</output>
      {receipt && show && (
        <LibraryBatchRecovery
          receipt={receipt}
          models={models}
          intent={["Move to: destination"]}
          onChanged={() => setChanged(true)}
          onClose={() => setReceipt(null)}
        />
      )}
    </>
  );
}

describe("LibraryBatchRecovery", () => {
  it("retains the original uncertainty after undoing confirmed changes", async () => {
    const answer = Promise.withResolvers<Response>();
    const responses = [json(acknowledgement), json({ detail: "unavailable" }, 503), answer.promise];
    const user = userEvent.setup();
    const app = renderApp(<Review />, {
      routes: {
        "POST /api/v1/models/batch/move": () => {
          const response = responses.shift();
          if (!response) throw new Error("Unexpected batch write");
          return response;
        },
      },
    });
    await user.click(screen.getByRole("button", { name: "Start" }));
    const dialog = await screen.findByRole("dialog", { name: "Batch needs review" });
    expect(within(dialog).getByText("500 confirmed")).toBeVisible();

    await user.click(within(dialog).getByRole("button", { name: "Undo confirmed changes" }));

    expect(within(dialog).getByRole("button", { name: "Undo confirmed changes" })).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "Discard remaining batch" })).toBeDisabled();
    await act(async () => answer.resolve(json(undone)));
    const result = await within(dialog).findByRole("region", { name: "Undo result" });
    expect(within(result).getByText("500 confirmed")).toBeVisible();
    expect(within(dialog).getByRole("link", { name: "Model 501" })).toHaveAttribute(
      "href",
      "/models/501",
    );
    expect(within(dialog).getByText("1 unconfirmed")).toBeVisible();
    expect(screen.getByLabelText("Displayed result")).toHaveTextContent("refreshed");
    const request = JSON.parse(app.requestsWithMethod("POST")[2].body);
    expect(request.model_ids).toEqual(ids);
    expect(request.expected_versions[500]).toEqual(anEditingBase({ edit_version: 9 }));
    expect(request.expected_versions[501]).toBeUndefined();
  });

  it.each([
    { label: "session", retire: () => act(() => clearLogin()) },
    {
      label: "view",
      retire: (app: ReturnType<typeof renderApp>) => app.rerender(<Review show={false} />),
    },
  ])("suppresses late undo presentation after $label retirement", async ({ retire }) => {
    const answer = Promise.withResolvers<Response>();
    const responses = [json(acknowledgement), json({ detail: "unavailable" }, 503), answer.promise];
    const user = userEvent.setup();
    const app = renderApp(<Review />, {
      routes: {
        "POST /api/v1/models/batch/move": () => {
          const response = responses.shift();
          if (!response) throw new Error("Unexpected batch write");
          return response;
        },
      },
    });
    await user.click(screen.getByRole("button", { name: "Start" }));
    const dialog = await screen.findByRole("dialog", { name: "Batch needs review" });
    await user.click(within(dialog).getByRole("button", { name: "Undo confirmed changes" }));

    retire(app);
    await act(async () => answer.resolve(json(undone)));

    expect(screen.queryByRole("region", { name: "Undo result" })).toBeNull();
    expect(screen.getByLabelText("Displayed result")).toHaveTextContent("original");
  });
});
