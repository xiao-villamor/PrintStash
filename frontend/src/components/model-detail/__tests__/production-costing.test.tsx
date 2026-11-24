/** A conflicting edit leaves the entered costing inputs available for review. */
import "@testing-library/jest-dom/vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";
import { ProductionCosting } from "@/components/model-detail/production-costing";
import { anEditingBase, aRevision } from "@/test-support/factories";
import { json, renderApp } from "@/test-support/render";
import type { ArtifactCostingRead } from "@/types/production-costing";

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
  await user.click(await screen.findByRole("button", { name: "Create estimate" }));
  const form = within(screen.getByRole("form", { name: "Production estimate" }));
  await user.type(form.getByLabelText("Print time (minutes)"), "30");
  await user.type(form.getByLabelText("Material name"), "PETG");
  await user.type(form.getByLabelText("Filament used (g)"), "10");
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
