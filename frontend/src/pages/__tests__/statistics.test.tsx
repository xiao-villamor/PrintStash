/*
 * What the library actually cost, in time and filament and money.
 *
 * Every figure here is derived from completed jobs, which makes the *period* the
 * most load-bearing control on the page: the same query over 7 days and over a
 * year produces two different answers and neither is wrong. So the period is
 * asserted on the request it produces, not on the number that comes back.
 *
 * Which widgets are shown is a per-browser preference, and it has to survive a
 * value the user could have edited by hand — a corrupted preference must fall
 * back to the default set rather than render a page with nothing on it, which
 * reads as "you have no statistics".
 *
 * The empty period is a real state rather than an error: a self-hoster who has
 * printed nothing this month should see that said, not a spinner that never
 * resolves or a grid of zeroes that looks like a bug.
 */

import "@testing-library/jest-dom/vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import StatisticsPage from "@/pages/statistics";
import { queryKeys } from "@/lib/query-client";
import { json, renderApp, type RenderAppOptions } from "@/test-support/render";
import type { PrintStatisticsRead } from "@/types";

const WIDGET_PREFERENCE_KEY = "printstash:statistics-widgets";

function statistics(over: Partial<PrintStatisticsRead> = {}): PrintStatisticsRead {
  return {
    period: "30d",
    start_at: "2026-01-01T00:00:00Z",
    end_at: "2026-01-31T00:00:00Z",
    total_prints: 12,
    total_cost: 34.5,
    total_filament_g: 900,
    avg_filament_g: 75,
    total_print_time_s: 36000,
    top_collections: [
      { collection_id: 1, name: "Parts", path: "parts", print_count: 8, total_cost: 20 },
    ],
    top_filaments: [
      {
        material_type: "PLA",
        material_brand: "Prusament",
        print_count: 9,
        total_g: 700,
        total_cost: 25,
      },
    ],
    top_models: [{ model_id: 1, name: "Benchy", print_count: 5, total_g: 300 }],
    top_printers: [{ printer_id: 4, name: "Voron", print_count: 12, print_time_s: 36000 }],
    cost_over_time: [{ bucket: "2026-01-01", cost: 10, filament_g: 200, print_count: 3 }],
    ...over,
  };
}

