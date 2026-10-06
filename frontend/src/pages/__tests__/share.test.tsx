/*
 * The one page in PrintStash a stranger sees.
 *
 * There is no session behind it: the token in the URL is the whole of the
 * authorisation, so every failure mode collapses to the same answer. An invalid
 * token, an expired one and a revoked one must all read as "this link doesn't
 * work" — distinguishing them would tell a stranger which tokens exist.
 *
 * What the page offers is scoped by what the share allowed. A view-only share
 * shows the model and no download; a share that permitted downloads shows one.
 * Rendering the download for a view-only share hands over a control that 403s,
 * which reads to the recipient as the owner having broken something.
 *
 * A share whose model has no mesh still has to render — the recipient did not
 * choose what was in it, and an empty viewer with no explanation looks like a
 * failure rather than like a G-code-only share.
 */

import "@testing-library/jest-dom/vitest";
import { act, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import SharePage from "@/pages/share";
import userEvent from "@testing-library/user-event";
import { useRouter } from "@/lib/navigation";
import { clearLogin } from "@/lib/auth-store";
import { focusManager } from "@tanstack/react-query";
import { json, renderApp, type RenderAppOptions } from "@/test-support/render";
import type { PublicFileRead, PublicModelRead } from "@/types";

/** One file as the public projection exposes it: metadata, no owning ids. */
function publicFile(over: Partial<PublicFileRead> = {}): PublicFileRead {
  return {
    id: 20,
    original_filename: "benchy.stl",
    file_type: "stl",
    size_bytes: 2048,
    version: 1,
    gcode_revision_number: null,
    revision_label: null,
    revision_status: null,
    revision_notes: null,
    is_recommended: false,
    bbox_x_mm: 60,
    bbox_y_mm: 30,
    bbox_z_mm: 48,
    triangle_count: 2000,
    slicer_name: null,
    slicer_version: null,
    printer_model: null,
    nozzle_diameter_mm: null,
    layer_height_mm: null,
    first_layer_height_mm: null,
    infill_percent: null,
    wall_loops: null,
    support_material: null,
    nozzle_temperature_c: null,
    bed_temperature_c: null,
    estimated_time_s: null,
    filament_weight_g: null,
    filament_length_mm: null,
    filament_cost: null,
    material_type: null,
    material_brand: null,
    ...over,
  };
}

/** The public projection of a model: no ids a stranger could walk. */
function sharedModel(over: Partial<PublicModelRead> = {}): PublicModelRead {
  return {
    name: "Benchy",
    description: "A calibration boat.",
    has_thumbnail: false,
    allow_download: false,
    files: [publicFile()],
    ...over,
  };
}

function renderShare(options: RenderAppOptions & { token?: string } = {}) {
  const { token = "abc123", routes = {}, ...rest } = options;
  return renderApp(<SharePage />, {
    at: `/share/${token}`,
    routePath: "/share/:token",
    routes: {
      [`GET /api/v1/share/${token}`]: json(sharedModel()),
      ...routes,
    },
    ...rest,
  });
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  focusManager.setFocused(undefined);
  vi.unstubAllGlobals();
});

