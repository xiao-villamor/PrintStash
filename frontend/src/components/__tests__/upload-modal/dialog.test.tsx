/*
 * Four ways to get something into the library, behind one dialog.
 *
 * Each mode posts a different request and, more importantly, *stops in a
 * different place*. Files and Bulk queue work and close; From URL and From ZIP
 * come back with a manifest the user has to choose from first. Conflating those
 * two shapes is how an import either runs without the user's selection or waits
 * for a selection that was never asked for.
 *
 * The submit button is a gate, not a decoration. It stays disabled until the
 * chosen mode actually has its input, and it is disabled outright for a
 * non-superuser with no writable collection — uploading into a collection you
 * cannot write to fails on the server, after the bytes have gone up.
 *
 * `BulkFiles` is separate because each dropped mesh becomes its own model and
 * folders mirror into nested collections. Its list is where a user removes the
 * files they did not mean to include, so it is the last point at which a bulk
 * import can still be corrected.
 */

import "@testing-library/jest-dom/vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { UploadModal } from "@/components/upload-modal";
import { queryKeys } from "@/lib/query-client";
import { aCollection, aTag } from "@/test-support/factories";
import { json, memberSession, renderApp, type RenderAppOptions } from "@/test-support/render";

const QUEUED = { job_id: "job-1", state: "pending", message: "queued" };

function renderUpload(
  options: RenderAppOptions & { onClose?: () => void; onUploaded?: () => Promise<void> } = {},
) {
  const {
    seed = [],
    routes = {},
    onClose = vi.fn<() => void>(),
    onUploaded = vi.fn<() => Promise<void>>().mockResolvedValue(undefined),
    ...rest
  } = options;
  const result = renderApp(
    <UploadModal open onClose={onClose} onUploaded={onUploaded} defaultCollection={null} />,
    {
      seed: [[queryKeys.collections, [aCollection()]], [queryKeys.tags, [aTag()]], ...seed],
      routes: {
        "GET /api/v1/libraries": json([]),
        "GET /api/v1/collections": json([aCollection()]),
        "GET /api/v1/tags": json([aTag()]),
        "POST /api/v1/ingest/model": json(QUEUED),
        "POST /api/v1/ingest/orca": json(QUEUED),
        "POST /api/v1/inbox": json({ id: 3, state: "review" }),
        "POST /api/v1/ingest/archive/inspect": json({
          archive_id: "arch-1",
          entries: [{ name: "cube.stl", size_bytes: 10, kind: "mesh" }],
        }),
        ...routes,
      },
      ...rest,
    },
  );
  return { ...result, onClose, onUploaded };
}

/** Choose one of the four ways in. */
function mode(name: "Files" | "Bulk" | "From URL" | "From ZIP") {
  return screen.getByRole("button", { name: new RegExp(`\\s*${name}\\s*`) });
}

