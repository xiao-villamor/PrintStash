/** A conflicting edit leaves the entered costing inputs available for review. */
import "@testing-library/jest-dom/vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { ProductionCosting } from "@/components/model-detail/production-costing";
import { anEditingBase, aRevision } from "@/test-support/factories";
import { json, renderApp } from "@/test-support/render";
import type { ArtifactCostingRead } from "@/types/production-costing";

describe("ProductionCosting", () => {
  it("renders an empty production breakdown for a zero-cost estimate", async () => {
    const receipt: ArtifactCostingRead = {
      ...anEditingBase(),
      currency: "USD",
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
        production: 0,
        price_before_tax: 0,
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
    expect(screen.getByText("No production estimate saved for this artifact.")).toBeVisible();
    expect(view.requestsWithMethod("PUT")).toHaveLength(0);
  });

  it("keeps the draft after a concurrent estimate edit", async () => {
    const url = "/api/v1/files/2/production-cost";
    const base: ArtifactCostingRead = {
      ...anEditingBase(),
      currency: "USD",
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
