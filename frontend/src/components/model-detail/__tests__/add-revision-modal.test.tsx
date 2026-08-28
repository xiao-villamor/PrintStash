/*
 * Adding another slice of a model without losing the ones before it.
 *
 * A revision is not a replacement. Earlier slices keep their settings and their
 * print history, which is the whole reason the feature exists — so what this
 * modal must never do is accept a file that is not G-code. A mesh or an archive
 * uploaded here would land in the revision chain as something no printer can
 * run, and the user finds out at the printer.
 *
 * The label and the notes are how a person tells two slices apart six months
 * later ("stronger walls", "0.2 draft"). They are optional, and an empty one has
 * to be *absent* rather than sent as an empty string, which would read as a
 * revision deliberately labelled with nothing.
 *
 * "Recommended" is the sharpest edge: it moves what downloads and printer sends
 * use, replacing the current recommendation. Defaulting it on would silently
 * repoint every send at an untested slice, so it starts off and travels
 * explicitly either way.
 */

import "@testing-library/jest-dom/vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AddGcodeRevisionModal } from "@/components/model-detail/add-revision-modal";
import { json, renderApp, type RenderAppOptions } from "@/test-support/render";
import type { ModelRead } from "@/types";

const FROZEN_NOW = "2026-01-01T00:00:00Z";

function aModel(over: Partial<ModelRead> = {}): ModelRead {
  return {
    id: 1,
    name: "Benchy",
    slug: "benchy",
    hash: "a".repeat(64),
    collection: null,
    collection_id: null,
    description: null,
    source_url: null,
    effective_role: null,
    tags: [],
    thumbnail_url: null,
    created_at: FROZEN_NOW,
    updated_at: FROZEN_NOW,
    files: [],
    starred: false,
    ...over,
  };
}

/** A file the modal should accept, named for the extension under test. */
function aGcodeFile(name = "walls.gcode"): File {
  return new File(["G28\nG1 X0\n"], name, { type: "text/plain" });
}

function renderModal(options: RenderAppOptions & { answer?: Response } = {}) {
  const { answer, routes = {}, ...rest } = options;
  const onClose = vi.fn<() => void>();
  const onUploaded = vi.fn<(model: ModelRead) => void>();
  // A revision is uploaded as multipart, so the request body is a `FormData`
  // rather than a string — the fields have to be read off it here, at the
  // route, instead of from the recorded body text.
  let sent: FormData | null = null;
  const result = renderApp(
    <AddGcodeRevisionModal modelId={1} onClose={onClose} onUploaded={onUploaded} />,
    {
      routes: {
        "POST /api/v1/models/1/gcode-revisions": (_url, init) => {
          const body = init?.body;
          if (body instanceof FormData) sent = body;
          return answer ?? json(aModel());
        },
        ...routes,
      },
      ...rest,
    },
  );
  return { ...result, onClose, onUploaded, sent: () => sent };
}

/** Put a file into the modal's hidden input, as the file picker would. */
async function choose(user: ReturnType<typeof userEvent.setup>, file: File) {
  // SAFETY: the modal always renders exactly one file input, asserted by every
  // test in this file failing to find a dropzone otherwise.
  const input = document.querySelector("input[type=file]") as HTMLInputElement;
  await user.upload(input, file);
}

/**
 * Drop a file on the empty-state dropzone. The picker filters by extension, so a
 * drop is the only way a non-G-code file reaches the modal at all.
 */
