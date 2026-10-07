/*
 * The filament and printer presets: the numbers every print cost is computed
 * from.
 *
 * These rows save on blur rather than on a button, which is what makes them
 * quick to edit and also what makes them easy to get wrong. A row that saves the
 * value it started with, or saves on every keystroke, is invisible either way —
 * the user sees the field they typed in and no error. So the tests assert the
 * request each edit produces, and that leaving a row unchanged produces none.
 *
 * Cost per kg is the one field with consequences beyond this card: it multiplies
 * into every print's price, so an empty box has to mean "unknown" rather than
 * zero. A preset silently costed at zero makes a whole library's statistics
 * wrong in a direction nobody questions.
 *
 * Spoolman is the other axis. When it is the source of truth its presets are
 * read-only here, because a local edit would be overwritten on the next sync and
 * the user would have no way to tell why their change reverted.
 */

import "@testing-library/jest-dom/vitest";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FilamentProfilesCard } from "@/components/filament-profiles-card";
import { anEditingBase } from "@/test-support/factories";
import { clearLogin } from "@/lib/auth-store";
import { queryKeys } from "@/lib/query-client";
import { json, renderApp, type RenderAppOptions } from "@/test-support/render";
import type { FilamentProfileRead, PrinterProfileRead } from "@/types";

const FROZEN_NOW = "2026-01-01T00:00:00Z";

function aFilamentProfile(over: Partial<FilamentProfileRead> = {}): FilamentProfileRead {
  return {
    ...anEditingBase(),
    id: 1,
    name: "Everyday PLA",
    material_type: "PLA",
    material_brand: "Prusament",
    cost_per_kg: 24.5,
    notes: null,
    usage_count: 0,
    spoolman_filament_id: null,
    density_g_cm3: null,
    diameter_mm: null,
    created_at: FROZEN_NOW,
    updated_at: FROZEN_NOW,
    ...over,
  };
}

function aPrinterProfile(over: Partial<PrinterProfileRead> = {}): PrinterProfileRead {
  return {
    ...anEditingBase(),
    id: 1,
    name: "Voron 2.4 — 0.4 mm",
    printer_model: "Voron 2.4",
    slicer_name: null,
    nozzle_diameter_mm: 0.4,
    notes: null,
    usage_count: 0,
    created_at: FROZEN_NOW,
    updated_at: FROZEN_NOW,
    ...over,
  };
}