describe("SharePage", () => {
  describe("a working link", () => {
    it("names the shared model", async () => {
      renderShare();

      expect(await screen.findByText("Benchy")).toBeInTheDocument();
    });

    it("fetches the model the token names", async () => {
      const { requests } = renderShare();

      await waitFor(() =>
        expect(requests().some((call) => call.url.includes("/share/abc123"))).toBe(true),
      );
    });

    it("titles the tab after the shared model", async () => {
      renderShare();

      await screen.findByText("Benchy");
      await waitFor(() => expect(document.title).toContain("Benchy"));
    });
  });

  describe("a link that does not work", () => {
    it("says so for a token nobody issued", async () => {
      renderShare({
        routes: { "GET /api/v1/share/abc123": json({ detail: "not_found" }, 404) },
      });

      expect(
        await screen.findByText("This share link is invalid, expired, or revoked."),
      ).toBeInTheDocument();
    });

    it("says the same thing for an expired one", async () => {
      // Telling the three cases apart would tell a stranger which tokens exist.
      renderShare({
        routes: { "GET /api/v1/share/abc123": json({ detail: "expired" }, 410) },
      });

      expect(
        await screen.findByText("This share link is invalid, expired, or revoked."),
      ).toBeInTheDocument();
    });

    it("says the same thing for a revoked one", async () => {
      renderShare({
        routes: { "GET /api/v1/share/abc123": json({ detail: "revoked" }, 403) },
      });

      expect(
        await screen.findByText("This share link is invalid, expired, or revoked."),
      ).toBeInTheDocument();
    });
  });

  describe("public token lifetime", () => {
    it("opens a valid token after an invalid token", async () => {
      function Next() {
        const router = useRouter();
        return <button onClick={() => router.push("/share/current")}>Next token</button>;
      }
      renderApp(
        <>
          <Next />
          <SharePage />
        </>,
        {
          at: "/share/expired",
          routePath: "/share/:token",
          routes: {
            "GET /api/v1/share/expired": json({}, 404),
            "GET /api/v1/share/current": json(sharedModel({ name: "Current boat", files: [] })),
          },
        },
      );
      await screen.findByText("This share link is invalid, expired, or revoked.");
      await userEvent.click(screen.getByRole("button", { name: "Next token" }));
      expect(await screen.findByRole("heading", { name: "Current boat" })).toBeVisible();
      expect(screen.queryByText("This share link is invalid, expired, or revoked.")).toBeNull();
    });
    it("aborts an obsolete token before presenting its model", async () => {
      let finish!: (response: Response) => void;
      let signal: AbortSignal | null | undefined;
      const held = new Promise<Response>((resolve) => {
        finish = resolve;
      });
      function Next() {
        const router = useRouter();
        return <button onClick={() => router.push("/share/current")}>Next token</button>;
      }
      const app = renderApp(
        <>
          <Next />
          <SharePage />
        </>,
        {
          at: "/share/old",
          routePath: "/share/:token",
          routes: {
            "GET /api/v1/share/old": (_url, init) => {
              signal = init?.signal;
              return held;
            },
            "GET /api/v1/share/current": json(sharedModel({ name: "Current boat", files: [] })),
          },
        },
      );
      await waitFor(() =>
        expect(app.requests().some(({ url }) => url.endsWith("/share/old"))).toBe(true),
      );
      await userEvent.click(screen.getByRole("button", { name: "Next token" }));
      expect(await screen.findByRole("heading", { name: "Current boat" })).toBeVisible();
      expect(signal?.aborted).toBe(true);
      await act(async () => {
        finish(json(sharedModel({ name: "Obsolete boat", files: [] })));
        await held;
      });
      expect(screen.queryByRole("heading", { name: "Obsolete boat" })).toBeNull();
      expect(document.title).toContain("Current boat");
    });
    it("keeps a public lookup alive after private cache retirement", async () => {
      let finish!: (response: Response) => void;
      let signal: AbortSignal | null | undefined;
      const held = new Promise<Response>((resolve) => {
        finish = resolve;
      });
      const app = renderShare({
        routes: {
          "GET /api/v1/share/abc123": (_url, init) => {
            signal = init?.signal;
            return held;
          },
        },
      });
      expect(screen.getByRole("status", { name: "Loading…" })).toBeVisible();
      await waitFor(() => expect(app.requests()).toHaveLength(1));
      act(() => {
        clearLogin();
        app.client.clear();
      });
      expect(signal?.aborted).toBe(false);
      await act(async () => {
        finish(json(sharedModel({ files: [] })));
        await held;
      });
      expect(await screen.findByRole("heading", { name: "Benchy" })).toBeVisible();
      expect(app.requests()).toHaveLength(1);
      expect(app.client.getQueriesData({ queryKey: ["public-share"] })).toHaveLength(0);
    });
    it("hides a cached public model after confirmed revocation", async () => {
      const app = renderShare({
        routes: {
          "GET /api/v1/share/abc123": json(sharedModel({ files: [], allow_download: true })),
        },
      });
      await screen.findByRole("heading", { name: "Benchy" });
      app.route({ "GET /api/v1/share/abc123": json({}, 403) });
      act(() => {
        focusManager.setFocused(false);
        focusManager.setFocused(true);
      });
      expect(
        await screen.findByText("This share link is invalid, expired, or revoked."),
      ).toBeVisible();
      expect(screen.queryByRole("heading", { name: "Benchy" })).toBeNull();
      expect(document.title).not.toContain("Benchy");
      expect(app.requests()).toHaveLength(2);
    });
    it("retries a temporary failure without calling the token revoked", async () => {
      const app = renderShare({ routes: { "GET /api/v1/share/abc123": json({}, 503) } });
      expect(
        await screen.findByText("The shared model could not be loaded. Try again."),
      ).toBeVisible();
      expect(screen.queryByText("This share link is invalid, expired, or revoked.")).toBeNull();
      app.route({ "GET /api/v1/share/abc123": json(sharedModel({ files: [] })) });
      await userEvent.click(screen.getByRole("button", { name: "Retry" }));
      expect(await screen.findByRole("heading", { name: "Benchy" })).toBeVisible();
      expect(app.requests().filter(({ url }) => url === "/api/v1/share/abc123")).toHaveLength(2);
    });
  });

  describe("what the share allowed", () => {
    it("offers no download for a view-only share", async () => {
      // The control would 403, which reads to the recipient as the owner having
      // broken something.
      renderShare();

      await screen.findByText("Benchy");
      expect(screen.queryByRole("link", { name: /Download/i })).toBeNull();
    });

    it("offers a download when the share permitted one", async () => {
      renderShare({
        routes: {
          "GET /api/v1/share/abc123": json(sharedModel({ allow_download: true })),
        },
      });

      await screen.findByText("Benchy");
      await waitFor(() => expect(screen.queryAllByText(/Download/i).length).toBeGreaterThan(0));
    });
  });

  describe("a share with no mesh", () => {
    it("says so rather than showing an empty viewer", async () => {
      // The recipient did not choose what was in it; an unexplained blank looks
      // like a failure rather than like a G-code-only share.
      renderShare({
        routes: {
          "GET /api/v1/share/abc123": json(
            sharedModel({
              files: [publicFile({ id: 21, original_filename: "part.gcode", file_type: "gcode" })],
            }),
          ),
        },
      });

      // The 3D toggle disables and says why, rather than opening on a blank canvas.
      const modelView = await screen.findByTitle("No mesh in this share");
      expect(modelView).toBeDisabled();
    });
  });
});