function drop(file: File) {
  fireEvent.drop(screen.getByRole("button", { name: /Choose G-code/ }), {
    dataTransfer: { files: [file], types: ["Files"] },
  });
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AddGcodeRevisionModal", () => {
  describe("what it asks for", () => {
    it("says what a revision is for", () => {
      renderModal();

      expect(
        screen.getByText(
          "Upload another slice while keeping earlier settings and print history available.",
        ),
      ).toBeInTheDocument();
    });

    it("names the file formats it takes", () => {
      renderModal();

      expect(screen.getByText(".gcode, .g, or .gco")).toBeInTheDocument();
    });

    it("cannot be submitted before a file is chosen", () => {
      renderModal();

      expect(screen.getByRole("button", { name: /Add revision/ })).toBeDisabled();
    });
  });

  describe("choosing the file", () => {
    it("shows the chosen file so the user can check it", async () => {
      const user = userEvent.setup();
      renderModal();

      await choose(user, aGcodeFile());

      expect(await screen.findByText("walls.gcode")).toBeInTheDocument();
    });

    it("refuses a file that is not G-code", async () => {
      // A mesh in the revision chain is something no printer can run, and the
      // user finds that out at the printer. The picker filters by extension, so
      // the way a wrong file actually arrives is a drop.
      renderModal();

      drop(new File(["solid"], "benchy.stl", { type: "model/stl" }));

      expect(await screen.findByRole("alert")).toHaveTextContent(
        "Choose a .gcode, .g, or .gco file.",
      );
    });

    it("keeps the form unsubmittable after a rejected file", async () => {
      renderModal();

      drop(new File(["solid"], "benchy.stl", { type: "model/stl" }));

      await screen.findByRole("alert");
      expect(screen.getByRole("button", { name: /Add revision/ })).toBeDisabled();
    });

    it("accepts the short .g extension too", async () => {
      // Several slicers write `.g`; rejecting it would send those users away.
      const user = userEvent.setup();
      renderModal();

      await choose(user, aGcodeFile("draft.g"));

      expect(await screen.findByText("draft.g")).toBeInTheDocument();
    });

    it("accepts .gco", async () => {
      const user = userEvent.setup();
      renderModal();

      await choose(user, aGcodeFile("draft.gco"));

      expect(await screen.findByText("draft.gco")).toBeInTheDocument();
    });

    it("lets the user take the file back off", async () => {
      const user = userEvent.setup();
      renderModal();
      await choose(user, aGcodeFile());

      await user.click(await screen.findByRole("button", { name: "Remove selected file" }));

      expect(screen.queryByText("walls.gcode")).toBeNull();
    });
  });

  describe("submitting the revision", () => {
    it("uploads the file the user chose", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderModal();
      await choose(user, aGcodeFile());

      await user.click(screen.getByRole("button", { name: /Add revision/ }));

      await waitFor(() =>
        expect(
          requestsWithMethod("POST").some((call) => call.url.includes("/gcode-revisions")),
        ).toBe(true),
      );
    });

    it("hands the updated model back to the page", async () => {
      // The detail page owns the revision list; a revision the modal keeps to
      // itself is invisible until a reload.
      const user = userEvent.setup();
      const { onUploaded } = renderModal();
      await choose(user, aGcodeFile());

      await user.click(screen.getByRole("button", { name: /Add revision/ }));

      await waitFor(() => expect(onUploaded).toHaveBeenCalled());
    });

    it("marks a new revision as needing a test print", async () => {
      // Nothing has printed it yet, so calling it tested would put an unproven
      // slice at the top of the list.
      const user = userEvent.setup();
      const { sent } = renderModal();
      await choose(user, aGcodeFile());

      await user.click(screen.getByRole("button", { name: /Add revision/ }));

      await waitFor(() => expect(sent()?.get("revision_status")).toBe("needs_test"));
    });

    it("does not recommend the revision unless asked", async () => {
      // Recommending replaces what every download and printer send uses.
      const user = userEvent.setup();
      const { sent } = renderModal();
      await choose(user, aGcodeFile());

      await user.click(screen.getByRole("button", { name: /Add revision/ }));

      await waitFor(() => expect(sent()?.get("is_recommended")).toBe("false"));
    });

    it("recommends it when the user asks", async () => {
      const user = userEvent.setup();
      const { sent } = renderModal();
      await choose(user, aGcodeFile());
      await user.click(screen.getByLabelText("Mark as recommended"));

      await user.click(screen.getByRole("button", { name: /Add revision/ }));

      await waitFor(() => expect(sent()?.get("is_recommended")).toBe("true"));
    });

    it("carries the label the user wrote", async () => {
      const user = userEvent.setup();
      const { sent } = renderModal();
      await choose(user, aGcodeFile());
      await user.type(screen.getByPlaceholderText("e.g. Stronger walls"), "Stronger walls");

      await user.click(screen.getByRole("button", { name: /Add revision/ }));

      await waitFor(() => expect(sent()?.get("revision_label")).toBe("Stronger walls"));
    });

    it("carries the notes the user wrote", async () => {
      const user = userEvent.setup();
      const { sent } = renderModal();
      await choose(user, aGcodeFile());
      await user.type(screen.getByPlaceholderText("What changed in this slice?"), "Wall loops 4");

      await user.click(screen.getByRole("button", { name: /Add revision/ }));

      await waitFor(() => expect(sent()?.get("revision_notes")).toBe("Wall loops 4"));
    });

    it("sends no label at all when the field is left blank", async () => {
      // An empty string reads back as a revision deliberately labelled nothing.
      const user = userEvent.setup();
      const { sent } = renderModal();
      await choose(user, aGcodeFile());

      await user.click(screen.getByRole("button", { name: /Add revision/ }));

      await waitFor(() => expect(sent()?.has("revision_label")).toBe(false));
    });

    it("surfaces a revision the server refused", async () => {
      const user = userEvent.setup();
      renderModal({
        routes: {
          "POST /api/v1/models/1/gcode-revisions": json({ detail: "duplicate_revision" }, 409),
        },
      });
      await choose(user, aGcodeFile());

      await user.click(screen.getByRole("button", { name: /Add revision/ }));

      expect(await screen.findByRole("alert")).toBeInTheDocument();
    });

    it("leaves the modal open after a failure so the work is not lost", async () => {
      // Closing on error throws away the label and notes the user just typed.
      const user = userEvent.setup();
      const { onClose } = renderModal({
        routes: {
          "POST /api/v1/models/1/gcode-revisions": json({ detail: "duplicate_revision" }, 409),
        },
      });
      await choose(user, aGcodeFile());

      await user.click(screen.getByRole("button", { name: /Add revision/ }));

      await screen.findByRole("alert");
      expect(onClose).not.toHaveBeenCalled();
    });
  });

  describe("backing out", () => {
    it("closes when the user cancels", async () => {
      const user = userEvent.setup();
      const { onClose } = renderModal();

      await user.click(screen.getByRole("button", { name: "Cancel" }));

      expect(onClose).toHaveBeenCalled();
    });
  });
});
