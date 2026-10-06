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
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ModelTagsDialog } from "@/components/model-tags-dialog";
import { json, renderApp, type RouteAnswer } from "@/test-support/render";
import { aModel, aModelListItem } from "@/test-support/factories";
import { clearLogin } from "@/lib/auth-store";
import type { ModelEditBatchResult, TagRead } from "@/types";

const model = aModelListItem({ id: 1, name: "Cable guide", tags: ["Workshop"], edit_version: 3 });
const tags: TagRead[] = [
  { id: 1, name: "Workshop", slug: "workshop", model_count: 2 },
  { id: 2, name: "Functional", slug: "functional", model_count: 4 },
];
const success: ModelEditBatchResult = {
  succeeded_versions: { 1: 9 },
  succeeded_ids: [1],
  failed: [],
  succeeded_count: 1,
  failed_count: 0,
};

function renderDialog(route: RouteAnswer = json(success)) {
  const onSaved = vi.fn<(tags: string[], version: number) => void>();
  const onClose = vi.fn<() => void>();
  const result = renderApp(
    <ModelTagsDialog model={model} suggestions={tags} open onClose={onClose} onSaved={onSaved} />,
    { routes: { "POST /api/v1/models/batch/tags": route } },
  );
  return { ...result, onSaved, onClose };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ModelTagsDialog", () => {
  it("publishes the confirmed tag version", async () => {
    const user = userEvent.setup();
    function Harness() {
      const [version, setVersion] = useState(model.edit_version);
      return (
        <>
          <output aria-label="Saved version">{version}</output>
          <ModelTagsDialog
            model={model}
            suggestions={tags}
            open
            onClose={() => {}}
            onSaved={(_tags, version) => setVersion(version)}
          />
        </>
      );
    }
    renderApp(<Harness />, { routes: { "POST /api/v1/models/batch/tags": json(success) } });
    await user.click(screen.getByRole("button", { name: "Remove Workshop" }));
    await user.click(screen.getByRole("button", { name: "Save tags" }));
    await waitFor(() => expect(screen.getByLabelText("Saved version")).toHaveTextContent("9"));
  });

  it("assigns an existing tag", async () => {
    const user = userEvent.setup();
    const { requestsWithMethod } = renderDialog();

    await user.type(screen.getByLabelText("Search or create a tag"), "Func");
    await user.click(screen.getByRole("option", { name: /Functional/ }));
    await user.click(screen.getByRole("button", { name: "Save tags" }));

    await waitFor(() =>
      expect(JSON.parse(requestsWithMethod("POST")[0]?.body ?? "{}")).toMatchObject({
        model_ids: [1],
        expected_versions: { 1: 3 },
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

describe("ModelTagsDialog conflict recovery", () => {
  const conflict: ModelEditBatchResult = {
    succeeded_versions: {},
    succeeded_ids: [],
    succeeded_count: 0,
    failed: [{ model_id: 1, reason: "edit_conflict" }],
    failed_count: 1,
  };

  it("requires explicit review after a conflict", async () => {
    const user = userEvent.setup();
    const result = renderDialog(json(conflict));
    await user.click(screen.getByRole("button", { name: "Remove Workshop" }));
    await user.click(screen.getByRole("button", { name: "Save tags" }));
    expect(await screen.findByRole("button", { name: "Review latest version" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Save tags" })).toBeDisabled();
    expect(screen.getByText("No tags assigned yet.")).toBeVisible();
    expect(result.requestsWithMethod("POST")).toHaveLength(1);
  });

  it("saves the reviewed draft against the exact reviewed version", async () => {
    const user = userEvent.setup();
    const result = renderDialog(json(conflict));
    result.route({
      "GET /api/v1/models/1": json(
        aModel({ id: 1, tags: ["Workshop", "Remote"], edit_version: 7, effective_role: "edit" }),
      ),
    });
    await user.click(screen.getByRole("button", { name: "Remove Workshop" }));
    await user.click(screen.getByRole("button", { name: "Save tags" }));
    await user.click(await screen.findByRole("button", { name: "Review latest version" }));
    expect(await screen.findByText("Workshop, Remote")).toBeVisible();
    result.route({ "POST /api/v1/models/batch/tags": json(success) });
    await user.click(screen.getByRole("button", { name: "Save my draft against this version" }));
    await waitFor(() => expect(result.onSaved).toHaveBeenCalledWith([], 9));
    expect(JSON.parse(result.requestsWithMethod("POST")[1].body)).toEqual({
      model_ids: [1],
      add: [],
      remove: ["Workshop", "Remote"],
      expected_versions: { 1: 7 },
    });
  });

  it("adopts the latest tags only by explicit choice", async () => {
    const user = userEvent.setup();
    const result = renderDialog(json(conflict));
    result.route({
      "GET /api/v1/models/1": json(
        aModel({ id: 1, tags: ["Remote"], edit_version: 7, effective_role: "edit" }),
      ),
    });
    await user.click(screen.getByRole("button", { name: "Remove Workshop" }));
    await user.click(screen.getByRole("button", { name: "Save tags" }));
    await user.click(await screen.findByRole("button", { name: "Review latest version" }));
    await user.click(await screen.findByRole("button", { name: "Use latest version" }));
    expect(screen.getByRole("button", { name: "Remove Remote" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Save tags" })).toBeDisabled();
    expect(result.requestsWithMethod("POST")).toHaveLength(1);
  });

  it("keeps a failed review recoverable", async () => {
    const user = userEvent.setup();
    const result = renderDialog(json(conflict));
    result.route({ "GET /api/v1/models/1": json({ detail: "unavailable" }, 503) });
    await user.click(screen.getByRole("button", { name: "Remove Workshop" }));
    await user.click(screen.getByRole("button", { name: "Save tags" }));
    await user.click(await screen.findByRole("button", { name: "Review latest version" }));
    await waitFor(() => expect(result.requestsWithMethod("GET")).toHaveLength(1));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Review latest version" })).toBeEnabled(),
    );
    expect(screen.getByText("No tags assigned yet.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Save tags" })).toBeDisabled();
  });

  it("prevents retry after reviewed edit permission loss", async () => {
    const user = userEvent.setup();
    const result = renderDialog(json(conflict));
    result.route({
      "GET /api/v1/models/1": json(aModel({ id: 1, edit_version: 7, effective_role: "view" })),
    });
    await user.click(screen.getByRole("button", { name: "Remove Workshop" }));
    await user.click(screen.getByRole("button", { name: "Save tags" }));
    await user.click(await screen.findByRole("button", { name: "Review latest version" }));
    expect(
      await screen.findByRole("button", { name: "Save my draft against this version" }),
    ).toBeDisabled();
    expect(result.requestsWithMethod("POST")).toHaveLength(1);
  });

  it("ignores acknowledgement after disposal", async () => {
    const user = userEvent.setup();
    const pending = Promise.withResolvers<Response>();
    const result = renderDialog(() => pending.promise);
    await user.click(screen.getByRole("button", { name: "Remove Workshop" }));
    await user.click(screen.getByRole("button", { name: "Save tags" }));
    result.unmount();
    await act(async () => pending.resolve(json(success)));
    expect(result.onSaved).not.toHaveBeenCalled();
    expect(result.onClose).not.toHaveBeenCalled();
  });

  it("keeps a pending save open on Escape", async () => {
    const user = userEvent.setup();
    const pending = Promise.withResolvers<Response>();
    const result = renderDialog(() => pending.promise);
    await user.click(screen.getByRole("button", { name: "Remove Workshop" }));
    await user.click(screen.getByRole("button", { name: "Save tags" }));
    await user.keyboard("{Escape}");
    expect(result.onClose).not.toHaveBeenCalled();
    await act(async () => pending.resolve(json(success)));
  });

  it("requires review after an unconfirmed write", async () => {
    const user = userEvent.setup();
    renderDialog(json({ detail: "unavailable" }, 503));
    await user.click(screen.getByRole("button", { name: "Remove Workshop" }));
    await user.click(screen.getByRole("button", { name: "Save tags" }));
    expect(await screen.findByRole("button", { name: "Review latest version" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Save tags" })).toBeDisabled();
  });

  it("retires private tag UI on session change", async () => {
    renderDialog();
    await act(async () => clearLogin());
    expect(screen.queryByText("Cable guide")).toBeNull();
    expect(screen.queryByLabelText("Search or create a tag")).toBeNull();
  });

  it.each([403, 404])("hides the private draft after a denied review (%s)", async (status) => {
    const user = userEvent.setup();
    const result = renderDialog(json(conflict));
    result.route({ "GET /api/v1/models/1": json({ detail: "unavailable" }, status) });
    await user.click(screen.getByRole("button", { name: "Remove Workshop" }));
    await user.click(screen.getByRole("button", { name: "Save tags" }));
    await user.click(await screen.findByRole("button", { name: "Review latest version" }));
    await waitFor(() => expect(screen.queryByText("Cable guide")).toBeNull());
    expect(screen.queryByLabelText("Search or create a tag")).toBeNull();
    expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
  });

  it("aborts review when the dialog closes", async () => {
    const user = userEvent.setup();
    const pending = Promise.withResolvers<Response>();
    let signal: AbortSignal | null | undefined;
    const result = renderDialog(json(conflict));
    result.route({
      "GET /api/v1/models/1": (_url, options) => {
        signal = options?.signal;
        return pending.promise;
      },
    });
    await user.click(screen.getByRole("button", { name: "Remove Workshop" }));
    await user.click(screen.getByRole("button", { name: "Save tags" }));
    await user.click(await screen.findByRole("button", { name: "Review latest version" }));
    await waitFor(() => expect(signal).toBeDefined());
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(signal?.aborted).toBe(true);
    await act(async () => pending.resolve(json(aModel({ tags: ["Late private tag"] }))));
    expect(screen.queryByText("Late private tag")).toBeNull();
    expect(screen.queryByRole("button", { name: "Save my draft against this version" })).toBeNull();
  });

  it("freezes selection while save is pending", async () => {
    const user = userEvent.setup();
    const pending = Promise.withResolvers<Response>();
    const result = renderDialog(() => pending.promise);
    await user.type(screen.getByLabelText("Search or create a tag"), "Functional");
    await user.keyboard("{Enter}");
    await user.click(screen.getByRole("button", { name: "Save tags" }));
    expect(screen.getByLabelText("Search or create a tag")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Remove Functional" })).toBeDisabled();
    await act(async () => pending.resolve(json(success)));
    expect(result.onSaved).toHaveBeenCalledWith(["Workshop", "Functional"], 9);
  });

  it("requires a new review when the reviewed base changes again", async () => {
    const user = userEvent.setup();
    const result = renderDialog(json(conflict));
    result.route({
      "GET /api/v1/models/1": json(
        aModel({ id: 1, tags: ["Remote"], edit_version: 7, effective_role: "edit" }),
      ),
    });
    await user.click(screen.getByRole("button", { name: "Remove Workshop" }));
    await user.click(screen.getByRole("button", { name: "Save tags" }));
    await user.click(await screen.findByRole("button", { name: "Review latest version" }));
    await user.click(
      await screen.findByRole("button", { name: "Save my draft against this version" }),
    );
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "Save my draft against this version" }),
      ).toBeNull(),
    );
    expect(screen.getByRole("button", { name: "Save tags" })).toBeDisabled();
    expect(result.requestsWithMethod("POST")).toHaveLength(2);
    expect(JSON.parse(result.requestsWithMethod("POST")[1].body).expected_versions).toEqual({
      1: 7,
    });
  });
});
