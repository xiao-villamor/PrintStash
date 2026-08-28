/*
 * Handing one model to somebody who has no account here.
 *
 * A share link is a capability: whoever holds it gets in. That makes two of this
 * dialog's defaults security decisions rather than conveniences — the link
 * expires, and downloading is *off* until the user turns it on. A share that
 * silently allowed downloads would hand over the files when the user meant to
 * show a preview.
 *
 * Scoping to selected revisions is the other half. A user sharing one tested
 * revision does not want the failed ones going with it, so "all" and "these
 * ones" have to produce different requests rather than the same one with a
 * cosmetic difference.
 *
 * Each open starts from a clean form. A dialog that remembers the last session's
 * selection offers a link the user did not configure, and the only way they find
 * out is after sending it.
 */

import "@testing-library/jest-dom/vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ShareDialog } from "@/components/model-detail/share-dialog";
import { json, renderApp, type RenderAppOptions } from "@/test-support/render";
import type { FileRead, ShareLinkCreated, ShareLinkRead } from "@/types";

const FROZEN_NOW = "2026-01-01T00:00:00Z";

function aGcode(over: Partial<FileRead> = {}): FileRead {
  return {
    id: 20,
    model_id: 1,
    original_filename: "part.gcode",
    file_type: "gcode",
    version: 1,
    size_bytes: 4096,
    sha256: "b".repeat(64),
    revision_label: "PLA draft",
    revision_status: null,
    revision_notes: null,
    is_recommended: false,
    uploaded_at: FROZEN_NOW,
    metadata: null,
    ...over,
  };
}

function aShareLink(over: Partial<ShareLinkRead> = {}): ShareLinkRead {
  return {
    id: 1,
    model_id: 1,
    allow_download: false,
    expires_at: "2026-02-01T00:00:00Z",
    created_at: FROZEN_NOW,
    revoked_at: null,
    is_active: true,
    access_count: 0,
    revision_file_ids: null,
    ...over,
  };
}

/**
 * The creation response, which carries the token and URL a listing does not.
 * The distinction is the point: the link is shown once, at creation, and never
 * retrievable afterwards.
 */
function aCreatedShareLink(over: Partial<ShareLinkCreated> = {}): ShareLinkCreated {
  return { ...aShareLink(), token: "abc123", url: "/share/abc123", ...over };
}

