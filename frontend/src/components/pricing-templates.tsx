import { useId, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { FieldErrors } from "@/components/ui/field-errors";
import { FormField } from "@/components/ui/form-field";
import { Input, inputClasses } from "@/components/ui/input";
import {
  pricingTemplatesOptions,
  usePricingTemplateCommands,
} from "@/features/library/model-detail";
import { pricingPageSize } from "@/lib/api/production-costing";
import { formatPercent } from "@/lib/format";
import { useFormValidation } from "@/lib/form-validation";
import { uiText } from "@/lib/locale";
import { getSessionVersion } from "@/lib/session-transport";
import { toast } from "@/lib/toast";
import type { PricingRates, PricingTemplate } from "@/types/production-costing";

function usePricingTemplatePage() {
  const [offset, setOffset] = useState(0);
  const query = useQuery(pricingTemplatesOptions(offset));
  const rows = query.data?.slice(0, pricingPageSize) ?? [];
  const hasNext = (query.data?.length ?? 0) > pricingPageSize;
  const navigation = (offset > 0 || hasNext) && (
    <div className="flex flex-wrap gap-2">
      <Button
        type="button"
        size="xs"
        variant="outline"
        disabled={offset === 0}
        onClick={() => setOffset(offset - pricingPageSize)}
      >
        {uiText("Previous")}
      </Button>
      <Button
        type="button"
        size="xs"
        variant="outline"
        disabled={!hasNext}
        onClick={() => setOffset(offset + pricingPageSize)}
      >
        {uiText("Next")}
      </Button>
    </div>
  );
  return { query, rows, navigation, returnToFirstPage: () => setOffset(0) };
}

export function PricingTemplatePicker({
  rates,
  onApply,
}: {
  rates: PricingRates | null;
  onApply: (rates: PricingRates) => void;
}) {
  const id = useId();
  const [selected, select] = useState("");
  const { query, rows, navigation } = usePricingTemplatePage();

  if (query.isError) {
    return (
      <p role="alert" className="col-span-2 text-sm text-destructive">
        {uiText("Could not load pricing templates.")}
      </p>
    );
  }
  if (rows.length === 0 && !navigation) return null;
  const applied = rows.find(
    (row) =>
      row.id === selected &&
      row.margin_percent === rates?.margin_percent &&
      row.tax_percent === rates?.tax_percent,
  );

  return (
    <div className="col-span-2 space-y-2">
      <FormField label={uiText("Pricing template")} htmlFor={id}>
        <select
          id={id}
          className={inputClasses}
          value={applied?.id ?? ""}
          disabled={query.isPending || rows.length === 0}
          onChange={(event) => {
            const template = rows.find((row) => row.id === event.target.value);
            if (!template) return;
            select(template.id);
            onApply(template);
          }}
        >
          <option value="" disabled>
            {uiText("Select a template")}
          </option>
          {rows.map((row) => (
            <option key={row.id} value={row.id}>
              {row.name}
            </option>
          ))}
        </select>
      </FormField>
      {navigation}
    </div>
  );
}

export function PricingTemplatesPanel({ admin }: { admin: boolean }) {
  const { query, rows, navigation, returnToFirstPage } = usePricingTemplatePage();
  const { createTemplate, deleteTemplate } = usePricingTemplateCommands();
  const validation = useFormValidation();
  const [name, setName] = useState("");
  const [margin, setMargin] = useState("0");
  const [tax, setTax] = useState("0");
  const [deleteTarget, setDeleteTarget] = useState<PricingTemplate | null>(null);

  return (
    <Card className="max-w-3xl space-y-4 rounded-lg p-4">
      <div className="space-y-1">
        <h2 className="text-base font-semibold">{uiText("Pricing templates")}</h2>
        <p className="text-sm text-muted-foreground">
          {uiText("Save margin and tax rates to reuse in Model cost estimates.")}
        </p>
      </div>
      {admin && (
        <form
          {...validation.formProps}
          className="grid gap-3 sm:grid-cols-2"
          onSubmit={(event) =>
            validation.handle(event, async () => {
              event.preventDefault();
              if (!name.trim() || createTemplate.isPending) return;
              try {
                await createTemplate.mutateAsync({
                  template: {
                    name: name.trim(),
                    margin_percent: Number(margin),
                    tax_percent: Number(tax),
                  },
                  session: getSessionVersion(),
                });
                setName("");
                returnToFirstPage();
                toast.success(uiText("Pricing template saved."));
              } catch (error) {
                toast.error(error);
              }
            })
          }
        >
          <FieldErrors errors={validation.errors}>
            <FormField
              label={uiText("New template name")}
              htmlFor="pricing-template-name"
              className="sm:col-span-2"
            >
              <Input
                id="pricing-template-name"
                required
                maxLength={200}
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
            </FormField>
            <FormField label={uiText("Profit margin (%)")} htmlFor="pricing-template-margin">
              <Input
                id="pricing-template-margin"
                type="number"
                min={0}
                max={99.99}
                step="any"
                required
                value={margin}
                onChange={(event) => setMargin(event.target.value)}
              />
            </FormField>
            <FormField label={uiText("Tax (%)")} htmlFor="pricing-template-tax">
              <Input
                id="pricing-template-tax"
                type="number"
                min={0}
                max={100}
                step="any"
                required
                value={tax}
                onChange={(event) => setTax(event.target.value)}
              />
            </FormField>
            <Button
              type="submit"
              size="sm"
              className="justify-self-start"
              loading={createTemplate.isPending}
            >
              {uiText("Save pricing template")}
            </Button>
          </FieldErrors>
        </form>
      )}
      {query.isPending ? (
        <p role="status" className="text-sm text-muted-foreground">
          {uiText("Loading...")}
        </p>
      ) : query.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {uiText("Could not load pricing templates.")}
        </p>
      ) : rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">{uiText("No pricing templates saved.")}</p>
      ) : (
        <ul className="divide-y divide-border border-t border-border">
          {rows.map((template) => (
            <li
              key={template.id}
              className="flex flex-wrap items-center justify-between gap-2 py-3"
            >
              <div>
                <p className="text-sm font-medium">{template.name}</p>
                <p className="text-sm text-muted-foreground">
                  {uiText("Margin {value1} · Tax {value2}", {
                    value1: formatPercent(template.margin_percent),
                    value2: formatPercent(template.tax_percent),
                  })}
                </p>
              </div>
              {admin && (
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  aria-label={uiText("Delete {value1}", { value1: template.name })}
                  onClick={() => setDeleteTarget(template)}
                >
                  {uiText("Delete template")}
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
      {navigation}
      {deleteTarget && (
        <ConfirmModal
          open
          onClose={() => setDeleteTarget(null)}
          busy={deleteTemplate.isPending}
          title={uiText("Delete pricing template?")}
          description={deleteTarget.name}
          confirmLabel={uiText("Delete template")}
          onConfirm={async () => {
            try {
              await deleteTemplate.mutateAsync({
                id: deleteTarget.id,
                session: getSessionVersion(),
              });
              setDeleteTarget(null);
              if (rows.length === 1) returnToFirstPage();
              toast.success(uiText("Pricing template deleted."));
            } catch (error) {
              toast.error(error);
            }
          }}
        />
      )}
    </Card>
  );
}
