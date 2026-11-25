import type { GcodePlateMetadata } from "@/lib/api/plates";
import { useGcodePlates } from "@/lib/queries/file-plates";
import { useState, type FormEvent, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { FormField } from "@/components/ui/form-field";
import { Input, inputClasses } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { productionCostOptions, useProductionCostCommands } from "@/features/library/model-detail";
import { parseApiError, userMessage } from "@/lib/errors";
import { formatDuration, formatNumber } from "@/lib/format";
import { uiText } from "@/lib/locale";
import { usePrinters, useSpoolmanStatus, useSpools } from "@/lib/queries";
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
  ProductionEstimate,
} from "@/types/production-costing";
import { InspectorSection, SoftCard } from "./inspector-section";
import { summarizePrintEstimate } from "./print-estimate";
import {
  embeddedRatePerKg,
  matchFilamentPreset,
  matchPrinterPreset,
  previewPieceCosts,
} from "./costing-defaults";
import { PricingTemplateControls } from "./pricing-template-controls";

type PlateCostInput = {
  fileId: number;
  number: number;
  metadata: GcodePlateMetadata;
};

type MaterialDraft = {
  key: number;
  name: string;
  grams: string;
  rate: string | null;
  spoolId: number | null;
};
type Draft = {
  pieces: string;
  minutes: string;
  materials: MaterialDraft[];
  printer: ProductionInputs["printer"] | undefined;
  margin: string;
  tax: string;
};

function blankMaterial(key: number): MaterialDraft {
  return { key, name: "", grams: "", rate: "", spoolId: null };
}

/** The estimate a new draft starts from when the file itself carries one. */
type DefaultEstimate = {
  time: number | null;
  grams: number | null;
  plateMetadata: GcodePlateMetadata[];
};

function makeDraft(
  file: FileRead,
  row: ArtifactCostingRead,
  plate: PlateCostInput | undefined,
  fileEstimate: DefaultEstimate | null,
): Draft {
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
  const metadata = plate?.metadata ?? fileEstimate?.plateMetadata[0] ?? file.metadata;
  const grams = plate
    ? plate.metadata.filament_weight_g
    : fileEstimate
      ? fileEstimate.grams
      : metadata?.filament_weight_g;
  const time = plate
    ? plate.metadata.estimated_time_s
    : fileEstimate
      ? fileEstimate.time
      : metadata?.estimated_time_s;
  return {
    pieces: "1",
    minutes: time == null ? "" : String(Math.round((time / 60) * 100) / 100),
    materials: [
      {
        ...blankMaterial(0),
        name:
          row.multiple_filaments || ((fileEstimate?.plateMetadata.length ?? 0) > 1 && !plate)
            ? uiText("Filament")
            : [metadata?.material_brand, metadata?.material_type].filter(Boolean).join(" ") ||
              (grams == null ? "" : uiText("Filament")),
        rate: null,
        grams: grams == null ? "" : String(grams),
      },
    ],
    printer: undefined,
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
    const rate = numberInRange(line.rate ?? "", 0, 1000000);
    if (!line.name.trim() || grams === null || rate === null) return null;
    materials.push({ name: line.name.trim(), grams, cost_per_kg: rate, spool_id: line.spoolId });
  }
  return {
    pieces,
    print_minutes: minutes,
    materials,
    printer: draft.printer ?? null,
    pricing: rates,
  };
}

const costComponents = [
  ["material", "Filament", "bg-chart-blue"],
  ["electricity", "Electricity", "bg-chart-cyan"],
  ["amortization", "Amortization", "bg-chart-violet"],
  ["maintenance", "Maintenance", "bg-chart-amber"],
  ["extra", "Additional hourly costs", "bg-chart-rose"],
] as const satisfies readonly [keyof PieceCosts, string, string][];

