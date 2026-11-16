/*
 * Handing a file straight to the slicer the user already has installed.
 *
 * The slicer is a separate process with no session, so it cannot send a bearer
 * token — which is why this never links to the artifact directly. It asks the
 * backend for a short-lived, file-scoped URL and hands the slicer that instead.
 * Linking to the protected path would produce a 401 inside an application that
 * has no way to report one.
 *
 * Which slicers are offered depends on the file. Bambu Studio loads only 3MF
 * from a URL and errors on anything else, so listing it beside an STL is an
 * entry that fails on click — and the user has no way to know it was never
 * going to work.
 *
 * macOS Bambu Studio uses a different scheme shape entirely (#27): the file URL
 * is appended to the host rather than passed as a query parameter, and the query
 * form silently does nothing there.
 */

import "@testing-library/jest-dom/vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SlicerOpenButton } from "@/components/slicer-open-button";
import { json, renderApp, type RenderAppOptions } from "@/test-support/render";

const SLICER_URL = "/api/v1/files/20/download?token=not-a-real-token&name=cube.stl";

const realLocation = window.location;

/** Replace `window.location` so the scheme handoff is observable. */
function captureNavigation() {
  const assign = vi.fn<(url: string) => void>();
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { origin: "http://vault.test", href: realLocation.href, assign },
  });
  return assign;
}

function renderButton(options: RenderAppOptions & { fileType?: string } = {}) {
  const { fileType = "stl", routes = {}, ...rest } = options;
  return renderApp(<SlicerOpenButton fileId={20} fileType={fileType} />, {
    routes: { "GET /api/v1/files/20/slicer-url": json({ url: SLICER_URL }), ...routes },
    ...rest,
  });
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  Object.defineProperty(window, "location", { configurable: true, value: realLocation });
  vi.unstubAllGlobals();
});

describe("SlicerOpenButton", () => {
  describe("which slicers it offers", () => {
    it("offers the broad slicers for a mesh", async () => {
      const user = userEvent.setup();
      renderButton();

      await user.click(screen.getByRole("button", { name: "Open in slicer" }));

      expect(screen.getByRole("menuitem", { name: "OrcaSlicer" })).toBeInTheDocument();
    });

    it("leaves Bambu Studio out for a file it cannot open", async () => {
      // It errors with "unknown format" on anything but 3MF, and the user has
      // no way to know it was never going to work.
      const user = userEvent.setup();
      renderButton({ fileType: "stl" });

      await user.click(screen.getByRole("button", { name: "Open in slicer" }));

      expect(screen.queryByRole("menuitem", { name: "Bambu Studio" })).toBeNull();
    });

    it("offers Bambu Studio for a 3MF", async () => {
      const user = userEvent.setup();
      renderButton({ fileType: "3mf" });

      await user.click(screen.getByRole("button", { name: "Open in slicer" }));

      expect(screen.getByRole("menuitem", { name: "Bambu Studio" })).toBeInTheDocument();
    });

    it("keeps a download fallback for unsupported formats", async () => {
      renderButton({ fileType: "bgcode" });
      await userEvent.setup().click(screen.getByRole("button", { name: "Open in slicer" }));
      expect(screen.getByRole("menuitem", { name: "Download original file" })).toBeInTheDocument();
      expect(screen.queryByRole("menuitem", { name: "OrcaSlicer" })).toBeNull();
    });
  });

  describe("handing the file over", () => {
    it("asks the backend for a file-scoped URL", async () => {
      // The slicer has no session, so it cannot fetch the protected path
      // itself — it would get a 401 it has no way to report.
      const user = userEvent.setup();
      captureNavigation();
      const { requests } = renderButton();
      await user.click(screen.getByRole("button", { name: "Open in slicer" }));

      await user.click(screen.getByRole("menuitem", { name: "OrcaSlicer" }));

      await waitFor(() =>
        expect(requests().some((call) => call.url.includes("/slicer-url"))).toBe(true),
      );
    });

    it("hands the slicer an absolute URL", async () => {
      // A relative path means nothing to a separate application.
      const user = userEvent.setup();
      const assign = captureNavigation();
      renderButton();
      await user.click(screen.getByRole("button", { name: "Open in slicer" }));

      await user.click(screen.getByRole("menuitem", { name: "OrcaSlicer" }));

      await waitFor(() =>
        expect(assign).toHaveBeenCalledWith(expect.stringContaining("http%3A%2F%2Fvault.test")),
      );
    });

    it("uses the scheme of the slicer that was chosen", async () => {
      const user = userEvent.setup();
      const assign = captureNavigation();
      renderButton();
      await user.click(screen.getByRole("button", { name: "Open in slicer" }));

      await user.click(screen.getByRole("menuitem", { name: "PrusaSlicer" }));

      await waitFor(() =>
        expect(assign).toHaveBeenCalledWith(expect.stringContaining("prusaslicer://open?file=")),
      );
    });

    it("says so when the URL could not be minted", async () => {
      // Nothing else reports it: the click would simply do nothing.
      const user = userEvent.setup();
      captureNavigation();
      renderButton({
        routes: { "GET /api/v1/files/20/slicer-url": json({ detail: "forbidden" }, 403) },
      });
      await user.click(screen.getByRole("button", { name: "Open in slicer" }));

      await user.click(screen.getByRole("menuitem", { name: "OrcaSlicer" }));

      expect(
        await screen.findByText(
          "Could not prepare the slicer download. You can still download this Artifact.",
        ),
      ).toBeInTheDocument();
    });
  });
});

it("does not carry the preferred slicer into another account", async () => {
  const { AuthContext } = await import("@/lib/auth-context");
  const { adminSession, memberSession } = await import("@/test-support/render");
  const user = userEvent.setup();
  captureNavigation();
  const view = renderApp(
    <AuthContext.Provider value={adminSession()}>
      <SlicerOpenButton fileId={20} fileType="stl" />
    </AuthContext.Provider>,
    { routes: { "GET /api/v1/files/20/slicer-url": json({ url: SLICER_URL }) } },
  );
  await user.click(screen.getByRole("button", { name: "Open in slicer" }));
  await user.click(screen.getByRole("menuitem", { name: "PrusaSlicer" }));
  await waitFor(() =>
    expect(localStorage.getItem("printstash.slicer.http://vault.test.1")).toBe("prusaslicer"),
  );
  view.rerender(
    <AuthContext.Provider value={memberSession()}>
      <SlicerOpenButton fileId={20} fileType="stl" />
    </AuthContext.Provider>,
  );
  await user.click(screen.getByRole("button", { name: "Open in slicer" }));
  expect(screen.getAllByRole("menuitem")[0]).toHaveTextContent("OrcaSlicer");
  expect(screen.queryByText("(preferred)")).toBeNull();
});
