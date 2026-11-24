import { useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { FilamentSpool } from "@/components/ui/filament-spool";
import { FormField } from "@/components/ui/form-field";
import { Input, inputClasses } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { productionCostOptions, useProductionCostCommands } from "@/features/library/model-detail";
import { parseApiError, userMessage } from "@/lib/errors";
import { formatNumber } from "@/lib/format";
import { uiText } from "@/lib/locale";
import { useSpoolmanStatus, useSpools } from "@/lib/queries";
import { filamentProfilesOptions, printerProfilesOptions } from "@/lib/queries/profiles";
import { getSessionVersion } from "@/lib/session-transport";
import { toast } from "@/lib/toast";
import type { FileRead, ModelPrintJobRead } from "@/types";
import type {
  ArtifactCostingRead,
  CostedMaterial,
  PieceCosts,
  PricingRates,
  ProductionInputs,
} from "@/types/production-costing";
import { PricingTemplateControls } from "./pricing-template-controls";

type MaterialDraft = {
  key: number;
  name: string;
  grams: string;
  rate: string;
  spoolId: number | null;
};
type Draft = {
  pieces: string;
  minutes: string;
  materials: MaterialDraft[];
  printer: ProductionInputs["printer"];
  margin: string;
  tax: string;
};

function blankMaterial(key: number): MaterialDraft {
  return { key, name: "", grams: "", rate: "", spoolId: null };
}

function makeDraft(file: FileRead, row: ArtifactCostingRead): Draft {
  const saved = row.estimate;
  if (saved) {
    return {
      pieces: String(saved.pieces),
      minutes: String(saved.print_minutes),
      materials: saved.materials.map((line, key) => ({
        key,
        name: line.name,
        grams: String(line.grams),
        rate: String(line.cost_per_kg),
        spoolId: line.spool_id,
      })),
      printer: saved.printer,
      margin: String(saved.pricing.margin_percent),
      tax: String(saved.pricing.tax_percent),
    };
  }
  const metadata = file.metadata;
  const grams = metadata?.filament_weight_g;
  const time = metadata?.estimated_time_s;
  return {
    pieces: "1",
    minutes: time == null ? "" : String(time / 60),
    materials: [
      {
        ...blankMaterial(0),
        name: [metadata?.material_brand, metadata?.material_type].filter(Boolean).join(" "),
        grams: grams == null ? "" : String(grams),
      },
    ],
    printer: null,
    margin: "0",
    tax: "0",
  };
}

function numberInRange(value: string, minimum: number, maximum: number) {
  if (!value.trim()) return null;
  const number = Number(value);
  return Number.isFinite(number) && number >= minimum && number <= maximum ? number : null;
}

function toRates(draft: Draft): PricingRates | null {
  const margin = numberInRange(draft.margin, 0, 99.99);
  const tax = numberInRange(draft.tax, 0, 100);
  return margin === null || tax === null ? null : { margin_percent: margin, tax_percent: tax };
}

function toEstimate(draft: Draft, rates: PricingRates | null): ProductionInputs | null {
  const pieces = numberInRange(draft.pieces, 1, 100000);
  const minutes = numberInRange(draft.minutes, 0, 1000000);
  if (pieces === null || !Number.isInteger(pieces) || minutes === null || !rates) return null;
  const materials: CostedMaterial[] = [];
  for (const line of draft.materials) {
    const grams = numberInRange(line.grams, 0, 1000000);
    const rate = numberInRange(line.rate, 0, 1000000);
    if (!line.name.trim() || grams === null || rate === null) return null;
    materials.push({ name: line.name.trim(), grams, cost_per_kg: rate, spool_id: line.spoolId });
  }
  return { pieces, print_minutes: minutes, materials, printer: draft.printer, pricing: rates };
}

const costComponents = [
  ["material", "Filament"],
  ["amortization", "Amortization"],
  ["maintenance", "Maintenance"],
  ["electricity", "Electricity"],
  ["extra", "Additional hourly costs"],
] as const satisfies readonly [keyof PieceCosts, string][];

function CostBreakdown({ row }: { row: ArtifactCostingRead }) {
  if (!row.estimate || !row.costs) return null;
  const { costs, estimate } = row;
  const currency = estimate.currency;
  const amount = (value: number) =>
    formatNumber(value, { minimumFractionDigits: 2, maximumFractionDigits: 4 });
  const componentTotal = costComponents.reduce((total, [key]) => total + costs[key], 0);
  return (
    <section className="min-w-0 border bg-card" aria-label={uiText("Cost per piece")}>
      <div className="space-y-3 border-b bg-muted/30 p-4">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h3 className="text-sm font-semibold">{uiText("Cost per piece")}</h3>
          <span className="font-mono text-xs text-muted-foreground">{currency}</span>
        </div>
        <dl className="grid grid-cols-2 gap-3">
          <div className="min-w-0">
            <dt className="text-xs text-muted-foreground">{uiText("Final price")}</dt>
            <dd className="break-words text-2xl font-semibold tabular-nums">
              {amount(costs.final_price)}
            </dd>
          </div>
          <div className="min-w-0 border-s ps-3">
            <dt className="text-xs text-muted-foreground">{uiText("Production cost")}</dt>
            <dd className="break-words text-2xl font-semibold tabular-nums">
              {amount(costs.production)}
            </dd>
          </div>
        </dl>
        <p className="break-words text-xs text-muted-foreground">
          {uiText("Pieces per print")}: {estimate.pieces} · {uiText("Print time (minutes)")}:{" "}
          {formatNumber(estimate.print_minutes, { maximumFractionDigits: 2 })}
          {estimate.printer && ` · ${estimate.printer.name}`}
        </p>
      </div>
      <div className="space-y-3 p-4">
        <h4 className="text-xs font-semibold">{uiText("Production cost breakdown")}</h4>
        <dl className="grid grid-cols-[minmax(0,1fr)_minmax(2rem,1fr)_auto] items-center gap-x-3 gap-y-2 text-xs">
          {costComponents.map(([key, label]) => (
            <div key={key} className="col-span-3 grid grid-cols-subgrid items-center">
              <dt className="break-words">{uiText(label)}</dt>
              <dd className="col-span-2 grid grid-cols-subgrid items-center">
                <span aria-hidden="true" className="h-1.5 overflow-hidden bg-muted">
                  <span
                    className="block h-full origin-left bg-chart-blue rtl:origin-right"
                    style={{
                      transform: `scaleX(${componentTotal === 0 ? 0 : costs[key] / componentTotal})`,
                    }}
                  />
                </span>
                <span className="text-end font-mono tabular-nums">{amount(costs[key])}</span>
              </dd>
            </div>
          ))}
        </dl>
        {!estimate.printer && (
          <p className="text-xs text-muted-foreground">
            {uiText("Machine expenses are excluded.")}
          </p>
        )}
        <dl className="grid grid-cols-[1fr_auto] gap-2 border-t pt-3 text-xs">
          <dt className="text-muted-foreground">{uiText("Price before tax")}</dt>
          <dd className="text-end font-mono tabular-nums">{amount(costs.price_before_tax)}</dd>
          <dt className="text-muted-foreground">{uiText("Tax")}</dt>
          <dd className="text-end font-mono tabular-nums">{amount(costs.tax)}</dd>
        </dl>
      </div>
    </section>
  );
}

export function ProductionCosting({
  modelId,
  files,
  initialFileId,
  jobs,
  canEdit,
  admin,
}: {
  modelId: number;
  files: FileRead[];
  initialFileId: number | null;
  jobs: ModelPrintJobRead[];
  canEdit: boolean;
  admin: boolean;
}) {
  const [selected, setSelected] = useState(initialFileId);
  const file = files.find((item) => item.id === selected) ?? files[0];

  if (!file)
    return (
      <p className="text-sm text-muted-foreground">
        {uiText("Add an STL or G-code file to estimate production costs.")}
      </p>
    );
  return (
    <div className="space-y-4">
      <FormField label={uiText("Artifact")}>
        <select
          className={inputClasses}
          value={file.id}
          onChange={(event) => setSelected(Number(event.target.value))}
        >
          {files.map((item) => (
            <option key={item.id} value={item.id}>
              {item.original_filename}
            </option>
          ))}
        </select>
      </FormField>
      <ArtifactEstimate
        key={file.id}
        modelId={modelId}
        file={file}
        jobs={jobs}
        canEdit={canEdit}
        admin={admin}
      />
    </div>
  );
}

function ArtifactEstimate({
  modelId,
  file,
  jobs,
  canEdit,
  admin,
}: {
  modelId: number;
  file: FileRead;
  jobs: ModelPrintJobRead[];
  canEdit: boolean;
  admin: boolean;
}) {
  const query = useQuery(productionCostOptions(modelId, file.id));
  const commands = useProductionCostCommands(modelId, file.id);
  const [editing, setEditing] = useState<ArtifactCostingRead | null>(null);
  const [clearBase, setClearBase] = useState<ArtifactCostingRead | null>(null);
  const [reviewRequired, setReviewRequired] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function persist(estimate: ProductionInputs | null, base: ArtifactCostingRead) {
    setMessage(null);
    try {
      await commands.save.mutateAsync({ estimate, base, session: getSessionVersion() });
      setEditing(null);
      setClearBase(null);
      toast.success(
        uiText(estimate ? "Production estimate saved." : "Production estimate cleared."),
      );
    } catch (error) {
      const parsed = parseApiError(error);
      setClearBase(null);
      setReviewRequired(
        parsed.status === 412 ||
          parsed.status === 428 ||
          parsed.status === 0 ||
          parsed.status >= 500,
      );
      setMessage(userMessage(error));
    }
  }

  if (query.isError && !editing)
    return (
      <p role="alert" className="text-sm text-destructive">
        {userMessage(query.error)}
      </p>
    );
  const row = query.data;
  if (!row && !editing) return <Skeleton className="h-40" />;

  return (
    <div className="space-y-4">
      {row && <CostBreakdown row={row} />}
      {!row?.estimate && !editing && (
        <p className="text-sm text-muted-foreground">
          {uiText("No production estimate saved for this artifact.")}
        </p>
      )}
      {message && (
        <p role="alert" className="text-sm text-destructive">
          {message}
        </p>
      )}
      {reviewRequired && (
        <div role="status" className="space-y-2 border p-3 text-sm">
          <p>
            {uiText("Review the current estimate before saving again. Your draft is preserved.")}
          </p>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={query.isFetching}
            onClick={async () => {
              const current = await query.refetch();
              if (current.isSuccess) {
                setEditing((base) => (base ? current.data : null));
                setMessage(null);
                setReviewRequired(false);
              }
            }}
          >
            {uiText("Review current estimate")}
          </Button>
        </div>
      )}
      {canEdit && editing && (
        <EstimateForm
          file={file}
          row={editing}
          jobs={jobs}
          admin={admin}
          blocked={reviewRequired}
          commands={commands}
          onSave={(estimate) => void persist(estimate, editing)}
          onCancel={() => {
            setEditing(null);
            setMessage(null);
            setReviewRequired(false);
          }}
        />
      )}
      {canEdit && row && !editing && (
        <div className="flex flex-wrap gap-2">
          <Button type="button" disabled={reviewRequired} onClick={() => setEditing(row)}>
            {uiText(row.estimate ? "Edit estimate" : "Create estimate")}
          </Button>
          {row.estimate && (
            <Button
              type="button"
              variant="ghost"
              disabled={reviewRequired}
              onClick={() => setClearBase(row)}
            >
              {uiText("Clear estimate")}
            </Button>
          )}
        </div>
      )}
      <ConfirmModal
        open={canEdit && clearBase !== null}
        onClose={() => setClearBase(null)}
        onConfirm={() => {
          if (clearBase) void persist(null, clearBase);
        }}
        busy={commands.save.isPending}
        title={uiText("Clear production estimate?")}
        description={uiText("This removes the saved costing inputs for this artifact.")}
        confirmLabel={uiText("Clear estimate")}
      />
    </div>
  );
}

function EstimateForm({
  file,
  row,
  jobs,
  admin,
  blocked,
  commands,
  onSave,
  onCancel,
}: {
  file: FileRead;
  row: ArtifactCostingRead;
  jobs: ModelPrintJobRead[];
  admin: boolean;
  blocked: boolean;
  commands: ReturnType<typeof useProductionCostCommands>;
  onSave: (estimate: ProductionInputs) => void;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState(() => makeDraft(file, row));
  const [nextKey, setNextKey] = useState(draft.materials.length);
  const currency = row.estimate?.currency ?? row.currency;
  const sameCurrency = currency === row.currency;
  const filamentProfiles = useQuery(filamentProfilesOptions()).data ?? [];
  const printerProfiles = useQuery(printerProfilesOptions()).data ?? [];
  const spoolman = useSpoolmanStatus({ enabled: admin });
  const spools = useSpools({ enabled: admin && spoolman.data?.enabled === true }).data ?? [];
  const measured = jobs.find(
    (job) => job.file_id === file.id && job.state === "completed" && job.actual_duration_s != null,
  );
  const duration = measured?.actual_duration_s;
  const rates = toRates(draft);
  const estimate = toEstimate(draft, rates);

  function updateMaterial(key: number, patch: Partial<MaterialDraft>) {
    setDraft((old) => ({
      ...old,
      materials: old.materials.map((line) => (line.key === key ? { ...line, ...patch } : line)),
    }));
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (estimate && !blocked && !commands.save.isPending) onSave(estimate);
  }

  return (
    <form aria-label={uiText("Production estimate")} className="space-y-4" onSubmit={submit}>
      <fieldset disabled={commands.save.isPending} className="space-y-4">
        <div className="grid grid-cols-2 gap-3">
          <FormField label={uiText("Pieces per print")}>
            <Input
              type="number"
              min={1}
              max={100000}
              value={draft.pieces}
              required
              onChange={(event) => setDraft({ ...draft, pieces: event.target.value })}
            />
          </FormField>
          <FormField label={uiText("Print time (minutes)")}>
            <Input
              type="number"
              min={0}
              max={1000000}
              step="any"
              value={draft.minutes}
              required
              onChange={(event) => setDraft({ ...draft, minutes: event.target.value })}
            />
          </FormField>
          {measured && duration != null && (
            <div className="col-span-2">
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => {
                  setDraft((old) => ({
                    ...old,
                    minutes: String(duration / 60),
                    materials:
                      old.materials.length === 1 && measured.filament_used_g != null
                        ? old.materials.map((line) => ({
                            ...line,
                            grams: String(measured.filament_used_g),
                          }))
                        : old.materials,
                  }));
                }}
              >
                {uiText("Use latest print measurements")} · {measured.printer_name}
              </Button>
            </div>
          )}
        </div>

        <section className="space-y-3 border-t pt-3">
          <h3 className="flex items-center gap-2 text-sm font-semibold">
            <FilamentSpool className="size-4 shrink-0 text-muted-foreground" aria-hidden />
            {uiText("Filament per print")}
          </h3>
          {!sameCurrency && (
            <p className="text-xs text-muted-foreground">
              {uiText("This estimate uses a different currency from the current vault presets.")}
            </p>
          )}
          {draft.materials.map((line) => (
            <fieldset key={line.key} className="space-y-3 border p-3">
              {sameCurrency && (filamentProfiles.length > 0 || spools.length > 0) && (
                <FormField label={uiText("Fill from filament preset or spool")}>
                  <select
                    className={inputClasses}
                    value=""
                    onChange={(event) => {
                      const option = event.target.value;
                      const spool = spools.find((item) => option === `spool-${item.id}`);
                      const profile = filamentProfiles.find(
                        (item) => option === `preset-${item.id}`,
                      );
                      if (spool) {
                        updateMaterial(line.key, {
                          name:
                            [spool.vendor_name, spool.filament_name ?? spool.name, spool.material]
                              .filter(Boolean)
                              .join(" ") || uiText("Filament"),
                          rate: spool.cost_per_kg == null ? "" : String(spool.cost_per_kg),
                          spoolId: spool.id,
                        });
                      } else if (profile) {
                        updateMaterial(line.key, {
                          name: [profile.name, profile.material_type].filter(Boolean).join(" · "),
                          rate: profile.cost_per_kg == null ? "" : String(profile.cost_per_kg),
                          spoolId: null,
                        });
                      }
                    }}
                  >
                    <option value="">{uiText("Select a preset or spool")}</option>
                    {filamentProfiles.map((item) => (
                      <option key={`preset-${item.id}`} value={`preset-${item.id}`}>
                        {item.name}
                      </option>
                    ))}
                    {spools.map((item) => (
                      <option key={`spool-${item.id}`} value={`spool-${item.id}`}>
                        #{item.id} ·{" "}
                        {[item.filament_name ?? item.name, item.material, item.location]
                          .filter(Boolean)
                          .join(" · ")}
                      </option>
                    ))}
                  </select>
                </FormField>
              )}
              {line.spoolId !== null && (
                <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
                  <span>{uiText("Spoolman #{id}", { id: line.spoolId })}</span>
                  <Button
                    type="button"
                    size="xs"
                    variant="ghost"
                    onClick={() => updateMaterial(line.key, { spoolId: null })}
                  >
                    {uiText("Clear spool association")}
                  </Button>
                </div>
              )}
              <div className="grid grid-cols-2 gap-3">
                <FormField className="col-span-2" label={uiText("Material name")}>
                  <Input
                    maxLength={200}
                    value={line.name}
                    required
                    onChange={(event) => updateMaterial(line.key, { name: event.target.value })}
                  />
                </FormField>
                <FormField label={uiText("Filament used (g)")}>
                  <Input
                    type="number"
                    min={0}
                    max={1000000}
                    step="any"
                    value={line.grams}
                    required
                    onChange={(event) => updateMaterial(line.key, { grams: event.target.value })}
                  />
                </FormField>
                <FormField label={`${uiText("Cost per kg")} (${currency})`}>
                  <Input
                    type="number"
                    min={0}
                    max={1000000}
                    step="any"
                    value={line.rate}
                    required
                    onChange={(event) => updateMaterial(line.key, { rate: event.target.value })}
                  />
                </FormField>
              </div>
              {draft.materials.length > 1 && (
                <Button
                  type="button"
                  size="xs"
                  variant="ghost"
                  onClick={() =>
                    setDraft({
                      ...draft,
                      materials: draft.materials.filter((item) => item.key !== line.key),
                    })
                  }
                >
                  {uiText("Remove material")}
                </Button>
              )}
            </fieldset>
          ))}
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={draft.materials.length >= 32}
            onClick={() => {
              setDraft({ ...draft, materials: [...draft.materials, blankMaterial(nextKey)] });
              setNextKey(nextKey + 1);
            }}
          >
            {uiText("Add material")}
          </Button>
        </section>

        <section className="space-y-2 border-t pt-3">
          <FormField label={uiText("Printer operating costs")}>
            <select
              className={inputClasses}
              value=""
              disabled={!sameCurrency}
              onChange={(event) => {
                const profile = printerProfiles.find(
                  (item) => item.id === Number(event.target.value),
                );
                if (profile?.expenses)
                  setDraft({
                    ...draft,
                    printer: { name: profile.name, expenses: profile.expenses },
                  });
              }}
            >
              <option value="">{uiText("Select a printer preset")}</option>
              {printerProfiles
                .filter((item) => item.expenses)
                .map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
            </select>
          </FormField>
          {draft.printer ? (
            <div className="flex items-center justify-between gap-2 text-sm">
              <p>{draft.printer.name}</p>
              <Button
                size="xs"
                variant="ghost"
                type="button"
                onClick={() => setDraft({ ...draft, printer: null })}
              >
                {uiText("Exclude machine expenses")}
              </Button>
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">
              {uiText("Set operating costs in Profiles → Printers to include machine expenses.")}
            </p>
          )}
        </section>

        <section className="space-y-3 border-t pt-3">
          <h3 className="text-sm font-semibold">{uiText("Pricing")}</h3>
          <div className="grid grid-cols-2 gap-3">
            <FormField
              label={uiText("Profit margin (%)")}
              hint={uiText("Profit as a share of the price before tax.")}
            >
              <Input
                type="number"
                min={0}
                max={99.99}
                step="any"
                value={draft.margin}
                required
                onChange={(event) => setDraft({ ...draft, margin: event.target.value })}
              />
            </FormField>
            <FormField label={uiText("Tax (%)")}>
              <Input
                type="number"
                min={0}
                max={100}
                step="any"
                value={draft.tax}
                required
                onChange={(event) => setDraft({ ...draft, tax: event.target.value })}
              />
            </FormField>
          </div>
          <PricingTemplateControls
            rates={rates}
            admin={admin}
            pending={commands.createTemplate.isPending || commands.deleteTemplate.isPending}
            onApply={(value) =>
              setDraft({
                ...draft,
                margin: String(value.margin_percent),
                tax: String(value.tax_percent),
              })
            }
            onSave={async (template) => {
              try {
                await commands.createTemplate.mutateAsync({
                  template,
                  session: getSessionVersion(),
                });
                toast.success(uiText("Pricing template saved."));
                return true;
              } catch (error) {
                toast.error(error);
                return false;
              }
            }}
            onDelete={async (id) => {
              try {
                await commands.deleteTemplate.mutateAsync({ id, session: getSessionVersion() });
                toast.success(uiText("Pricing template deleted."));
                return true;
              } catch (error) {
                toast.error(error);
                return false;
              }
            }}
          />
        </section>
      </fieldset>
      <div className="flex flex-wrap gap-2">
        <Button type="submit" loading={commands.save.isPending} disabled={!estimate || blocked}>
          {uiText("Save estimate")}
        </Button>
        <Button
          type="button"
          variant="outline"
          disabled={commands.save.isPending}
          onClick={onCancel}
        >
          {uiText("Cancel")}
        </Button>
      </div>
    </form>
  );
}