function renderCard(
  options: RenderAppOptions & {
    filaments?: FilamentProfileRead[];
    printers?: PrinterProfileRead[];
    spoolman?: boolean;
  } = {},
) {
  const {
    filaments = [aFilamentProfile()],
    printers = [aPrinterProfile()],
    spoolman = false,
    seed = [],
    routes = {},
    ...rest
  } = options;
  return renderApp(<FilamentProfilesCard />, {
    seed: [
      [queryKeys.spoolmanStatus, { enabled: spoolman, url: null, reachable: spoolman }],
      ...seed,
    ],
    routes: {
      "GET /api/v1/filament-profiles": json(filaments),
      "GET /api/v1/printer-profiles": json(printers),
      "GET /api/v1/spoolman/status": json({ enabled: spoolman, url: null, reachable: spoolman }),
      ...routes,
    },
    ...rest,
  });
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("FilamentProfilesCard", () => {
  describe("listing presets", () => {
    it.each(["filament", "printer"] as const)("hides a denied %s catalog", async (kind) => {
      const app = renderCard();
      if (kind === "printer") await userEvent.click(screen.getByRole("tab", { name: /Printers/ }));
      const label = kind === "filament" ? "Filament preset name 1" : "Printer preset name 1";
      await screen.findByLabelText(label);
      const path =
        kind === "filament" ? "GET /api/v1/filament-profiles" : "GET /api/v1/printer-profiles";
      const queryKey = kind === "filament" ? queryKeys.filamentProfiles : queryKeys.printerProfiles;
      app.route({ [path]: json({ detail: "permission_denied" }, 403) });

      await act(async () => {
        await app.client.invalidateQueries({ queryKey });
      });

      await screen.findByRole("alert");
      expect(screen.queryByLabelText(label)).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
      app.route({ [path]: json(kind === "filament" ? [aFilamentProfile()] : [aPrinterProfile()]) });
      await userEvent.click(screen.getByRole("button", { name: "Retry" }));
      expect(await screen.findByLabelText(label)).toBeVisible();
    });

    it("lists the filament presets", async () => {
      renderCard();

      expect(await screen.findByLabelText("Filament preset name 1")).toHaveValue("Everyday PLA");
    });

    it("preserves a filament draft during a catalog refresh", async () => {
      const user = userEvent.setup();
      const app = renderCard();
      const name = await screen.findByRole("textbox", { name: "Filament preset name 1" });
      await user.clear(name);
      await user.type(name, "Local draft");
      app.route({
        "GET /api/v1/filament-profiles": json([aFilamentProfile({ name: "Remote rename" })]),
      });

      await act(async () => {
        await app.client.invalidateQueries({ queryKey: queryKeys.filamentProfiles });
      });

      expect(
        app.requestsWithMethod("GET").filter((call) => call.url.endsWith("/filament-profiles")),
      ).toHaveLength(2);
      expect(name).toHaveValue("Local draft");
    });

    it("keeps another row draft after creating a preset", async () => {
      const user = userEvent.setup();
      const app = renderCard({
        routes: {
          "POST /api/v1/filament-profiles": json(
            aFilamentProfile({ id: 2, name: "Second preset" }),
          ),
        },
      });
      fireEvent.change(await screen.findByRole("textbox", { name: "Filament preset name 1" }), {
        target: { value: "Local draft" },
      });
      await user.click(screen.getByRole("button", { name: "New filament" }));
      await user.type(
        within(screen.getByRole("form", { name: "Create filament preset" })).getByRole("textbox", {
          name: "Name",
        }),
        "Second preset",
      );

      await user.click(screen.getByRole("button", { name: "Add preset" }));

      await waitFor(() =>
        expect(
          app.requestsWithMethod("GET").filter((call) => call.url.endsWith("/filament-profiles")),
        ).toHaveLength(2),
      );
      expect(app.requestsWithMethod("POST")).toHaveLength(1);
      expect(screen.getByRole("textbox", { name: "Filament preset name 1" })).toHaveValue(
        "Local draft",
      );
    });

    it("recovers a failed catalog read without rendering empty success", async () => {
      const user = userEvent.setup();
      const app = renderCard({
        routes: { "GET /api/v1/filament-profiles": json({ detail: "offline" }, 500) },
      });
      await screen.findByRole("alert");
      expect(screen.queryByText("No filament presets")).toBeNull();
      app.route({ "GET /api/v1/filament-profiles": json([aFilamentProfile()]) });

      await user.click(screen.getByRole("button", { name: "Retry" }));

      expect(await screen.findByRole("textbox", { name: "Filament preset name 1" })).toHaveValue(
        "Everyday PLA",
      );
    });

    it("preserves a printer draft during a catalog refresh", async () => {
      const user = userEvent.setup();
      const app = renderCard();
      await user.click(screen.getByRole("tab", { name: /Printers/ }));
      const name = await screen.findByRole("textbox", { name: "Printer preset name 1" });
      await user.clear(name);
      await user.type(name, "Local printer draft");
      app.route({
        "GET /api/v1/printer-profiles": json([aPrinterProfile({ name: "Remote printer" })]),
      });

      await act(async () => {
        await app.client.invalidateQueries({ queryKey: queryKeys.printerProfiles });
      });

      expect(
        app.requestsWithMethod("GET").filter((call) => call.url.endsWith("/printer-profiles")),
      ).toHaveLength(2);
      expect(name).toHaveValue("Local printer draft");
    });

    it("opens on the filament tab", async () => {
      renderCard();

      expect(await screen.findByText("Filament presets")).toBeInTheDocument();
    });

    it("shows the printer presets on their own tab", async () => {
      const user = userEvent.setup();
      renderCard();
      await screen.findByText("Filament presets");

      await user.click(screen.getByRole("tab", { name: /Printers/ }));

      expect(await screen.findByText("Printer presets")).toBeInTheDocument();
    });

    it("says so when the presets could not be loaded", async () => {
      renderCard({
        routes: { "GET /api/v1/filament-profiles": json({ detail: "unavailable" }, 503) },
      });

      expect(await screen.findByRole("alert")).toBeInTheDocument();
    });
  });

  describe("creating a filament preset", () => {
    async function openForm(user: ReturnType<typeof userEvent.setup>) {
      await screen.findByText("Filament presets");
      await user.click(screen.getByRole("button", { name: /New filament/ }));
    }

    it("refuses a preset with no name", async () => {
      const user = userEvent.setup();
      renderCard();

      await openForm(user);

      expect(screen.getByRole("button", { name: "Add preset" })).toBeDisabled();
    });

    it("POSTs the preset the user described", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderCard({
        routes: {
          "POST /api/v1/filament-profiles": json(aFilamentProfile({ id: 2, name: "PETG" })),
        },
      });
      await openForm(user);
      await user.type(screen.getByPlaceholderText("Everyday PLA"), "PETG");
      await user.type(screen.getByPlaceholderText("PLA, PETG…"), "PETG");

      await user.click(screen.getByRole("button", { name: "Add preset" }));

      await waitFor(() =>
        expect(
          JSON.parse(
            requestsWithMethod("POST").find((call) => call.url.includes("filament-profiles"))
              ?.body ?? "{}",
          ),
        ).toMatchObject({ name: "PETG", material_type: "PETG" }),
      );
    });

    it("sends no cost when the field is left empty", async () => {
      // An empty box means "unknown", not zero. A preset silently costed at zero
      // makes every print using it look free.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderCard({
        routes: {
          "POST /api/v1/filament-profiles": json(aFilamentProfile({ id: 2, name: "PETG" })),
        },
      });
      await openForm(user);
      await user.type(screen.getByPlaceholderText("Everyday PLA"), "PETG");

      await user.click(screen.getByRole("button", { name: "Add preset" }));

      await waitFor(() =>
        expect(
          JSON.parse(
            requestsWithMethod("POST").find((call) => call.url.includes("filament-profiles"))
              ?.body ?? "{}",
          ),
        ).toMatchObject({ cost_per_kg: null }),
      );
    });
  });

  describe("editing a preset in place", () => {
    it.each(["filament", "printer"] as const)(
      "sends the original %s base after background refresh",
      async (kind) => {
        const user = userEvent.setup();
        let headers = new Headers();
        const app = renderCard({
          routes: {
            [`PATCH /api/v1/${kind}-profiles/1`]: (_url, init) => {
              headers = new Headers(init?.headers);
              return json({ detail: "edit_conflict" }, 412);
            },
          },
        });
        if (kind === "printer") await user.click(screen.getByRole("tab", { name: /Printers/ }));
        const label = kind === "filament" ? "Filament" : "Printer";
        const name = await screen.findByRole("textbox", { name: `${label} preset name 1` });
        await user.type(name, " draft");
        app.route({
          [`GET /api/v1/${kind}-profiles`]: json([
            { ...(kind === "filament" ? aFilamentProfile() : aPrinterProfile()), edit_version: 7 },
          ]),
        });
        await act(async () =>
          app.client.invalidateQueries({
            queryKey: kind === "filament" ? queryKeys.filamentProfiles : queryKeys.printerProfiles,
          }),
        );
        await user.click(screen.getByRole("heading", { name: `${label} presets` }));
        await screen.findByRole("button", { name: "Review current values" });
        expect(headers.get("If-Match")).toBe(
          `"${kind}-profile-1-e${anEditingBase().edit_epoch}-v1"`,
        );
        expect(app.requestsWithMethod("PATCH")).toHaveLength(1);
      },
    );

    it("keeps a pending profile locked after an earlier saved indicator expires", async () => {
      let current = aFilamentProfile();
      const pending = Promise.withResolvers<Response>();
      let writes = 0;
      renderCard({
        routes: {
          "GET /api/v1/filament-profiles": () => json([current]),
          "PATCH /api/v1/filament-profiles/1": () => {
            writes += 1;
            if (writes === 1) {
              current = aFilamentProfile({ name: "First", edit_version: 2 });
              return json(current);
            }
            return pending.promise;
          },
        },
      });
      const name = await screen.findByRole("textbox", { name: "Filament preset name 1" });
      vi.useFakeTimers({ shouldAdvanceTime: true });
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      try {
        await user.clear(name);
        await user.type(name, "First");
        await user.click(screen.getByRole("heading", { name: "Filament presets" }));
        await waitFor(() => expect(name).toBeEnabled());
        await user.clear(name);
        await user.type(name, "Second");
        await user.click(screen.getByRole("heading", { name: "Filament presets" }));
        await waitFor(() => expect(writes).toBe(2));
        expect(name).toBeDisabled();
        await act(async () => vi.advanceTimersByTimeAsync(1600));
        expect(name).toBeDisabled();
      } finally {
        current = aFilamentProfile({ name: "Second", edit_version: 3 });
        await act(async () => {
          pending.resolve(json(current));
          await pending.promise;
        });
        vi.useRealTimers();
      }
    });

    it("clears obsolete filament validation after adopting current values", async () => {
      const user = userEvent.setup();
      const app = renderCard({
        routes: { "PATCH /api/v1/filament-profiles/1": json({ detail: "edit_conflict" }, 412) },
      });
      await user.type(
        await screen.findByRole("textbox", { name: "Filament preset name 1" }),
        " draft",
      );
      await user.click(screen.getByRole("heading", { name: "Filament presets" }));
      const review = await screen.findByRole("button", { name: "Review current values" });
      app.route({ "GET /api/v1/filament-profiles": json([aFilamentProfile({ edit_version: 2 })]) });
      await user.click(review);
      const cost = screen.getByRole("textbox", { name: "Filament cost per kg 1" });
      await user.clear(cost);
      await user.type(cost, "-1");
      await user.click(screen.getByRole("button", { name: "Save revised changes" }));
      expect(cost).toHaveAttribute("aria-invalid", "true");
      await user.click(screen.getByRole("button", { name: "Use current values" }));
      expect(cost).toHaveValue("24.5");
      expect(cost).toHaveAttribute("aria-invalid", "false");
      expect(app.requestsWithMethod("PATCH")).toHaveLength(1);
    });

    it.each(["filament", "printer"] as const)(
      "requires adoption of a different %s history",
      async (kind) => {
        const user = userEvent.setup();
        const app = renderCard({
          routes: { [`PATCH /api/v1/${kind}-profiles/1`]: json({ detail: "edit_conflict" }, 412) },
        });
        if (kind === "printer") await user.click(screen.getByRole("tab", { name: /Printers/ }));
        const label = kind === "filament" ? "Filament" : "Printer";
        const name = await screen.findByRole("textbox", { name: `${label} preset name 1` });
        await user.type(name, " draft");
        await user.click(screen.getByRole("heading", { name: `${label} presets` }));
        const review = await screen.findByRole("button", { name: "Review current values" });
        app.route({
          [`GET /api/v1/${kind}-profiles`]: json([
            {
              ...(kind === "filament" ? aFilamentProfile() : aPrinterProfile()),
              edit_epoch: "f".repeat(32),
              name: "New history",
            },
          ]),
        });
        await user.click(review);
        expect(await screen.findByRole("button", { name: "Save revised changes" })).toBeDisabled();
        expect(
          screen.getByText(
            "Preset history changed. Use current values before starting another edit.",
          ),
        ).toBeVisible();
        await user.click(screen.getByRole("button", { name: "Use current values" }));
        expect(name).toHaveValue("New history");
        expect(app.requestsWithMethod("PATCH")).toHaveLength(1);
      },
    );

    it.each(["filament", "printer"] as const)(
      "adopts an uncertain %s save without another write",
      async (kind) => {
        const user = userEvent.setup();
        const app = renderCard({
          routes: {
            [`PATCH /api/v1/${kind}-profiles/1`]: () =>
              Promise.reject(new TypeError("Failed to fetch")),
          },
        });
        if (kind === "printer") await user.click(screen.getByRole("tab", { name: /Printers/ }));
        const label = kind === "filament" ? "Filament" : "Printer";
        const name = await screen.findByRole("textbox", { name: `${label} preset name 1` });
        await user.clear(name);
        await user.type(name, "Possibly saved");
        await user.click(screen.getByRole("heading", { name: `${label} presets` }));
        const review = await screen.findByRole("button", { name: "Review current values" });
        const current = {
          ...(kind === "filament" ? aFilamentProfile() : aPrinterProfile()),
          edit_version: 2,
          name: "Possibly saved",
        };
        app.route({ [`GET /api/v1/${kind}-profiles`]: json([current]) });
        await user.click(review);
        await user.click(await screen.findByRole("button", { name: "Use current values" }));
        expect(name).toHaveValue("Possibly saved");
        expect(app.requestsWithMethod("PATCH")).toHaveLength(1);
        expect(
          screen.queryByRole("button", { name: "Save revised changes" }),
        ).not.toBeInTheDocument();
      },
    );

    it.each(["filament", "printer"] as const)(
      "retires a pending %s review with its session",
      async (kind) => {
        const user = userEvent.setup();
        const app = renderCard({
          routes: { [`PATCH /api/v1/${kind}-profiles/1`]: json({ detail: "edit_conflict" }, 412) },
        });
        if (kind === "printer") await user.click(screen.getByRole("tab", { name: /Printers/ }));
        const label = kind === "filament" ? "Filament" : "Printer";
        await user.type(
          await screen.findByRole("textbox", { name: `${label} preset name 1` }),
          " draft",
        );
        await user.click(screen.getByRole("heading", { name: `${label} presets` }));
        const review = await screen.findByRole("button", { name: "Review current values" });
        const pending = Promise.withResolvers<Response>();
        let signal: AbortSignal | null | undefined;
        app.route({
          [`GET /api/v1/${kind}-profiles`]: (_url, init) => {
            signal = init?.signal;
            return pending.promise;
          },
        });
        await user.click(review);
        await waitFor(() => expect(signal).toBeDefined());
        await act(async () => {
          app.unmount();
          clearLogin();
          pending.resolve(
            json([
              {
                ...(kind === "filament" ? aFilamentProfile() : aPrinterProfile()),
                name: "Retired values",
                edit_version: 2,
              },
            ]),
          );
          await pending.promise;
        });
        expect(signal?.aborted).toBe(true);
        expect(
          app.client.getQueryData(
            kind === "filament" ? queryKeys.filamentProfiles : queryKeys.printerProfiles,
          ),
        ).toBeUndefined();
        expect(app.requestsWithMethod("PATCH")).toHaveLength(1);
      },
    );

    it.each(["filament", "printer"] as const)(
      "retains a newer %s row across a delayed acknowledgement",
      async (kind) => {
        const user = userEvent.setup();
        const pending = Promise.withResolvers<Response>();
        const app = renderCard({
          routes: { [`PATCH /api/v1/${kind}-profiles/1`]: () => pending.promise },
        });
        if (kind === "printer") await user.click(screen.getByRole("tab", { name: /Printers/ }));
        const label = kind === "filament" ? "Filament" : "Printer";
        const name = await screen.findByRole("textbox", { name: `${label} preset name 1` });
        await user.type(name, " draft");
        await user.click(screen.getByRole("heading", { name: `${label} presets` }));
        await waitFor(() => expect(app.requestsWithMethod("PATCH")).toHaveLength(1));
        const newer = {
          ...(kind === "filament" ? aFilamentProfile() : aPrinterProfile()),
          edit_version: 3,
          name: "Newest profile",
        };
        app.route({ [`GET /api/v1/${kind}-profiles`]: json([newer]) });
        await act(async () => {
          app.client.setQueryData(
            kind === "filament" ? queryKeys.filamentProfiles : queryKeys.printerProfiles,
            [newer],
          );
          pending.resolve(json({ ...newer, edit_version: 2, name: "Earlier receipt" }));
          await pending.promise;
        });
        await waitFor(() => expect(name).toHaveValue("Newest profile"));
        expect(
          app.client.getQueryData(
            kind === "filament" ? queryKeys.filamentProfiles : queryKeys.printerProfiles,
          ),
        ).toEqual([newer]);
      },
    );

    it.each(["filament", "printer"] as const)(
      "validates a revised %s numeric draft",
      async (kind) => {
        const user = userEvent.setup();
        const app = renderCard({
          routes: { [`PATCH /api/v1/${kind}-profiles/1`]: json({ detail: "edit_conflict" }, 412) },
        });
        if (kind === "printer") await user.click(screen.getByRole("tab", { name: /Printers/ }));
        const label = kind === "filament" ? "Filament" : "Printer";
        await user.type(
          await screen.findByRole("textbox", { name: `${label} preset name 1` }),
          " draft",
        );
        await user.click(screen.getByRole("heading", { name: `${label} presets` }));
        const review = await screen.findByRole("button", { name: "Review current values" });
        app.route({
          [`GET /api/v1/${kind}-profiles`]: json([
            { ...(kind === "filament" ? aFilamentProfile() : aPrinterProfile()), edit_version: 2 },
          ]),
        });
        await user.click(review);
        const input = screen.getByRole("textbox", {
          name: kind === "filament" ? "Filament cost per kg 1" : "Printer nozzle diameter 1",
        });
        await user.clear(input);
        await user.type(input, "-1");
        await user.click(screen.getByRole("button", { name: "Save revised changes" }));
        expect(input).toHaveValue("-1");
        expect(app.requestsWithMethod("PATCH")).toHaveLength(1);
      },
    );

    it.each([
      { kind: "filament", failure: "denied" },
      { kind: "filament", failure: "deleted" },
      { kind: "printer", failure: "denied" },
      { kind: "printer", failure: "deleted" },
    ])("retires a $failure $kind profile review", async ({ kind, failure }) => {
      const user = userEvent.setup();
      const app = renderCard({
        routes: { [`PATCH /api/v1/${kind}-profiles/1`]: json({ detail: "edit_conflict" }, 412) },
      });
      if (kind === "printer") await user.click(screen.getByRole("tab", { name: /Printers/ }));
      const label = kind === "filament" ? "Filament" : "Printer";
      await user.type(
        await screen.findByRole("textbox", { name: `${label} preset name 1` }),
        " draft",
      );
      await user.click(screen.getByRole("heading", { name: `${label} presets` }));
      const review = await screen.findByRole("button", { name: "Review current values" });
      app.route({
        [`GET /api/v1/${kind}-profiles`]:
          failure === "denied" ? json({ detail: "permission_denied" }, 403) : json([]),
      });
      await user.click(review);
      await waitFor(() =>
        expect(
          screen.queryByRole("textbox", { name: `${label} preset name 1` }),
        ).not.toBeInTheDocument(),
      );
      expect(
        screen.queryByRole("button", { name: "Save revised changes" }),
      ).not.toBeInTheDocument();
      expect(app.requestsWithMethod("PATCH")).toHaveLength(1);
    });

    it("refuses a reviewed filament that now belongs to Spoolman", async () => {
      const user = userEvent.setup();
      const app = renderCard({
        routes: {
          "PATCH /api/v1/filament-profiles/1": json({ detail: "filament_profile_linked" }, 409),
        },
      });
      const name = await screen.findByRole("textbox", { name: "Filament preset name 1" });
      await user.type(name, " draft");
      await user.click(screen.getByRole("heading", { name: "Filament presets" }));
      const review = await screen.findByRole("button", { name: "Review current values" });
      app.route({
        "GET /api/v1/filament-profiles": json([
          aFilamentProfile({ edit_version: 2, spoolman_filament_id: 42 }),
        ]),
      });
      await user.click(review);
      expect(await screen.findByRole("button", { name: "Save revised changes" })).toBeDisabled();
      expect(name).toBeDisabled();
      expect(name).toHaveValue("Everyday PLA draft");
      expect(app.requestsWithMethod("PATCH")).toHaveLength(1);
    });

    it.each(["filament", "printer"] as const)(
      "preserves a conflicting %s profile draft for explicit review",
      async (kind) => {
        const user = userEvent.setup();
        const app = renderCard({
          routes: {
            [`PATCH /api/v1/${kind}-profiles/1`]: json({ detail: "edit_conflict" }, 412),
          },
        });
        if (kind === "printer") await user.click(screen.getByRole("tab", { name: /Printers/ }));
        const label = kind === "filament" ? "Filament" : "Printer";
        const name = await screen.findByRole("textbox", { name: `${label} preset name 1` });
        await user.clear(name);
        await user.type(name, "My draft");
        await user.click(screen.getByRole("heading", { name: `${label} presets` }));
        await screen.findByRole("button", { name: "Review current values" });
        expect(name).toHaveValue("My draft");
        await user.click(name);
        await user.click(screen.getByRole("heading", { name: `${label} presets` }));
        expect(app.requestsWithMethod("PATCH")).toHaveLength(1);
        const current = {
          ...(kind === "filament" ? aFilamentProfile() : aPrinterProfile()),
          edit_version: 2,
          notes: "Other editor notes",
        };
        app.route({ [`GET /api/v1/${kind}-profiles`]: json([current]) });
        await user.click(screen.getByRole("button", { name: "Review current values" }));
        expect(await screen.findByText("Other editor notes")).toBeVisible();
        const saved = { ...current, edit_version: 3, name: "My draft" };
        let revisedHeaders = new Headers();
        app.route({
          [`PATCH /api/v1/${kind}-profiles/1`]: (_url, init) => {
            revisedHeaders = new Headers(init?.headers);
            return json(saved);
          },
          [`GET /api/v1/${kind}-profiles`]: json([saved]),
        });
        await user.click(screen.getByRole("button", { name: "Save revised changes" }));
        await waitFor(() => expect(app.requestsWithMethod("PATCH")).toHaveLength(2));
        const revised = app.requestsWithMethod("PATCH")[1];
        expect(revisedHeaders.get("If-Match")).toBe(
          `"${kind}-profile-1-e${current.edit_epoch}-v2"`,
        );
        expect(JSON.parse(revised.body)).toEqual({ name: "My draft" });
        await waitFor(() =>
          expect(
            screen.queryByRole("button", { name: "Save revised changes" }),
          ).not.toBeInTheDocument(),
        );
        expect(screen.getByRole("textbox", { name: `${label} notes 1` })).toHaveValue(
          "Other editor notes",
        );
      },
    );

    it("saves a changed field when the row is left", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderCard({
        routes: {
          "PATCH /api/v1/filament-profiles/1": json(
            aFilamentProfile({ name: "PETG", edit_version: 2 }),
          ),
        },
      });
      const name = await screen.findByLabelText("Filament preset name 1");

      await user.clear(name);
      await user.type(name, "PETG");
      // The row saves when focus leaves the *row*, not the field — tabbing to
      // the next input in the same row is still editing.
      await user.click(screen.getByText("Filament presets"));

      await waitFor(() =>
        expect(
          requestsWithMethod("PATCH").some((call) => call.url.includes("filament-profiles/1")),
        ).toBe(true),
      );
    });

    it("publishes the authoritative saved filament row", async () => {
      const user = userEvent.setup();
      let current = aFilamentProfile();
      const app = renderCard({
        routes: {
          "GET /api/v1/filament-profiles": () => json([current]),
          "PATCH /api/v1/filament-profiles/1": () => {
            current = aFilamentProfile({ name: "Server normalized name", edit_version: 2 });
            return json(current);
          },
        },
      });
      const name = await screen.findByRole("textbox", { name: "Filament preset name 1" });
      await user.clear(name);
      await user.type(name, "typed name");

      await user.click(screen.getByRole("heading", { name: "Filament presets" }));

      await waitFor(() => expect(name).toHaveValue("Server normalized name"));
      expect(app.client.getQueryData(queryKeys.filamentProfiles)).toEqual([current]);
    });

    it("blocks duplicate autosave while a filament row is pending", async () => {
      let respond!: (response: Response) => void;
      const pending = new Promise<Response>((resolve) => {
        respond = resolve;
      });
      const user = userEvent.setup();
      const app = renderCard({ routes: { "PATCH /api/v1/filament-profiles/1": () => pending } });
      const name = await screen.findByRole("textbox", { name: "Filament preset name 1" });
      await user.type(name, " edited");

      await user.click(screen.getByRole("heading", { name: "Filament presets" }));

      await waitFor(() => expect(app.requestsWithMethod("PATCH")).toHaveLength(1));
      expect(name).toBeDisabled();
      await act(async () => {
        respond(json(aFilamentProfile({ name: "Everyday PLA edited", edit_version: 2 })));
        await pending;
      });
    });

    it("keeps a confirmed profile save across a late catalog read", async () => {
      let respond!: (response: Response) => void;
      let signal: AbortSignal | null | undefined;
      const pending = new Promise<Response>((resolve) => {
        respond = resolve;
      });
      const user = userEvent.setup();
      const app = renderCard();
      const name = await screen.findByRole("textbox", { name: "Filament preset name 1" });
      await user.type(name, " edited");
      app.route({
        "GET /api/v1/filament-profiles": (_url, init) => {
          signal = init?.signal;
          return pending;
        },
      });
      const refresh = app.client.invalidateQueries({ queryKey: queryKeys.filamentProfiles });
      await waitFor(() => expect(signal).toBeDefined());
      const saved = aFilamentProfile({ name: "Authoritative saved name", edit_version: 2 });
      app.route({
        "GET /api/v1/filament-profiles": json([saved]),
        "PATCH /api/v1/filament-profiles/1": json(saved),
      });

      await user.click(screen.getByRole("heading", { name: "Filament presets" }));
      await waitFor(() => expect(name).toHaveValue(saved.name));
      expect(signal?.aborted).toBe(true);
      await act(async () => {
        respond(json([aFilamentProfile({ name: "Stale name" })]));
        await pending;
        await refresh;
      });

      expect(name).toHaveValue(saved.name);
      expect(app.client.getQueryData(queryKeys.filamentProfiles)).toEqual([saved]);
    });

    it("never starts a retired gesture after delayed preparation", async () => {
      const user = userEvent.setup();
      const app = renderCard({
        routes: { "PATCH /api/v1/filament-profiles/1": json(aFilamentProfile()) },
      });
      const name = await screen.findByRole("textbox", { name: "Filament preset name 1" });
      await user.type(name, " edited");
      let resume!: () => void;
      const paused = new Promise<void>((resolve) => {
        resume = resolve;
      });
      const spy = vi.spyOn(app.client, "cancelQueries").mockImplementationOnce(() => paused);

      await user.click(screen.getByRole("heading", { name: "Filament presets" }));
      await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
      await act(async () => {
        // The real AuthProvider retires the feature subtree with the incarnation.
        app.unmount();
        clearLogin();
        resume();
        await paused;
      });

      expect(app.requestsWithMethod("PATCH")).toHaveLength(0);
      expect(app.client.getQueriesData({ queryKey: queryKeys.filamentProfiles })).toEqual([]);
      spy.mockRestore();
    });

    it("does not publish a retired profile save after delayed cancellation", async () => {
      const user = userEvent.setup();
      const app = renderCard({
        routes: {
          "PATCH /api/v1/filament-profiles/1": json(
            aFilamentProfile({ name: "Saved", edit_version: 2 }),
          ),
        },
      });
      const name = await screen.findByRole("textbox", { name: "Filament preset name 1" });
      await user.type(name, " edited");
      let resume!: () => void;
      const paused = new Promise<void>((resolve) => {
        resume = resolve;
      });
      const cancel = app.client.cancelQueries.bind(app.client);
      const spy = vi
        .spyOn(app.client, "cancelQueries")
        .mockImplementationOnce(cancel)
        .mockImplementationOnce(cancel)
        .mockImplementationOnce(() => paused);

      await user.click(screen.getByRole("heading", { name: "Filament presets" }));
      await waitFor(() => expect(spy).toHaveBeenCalledTimes(4));
      await act(async () => {
        // The real AuthProvider retires the feature subtree with the incarnation.
        app.unmount();
        clearLogin();
        resume();
        await paused;
      });

      await waitFor(() =>
        expect(app.client.getQueriesData({ queryKey: queryKeys.filamentProfiles })).toEqual([]),
      );
      expect(app.client.getQueriesData({ queryKey: queryKeys.printerProfiles })).toEqual([]);
      expect(app.requestsWithMethod("PATCH")).toHaveLength(1);
      spy.mockRestore();
    });

    it("saves nothing when the row is left unchanged", async () => {
      // Blur fires on every pass through a row, so an unconditional save would
      // rewrite every preset a user merely scrolled past.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderCard();
      const name = await screen.findByLabelText("Filament preset name 1");

      await user.click(name);
      await user.click(screen.getByText("Filament presets"));

      expect(requestsWithMethod("PATCH")).toHaveLength(0);
    });

    it("asks before deleting a filament preset", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderCard({
        routes: { "DELETE /api/v1/filament-profiles/1": json(null, 204) },
      });
      await screen.findByLabelText("Filament preset name 1");

      await user.click(screen.getByRole("button", { name: /Delete filament preset/ }));

      expect(requestsWithMethod("DELETE")).toHaveLength(0);
      expect(screen.getByRole("dialog", { name: "Delete filament preset?" })).toBeInTheDocument();
    });
  });

  describe("when Spoolman owns the presets", () => {
    it("marks a synced preset as such", async () => {
      renderCard({ spoolman: true, filaments: [aFilamentProfile({ spoolman_filament_id: 7 })] });

      expect(await screen.findByText("Synced")).toBeInTheDocument();
    });

    it("refuses to edit a synced preset locally", async () => {
      // A local edit would be overwritten on the next sync, with nothing said.
      renderCard({ spoolman: true, filaments: [aFilamentProfile({ spoolman_filament_id: 7 })] });

      expect(await screen.findByLabelText("Filament preset name 1")).toBeDisabled();
    });

    it("offers to pull the presets from Spoolman", async () => {
      renderCard({ spoolman: true });

      expect(await screen.findByRole("button", { name: /Sync|Import/ })).toBeInTheDocument();
    });

    it("offers no sync when Spoolman is not configured", async () => {
      renderCard();

      await screen.findByText("Filament presets");
      expect(screen.queryByRole("button", { name: /Sync from Spoolman/ })).toBeNull();
    });
  });

  describe("syncing from Spoolman", () => {
    it("offers no sync when Spoolman is not configured", async () => {
      // The button would 400; a control that only fails reads as the feature
      // being broken rather than absent.
      renderCard();

      await screen.findByText("Filament presets");
      expect(screen.queryByRole("button", { name: /Sync Spoolman/ })).toBeNull();
    });

    it("pulls the filaments Spoolman knows about", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderCard({
        spoolman: true,
        routes: {
          "POST /api/v1/spoolman/sync-filaments": json({ created: 2, updated: 1, adopted: 0 }),
        },
      });
      await screen.findByText("Filament presets");

      await user.click(screen.getByRole("button", { name: /Sync Spoolman/ }));

      await waitFor(() =>
        expect(requestsWithMethod("POST").some((call) => call.url.includes("sync-filaments"))).toBe(
          true,
        ),
      );
    });

    it("says what the sync changed", async () => {
      // "Synced" alone leaves the operator unsure whether anything moved.
      const user = userEvent.setup();
      renderCard({
        spoolman: true,
        routes: {
          "POST /api/v1/spoolman/sync-filaments": json({ created: 2, updated: 1, adopted: 0 }),
        },
      });
      await screen.findByText("Filament presets");

      await user.click(screen.getByRole("button", { name: /Sync Spoolman/ }));

      expect(await screen.findByText(/2 added, 1 updated/)).toBeInTheDocument();
    });

    it("preserves a local draft when Spoolman refreshes the catalog", async () => {
      const user = userEvent.setup();
      const app = renderCard({
        spoolman: true,
        routes: {
          "POST /api/v1/spoolman/sync-filaments": json({
            created: 0,
            updated: 1,
            adopted: 0,
            unlinked: 0,
          }),
        },
      });
      const name = await screen.findByRole("textbox", { name: "Filament preset name 1" });
      fireEvent.change(name, { target: { value: "Local draft" } });
      app.route({
        "GET /api/v1/filament-profiles": json([aFilamentProfile({ name: "Remote sync" })]),
      });

      await user.click(screen.getByRole("button", { name: /Sync Spoolman/ }));

      await waitFor(() =>
        expect(app.client.getQueryData(queryKeys.filamentProfiles)).toEqual([
          aFilamentProfile({ name: "Remote sync" }),
        ]),
      );
      expect(name).toHaveValue("Local draft");
      expect(
        app.requestsWithMethod("GET").filter((call) => call.url.endsWith("/filament-profiles")),
      ).toHaveLength(2);
    });

    it("surfaces a Spoolman that could not be reached", async () => {
      const user = userEvent.setup();
      renderCard({
        spoolman: true,
        routes: {
          "POST /api/v1/spoolman/sync-filaments": json({ detail: "spoolman_unreachable" }, 502),
        },
      });
      await screen.findByText("Filament presets");

      await user.click(screen.getByRole("button", { name: /Sync Spoolman/ }));

      expect(await screen.findByRole("alert")).toHaveTextContent("Spoolman unreachable.");
    });
  });

  describe("removing a preset", () => {
    it("removes the filament preset the user chose", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderCard({
        routes: { "DELETE /api/v1/filament-profiles/1": json(null, 204) },
      });
      await screen.findByText("Filament presets");

      await user.click(
        await screen.findByRole("button", { name: "Delete filament preset Everyday PLA" }),
      );
      await user.click(
        within(screen.getByRole("dialog", { name: "Delete filament preset?" })).getByRole(
          "button",
          { name: "Delete preset" },
        ),
      );

      await waitFor(() =>
        expect(
          requestsWithMethod("DELETE").some((call) => call.url.endsWith("/filament-profiles/1")),
        ).toBe(true),
      );
    });

    it("removes the printer preset the user chose", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderCard({
        routes: { "DELETE /api/v1/printer-profiles/1": json(null, 204) },
      });
      await screen.findByText("Filament presets");
      await user.click(screen.getByRole("tab", { name: /Printers/ }));

      await user.click(
        screen.getByRole("button", { name: "Delete printer preset Voron 2.4 — 0.4 mm" }),
      );
      await user.click(
        within(screen.getByRole("dialog", { name: "Delete printer preset?" })).getByRole("button", {
          name: "Delete preset",
        }),
      );

      await waitFor(() =>
        expect(
          requestsWithMethod("DELETE").some((call) => call.url.endsWith("/printer-profiles/1")),
        ).toBe(true),
      );
    });

    it("surfaces a preset the server would not remove", async () => {
      // A preset in use by a revision cannot go; silently leaving it on screen
      // reads as the button not working.
      const user = userEvent.setup();
      renderCard({
        routes: {
          "DELETE /api/v1/filament-profiles/1": json({ detail: "profile_in_use" }, 409),
        },
      });
      await user.click(
        await screen.findByRole("button", { name: "Delete filament preset Everyday PLA" }),
      );
      await user.click(
        within(screen.getByRole("dialog", { name: "Delete filament preset?" })).getByRole(
          "button",
          { name: "Delete preset" },
        ),
      );

      expect(await screen.findByRole("alert")).toHaveTextContent("Profile in use.");
    });
  });

  describe("creating a printer preset", () => {
    it("POSTs the preset the user described", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderCard({
        routes: {
          "POST /api/v1/printer-profiles": json(aPrinterProfile({ id: 2, name: "Prusa MK4" })),
        },
      });
      await screen.findByText("Filament presets");
      await user.click(screen.getByRole("tab", { name: /Printers/ }));
      await user.click(screen.getByRole("button", { name: /New printer/ }));
      await user.type(screen.getByPlaceholderText("Voron 2.4 — 0.4 mm"), "Prusa MK4");

      await user.click(screen.getByRole("button", { name: "Add preset" }));

      await waitFor(() =>
        expect(
          requestsWithMethod("POST").some((call) => call.url.includes("printer-profiles")),
        ).toBe(true),
      );
    });
  });
  describe("editing a printer preset in place", () => {
    it("saves a changed field when the row is left", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderCard({
        routes: {
          "PATCH /api/v1/printer-profiles/1": json(
            aPrinterProfile({ name: "MK4", edit_version: 2 }),
          ),
        },
      });
      await screen.findByText("Filament presets");
      await user.click(screen.getByRole("tab", { name: /Printers/ }));
      const name = await screen.findByLabelText("Printer preset name 1");

      await user.clear(name);
      await user.type(name, "MK4");
      await user.click(screen.getByText("Printer presets"));

      await waitFor(() =>
        expect(
          requestsWithMethod("PATCH").some((call) => call.url.includes("printer-profiles/1")),
        ).toBe(true),
      );
    });

    it("saves nothing when the row is left unchanged", async () => {
      // Blur fires on every pass through a row, so an unconditional save would
      // rewrite every preset a user merely scrolled past.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderCard();
      await screen.findByText("Filament presets");
      await user.click(screen.getByRole("tab", { name: /Printers/ }));
      const name = await screen.findByLabelText("Printer preset name 1");

      await user.click(name);
      await user.click(screen.getByText("Printer presets"));

      expect(requestsWithMethod("PATCH")).toHaveLength(0);
    });

    it("refuses a nozzle diameter that is not a number", async () => {
      // It is written into every G-code compatibility check; a NaN there
      // silently matches nothing.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderCard();
      await screen.findByText("Filament presets");
      await user.click(screen.getByRole("tab", { name: /Printers/ }));
      const nozzle = await screen.findByLabelText("Printer nozzle diameter 1");

      await user.clear(nozzle);
      await user.type(nozzle, "wide");
      await user.click(screen.getByText("Printer presets"));

      expect(requestsWithMethod("PATCH")).toHaveLength(0);
    });

    it("surfaces a preset the server would not save", async () => {
      const user = userEvent.setup();
      renderCard({
        routes: { "PATCH /api/v1/printer-profiles/1": json({ detail: "name_taken" }, 409) },
      });
      await screen.findByText("Filament presets");
      await user.click(screen.getByRole("tab", { name: /Printers/ }));
      const name = await screen.findByLabelText("Printer preset name 1");

      await user.clear(name);
      await user.type(name, "MK4");
      await user.click(screen.getByText("Printer presets"));

      expect(await screen.findByRole("alert")).toHaveTextContent("Name taken.");
    });
  });

  describe("when a filament preset cannot be saved", () => {
    it("surfaces the server's refusal", async () => {
      const user = userEvent.setup();
      renderCard({
        routes: { "PATCH /api/v1/filament-profiles/1": json({ detail: "name_taken" }, 409) },
      });
      const name = await screen.findByLabelText("Filament preset name 1");

      await user.clear(name);
      await user.type(name, "PETG");
      await user.click(screen.getByText("Filament presets"));

      expect(await screen.findByRole("alert")).toHaveTextContent("Name taken.");
    });

    it("refuses a cost that is not a number", async () => {
      // Cost feeds the statistics page; a NaN there poisons every total.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderCard();
      const cost = await screen.findByLabelText("Filament cost per kg 1");

      await user.clear(cost);
      await user.type(cost, "cheap");
      await user.click(screen.getByText("Filament presets"));

      expect(requestsWithMethod("PATCH")).toHaveLength(0);
    });

    it("refuses a negative filament cost visibly", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderCard();
      const cost = await screen.findByLabelText("Filament cost per kg 1");

      await user.clear(cost);
      await user.type(cost, "-1");
      await user.click(screen.getByText("Filament presets"));

      expect(await screen.findByText("Cost must be 0 or more.")).toBeVisible();
      expect(cost).toHaveAttribute("aria-invalid", "true");
      expect(requestsWithMethod("PATCH")).toHaveLength(0);
    });
  });
});