function renderShare(options: RenderAppOptions & { files?: FileRead[]; open?: boolean } = {}) {
  const { files = [aGcode()], open = true, routes = {}, ...rest } = options;
  const onClose = vi.fn<() => void>();
  const result = renderApp(
    <ShareDialog modelId={1} files={files} open={open} onClose={onClose} />,
    {
      routes: {
        "GET /api/v1/models/1/shares": json([]),
        "POST /api/v1/models/1/shares": json(aCreatedShareLink()),
        ...routes,
      },
      ...rest,
    },
  );
  return { ...result, onClose };
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ShareDialog", () => {
  describe("what it offers", () => {
    it("renders nothing while closed", () => {
      renderShare({ open: false });

      expect(screen.queryByRole("dialog")).toBeNull();
    });

    it("opens when asked", async () => {
      renderShare();

      expect(await screen.findByRole("dialog")).toBeInTheDocument();
    });

    it("lists the links already granted", async () => {
      renderShare({ routes: { "GET /api/v1/models/1/shares": json([aShareLink()]) } });

      await screen.findByRole("dialog");
      // "Active · view-only · all revs" — one line describing the whole grant.
      expect(await screen.findByText(/Active · view-only/)).toBeInTheDocument();
    });

    it("says so when nothing has been shared yet", async () => {
      renderShare();

      expect(await screen.findByText("No share links yet.")).toBeInTheDocument();
    });

    it("marks a revoked link as such", async () => {
      // A revoked link that still reads as active is a link somebody believes
      // still works.
      renderShare({
        routes: {
          "GET /api/v1/models/1/shares": json([
            aShareLink({ is_active: false, revoked_at: FROZEN_NOW }),
          ]),
        },
      });

      expect(await screen.findByText(/Revoked/)).toBeInTheDocument();
    });
  });

  describe("creating a link", () => {
    it("keeps downloading off unless the user asks for it", async () => {
      // A share that silently allowed downloads hands over the files when the
      // user meant to show a preview.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderShare();
      await screen.findByRole("dialog");

      await user.click(screen.getByRole("button", { name: /Create link|Create/ }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("POST").at(-1)?.body ?? "{}")).toMatchObject({
          allow_download: false,
        }),
      );
    });

    it("allows downloading when the user turns it on", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderShare();
      await screen.findByRole("dialog");
      await user.click(screen.getByRole("checkbox", { name: /download/i }));

      await user.click(screen.getByRole("button", { name: /Create link|Create/ }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("POST").at(-1)?.body ?? "{}")).toMatchObject({
          allow_download: true,
        }),
      );
    });

    it("gives the link an expiry", async () => {
      // A link that never expires is a permanent hole in an otherwise private
      // library.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderShare();
      await screen.findByRole("dialog");

      await user.click(screen.getByRole("button", { name: /Create link|Create/ }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("POST").at(-1)?.body ?? "{}")).toMatchObject({
          expires_in_days: 7,
        }),
      );
    });

    it("shows the created link so it can be sent", async () => {
      const user = userEvent.setup();
      renderShare({
        routes: { "GET /api/v1/models/1/shares": json([]) },
      });
      await screen.findByRole("dialog");

      await user.click(screen.getByRole("button", { name: /Create link|Create/ }));

      // In a read-only box, so the user copies it rather than retyping it.
      expect(await screen.findByDisplayValue(/\/share\/abc123/)).toBeInTheDocument();
    });
  });

  describe("revoking a link", () => {
    it("revokes the link the user chose", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderShare({
        routes: {
          "GET /api/v1/models/1/shares": json([aShareLink()]),
          "DELETE /api/v1/shares/1": json(null, 204),
        },
      });
      await screen.findByRole("dialog");

      await user.click(await screen.findByRole("button", { name: /Revoke/ }));

      await waitFor(() =>
        expect(requestsWithMethod("DELETE").some((call) => call.url.includes("/shares/1"))).toBe(
          true,
        ),
      );
    });
  });

  describe("closing", () => {
    it("tells the caller when the user dismisses it", async () => {
      const user = userEvent.setup();
      const { onClose } = renderShare();
      await screen.findByRole("dialog");

      await user.click(screen.getByRole("button", { name: /Close/ }));

      expect(onClose).toHaveBeenCalled();
    });
  });
  describe("scoping a link to particular revisions", () => {
    it("shares every revision by default", async () => {
      // The common case is "here is the model"; requiring a selection for it
      // would make every share a two-step decision.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderShare();
      await screen.findByRole("dialog");

      await user.click(screen.getByRole("button", { name: /Create link|Create/ }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("POST").at(-1)?.body ?? "{}")).toMatchObject({
          revision_file_ids: null,
        }),
      );
    });

    it("shares only the revisions the user ticked", async () => {
      // A user sharing one tested revision does not want the failed ones going
      // with it.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderShare({
        files: [aGcode(), aGcode({ id: 21, gcode_revision_number: 2 })],
      });
      await screen.findByRole("dialog");
      await user.click(screen.getByRole("button", { name: "Selected revisions" }));

      await user.click(screen.getAllByRole("checkbox")[1]);
      await user.click(screen.getByRole("button", { name: /Create link|Create/ }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("POST").at(-1)?.body ?? "{}")).toMatchObject({
          revision_file_ids: [20],
        }),
      );
    });

    it("cannot create a scoped link with nothing ticked", async () => {
      // A link scoped to no revisions grants nothing; creating one is a link the
      // recipient reports as broken.
      const user = userEvent.setup();
      renderShare();
      await screen.findByRole("dialog");
      await user.click(screen.getByRole("button", { name: "Selected revisions" }));
      await user.click(screen.getAllByRole("checkbox")[1]);

      await user.click(screen.getAllByRole("checkbox")[1]);

      expect(screen.getByRole("button", { name: /Create link|Create/ })).toBeDisabled();
    });

    it("says how many revisions an existing link covers", async () => {
      // "All revisions" and "two of nine" are different grants, and the list is
      // the only place the difference shows.
      renderShare({
        routes: {
          "GET /api/v1/models/1/shares": json([aShareLink({ revision_file_ids: [20, 21] })]),
        },
      });

      expect(await screen.findByText(/2 revs/)).toBeInTheDocument();
    });
  });

  describe("handing the link over", () => {
    it("copies the created link to the clipboard", async () => {
      // It is shown once and never retrievable; retyping a token by hand is not
      // a realistic alternative.
      // `userEvent.setup()` installs a working clipboard stub, so what is asserted
      // is the text a user would paste.
      const user = userEvent.setup();
      renderShare();
      await screen.findByRole("dialog");
      await user.click(screen.getByRole("button", { name: /Create link|Create/ }));
      await screen.findByDisplayValue(/\/share\/abc123/);

      // The copy control sits immediately after the read-only token box; it
      // carries only an icon.
      // SAFETY: the sibling is that button — the box is rendered only inside the
      // created-link row, which is the row this test just made appear.
      await user.click(
        screen.getByDisplayValue(/\/share\/abc123/).nextElementSibling as HTMLElement,
      );

      await waitFor(async () =>
        expect(await navigator.clipboard.readText()).toContain("/share/abc123"),
      );
    });
  });
});
