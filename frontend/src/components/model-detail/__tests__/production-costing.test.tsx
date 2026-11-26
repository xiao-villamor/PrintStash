/** A conflicting edit leaves the entered costing inputs available for review. */
import "@testing-library/jest-dom/vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { ProductionCosting } from "@/components/model-detail/production-costing";
import {
  aMetadata,
  aModelPrintJob,
  anEditingBase,
  aPrinter,
  aRevision,
} from "@/test-support/factories";
import { json, renderApp } from "@/test-support/render";
import type { ModelPrintJobRead, PrinterRead } from "@/types";
import type { ArtifactCostingRead } from "@/types/production-costing";

describe("ProductionCosting", () => {
  it("labels the empty cost estimate consistently", () => {
    renderApp(
      <ProductionCosting
        modelId={1}
        files={[]}
        initialFileId={null}
        jobs={[]}
        canEdit
        admin={false}
      />,
    );

    expect(screen.getByRole("heading", { name: "Cost estimate" })).toBeVisible();
    expect(
      screen.getByText("Add an STL or G-code file to estimate production costs."),
    ).toBeVisible();
  });

  it("renders an empty production breakdown for a zero-cost estimate", async () => {
    const receipt: ArtifactCostingRead = {
      ...anEditingBase(),
      currency: "USD",
      pricing_defaults: { margin_percent: 0, tax_percent: 0 },
      estimate: {
        currency: "USD",
        pieces: 1,
        print_minutes: 20,
        materials: [{ name: "PLA", grams: 20, cost_per_kg: 0, spool_id: null }],
        printer: null,
        pricing: { margin_percent: 0, tax_percent: 0 },
      },
      costs: {
        material: 0,
        amortization: 0,
        maintenance: 0,
        electricity: 0,
        extra: 0,
        reprints: 0,
        production: 0,
        price_before_tax: 0,
        profit: 0,
        tax: 0,
        final_price: 0,
      },
    };
    renderApp(
      <ProductionCosting
        modelId={1}
        files={[aRevision()]}
        initialFileId={1}
        jobs={[]}
        canEdit={false}
        admin={false}
      />,
      { routes: { "GET /api/v1/files/1/production-cost": json(receipt) } },
    );

    const summary = within(await screen.findByLabelText("Cost per piece"));
    expect(summary.getByText("Final price")).toBeVisible();
    expect(summary.queryByText(/NaN/)).not.toBeInTheDocument();
  });

  it("offers the estimate form at once without saving anything", async () => {
    const receipt: ArtifactCostingRead = {
      ...anEditingBase(),
      currency: "USD",
      pricing_defaults: { margin_percent: 18.5, tax_percent: 7.25 },
      estimate: null,
      costs: null,
    };
    const view = renderApp(
      <ProductionCosting
        modelId={1}
        files={[aRevision()]}
        initialFileId={1}
        jobs={[]}
        canEdit
        admin={false}
      />,
      { routes: { "GET /api/v1/files/1/production-cost": json(receipt) } },
    );

    const form = within(await screen.findByRole("form", { name: "Production estimate" }));
    expect(form.getByRole("button", { name: "Save estimate" })).toBeVisible();
    expect(form.getByRole("link", { name: "Manage profiles" })).toHaveAttribute(
      "href",
      "/profiles?tab=printers",
    );
    expect(form.queryByRole("button", { name: "Save pricing template" })).toBeNull();
    expect(form.getByLabelText("Estimated time per copy")).not.toBeVisible();
    expect(form.queryByLabelText("Tax (%)")).not.toBeInTheDocument();
    expect(form.queryByLabelText("Profit margin (%)")).not.toBeInTheDocument();
    expect(form.queryByRole("combobox", { name: "Pricing template" })).not.toBeInTheDocument();
    expect(screen.getByText("No production estimate saved for this artifact.")).toBeVisible();
    expect(view.requestsWithMethod("PUT")).toHaveLength(0);
  });

  it("uses vault pricing for an unsaved estimate", async () => {
    renderApp(
      <ProductionCosting
        modelId={1}
        files={[
          aRevision({ metadata: aMetadata({ filament_weight_g: 100, estimated_time_s: 3600 }) }),
        ]}
        initialFileId={1}
        jobs={[]}
        canEdit
        admin={false}
      />,
      {
        routes: {
          "GET /api/v1/files/1/production-cost": json({
            ...anEditingBase(),
            currency: "USD",
            pricing_defaults: { margin_percent: 20, tax_percent: 10 },
            estimate: null,
            costs: null,
            slicer_filament_cost: 2,
          }),
        },
      },
    );

    const summary = within(await screen.findByLabelText("Cost per piece"));
    expect(summary.getByText("$2.75")).toBeVisible();
    expect(summary.getByText("Tax (10%)")).toBeVisible();
    expect(summary.getByText("$2.00 to make · $0.50 profit per piece")).toBeVisible();
  });

  it("lets an editor apply an updated printer failure rate to a saved estimate", async () => {
    const user = userEvent.setup();
    const expenses = {
      acquisition_cost: 1000,
      lifetime_hours: 1000,
      lifetime_maintenance: 0,
      power_watts: 0,
      electricity_per_kwh: 0,
      extra_per_hour: 0,
      failure_rate_percent: 0,
    };
    const receipt: ArtifactCostingRead = {
      ...anEditingBase(),
      currency: "USD",
      pricing_defaults: { margin_percent: 35, tax_percent: 30 },
      estimate: {
        currency: "USD",
        pieces: 2,
        print_minutes: 120,
        materials: [{ name: "PLA", grams: 200, cost_per_kg: 40, spool_id: null }],
        printer: { name: "Workshop", expenses },
        pricing: { margin_percent: 20, tax_percent: 10 },
      },
      costs: {
        material: 4,
        amortization: 1,
        maintenance: 0,
        electricity: 0,
        extra: 0,
        reprints: 0,
        production: 5,
        price_before_tax: 6.25,
        profit: 1.25,
        tax: 0.625,
        final_price: 6.875,
      },
    };
    const view = renderApp(
      <ProductionCosting
        modelId={1}
        files={[aRevision()]}
        initialFileId={1}
        jobs={[]}
        canEdit
        admin={false}
      />,
      {
        routes: {
          "GET /api/v1/files/1/production-cost": json(receipt),
          "GET /api/v1/printer-profiles": json([
            {
              id: 1,
              name: "Workshop",
              expenses: { ...expenses, failure_rate_percent: 20 },
            },
          ]),
        },
      },
    );

    const apply = await screen.findByRole("button", { name: "Apply current printer costs" });
    const summary = within(screen.getByLabelText("Cost per piece"));
    expect(summary.getByText("Tax (10%)")).toBeVisible();
    expect(summary.queryByText("Expected reprints")).toBeNull();
    await user.click(apply);

    expect(summary.getByText("Expected reprints")).toBeVisible();
    expect(summary.getByText("$6.25 to make · $1.56 profit per piece")).toBeVisible();
    expect(summary.getByText("$0.78")).toBeVisible();
    expect(summary.getByText("Tax (10%)")).toBeVisible();
    expect(summary.getByText("$8.59")).toBeVisible();
    expect(apply).not.toBeInTheDocument();
    expect(view.requestsWithMethod("PUT")).toHaveLength(0);
  });

  it.each([
    {
      name: "uses a unique preset ahead of the embedded rate",
      profiles: [{ id: 1, name: "Generic PLA", cost_per_kg: 20 }],
      embedded: 1,
      rate: 20,
      total: "$3.00",
    },
    {
      name: "falls back to the embedded rate when presets are ambiguous",
      profiles: [
        { id: 1, name: "Generic PLA", cost_per_kg: 20 },
        { id: 2, name: "generic pla", cost_per_kg: 30 },
      ],
      embedded: 1,
      rate: 10,
      total: "$2.00",
    },
    {
      name: "requires a price when no rate is available",
      profiles: [],
      embedded: null,
      rate: null,
      total: null,
    },
    {
      name: "uses the embedded total for a multi-filament print",
      profiles: [{ id: 1, name: "Generic PLA", cost_per_kg: 20 }],
      embedded: 1,
      multipleFilaments: true,
      rate: 10,
      total: "$2.00",
    },
  ])("$name", async ({ profiles, embedded, multipleFilaments, rate, total }) => {
    renderApp(
      <ProductionCosting
        modelId={1}
        files={[
          aRevision({
            metadata: aMetadata({
              estimated_time_s: 3600,
              filament_weight_g: 100,
              material_type: "PLA",
              material_brand: "Generic",
              printer_model: "Workshop",
            }),
          }),
        ]}
        initialFileId={1}
        jobs={[]}
        canEdit
        admin={false}
      />,
      {
        routes: {
          "GET /api/v1/files/1/production-cost": json({
            ...anEditingBase(),
            estimate: null,
            costs: null,
            currency: "USD",
            slicer_filament_cost: embedded,
            multiple_filaments: multipleFilaments ?? false,
          }),
          "GET /api/v1/filament-profiles": json(profiles),
          "GET /api/v1/printer-profiles": json([
            {
              id: 5,
              name: "Workshop",
              expenses: {
                acquisition_cost: 100,
                lifetime_hours: 100,
                lifetime_maintenance: 0,
                power_watts: 0,
                electricity_per_kwh: 0,
                extra_per_hour: 0,
                failure_rate_percent: 0,
              },
            },
          ]),
          "GET /api/v1/pricing-templates": json([]),
        },
      },
    );

    const form = within(await screen.findByRole("form", { name: "Production estimate" }));
    await waitFor(() => expect(form.getByLabelText("Printer or cost profile")).toHaveValue("5"));
    const price = form.getByLabelText("Cost per kg (USD)");
    await waitFor(() => expect(price).toHaveValue(rate));
    const save = form.getByRole("button", { name: "Save estimate" });
    expect(save).toHaveProperty("disabled", total === null);
    if (total === null) return;
    const finalPrice = () =>
      within(
        within(screen.getByLabelText("Cost per piece")).getByText("Final price").parentElement!,
      );
    expect(finalPrice().getByText(total)).toBeVisible();
    const user = userEvent.setup();
    await user.clear(price);
    expect(save).toBeDisabled();
    await user.type(price, "40");
    expect(finalPrice().getByText("$5.00")).toBeVisible();
    await user.selectOptions(form.getByLabelText("Printer or cost profile"), "");
    expect(finalPrice().getByText("$4.00")).toBeVisible();
  });

  describe("printer assignment", () => {
    const previousPrinter = aPrinter({
      id: 12,
      name: "Lab printer",
      model_name: "MK4",
      cost_profile_id: 5,
    });
    const otherPrinter = aPrinter({
      id: 13,
      name: "Spare printer",
      model_name: "MK4",
      cost_profile_id: 5,
    });
    const unlinked = aPrinter({ id: 14, name: "Unlinked printer", model_name: "MK4" });
    const expenses = {
      acquisition_cost: 100,
      lifetime_hours: 100,
      lifetime_maintenance: 0,
      power_watts: 0,
      electricity_per_kwh: 0,
      extra_per_hour: 0,
      failure_rate_percent: 0,
    };

    function openEstimate(printers: PrinterRead[], jobs: ModelPrintJobRead[] = []) {
      renderApp(
        <ProductionCosting
          modelId={1}
          files={[aRevision({ id: 2, metadata: aMetadata({ printer_model: "MK4" }) })]}
          initialFileId={2}
          jobs={jobs}
          canEdit
          admin
        />,
        {
          routes: {
            "GET /api/v1/files/2/production-cost": json({
              ...anEditingBase(),
              currency: "USD",
              estimate: null,
              costs: null,
            }),
            "GET /api/v1/printers": json(printers),
            "GET /api/v1/printer-profiles": json([
              { id: 5, name: "Workshop", printer_model: "MK4", expenses },
            ]),
            "GET /api/v1/filament-profiles": json([]),
            "GET /api/v1/pricing-templates": json([]),
          },
        },
      );
    }

    it("prefers the linked printer recorded for the current revision", async () => {
      openEstimate(
        [previousPrinter, otherPrinter],
        [
          aModelPrintJob({ file_id: 1, state: "completed", printer_id: otherPrinter.id }),
          aModelPrintJob({ file_id: 2, state: "completed", printer_id: previousPrinter.id }),
        ],
      );
      const field = await screen.findByRole("combobox", { name: "Printer or cost profile" });
      await waitFor(() => expect(field).toHaveValue("printer-12"));
    });

    it("selects the unique linked printer matching the G-code", async () => {
      openEstimate([previousPrinter, unlinked]);
      const field = await screen.findByRole("combobox", { name: "Printer or cost profile" });
      await waitFor(() => expect(field).toHaveValue("printer-12"));
      expect(within(field).queryByRole("option", { name: "Unlinked printer" })).toBeNull();
    });

    it("keeps a recorded unlinked printer distinct from another machine of the same model", async () => {
      openEstimate(
        [previousPrinter, unlinked],
        [aModelPrintJob({ file_id: 2, state: "completed", printer_id: unlinked.id })],
      );
      const field = await screen.findByRole("combobox", { name: "Printer or cost profile" });
      await within(field).findByRole("option", { name: "Lab printer" });
      expect(field).toHaveValue("5");
    });

    it("asks for a choice when linked printers match the same model", async () => {
      openEstimate([
        previousPrinter,
        otherPrinter,
        aPrinter({ id: 15, name: "No usable profile", model_name: "MK4", cost_profile_id: 6 }),
      ]);
      const field = await screen.findByRole("combobox", { name: "Printer or cost profile" });
      await within(field).findByRole("option", { name: "Spare printer" });
      expect(field).toHaveValue("");
      expect(within(field).queryByRole("option", { name: "No usable profile" })).toBeNull();
      await userEvent.setup().selectOptions(field, "printer-13");
      expect(field).toHaveValue("printer-13");
      expect(screen.getByText("Machine costs from Workshop.")).toBeVisible();
    });
  });

  it("keeps manual inputs when returning to the cost sources", async () => {
    renderApp(
      <ProductionCosting
        modelId={1}
        files={[aRevision({ id: 2 })]}
        initialFileId={2}
        jobs={[]}
        canEdit
        admin={false}
      />,
      {
        routes: {
          "GET /api/v1/files/2/production-cost": json({
            ...anEditingBase(),
            currency: "USD",
            estimate: null,
            costs: null,
          }),
          "GET /api/v1/printer-profiles": json([]),
          "GET /api/v1/filament-profiles": json([]),
          "GET /api/v1/pricing-templates": json([]),
        },
      },
    );
    const form = within(await screen.findByRole("form", { name: "Production estimate" }));
    const user = userEvent.setup();
    expect(form.queryByRole("combobox", { name: "Pricing template" })).toBeNull();
    expect(form.getByRole("combobox", { name: "Printer or cost profile" })).toBeDisabled();
    await user.click(form.getByRole("button", { name: "Edit inputs" }));
    const pieces = form.getByRole("spinbutton", { name: "Pieces per print" });
    await user.clear(pieces);
    await user.type(pieces, "5");
    expect(form.queryByRole("combobox", { name: "Printer or cost profile" })).toBeNull();
    await user.click(form.getByRole("button", { name: "Cost sources" }));
    await user.click(form.getByRole("button", { name: "Edit inputs" }));
    expect(form.getByRole("spinbutton", { name: "Pieces per print" })).toHaveValue(5);
  });

  it("keeps the draft after a concurrent estimate edit", async () => {
    const url = "/api/v1/files/2/production-cost";
    const base: ArtifactCostingRead = {
      ...anEditingBase(),
      currency: "USD",
      pricing_defaults: { margin_percent: 0, tax_percent: 0 },
      estimate: null,
      costs: null,
    };
    const app = renderApp(
      <ProductionCosting
        modelId={1}
        files={[aRevision({ id: 2 })]}
        initialFileId={2}
        jobs={[]}
        canEdit
        admin={false}
      />,
      {
        routes: {
          [`GET ${url}`]: json(base),
          [`PUT ${url}`]: json({ detail: "edit_conflict" }, 412),
          "GET /api/v1/filament-profiles": json([]),
          "GET /api/v1/printer-profiles": json([]),
          "GET /api/v1/pricing-templates": json([]),
        },
      },
    );

    const user = userEvent.setup();
    const form = within(await screen.findByRole("form", { name: "Production estimate" }));
    await user.click(form.getByRole("button", { name: "Edit inputs" }));
    await user.type(form.getByLabelText("Estimated time per copy"), "30");
    await user.type(form.getByLabelText("Material name"), "PETG");
    await user.type(form.getByLabelText("Filament per copy"), "10");
    await user.type(form.getByLabelText("Cost per kg (USD)"), "20");
    const save = form.getByRole("button", { name: "Save estimate" });
    await user.click(save);

    const review = await screen.findByRole("button", { name: "Review current estimate" });
    expect(form.getByLabelText("Material name")).toHaveValue("PETG");
    expect(save).toBeDisabled();

    app.route({ [`GET ${url}`]: json({ ...base, edit_version: base.edit_version + 1 }) });
    await user.click(review);
    expect(form.getByLabelText("Material name")).toHaveValue("PETG");
    await waitFor(() => expect(save).toBeEnabled());
  });
});