function CostBreakdown({
  estimate,
  costs,
  preview = false,
}: {
  estimate: ProductionEstimate | null;
  costs: PieceCosts | null;
  preview?: boolean;
}) {
  const title = uiText(preview ? "Production cost preview" : "Production cost breakdown");
  if (!estimate || !costs) {
    return (
      <InspectorSection title={title}>
        <p className="px-1 text-sm text-muted-foreground">
          {uiText("No production estimate saved for this artifact.")}
        </p>
      </InspectorSection>
    );
  }

  const money = (value: number, maximumFractionDigits = 4) =>
    formatNumber(value, {
      style: "currency",
      currency: estimate.currency,
      maximumFractionDigits,
    });
  const percent = (value: number) =>
    formatNumber(value, { style: "percent", maximumFractionDigits: 0 });
  const hasMachine = estimate.printer !== null;
  const components = costComponents.filter(([key]) => hasMachine || key === "material");
  const total = components.reduce((sum, [key]) => sum + costs[key], 0);
  const profit = Math.max(0, costs.price_before_tax - costs.production);
  const charge = [
    [
      uiText("Profit margin (%)"),
      formatNumber(estimate.pricing.margin_percent / 100, {
        style: "percent",
        maximumFractionDigits: 2,
      }),
    ],
    [uiText("Price before tax"), money(costs.price_before_tax)],
    [uiText("Tax"), money(costs.tax)],
    [uiText("Print time"), formatDuration(estimate.print_minutes * 60)],
  ];

  return (
    <InspectorSection title={title}>
      <SoftCard className="divide-y-0 p-3">
        <dl aria-label={uiText("Cost per piece")} className="space-y-3 text-sm">
          <div className="rounded-md border border-outline-variant bg-surface-container-low p-3">
            <dt className="text-sm text-muted-foreground">{uiText("Final price")}</dt>
            <dd className="text-3xl font-bold tracking-tight tabular-nums">
              {money(costs.final_price, 2)}
            </dd>
            <dd className="mt-1 text-xs text-muted-foreground">
              {uiText("{value1} to make · {value2} profit per piece", {
                value1: money(costs.production),
                value2: money(profit),
              })}
            </dd>
          </div>

          <div className="space-y-1.5">
            <div className="flex flex-wrap justify-between gap-x-2 font-semibold">
              <dt>{uiText("Production cost")}</dt>
              <dd className="tabular-nums">{money(costs.production)}</dd>
            </div>
            <div aria-hidden="true" className="flex h-3 overflow-hidden rounded-full bg-muted">
              {total > 0 &&
                components
                  .filter(([key]) => costs[key] > 0)
                  .map(([key, , color]) => (
                    <span
                      key={key}
                      className={`h-full ${color} [&+&]:border-s [&+&]:border-card`}
                      style={{ width: `${(costs[key] / total) * 100}%` }}
                    />
                  ))}
            </div>
            {components.map(([key, label, color]) => (
              <div
                key={key}
                className="grid grid-cols-[minmax(0,1fr)_auto_2.75rem] items-baseline gap-2"
              >
                <dt className="flex min-w-0 items-center gap-2">
                  <span aria-hidden="true" className={`size-2 shrink-0 rounded-sm ${color}`} />
                  <span>{uiText(label)}</span>
                </dt>
                <dd className="text-end font-medium tabular-nums">{money(costs[key])}</dd>
                <dd className="text-end text-xs tabular-nums text-muted-foreground">
                  {total > 0 ? percent(costs[key] / total) : "—"}
                </dd>
              </div>
            ))}
          </div>

          <div className="space-y-1.5 border-t border-border pt-3">
            {charge.map(([label, value]) => (
              <div key={label} className="flex flex-wrap justify-between gap-x-2">
                <dt className="text-muted-foreground">{label}</dt>
                <dd className="font-medium tabular-nums">{value}</dd>
              </div>
            ))}
          </div>
        </dl>
        {!hasMachine && (
          <p className="mt-3 border-t border-border pt-3 text-xs text-muted-foreground">
            {uiText("Machine expenses are excluded.")}{" "}
            {uiText("Set operating costs in Profiles → Printers to include machine expenses.")}
          </p>
        )}
      </SoftCard>
    </InspectorSection>
  );
}

