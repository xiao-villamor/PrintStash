/* The authenticated guide preserves storage recovery and shows only verified Models. */
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import GettingStartedPage from "@/pages/getting-started";
import { usePathname } from "@/lib/navigation";
import { aModelListItem } from "@/test-support/factories";
import {
  adminSession,
  json,
  memberSession,
  renderApp,
  type RouteTable,
} from "@/test-support/render";

function Path() {
  return <span data-testid="path">{usePathname()}</span>;
}
function renderGuide(routes: RouteTable = {}, auth = adminSession()) {
  return renderApp(
    <>
      <GettingStartedPage />
      <Path />
    </>,
    {
      at: "/getting-started",
      auth,
      routes: {
        "POST /api/v1/setup/prepare-storage": json({
          ready: true,
          storage_provider: "local",
          checks: [],
        }),
        "GET /api/v1/models/page": json({ items: [], total: 0, next_cursor: null }),
        "GET /api/v1/libraries/locations": json([]),
        "GET /api/v1/collections": json([]),
        "GET /api/v1/tags": json([]),
        ...routes,
      },
    },
  );
}
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Getting started", () => {
  it("keeps uploads unavailable while storage needs preparation", async () => {
    renderGuide({
      "POST /api/v1/setup/prepare-storage": json({ detail: "storage_root_enrollment_failed" }, 500),
    });
    expect(await screen.findByText(/Account created; storage preparation pending/)).toBeVisible();
    expect(screen.queryByRole("button", { name: "Upload my first files" })).not.toBeInTheDocument();
  });
  it("recovers preparation without creating another account", async () => {
    const guide = renderGuide({
      "POST /api/v1/setup/prepare-storage": json({ detail: "storage_root_enrollment_failed" }, 500),
    });
    const retry = await screen.findByRole("button", { name: "Retry" });
    guide.route({
      "POST /api/v1/setup/prepare-storage": json({
        ready: true,
        storage_provider: "local",
        checks: [],
      }),
    });
    await userEvent.click(retry);
    expect(await screen.findByRole("button", { name: "Upload my first files" })).toBeVisible();
    expect(guide.requests().some((request) => request.url === "/api/v1/setup")).toBe(false);
  });
  it("lets an administrator postpone the guide", async () => {
    renderGuide();
    await userEvent.click(await screen.findByRole("button", { name: "I'll do this later" }));
    expect(screen.getByTestId("path")).toHaveTextContent(/^\/$/);
  });
  it("links to a verified Model in the library", async () => {
    renderGuide({
      "GET /api/v1/models/page": json({
        items: [aModelListItem({ id: 42, name: "First model" })],
        total: 1,
        next_cursor: null,
      }),
    });
    expect(await screen.findByRole("link", { name: "First model" })).toHaveAttribute(
      "href",
      "/models/42",
    );
  });
  it("reports a catalog failure separately from storage preparation", async () => {
    renderGuide({ "GET /api/v1/models/page": json({ detail: "unavailable" }, 503) });
    expect(await screen.findByRole("alert")).toBeVisible();
    expect(screen.queryByText(/storage preparation pending/)).not.toBeInTheDocument();
  });
  it("keeps non-administrators out of the guide", async () => {
    renderGuide({}, memberSession());
    await waitFor(() => expect(screen.getByTestId("path")).toHaveTextContent(/^\/$/));
  });
  it("sends an unauthenticated visitor to sign in", async () => {
    renderGuide({}, adminSession({ user: null }));
    await waitFor(() => expect(screen.getByTestId("path")).toHaveTextContent("/login"));
  });
  it("preselects an accessible mounted folder without enabling sources", async () => {
    renderGuide({
      "GET /api/v1/libraries/locations": json(["/libraries/models"]),
      "GET /api/v1/config": json({ external_libraries_enabled: false }),
      "GET /api/v1/libraries": json([]),
      "GET /api/v1/storage-connections": json([]),
    });
    await userEvent.click(
      await screen.findByRole("button", { name: "Connect an existing folder" }),
    );
    await userEvent.click(await screen.findByRole("button", { name: "/libraries/models" }));
    expect(await screen.findByRole("switch", { name: "Library sources enabled" })).toHaveAttribute(
      "aria-checked",
      "false",
    );
  });
});