function renderStatistics(options: RenderAppOptions & { stats?: PrintStatisticsRead } = {}) {
  const { stats = statistics(), seed = [], routes = {}, ...rest } = options;
  // The statistics query is deliberately *not* seeded: the period is the whole
  // subject here, and a pre-filled cache means no request is ever made.
  return renderApp(<StatisticsPage />, {
    seed: [[queryKeys.vaultConfig, { currency: "USD" }], ...seed],
    routes: {
      "GET /api/v1/models/stats/prints": json(stats),
      "GET /api/v1/config": json({ currency: "USD" }),
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

describe("StatisticsPage", () => {
  describe("read recovery", () => {
    it("retries failed statistics for the selected period", async () => {
      const user = userEvent.setup();
      const app = renderStatistics({
        routes: { "GET /api/v1/models/stats/prints": json({ detail: "offline" }, 500) },
      });
      await screen.findByText("Failed to load statistics.");
      app.route({ "GET /api/v1/models/stats/prints": json(statistics()) });

      await user.click(screen.getByRole("button", { name: "Retry" }));

      expect(await screen.findByText("34.50", { exact: false })).toBeInTheDocument();
    });
  });

  describe("what it reports", () => {
    it("names the page", async () => {
      renderStatistics();

      expect(await screen.findByText("Statistics")).toBeInTheDocument();
    });

    it("reports the total cost", async () => {
      renderStatistics();

      expect(await screen.findByText("Total cost")).toBeInTheDocument();
    });

    it("reports the filament used", async () => {
      renderStatistics();

      expect(await screen.findByText("Filament used")).toBeInTheDocument();
    });

    it("lists the collections printed from most", async () => {
      renderStatistics();

      expect(await screen.findByText("Top collections")).toBeInTheDocument();
    });

    it("lists the models printed most", async () => {
      renderStatistics();

      expect(await screen.findByText("Most printed models")).toBeInTheDocument();
    });
  });

  describe("the reporting period", () => {
    it("asks for thirty days by default", async () => {
      const { requests } = renderStatistics();

      await waitFor(() =>
        expect(requests().some((call) => call.url.includes("period=30d"))).toBe(true),
      );
    });

    it("asks for the period the user chose", async () => {
      // The same query over 7 days and over a year produces two different
      // answers, and neither is wrong — so this is the control that matters.
      const user = userEvent.setup();
      const { requests } = renderStatistics();
      await screen.findByText("Statistics");

      await user.click(screen.getByRole("button", { name: "7 days" }));

      await waitFor(() =>
        expect(requests().some((call) => call.url.includes("period=7d"))).toBe(true),
      );
    });

    it("ignores an obsolete period read", async () => {
      let respond!: (response: Response) => void;
      let signal: AbortSignal | null | undefined;
      const pending = new Promise<Response>((resolve) => {
        respond = resolve;
      });
      const user = userEvent.setup();
      const app = renderStatistics({
        routes: {
          "GET /api/v1/models/stats/prints?period=30d": (_url, init) => {
            signal = init?.signal;
            return pending;
          },
          "GET /api/v1/models/stats/prints?period=7d": json(
            statistics({ period: "7d", total_cost: 77 }),
          ),
        },
      });
      await waitFor(() => expect(signal).toBeDefined());

      await user.click(screen.getByRole("button", { name: "7 days" }));
      expect(await screen.findByText("77.00", { exact: false })).toBeInTheDocument();
      expect(signal?.aborted).toBe(true);
      await act(async () => {
        respond(json(statistics({ total_cost: 1 })));
        await pending;
      });

      expect(screen.getByText("77.00", { exact: false })).toBeInTheDocument();
      expect(app.client.getQueryData(queryKeys.printStats("30d"))).toBeUndefined();
    });

    it("asks for everything when the user picks all time", async () => {
      const user = userEvent.setup();
      const { requests } = renderStatistics();
      await screen.findByText("Statistics");

      await user.click(screen.getByRole("button", { name: "All time" }));

      await waitFor(() =>
        expect(requests().some((call) => call.url.includes("period=all"))).toBe(true),
      );
    });
  });

  describe("the time-series controls", () => {
    it("plots filament usage when the filament metric is selected", async () => {
      const user = userEvent.setup();
      renderStatistics();
      await screen.findByText("Total cost");

      await user.click(screen.getByRole("button", { name: "Filament" }));

      expect(await screen.findByText("Filament over time")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Filament" })).toHaveAttribute(
        "aria-pressed",
        "true",
      );
    });

    it("plots print counts when the prints metric is selected", async () => {
      const user = userEvent.setup();
      renderStatistics();
      await screen.findByText("Total cost");

      await user.click(screen.getByRole("button", { name: "Prints" }));

      expect(await screen.findByText("Prints over time")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Prints" })).toHaveAttribute(
        "aria-pressed",
        "true",
      );
    });

    it("renders a line chart when the line chart type is selected", async () => {
      const user = userEvent.setup();
      renderStatistics();
      await screen.findByText("Total cost");

      await user.click(screen.getByRole("button", { name: "Line" }));

      expect(screen.getByRole("button", { name: "Line" })).toHaveAttribute("aria-pressed", "true");
    });

    it("renders a bar chart when the bar chart type is selected", async () => {
      const user = userEvent.setup();
      renderStatistics();
      await screen.findByText("Total cost");

      await user.click(screen.getByRole("button", { name: "Bar" }));

      expect(screen.getByRole("button", { name: "Bar" })).toHaveAttribute("aria-pressed", "true");
    });

    it("shows placeholders when optional statistics values are missing", async () => {
      renderStatistics({
        stats: statistics({
          start_at: null,
          total_cost: null,
          total_filament_g: null,
          cost_over_time: [{ bucket: "2026-01-01", cost: null, filament_g: null, print_count: 3 }],
        }),
      });

      expect(await screen.findByText("7-day forecast")).toBeInTheDocument();
      expect(screen.getAllByText("—").length).toBeGreaterThan(0);
    });

    it("uses zero for a missing filament datapoint", async () => {
      const user = userEvent.setup();
      renderStatistics({
        stats: statistics({
          cost_over_time: [{ bucket: "2026-01-01", cost: 10, filament_g: null, print_count: 3 }],
        }),
      });
      await screen.findByText("Total cost");

      await user.click(screen.getByRole("button", { name: "Filament" }));

      expect(screen.getByRole("button", { name: "Filament" })).toHaveAttribute(
        "aria-pressed",
        "true",
      );
    });
  });

  describe("choosing which widgets to show", () => {
    it("offers the customisation menu", async () => {
      const user = userEvent.setup();
      renderStatistics();
      await screen.findByText("Statistics");

      await user.click(screen.getByRole("button", { name: /Customize/ }));

      expect(await screen.findByRole("dialog")).toBeInTheDocument();
    });

    it("remembers a widget the user hid", async () => {
      const user = userEvent.setup();
      renderStatistics();
      await screen.findByText("Statistics");
      await user.click(screen.getByRole("button", { name: /Customize/ }));

      const dialog = await screen.findByRole("dialog");
      await user.click(within(dialog).getAllByRole("checkbox")[0]);

      expect(window.localStorage.getItem(WIDGET_PREFERENCE_KEY)).not.toBeNull();
    });

    it("remembers a widget the user restored", async () => {
      const user = userEvent.setup();
      window.localStorage.setItem(
        WIDGET_PREFERENCE_KEY,
        JSON.stringify(["printers", "filaments", "collections"]),
      );
      renderStatistics();
      await screen.findByText("Statistics");
      await user.click(screen.getByRole("button", { name: /Customize/ }));

      const dialog = await screen.findByRole("dialog");
      await user.click(within(dialog).getAllByRole("checkbox")[0]);

      expect(JSON.parse(window.localStorage.getItem(WIDGET_PREFERENCE_KEY) ?? "[]")).toContain(
        "models",
      );
    });

    it("falls back to the default widgets for a preference it cannot read", async () => {
      // The value is per-browser and hand-editable; a page with nothing on it
      // reads as "you have no statistics".
      window.localStorage.setItem(WIDGET_PREFERENCE_KEY, "not json");

      renderStatistics();

      expect(await screen.findByText("Total cost")).toBeInTheDocument();
    });

    it("falls back to the default widgets for a preference of the wrong shape", async () => {
      window.localStorage.setItem(WIDGET_PREFERENCE_KEY, JSON.stringify({ not: "an array" }));

      renderStatistics();

      expect(await screen.findByText("Total cost")).toBeInTheDocument();
    });
  });

  describe("a period with nothing in it", () => {
    it("says so rather than showing a grid of zeroes", async () => {
      renderStatistics({
        stats: statistics({
          total_prints: 0,
          total_cost: null,
          total_filament_g: null,
          top_collections: [],
          top_filaments: [],
          top_models: [],
          top_printers: [],
          cost_over_time: [],
        }),
      });

      // One sentence in place of every widget: a grid of zeroes and empty charts
      // reads as a bug rather than as an honest answer.
      expect(await screen.findByText("No completed prints in this period.")).toBeInTheDocument();
    });
  });
});