/** The mesh and G-code slots, in the order the dialog renders them. */
function fileInputs(container: HTMLElement) {
  return container.ownerDocument.querySelectorAll<HTMLInputElement>('input[type="file"]');
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("UploadModal", () => {
  describe("choosing a way in", () => {
    it("opens on the files tab", async () => {
      renderUpload();

      expect(await screen.findByText(".stl .3mf .obj .step")).toBeInTheDocument();
    });

    it("offers a bulk drop", async () => {
      const user = userEvent.setup();
      renderUpload();
      await screen.findByText(".stl .3mf .obj .step");

      await user.click(mode("Bulk"));

      expect(screen.getByRole("button", { name: /Upload to vault/ })).toBeDisabled();
    });

    it("offers a URL import", async () => {
      const user = userEvent.setup();
      renderUpload();
      await screen.findByText(".stl .3mf .obj .step");

      await user.click(mode("From URL"));

      expect(screen.getByPlaceholderText(/Model page, collection/)).toBeInTheDocument();
    });

    it("offers an archive import", async () => {
      const user = userEvent.setup();
      renderUpload();
      await screen.findByText(".stl .3mf .obj .step");

      await user.click(mode("From ZIP"));

      expect(screen.getByText(".zip")).toBeInTheDocument();
    });
  });

  describe("the submit gate", () => {
    it("refuses an empty files upload", async () => {
      renderUpload();

      await screen.findByText(".stl .3mf .obj .step");
      expect(screen.getByRole("button", { name: "Upload to vault" })).toBeDisabled();
    });

    it("refuses an empty URL import", async () => {
      const user = userEvent.setup();
      renderUpload();
      await screen.findByText(".stl .3mf .obj .step");

      await user.click(mode("From URL"));

      expect(screen.getByRole("button", { name: "Review URL" })).toBeDisabled();
    });

    it("refuses an empty archive import", async () => {
      const user = userEvent.setup();
      renderUpload();
      await screen.findByText(".stl .3mf .obj .step");

      await user.click(mode("From ZIP"));

      expect(screen.getByRole("button", { name: "Inspect archive" })).toBeDisabled();
    });

    it("accepts a URL once one is typed", async () => {
      const user = userEvent.setup();
      renderUpload();
      await screen.findByText(".stl .3mf .obj .step");
      await user.click(mode("From URL"));

      await user.type(
        screen.getByPlaceholderText(/Model page, collection/),
        "https://example.test/model/1",
      );

      expect(screen.getByRole("button", { name: "Review URL" })).toBeEnabled();
    });

    it("refuses everything for a member with nowhere to write", async () => {
      // Uploading into a collection you cannot write to fails on the server,
      // after the bytes have gone up — so the gate is here, before them.
      const user = userEvent.setup();
      renderUpload({
        auth: memberSession(),
        seed: [[queryKeys.collections, [aCollection({ effective_role: "view" })]]],
      });
      await screen.findByText(".stl .3mf .obj .step");
      await user.click(mode("From URL"));
      await user.type(
        screen.getByPlaceholderText(/Model page, collection/),
        "https://example.test/model/1",
      );

      expect(screen.getByRole("button", { name: "Review URL" })).toBeDisabled();
    });
  });

  describe("importing from a URL", () => {
    async function pasteAndReview(user: ReturnType<typeof userEvent.setup>) {
      await screen.findByText(".stl .3mf .obj .step");
      await user.click(mode("From URL"));
      await user.type(
        screen.getByPlaceholderText(/Model page, collection/),
        "https://example.test/model/1",
      );
      await user.click(screen.getByRole("button", { name: "Review URL" }));
    }

    it("captures the URL as a pending import rather than importing it", async () => {
      // A remote page is captured first and reviewed afterwards, so nothing a
      // third-party site claims about a model lands in the library unseen.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderUpload();

      await pasteAndReview(user);

      await waitFor(() =>
        expect(requestsWithMethod("POST").some((call) => call.url.includes("/inbox"))).toBe(true),
      );
    });

    it("sends the pasted URL verbatim", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderUpload();

      await pasteAndReview(user);

      await waitFor(() => {
        const capture = requestsWithMethod("POST").find((call) => call.url.includes("/inbox"));
        expect(JSON.parse(capture?.body ?? "{}")).toMatchObject({
          url: "https://example.test/model/1",
          tags: [],
        });
      });
    });

    it("closes once the capture is queued", async () => {
      const user = userEvent.setup();
      const { onClose } = renderUpload();

      await pasteAndReview(user);

      await waitFor(() => expect(onClose).toHaveBeenCalled());
    });

    it("keeps the dialog open when the capture is refused", async () => {
      // Closing on failure loses the URL the user pasted, with nothing said.
      const user = userEvent.setup();
      const { onClose } = renderUpload({
        routes: { "POST /api/v1/inbox": json({ detail: "url_not_allowed" }, 400) },
      });

      await pasteAndReview(user);

      await waitFor(() => expect(screen.getByRole("button", { name: "Review URL" })).toBeEnabled());
      expect(onClose).not.toHaveBeenCalled();
    });
  });

  describe("naming and filing", () => {
    it("offers the collections the user may write to", async () => {
      renderUpload();

      expect(await screen.findByPlaceholderText(/Search or create/)).toBeInTheDocument();
    });

    it("takes a model name", async () => {
      const user = userEvent.setup();
      renderUpload();

      const name = await screen.findByPlaceholderText("e.g. Bracket v2");
      await user.type(name, "Bracket v2");

      expect(name).toHaveValue("Bracket v2");
    });
  });

  describe("uploading files", () => {
    /** The mesh and G-code slots, in the order the dialog renders them. */
    function fileInputs(container: HTMLElement) {
      return container.ownerDocument.querySelectorAll<HTMLInputElement>('input[type="file"]');
    }

    it("accepts a mesh once one is chosen", async () => {
      const user = userEvent.setup();
      const { container } = renderUpload();
      await screen.findByText(".stl .3mf .obj .step");

      await user.upload(fileInputs(container)[0], new File(["x"], "cube.stl"));

      expect(screen.getByRole("button", { name: "Upload to vault" })).toBeEnabled();
    });

    it("names the model after the file when the user does not", async () => {
      // An untitled upload is findable only by scrolling; the filename is the
      // one name the user already recognises.
      const user = userEvent.setup();
      const { container } = renderUpload();
      await screen.findByText(".stl .3mf .obj .step");

      await user.upload(fileInputs(container)[0], new File(["x"], "cube.stl"));

      expect(screen.getByPlaceholderText("e.g. Bracket v2")).toHaveValue("cube");
    });

    it("accepts a G-code on its own", async () => {
      const user = userEvent.setup();
      const { container } = renderUpload();
      await screen.findByText(".stl .3mf .obj .step");

      await user.upload(fileInputs(container)[1], new File(["x"], "part.gcode"));

      expect(screen.getByRole("button", { name: "Upload to vault" })).toBeEnabled();
    });

    it("lets the user take a chosen file back out", async () => {
      const user = userEvent.setup();
      const { container } = renderUpload();
      await screen.findByText(".stl .3mf .obj .step");
      await user.upload(fileInputs(container)[0], new File(["x"], "cube.stl"));

      await user.click(screen.getByRole("button", { name: /Remove/ }));

      expect(screen.getByRole("button", { name: "Upload to vault" })).toBeDisabled();
    });

    it("closes once the upload is queued", async () => {
      // The upload runs in the task centre, so holding the dialog open would
      // block the user behind work they can already watch elsewhere.
      const user = userEvent.setup();
      const { container, onClose } = renderUpload();
      await screen.findByText(".stl .3mf .obj .step");
      await user.upload(fileInputs(container)[0], new File(["x"], "cube.stl"));

      await user.click(screen.getByRole("button", { name: "Upload to vault" }));

      await waitFor(() => expect(onClose).toHaveBeenCalled());
    });
  });

  describe("tagging the upload", () => {
    /** The picker only lists once there is something to filter by. */
    async function search(user: ReturnType<typeof userEvent.setup>, term: string) {
      await user.type(await screen.findByPlaceholderText(/Search or create/), term);
    }

    it("offers a tag that already exists", async () => {
      const user = userEvent.setup();
      renderUpload();

      await search(user, "func");

      expect(await screen.findByRole("option", { name: /functional/ })).toBeInTheDocument();
    });

    it("adds a tag the user picks", async () => {
      const user = userEvent.setup();
      renderUpload();
      await search(user, "func");

      await user.click(await screen.findByRole("option", { name: /functional/ }));

      expect(await screen.findByRole("button", { name: "Remove functional" })).toBeInTheDocument();
    });

    it("takes a tag back off", async () => {
      const user = userEvent.setup();
      renderUpload();
      await search(user, "func");
      await user.click(await screen.findByRole("option", { name: /functional/ }));

      await user.click(screen.getByRole("button", { name: "Remove functional" }));

      expect(screen.queryByRole("button", { name: "Remove functional" })).toBeNull();
    });

    it("offers to create a tag that does not exist yet", async () => {
      const user = userEvent.setup();
      renderUpload({ routes: { "POST /api/v1/tags": json(aTag({ id: 9, name: "petg" })) } });

      await search(user, "petg");

      expect(await screen.findByRole("option", { name: /Create/ })).toBeInTheDocument();
    });
  });

  describe("filing the upload", () => {
    it("offers the collections the user may write to", async () => {
      const user = userEvent.setup();
      renderUpload();

      await user.click(await screen.findByRole("button", { name: "None" }));

      expect(await screen.findByRole("option", { name: /parts/ })).toBeInTheDocument();
    });

    it("leaves a read-only collection out of the choices", async () => {
      // Offering one produces an upload that fails on the server after the bytes
      // have gone up.
      const user = userEvent.setup();
      renderUpload({
        seed: [[queryKeys.collections, [aCollection({ effective_role: "view" })]]],
      });

      await user.click(await screen.findByRole("button", { name: "None" }));

      expect(await screen.findByText("No editable collections.")).toBeInTheDocument();
    });

    it("files the upload in the collection the user chose", async () => {
      const user = userEvent.setup();
      renderUpload();
      await user.click(await screen.findByRole("button", { name: "None" }));

      await user.click(await screen.findByRole("option", { name: /parts/ }));

      expect(await screen.findByRole("button", { name: /parts/ })).toBeInTheDocument();
    });
  });

  describe("closing", () => {
    it("tells the caller when the user dismisses it", async () => {
      const user = userEvent.setup();
      const { onClose } = renderUpload();
      await screen.findByText(".stl .3mf .obj .step");

      await user.click(screen.getByRole("button", { name: "Close" }));

      expect(onClose).toHaveBeenCalledTimes(1);
    });
  });
  describe("dropping a file on a slot", () => {
    /** The mesh slot, which is the first drop target the dialog renders. */
    function meshSlot() {
      // The slot is the sibling of its label: the drop handlers live on the box,
      // not on the caption above it.
      return screen.getByText("3D Model").nextElementSibling!;
    }

    it("takes a mesh dropped on the mesh slot", async () => {
      // Dragging out of a file manager is how most uploads start; requiring the
      // picker turns one gesture into three.
      renderUpload();
      await screen.findByText(".stl .3mf .obj .step");

      fireEvent.drop(meshSlot(), {
        dataTransfer: { files: [new File(["x"], "cube.stl")], types: ["Files"] },
      });

      expect(await screen.findByText("cube.stl")).toBeInTheDocument();
    });

    it("refuses a file the slot does not take", async () => {
      // Accepting a G-code into the mesh slot uploads it to the wrong ingester,
      // which loses every slicer setting the file carries.
      renderUpload();
      await screen.findByText(".stl .3mf .obj .step");

      fireEvent.drop(meshSlot(), {
        dataTransfer: { files: [new File(["x"], "part.gcode")], types: ["Files"] },
      });

      expect(await screen.findByText(/Wrong file type for 3D Model/)).toBeInTheDocument();
    });

    it("says what the slot does take", async () => {
      renderUpload();
      await screen.findByText(".stl .3mf .obj .step");

      fireEvent.drop(meshSlot(), {
        dataTransfer: { files: [new File(["x"], "part.gcode")], types: ["Files"] },
      });

      expect(await screen.findByText(/Drop a .* file here/)).toBeInTheDocument();
    });

    it("ignores a drop carrying no file at all", async () => {
      // A dragged selection of text produces a drop event with an empty file
      // list, and treating it as a file would clear whatever was chosen.
      const user = userEvent.setup();
      const { container } = renderUpload();
      await screen.findByText(".stl .3mf .obj .step");
      await user.upload(fileInputs(container)[0], new File(["x"], "cube.stl"));

      fireEvent.drop(meshSlot(), { dataTransfer: { files: [], types: [] } });

      expect(screen.getByText("cube.stl")).toBeInTheDocument();
    });
  });

  describe("creating a tag while uploading", () => {
    it("creates one the vault does not have yet", async () => {
      // Tagging at upload time is the only moment the user remembers what the
      // model was for; sending them to settings first loses that.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderUpload({
        routes: { "POST /api/v1/tags": json({ id: 9, name: "spares", slug: "spares" }) },
      });
      await screen.findByText(".stl .3mf .obj .step");

      await user.type(await screen.findByPlaceholderText(/Search or create/), "spares{Enter}");

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("POST").at(-1)?.body ?? "{}")).toMatchObject({
          name: "spares",
        }),
      );
    });

    it("selects an existing tag rather than creating a duplicate", async () => {
      // Two tags differing only in case are two tags nobody can tell apart in
      // the filter list.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderUpload();
      await screen.findByText(".stl .3mf .obj .step");

      await user.type(await screen.findByPlaceholderText(/Search or create/), "Functional{Enter}");

      expect(requestsWithMethod("POST")).toHaveLength(0);
    });

    it("survives a tag the server refused", async () => {
      const user = userEvent.setup();
      renderUpload({
        routes: { "POST /api/v1/tags": json({ detail: "tag_exists" }, 409) },
      });
      await screen.findByText(".stl .3mf .obj .step");

      await user.type(await screen.findByPlaceholderText(/Search or create/), "spares{Enter}");

      expect(await screen.findByPlaceholderText(/Search or create/)).toBeInTheDocument();
    });
  });
});