function revisionLabel(file: FileRead) {
  return file.file_type === "gcode"
    ? uiText("Rev {value1}", { value1: String(file.gcode_revision_number ?? file.version) })
    : file.file_type.toUpperCase();
}

export function ProductionCosting({
  modelId,
  files,
  initialFileId,
  plateEstimate,
  jobs,
  canEdit,
  admin,
  onAddRevision,
  onFiles,
}: {
  modelId: number;
  files: FileRead[];
  initialFileId: number | null;
  plateEstimate?: PlateCostInput;
  jobs: ModelPrintJobRead[];
  canEdit: boolean;
  admin: boolean;
  onAddRevision?: () => void;
  onFiles?: () => void;
}) {
  const [selected, setSelected] = useState(initialFileId);
  const file = files.find((item) => item.id === selected) ?? files[0];

  if (!file)
    return (
      <section className="space-y-3">
        <h2 className="text-base font-semibold">{uiText("Costing")}</h2>
        <p className="text-sm text-muted-foreground">
          {uiText("Add an STL or G-code file to estimate production costs.")}
        </p>
        <div className="flex flex-wrap gap-2">
          {onAddRevision && (
            <Button size="sm" onClick={onAddRevision}>
              {uiText("Add revision")}
            </Button>
          )}
          {onFiles && (
            <Button size="sm" variant="outline" onClick={onFiles}>
              {uiText("View source files")}
            </Button>
          )}
        </div>
      </section>
    );
  return (
    <div className="space-y-5">
      {files.length > 1 && (
        <div className="flex min-w-0 items-center gap-2.5">
          <label htmlFor="costing-file" className="text-sm text-muted-foreground">
            {uiText("File")}
          </label>
          <select
            id="costing-file"
            className={`${inputClasses} h-8 min-w-0 max-w-full flex-1`}
            value={file.id}
            onChange={(event) => setSelected(Number(event.target.value))}
          >
            {files.map((item) => (
              <option key={item.id} value={item.id}>
                {revisionLabel(item)}
                {" · "}
                {item.original_filename}
              </option>
            ))}
          </select>
        </div>
      )}
      <ArtifactEstimate
        key={file.id}
        modelId={modelId}
        file={file}
        plate={plateEstimate?.fileId === file.id ? plateEstimate : undefined}
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
  plate,
  jobs,
  canEdit,
  admin,
}: {
  modelId: number;
  file: FileRead;
  plate?: PlateCostInput;
  jobs: ModelPrintJobRead[];
  canEdit: boolean;
  admin: boolean;
}) {
  const query = useQuery(productionCostOptions(modelId, file.id));
  const commands = useProductionCostCommands(modelId, file.id);
  // A sliced archive's whole-file metadata is not an estimate; its plates are.
  const plates = useGcodePlates(
    modelId,
    file.original_filename.toLowerCase().endsWith(".3mf") ? file.id : null,
  );
  const summary = summarizePrintEstimate(file, plates);
  const fileEstimate: DefaultEstimate | null =
    summary.kind === "single" || summary.kind === "plates"
      ? {
          time: summary.time,
          grams: summary.weight,
          plateMetadata: plates?.plate_metadata.map((entry) => entry.metadata) ?? [],
        }
      : null;
  // The form restarts from the server's row after every confirmed write.
  const [formVersion, setFormVersion] = useState(0);
  const [clearBase, setClearBase] = useState<ArtifactCostingRead | null>(null);
  const [reviewRequired, setReviewRequired] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function persist(estimate: ProductionInputs | null, base: ArtifactCostingRead) {
    setMessage(null);
    try {
      await commands.save.mutateAsync({ estimate, base, session: getSessionVersion() });
      setClearBase(null);
      setFormVersion((version) => version + 1);
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

  if (query.isError)
    return (
      <p role="alert" className="text-sm text-destructive">
        {userMessage(query.error)}
      </p>
    );
  const row = query.data;
  if (!row) return <Skeleton className="h-40" />;

  return (
    <div className="space-y-5">
      {canEdit ? (
        <EstimateForm
          key={`${file.id}-${formVersion}-${fileEstimate === null ? "waiting" : "ready"}`}
          file={file}
          plate={plate}
          fileEstimate={fileEstimate}
          row={row}
          jobs={jobs}
          admin={admin}
          blocked={reviewRequired}
          commands={commands}
          messages={
            <>
              {message && (
                <p role="alert" className="text-sm text-destructive">
                  {message}
                </p>
              )}
              {reviewRequired && (
                <div role="status" className="space-y-2 border p-3 text-sm">
                  <p>
                    {uiText(
                      "Review the current estimate before saving again. Your draft is preserved.",
                    )}
                  </p>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={query.isFetching}
                    onClick={async () => {
                      const current = await query.refetch();
                      if (current.isSuccess) {
                        setMessage(null);
                        setReviewRequired(false);
                      }
                    }}
                  >
                    {uiText("Review current estimate")}
                  </Button>
                </div>
              )}
            </>
          }
          onClear={row.estimate ? () => setClearBase(row) : undefined}
          onSave={(estimate) => void persist(estimate, row)}
        />
      ) : (
        <CostBreakdown estimate={row.estimate} costs={row.costs} />
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
  plate,
  fileEstimate,
  row,
  jobs,
  admin,
  blocked,
  commands,
  messages,
  onClear,
  onSave,
}: {
  file: FileRead;
  plate?: PlateCostInput;
  fileEstimate: DefaultEstimate | null;
  row: ArtifactCostingRead;
  jobs: ModelPrintJobRead[];
  admin: boolean;
  blocked: boolean;
  commands: ReturnType<typeof useProductionCostCommands>;
  messages: ReactNode;
  onClear?: () => void;
  onSave: (estimate: ProductionInputs) => void;
}) {
  const [estimatePlate] = useState(() => (row.estimate ? undefined : plate));
  const [initialDraft] = useState(() => makeDraft(file, row, estimatePlate, fileEstimate));
  const [draft, setDraft] = useState(initialDraft);
  const [nextKey, setNextKey] = useState(draft.materials.length);
  // Where each material's rate came from, until the reader types over it.
  const [rateOrigins, setRateOrigins] = useState<Record<number, string>>({});
  const currency = row.estimate?.currency ?? row.currency;
  const sameCurrency = currency === row.currency;
  const filamentProfiles = useQuery(filamentProfilesOptions()).data ?? [];
  const printerProfiles = useQuery(printerProfilesOptions()).data ?? [];
  const printers = usePrinters({ enabled: admin && !row.estimate && jobs.length > 0 });
  const spoolman = useSpoolmanStatus({ enabled: admin });
  const spools = useSpools({ enabled: admin && spoolman.data?.enabled === true }).data ?? [];
  const measured = jobs.find(
    (job) => job.file_id === file.id && job.state === "completed" && job.actual_duration_s != null,
  );
  const duration = measured?.actual_duration_s;
  const rates = toRates(draft);
  const plateMetadata = estimatePlate
    ? [estimatePlate.metadata]
    : (fileEstimate?.plateMetadata ?? []);
  const metadata = plateMetadata[0] ?? file.metadata;
  const pickedFilament =
    sameCurrency && !row.multiple_filaments && plateMetadata.length <= 1
      ? matchFilamentPreset(filamentProfiles, metadata ?? undefined)
      : undefined;
  const embeddedRate = file.original_filename.toLowerCase().endsWith(".3mf")
    ? embeddedRatePerKg(plateMetadata)
    : embeddedRatePerKg([
        {
          filament_cost: row.slicer_filament_cost,
          filament_weight_g: file.metadata?.filament_weight_g,
        },
      ]);
  const suggestedRate = pickedFilament?.cost_per_kg ?? embeddedRate;
  const printerId = jobs.find(
    (job) => job.file_id === file.id && job.state === "completed",
  )?.printer_id;
  const linkedProfileId = printers.data?.find(
    (printer) => printer.id === printerId,
  )?.cost_profile_id;
  const pickedPrinter =
    printerProfiles.find((profile) => profile.id === linkedProfileId && profile.expenses) ??
    matchPrinterPreset(
      printerProfiles,
      metadata?.printer_model ?? file.metadata?.printer_model,
      metadata?.nozzle_diameter_mm ?? file.metadata?.nozzle_diameter_mm,
    );
  const printer =
    draft.printer === undefined
      ? sameCurrency && pickedPrinter?.expenses
        ? { name: pickedPrinter.name, expenses: pickedPrinter.expenses }
        : null
      : draft.printer;
  const materials = draft.materials.map((line) => ({
    ...line,
    autoRate: line.rate === null,
    rate: line.rate ?? (suggestedRate == null ? "" : String(Number(suggestedRate.toFixed(4)))),
  }));
  const estimate = toEstimate({ ...draft, printer, materials }, rates);
  const preview = estimate !== null && (row.estimate === null || draft !== initialDraft);
  const fromFile = !row.estimate;
  const fileOrigin = estimatePlate
    ? uiText("modelDetail.plateCostSource", {
        value1: String(estimatePlate.number).padStart(2, "0"),
      })
    : fromFile && file.file_type === "gcode"
      ? uiText("From the G-code")
      : row.estimate
        ? uiText("Saved estimate")
        : null;
  const costedPrinters = printerProfiles.filter((item) => item.expenses);
  const matchedPrinter = printer
    ? costedPrinters.find((item) => item.name === printer.name)
    : undefined;

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
    <form
      aria-label={uiText("Production estimate")}
      className="production-estimate-form space-y-5"
      onSubmit={submit}
    >
      <fieldset disabled={commands.save.isPending} className="space-y-5">
        <CostBreakdown
          estimate={preview ? { ...estimate, currency } : row.estimate}
          costs={preview ? previewPieceCosts(estimate) : row.costs}
          preview={preview}
        />
        <InspectorSection
          title={`${uiText("Production estimate")} · ${revisionLabel(file)}`}
          actions={
            measured && duration != null ? (
              <Button
                type="button"
                size="xs"
                variant="ghost"
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
            ) : undefined
          }
        >
          <SoftCard className="divide-y-0">
            <div className="grid grid-cols-2 items-start gap-3 p-4">
              <FormField
                className="col-span-2"
                label={uiText("Cost profile")}
                hint={
                  draft.printer === undefined && printer
                    ? uiText("From the {value1} preset", { value1: printer.name })
                    : undefined
                }
              >
                <select
                  className={inputClasses}
                  value={matchedPrinter ? String(matchedPrinter.id) : printer ? "current" : ""}
                  disabled={!sameCurrency}
                  onChange={(event) => {
                    if (event.target.value === "") {
                      setDraft({ ...draft, printer: null });
                      return;
                    }
                    const profile = costedPrinters.find(
                      (item) => item.id === Number(event.target.value),
                    );
                    if (profile?.expenses)
                      setDraft({
                        ...draft,
                        printer: { name: profile.name, expenses: profile.expenses },
                      });
                  }}
                >
                  <option value="">
                    {printer
                      ? uiText("Exclude machine expenses")
                      : uiText("Choose a saved printer profile")}
                  </option>
                  {printer && !matchedPrinter && <option value="current">{printer.name}</option>}
                  {costedPrinters.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name}
                    </option>
                  ))}
                </select>
              </FormField>
              <FormField
                label={uiText("Estimated time per copy")}
                hint={[uiText("Minutes"), fileOrigin].filter(Boolean).join(" · ")}
              >
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
              {!sameCurrency && (
                <p className="col-span-2 text-xs text-muted-foreground">
                  {uiText(
                    "This estimate uses a different currency from the current vault presets.",
                  )}
                </p>
              )}
              {materials.map((line) => (
                <fieldset
                  key={line.key}
                  className={
                    draft.materials.length > 1
                      ? "col-span-2 grid grid-cols-2 gap-3 border p-3"
                      : "col-span-2 grid grid-cols-2 gap-3"
                  }
                >
                  {sameCurrency && (filamentProfiles.length > 0 || spools.length > 0) && (
                    <FormField
                      className="col-span-2"
                      label={uiText("Fill from filament preset or spool")}
                    >
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
                                [
                                  spool.vendor_name,
                                  spool.filament_name ?? spool.name,
                                  spool.material,
                                ]
                                  .filter(Boolean)
                                  .join(" ") || uiText("Filament"),
                              rate: spool.cost_per_kg == null ? "" : String(spool.cost_per_kg),
                              spoolId: spool.id,
                            });
                            setRateOrigins((old) => ({
                              ...old,
                              [line.key]: uiText("From Spoolman spool #{id}", { id: spool.id }),
                            }));
                          } else if (profile) {
                            updateMaterial(line.key, {
                              name: [profile.name, profile.material_type]
                                .filter(Boolean)
                                .join(" · "),
                              rate: profile.cost_per_kg == null ? "" : String(profile.cost_per_kg),
                              spoolId: null,
                            });
                            setRateOrigins((old) => ({
                              ...old,
                              [line.key]: uiText("From the {value1} preset", {
                                value1: profile.name,
                              }),
                            }));
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
                    <div className="col-span-2 flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
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
                  <FormField className="col-span-2" label={uiText("Material name")}>
                    <Input
                      maxLength={200}
                      value={line.name}
                      required
                      onChange={(event) => updateMaterial(line.key, { name: event.target.value })}
                    />
                  </FormField>
                  <FormField
                    label={uiText("Filament per copy")}
                    hint={[uiText("Grams"), fileOrigin].filter(Boolean).join(" · ")}
                  >
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
                  <FormField
                    label={`${uiText("Cost per kg")} (${currency})`}
                    hint={
                      rateOrigins[line.key] ??
                      (line.autoRate
                        ? pickedFilament
                          ? uiText("From the {value1} preset", { value1: pickedFilament.name })
                          : embeddedRate !== null
                            ? uiText("From the G-code")
                            : undefined
                        : undefined)
                    }
                  >
                    <Input
                      type="number"
                      min={0}
                      max={1000000}
                      step="any"
                      value={line.rate ?? ""}
                      required
                      onChange={(event) => {
                        updateMaterial(line.key, { rate: event.target.value });
                        setRateOrigins((old) => {
                          const next = { ...old };
                          delete next[line.key];
                          return next;
                        });
                      }}
                    />
                  </FormField>
                  {draft.materials.length > 1 && (
                    <Button
                      type="button"
                      size="xs"
                      variant="ghost"
                      className="col-span-2 justify-self-start"
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
              <div className="col-span-2">
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
              </div>
            </div>
            <div className="space-y-3 border-t border-border p-4">
              <h3 className="text-sm font-medium text-muted-foreground">{uiText("Pricing")}</h3>
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
                    await commands.deleteTemplate.mutateAsync({
                      id,
                      session: getSessionVersion(),
                    });
                    toast.success(uiText("Pricing template deleted."));
                    return true;
                  } catch (error) {
                    toast.error(error);
                    return false;
                  }
                }}
              />
            </div>
          </SoftCard>
        </InspectorSection>
        {messages}
      </fieldset>
      <div className="flex flex-wrap justify-end gap-2">
        {onClear && (
          <Button type="button" variant="ghost" disabled={blocked} onClick={onClear}>
            {uiText("Clear estimate")}
          </Button>
        )}
        <Button type="submit" loading={commands.save.isPending} disabled={!estimate || blocked}>
          {uiText("Save estimate")}
        </Button>
      </div>
    </form>
  );
}
