import { useId, useState, type CSSProperties } from "react";

import { RefreshCw, Ruler } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { printerControlAvailability } from "@/features/printers/controls";
import { usePrinterBedMesh, type BedMesh } from "@/features/printers/inspection";
import { ApiError, parseApiError } from "@/lib/errors";
import { formatNumber } from "@/lib/format";
import { uiMessage, uiText, type MessageDescriptor } from "@/lib/locale";
import type { PrinterRead, PrinterSnapshot } from "@/types";

import "./printer-bed-surface.css";

const height = (value: number) => formatNumber(value, { maximumFractionDigits: 4 });
const position = (value: number) => formatNumber(value, { maximumFractionDigits: 2 });

function meshCoordinate(min: number, max: number, index: number, count: number) {
  const fraction = index / (count - 1);
  return min * (1 - fraction) + max * fraction;
}

function BedMeshReadout({ mesh }: { mesh: Extract<BedMesh, { state: "active" }> }) {
  const tableTitle = useId();
  const { mesh_min: min, mesh_max: max, probed_matrix: rows, variation_mm: variation } = mesh;
  const columnCount = rows[0].length;
  const lowest = rows.reduce(
    (minimum, row) => row.reduce((rowMin, z) => Math.min(rowMin, z), minimum),
    Infinity,
  );
  const x = (index: number) => position(meshCoordinate(min[0], max[0], index, columnCount));
  const y = (index: number) => position(meshCoordinate(min[1], max[1], index, rows.length));
  return (
    <>
      <dl className="flex flex-wrap gap-x-6 gap-y-2 text-xs">
        <div>
          <dt className="text-muted-foreground">{uiText("Active profile")}</dt>
          <dd className="font-mono">{mesh.profile_name ?? uiText("Unnamed")}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">{uiText("X extent")}</dt>
          <dd className="font-mono">
            {uiText("{value1} to {value2} mm", { value1: x(0), value2: x(columnCount - 1) })}
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">{uiText("Y extent")}</dt>
          <dd className="font-mono">
            {uiText("{value1} to {value2} mm", { value1: y(0), value2: y(rows.length - 1) })}
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">{uiText("Height variation")}</dt>
          <dd className="font-mono">{uiText("{value1} mm", { value1: height(variation) })}</dd>
        </div>
      </dl>
      <figure className="min-w-0 space-y-2">
        <figcaption id={tableTitle} className="text-xs text-muted-foreground">
          {uiText("Klipper probe grid · X increases right, Y increases up")}
        </figcaption>
        <div
          role="region"
          aria-labelledby={tableTitle}
          tabIndex={0}
          className="max-h-80 max-w-full overflow-auto focus-visible:ring-2 focus-visible:ring-ring"
        >
          <table className="border-collapse text-xs tabular-nums">
            <caption className="sr-only">{uiText("Probe heights in millimetres")}</caption>
            <thead>
              <tr>
                <th scope="col" className="p-1 text-muted-foreground">
                  {uiText("Y ↑ / X → (mm)")}
                </th>
                {rows[0].map((_, index) => (
                  <th key={index} scope="col" className="px-2 pb-1 text-muted-foreground">
                    {x(index)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((_, index) => {
                const rowIndex = rows.length - 1 - index;
                return (
                  <tr key={rowIndex}>
                    <th scope="row" className="px-2 text-right text-muted-foreground">
                      {y(rowIndex)}
                    </th>
                    {rows[rowIndex].map((z, column) => {
                      const intensity = variation === 0 ? 0 : (z - lowest) / variation;
                      const tint: CSSProperties & { "--printer-bed-height": string } = {
                        "--printer-bed-height": `${intensity * 28}%`,
                      };
                      return (
                        <td
                          className="printer-bed-cell rounded-lg border border-border p-2 text-center font-mono"
                          key={column}
                          style={tint}
                        >
                          {height(z)}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="text-xs text-muted-foreground">
          {uiText("Z height (mm) · stronger tint marks higher points")}
        </p>
      </figure>
    </>
  );
}

interface BedMeasurementControl {
  printer: PrinterRead;
  snapshot: PrinterSnapshot;
  live: boolean;
  busy: boolean;
  run: (
    key: "measure-bed",
    command: () => Promise<void>,
    success: MessageDescriptor,
    report: (kind: "success" | "error", message: string) => void,
  ) => Promise<void>;
}

type MeasurementFeedback =
  | { state: "pending" }
  | { state: "success"; message: string }
  | { state: "error" | "needs-refresh"; message: string };

export function PrinterBedSurface({
  printerId,
  control,
}: {
  printerId: number;
  control?: BedMeasurementControl;
}) {
  const title = useId();
  const controls = control
    ? printerControlAvailability(control.printer, control.snapshot, control.live)
    : null;
  const canCheck = controls?.moonraker === true && controls.connected;
  const { mesh, availability, measure, session } = usePrinterBedMesh(printerId, canCheck);
  const { data, isFetching, isPending, isError, refetch } = mesh;
  const [confirmation, setConfirmation] = useState<{ printerId: number; session: number } | null>(
    null,
  );
  const confirming = confirmation?.session === session && confirmation.printerId === printerId;
  const [feedback, setFeedback] = useState<{
    session: number;
    value: MeasurementFeedback;
  } | null>(null);
  const measurement = feedback?.session === session ? feedback.value : null;
  const measuring = measurement?.state === "pending";
  const permitted = canCheck && !availability.isError && availability.data?.supported === true;
  const homedAxes = control?.snapshot.toolhead?.homed_axes;
  const homed = homedAxes != null && [..."xyz"].every((axis) => homedAxes.includes(axis));
  const idle = controls?.idle === true && control?.snapshot.idle_timeout?.state !== "Printing";
  const canMeasure =
    permitted &&
    idle &&
    homed &&
    !control?.busy &&
    !isFetching &&
    !availability.isFetching &&
    !measuring &&
    measurement?.state !== "needs-refresh";

  async function refresh() {
    const [updated] = await Promise.all([
      refetch({ cancelRefetch: false }),
      canCheck ? availability.refetch({ cancelRefetch: false }) : undefined,
    ]);
    if (updated.isSuccess && measurement?.state === "needs-refresh") {
      setFeedback((current) => (current?.session === session ? null : current));
    }
  }

  function confirmMeasurement() {
    setConfirmation(null);
    if (!control || !canMeasure || !confirming) return;
    let needsRefresh = false;
    void control.run(
      "measure-bed",
      async () => {
        setFeedback({ session, value: { state: "pending" } });
        try {
          await measure();
        } catch (error) {
          const { status, code } = parseApiError(error);
          const unknown = status === 0 || (status >= 500 && code === String(status));
          needsRefresh =
            unknown ||
            code === "bed_measurement_unknown" ||
            code === "bed_measurement_mesh_unavailable";
          if (unknown) throw new ApiError(502, "bed_measurement_unknown", "");
          throw error;
        }
      },
      uiMessage("Bed measured"),
      (kind, message) => {
        setFeedback(
          kind === "error"
            ? { session, value: { state: needsRefresh ? "needs-refresh" : "error", message } }
            : { session, value: { state: "success", message } },
        );
      },
    );
  }
  return (
    <section className="printer-desk-section" aria-labelledby={title} aria-busy={isFetching}>
      <div className="printer-desk-heading">
        <h2 id={title}>{uiText("Bed surface")}</h2>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="xs"
            variant="outline"
            disabled={isFetching || measuring}
            onClick={() => void refresh()}
          >
            <RefreshCw className="size-3.5" aria-hidden="true" />
            {uiText("Refresh bed mesh")}
          </Button>
          {permitted && (
            <Button
              size="xs"
              variant="outline"
              disabled={!canMeasure}
              onClick={() => setConfirmation({ printerId, session })}
            >
              <Ruler className="size-3.5" aria-hidden="true" />
              {uiText("Measure bed")}
            </Button>
          )}
        </div>
      </div>
      {measuring ? (
        <p role="status" className="text-sm text-muted-foreground">
          {uiText("Measuring bed…")}
        </p>
      ) : measurement?.state === "success" ? (
        <p role="status" className="text-sm text-muted-foreground">
          {measurement.message}
        </p>
      ) : measurement ? (
        <p role="alert" className="text-sm text-destructive">
          {measurement.message}
        </p>
      ) : permitted && !idle ? (
        <p className="text-sm text-muted-foreground">
          {uiText("Wait until the printer is idle to measure the bed.")}
        </p>
      ) : permitted && !homed ? (
        <p className="text-sm text-muted-foreground">
          {uiText("Home X, Y and Z before measuring the bed.")}
        </p>
      ) : null}
      {isPending || isError || data.state !== "active" ? (
        <p role="status" className="text-sm text-muted-foreground">
          {isPending
            ? uiText("Reading bed mesh…")
            : isError
              ? uiText("Could not read the bed mesh.")
              : data.state === "inactive"
                ? uiText("No active bed mesh.")
                : uiText("Klipper bed mesh is not configured.")}
        </p>
      ) : (
        <BedMeshReadout mesh={data} />
      )}
      <ConfirmModal
        open={confirming}
        onClose={() => setConfirmation(null)}
        onConfirm={confirmMeasurement}
        title={uiText("Measure bed")}
        description={uiText(
          "Clear the bed and keep hands away from the toolhead while it probes. This activates the measured mesh.",
        )}
        confirmLabel={uiText("Measure bed")}
      />
    </section>
  );
}
