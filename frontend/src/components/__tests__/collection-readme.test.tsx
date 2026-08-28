/*
 * The description that sits above a folder's models.
 *
 * It is optional, and that shapes everything here: a folder with no description
 * and a viewer who cannot write one renders *nothing*, not an empty box. An
 * always-present placeholder pushes the model grid down on every folder in the
 * vault to advertise a feature most folders will never use.
 *
 * A long description is clamped rather than shown in full, because a technical
 * one runs to screens and buries the list it is supposed to introduce. The
 * "Show more" affordance only appears when there is more to show — offering it
 * over a two-line description is a control that does nothing.
 *
 * Switching folders has to reset the panel in the same render that sees the new
 * id. Fetching in an effect repaints the previous folder's description first,
 * which reads as the new folder having inherited it.
 *
 * Saving an emptied description means *removing* it, so an empty string travels
 * as null rather than as a description consisting of nothing.
 */

import "@testing-library/jest-dom/vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CollectionReadme } from "@/components/collection-readme";
import { json, renderApp, type RenderAppOptions } from "@/test-support/render";

function renderReadme(
  options: RenderAppOptions & { readme?: string | null; canEdit?: boolean } = {},
) {
  const { readme = null, canEdit = true, routes = {}, ...rest } = options;
  return renderApp(<CollectionReadme collectionId={5} canEdit={canEdit} />, {
    routes: {
      "GET /api/v1/collections/5/readme": json({ readme }),
      "PUT /api/v1/collections/5/readme": json({ readme: "saved" }),
      ...routes,
    },
    ...rest,
  });
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("CollectionReadme", () => {
  describe("a folder with a description", () => {
    it("shows it", async () => {
      renderReadme({ readme: "Brackets for the shelf rig." });

      expect(await screen.findByText("Brackets for the shelf rig.")).toBeInTheDocument();
    });

    it("renders it as markdown rather than as text", async () => {
      // The field is markdown by design; showing the source turns a heading into
      // a line starting with a hash.
      renderReadme({ readme: "# Shelf rig" });

      expect(await screen.findByRole("heading", { name: "Shelf rig" })).toBeInTheDocument();
    });

    it("offers editing to somebody who may write", async () => {
      renderReadme({ readme: "Brackets." });

      expect(await screen.findByRole("button", { name: /Edit/ })).toBeInTheDocument();
    });

    it("offers no editing to a viewer", async () => {
      renderReadme({ readme: "Brackets.", canEdit: false });

      await screen.findByText("Brackets.");
      expect(screen.queryByRole("button", { name: /Edit/ })).toBeNull();
    });
  });

  describe("a folder with none", () => {
    it("invites somebody who may write to add one", async () => {
      renderReadme();

      expect(
        await screen.findByRole("button", { name: /Add a description for this collection/ }),
      ).toBeInTheDocument();
    });

    it("renders nothing at all for a viewer", async () => {
      // An empty box on every folder in the vault advertises a feature most
      // folders will never use, and pushes the model grid down to do it.
      const { container } = renderReadme({ canEdit: false });

      await waitFor(() => expect(container.querySelector(".border-b")).toBeNull());
    });
  });

  describe("writing one", () => {
    it("saves what the user wrote", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderReadme();
      await user.click(await screen.findByRole("button", { name: /Add a description/ }));
      await user.type(screen.getByRole("textbox"), "Brackets for the shelf rig.");

      await user.click(screen.getByRole("button", { name: "Save" }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("PUT").at(-1)?.body ?? "{}")).toMatchObject({
          readme: "Brackets for the shelf rig.",
        }),
      );
    });

    it("removes the description when it is emptied", async () => {
      // An empty string is a description consisting of nothing, which still
      // reserves the space above the grid.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderReadme({
        readme: "Brackets.",
        routes: { "PUT /api/v1/collections/5/readme": json({ readme: null }) },
      });
      await user.click(await screen.findByRole("button", { name: /Edit/ }));
      await user.clear(screen.getByRole("textbox"));

      await user.click(screen.getByRole("button", { name: "Save" }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("PUT").at(-1)?.body ?? "{}")).toMatchObject({
          readme: null,
        }),
      );
    });

    it("opens the editor on what is already there", async () => {
      const user = userEvent.setup();
      renderReadme({ readme: "Brackets." });

      await user.click(await screen.findByRole("button", { name: /Edit/ }));

      expect(screen.getByRole("textbox")).toHaveValue("Brackets.");
    });

    it("abandons the draft on cancel", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderReadme({ readme: "Brackets." });
      await user.click(await screen.findByRole("button", { name: /Edit/ }));
      await user.type(screen.getByRole("textbox"), " And bolts.");

      await user.click(screen.getByRole("button", { name: "Cancel" }));

      expect(requestsWithMethod("PUT")).toHaveLength(0);
    });

    it("keeps the editor open when the save is refused", async () => {
      // Closing on failure throws away what the user just wrote.
      const user = userEvent.setup();
      renderReadme({
        readme: "Brackets.",
        routes: { "PUT /api/v1/collections/5/readme": json({ detail: "forbidden" }, 403) },
      });
      await user.click(await screen.findByRole("button", { name: /Edit/ }));

      await user.click(screen.getByRole("button", { name: "Save" }));

      expect(await screen.findByRole("textbox")).toBeInTheDocument();
    });
  });

  describe("a description that cannot be read", () => {
    it("treats it as absent rather than as an error", async () => {
      // A folder whose description 500s is still a folder full of models; an
      // error banner above the grid helps nobody.
      renderReadme({
        routes: { "GET /api/v1/collections/5/readme": json({ detail: "boom" }, 500) },
      });

      expect(await screen.findByRole("button", { name: /Add a description/ })).toBeInTheDocument();
    });
  });
  describe("embedding an image", () => {
    /** A pasted screenshot — how most images reach a folder description. */
    function anImage() {
      return new File(["png-bytes"], "shelf.png", { type: "image/png" });
    }

    it("uploads an image pasted into the editor", async () => {
      // The alternative is asking somebody to host a photo of their own shelf
      // somewhere else and paste a link to it.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderReadme({
        readme: "Brackets.",
        routes: {
          "POST /api/v1/collections/5/images": json({ url: "/api/v1/collections/5/images/1" }),
        },
      });
      await user.click(await screen.findByRole("button", { name: /Edit/ }));

      fireEvent.paste(screen.getByRole("textbox"), {
        clipboardData: { files: [anImage()], types: ["Files"] },
      });

      await waitFor(() =>
        expect(requestsWithMethod("POST").some((call) => call.url.includes("/images"))).toBe(true),
      );
    });

    it("writes the uploaded image into the markdown", async () => {
      const user = userEvent.setup();
      renderReadme({
        readme: "Brackets.",
        routes: {
          "POST /api/v1/collections/5/images": json({ url: "/api/v1/collections/5/images/1" }),
        },
      });
      await user.click(await screen.findByRole("button", { name: /Edit/ }));

      fireEvent.paste(screen.getByRole("textbox"), {
        clipboardData: { files: [anImage()], types: ["Files"] },
      });

      await waitFor(() =>
        expect(screen.getByRole("textbox")).toHaveDisplayValue(/collections\/5\/images\/1/),
      );
    });

    it("uploads an image dropped on the editor", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderReadme({
        readme: "Brackets.",
        routes: {
          "POST /api/v1/collections/5/images": json({ url: "/api/v1/collections/5/images/1" }),
        },
      });
      await user.click(await screen.findByRole("button", { name: /Edit/ }));

      fireEvent.drop(screen.getByRole("textbox"), {
        dataTransfer: { files: [anImage()], types: ["Files"] },
      });

      await waitFor(() =>
        expect(requestsWithMethod("POST").some((call) => call.url.includes("/images"))).toBe(true),
      );
    });

    it("ignores a pasted file that is not an image", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderReadme({ readme: "Brackets." });
      await user.click(await screen.findByRole("button", { name: /Edit/ }));

      fireEvent.paste(screen.getByRole("textbox"), {
        clipboardData: {
          files: [new File(["x"], "notes.txt", { type: "text/plain" })],
          types: ["Files"],
        },
      });

      await waitFor(() => expect(requestsWithMethod("POST")).toHaveLength(0));
    });

    it("surfaces an image the server refused", async () => {
      const user = userEvent.setup();
      renderReadme({
        readme: "Brackets.",
        routes: {
          "POST /api/v1/collections/5/images": json({ detail: "file_too_large" }, 413),
        },
      });
      await user.click(await screen.findByRole("button", { name: /Edit/ }));

      fireEvent.paste(screen.getByRole("textbox"), {
        clipboardData: { files: [anImage()], types: ["Files"] },
      });

      expect(await screen.findByText("File exceeds the upload size limit.")).toBeInTheDocument();
    });
  });
});
