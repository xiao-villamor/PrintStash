import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { FormField } from "@/components/ui/form-field";
import { Input, inputClasses } from "@/components/ui/input";
import { pricingTemplatesOptions } from "@/features/library/production-costing";
import { pricingPageSize } from "@/lib/api/production-costing";
import { uiText } from "@/lib/locale";
import type { PricingRates, PricingTemplate } from "@/types/production-costing";

export function PricingTemplateControls({
  rates,
  admin,
  pending,
  onApply,
  onSave,
  onDelete,
}: {
  rates: PricingRates | null;
  admin: boolean;
  pending: boolean;
  onApply: (rates: PricingRates) => void;
  onSave: (data: Omit<PricingTemplate, "id">) => Promise<boolean>;
  onDelete: (id: string) => Promise<boolean>;
}) {
  const [offset, setOffset] = useState(0);
  const [selected, select] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [confirmDelete, setConfirmDelete] = useState<PricingTemplate | null>(null);
  const templates = useQuery(pricingTemplatesOptions(offset));
  const rows = templates.data?.slice(0, pricingPageSize) ?? [];
  const template = rows.find((row) => row.id === selected);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-2">
        <FormField className="min-w-48 flex-1" label={uiText("Pricing template")}>
          <select
            className={inputClasses}
            value={template?.id ?? ""}
            disabled={templates.isPending || templates.isError}
            onChange={(event) => select(event.target.value)}
          >
            <option value="">{uiText("Select a template")}</option>
            {rows.map((row) => (
              <option key={row.id} value={row.id}>
                {row.name}
              </option>
            ))}
          </select>
        </FormField>
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={!template}
          onClick={() => {
            if (template) onApply(template);
          }}
        >
          {uiText("Apply template")}
        </Button>
        {admin && template && (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={() => setConfirmDelete(template)}
          >
            {uiText("Delete template")}
          </Button>
        )}
      </div>
      {(offset > 0 || (templates.data?.length ?? 0) > pricingPageSize) && (
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            variant="outline"
            size="xs"
            disabled={offset === 0}
            onClick={() => setOffset(offset - pricingPageSize)}
          >
            {uiText("Previous")}
          </Button>
          <Button
            type="button"
            variant="outline"
            size="xs"
            disabled={(templates.data?.length ?? 0) <= pricingPageSize}
            onClick={() => setOffset(offset + pricingPageSize)}
          >
            {uiText("Next")}
          </Button>
        </div>
      )}
      {templates.isError && (
        <p role="alert" className="text-sm text-destructive">
          {uiText("Could not load pricing templates.")}
        </p>
      )}
      {admin && (
        <div className="flex flex-wrap items-end gap-2">
          <FormField className="min-w-48 flex-1" label={uiText("New template name")}>
            <Input value={name} maxLength={200} onChange={(event) => setName(event.target.value)} />
          </FormField>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={!rates || !name.trim() || pending}
            onClick={async () => {
              if (rates && (await onSave({ ...rates, name: name.trim() }))) setName("");
            }}
          >
            {uiText("Save pricing template")}
          </Button>
        </div>
      )}
      <ConfirmModal
        open={confirmDelete !== null}
        onClose={() => setConfirmDelete(null)}
        onConfirm={async () => {
          if (confirmDelete && (await onDelete(confirmDelete.id))) {
            select(null);
            setConfirmDelete(null);
          }
        }}
        busy={pending}
        title={uiText("Delete pricing template?")}
        description={uiText("Saved production estimates keep the rates they used.")}
        confirmLabel={uiText("Delete template")}
      />
    </div>
  );
}
