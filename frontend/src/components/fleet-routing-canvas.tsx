import { useEffect, useRef, useState } from "react";
import {
  Background,
  getViewportForBounds,
  Handle,
  Position,
  ReactFlow,
  useReactFlow,
  useStoreApi,
  ReactFlowProvider,
  type Connection,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import {
  ArrowRight,
  Cable,
  ChevronLeft,
  ChevronRight,
  CircuitBoard,
  FileBox,
  Focus,
  GitBranch,
  Layers3,
  Minus,
  Plus,
} from "lucide-react";
import { Printer3D } from "@/components/ui/printer-3d";
import { Button } from "@/components/ui/button";
import { knownUiText, uiText } from "@/lib/locale";
import { Link } from "@/lib/link";
import { dispatchName } from "@/lib/format";
import { providerLabel } from "@/lib/printer-providers";
import { printerArtwork } from "@/lib/orca-printer-images";
import { usePrinterArtworkPreference } from "@/lib/printer-card-display";
import type { FleetSummary, PrinterRead, PrintJobRead, RoutingStrategy } from "@/types";
import "@xyflow/react/dist/style.css";
import "./fleet-routing-canvas.css";

type Machine = FleetSummary["printers"][number];
type StationKind = "job" | "rule" | "printer" | "inlet";
type Station = Node<
  {
    kind: StationKind;
    label: string;
    title: string;
    detail: string;
    chosen: boolean;
    image?: string;
    held?: boolean;
    printerStatus?: Machine["status"];
    printing?: boolean;
    progress?: number | null;
    remaining?: number | null;
    select: () => void;
  },
  "station"
>;

const icons = { job: FileBox, rule: GitBranch, printer: Printer3D, inlet: Layers3 };
const nodeTypes = { station: StationNode };
const fitOptions = { padding: 0.17, maxZoom: 1.2 };
const pageSize = 3;
function strategyLabel(strategy: RoutingStrategy) {
  return strategy === "manual"
    ? uiText("Manual assignment")
    : strategy === "least_busy"
      ? uiText("Least busy")
      : uiText("Default printer");
}
function machineState(row: Machine) {
  if (row.status === "error") return uiText("Error");
  if (row.maintenance) return uiText("Maintenance");
  if (row.pending_operator_release) return uiText("Operator release required");
  if (row.drain_mode) return uiText("Draining");
  if (row.status === "ready" && row.current_job_id != null) return uiText("Review active job");
  if (row.status === "ready") return uiText("Ready");
  if (row.status === "printing") return uiText("Printing");
  return knownUiText(row.status);
}

function StationNode({ data }: NodeProps<Station>) {
  const Icon = icons[data.kind];
  return (
    <div
      className="routing-station"
      data-kind={data.kind}
      data-chosen={data.chosen}
      data-held={data.held}
      data-printer-status={data.printerStatus}
      data-printing={data.printing}
    >
      {data.kind !== "job" && data.kind !== "inlet" && (
        <Handle type="target" position={Position.Left} isConnectable={data.kind === "printer"} />
      )}
      <button type="button" aria-pressed={data.chosen} onClick={data.select}>
        <span className="routing-station-glyph">
          {data.image ? (
            <img
              alt=""
              src={data.image}
              onError={(e) => {
                const fallback = printerArtwork(null).imageUrl;
                if (e.currentTarget.getAttribute("src") !== fallback)
                  e.currentTarget.src = fallback;
              }}
            />
          ) : (
            <Icon size={22} />
          )}
        </span>
        <span className="routing-station-copy">
          <small>{data.label}</small>
          <strong title={data.title}>{data.title}</strong>
          <span title={data.detail}>{data.detail}</span>
        </span>
      </button>
      {data.progress !== undefined && (
        <div className="routing-print-progress">
          <div>
            <strong>
              {data.progress === null
                ? uiText("Awaiting progress")
                : Math.round(100 * data.progress) + "%"}
            </strong>
            {data.remaining != null && (
              <span>{uiText("{value1} min remaining", { value1: data.remaining })}</span>
            )}
          </div>
          <progress aria-label={uiText("Progress")} value={data.progress ?? undefined} max={1} />
        </div>
      )}
      {data.kind !== "printer" && (
        <Handle type="source" position={Position.Right} isConnectable={data.kind === "job"} />
      )}
    </div>
  );
}

interface Props {
  rows: Machine[];
  printers: PrinterRead[];
  jobs: PrintJobRead[];
  onEdit: (job: PrintJobRead, printer?: PrinterRead) => void;
  onAdd?: () => void;
  onJobs: (id: string, active: boolean) => void;
}
function RoutingMap({ rows, printers, jobs, onEdit, onAdd, onJobs }: Props) {
  const flow = useReactFlow();
  const store = useStoreApi();
  const manualCamera = useRef(false);
  const artwork = usePrinterArtworkPreference();
  const [selected, setSelected] = useState("");
  const [page, setPage] = useState(0);
  const pages = Math.max(1, Math.ceil(jobs.length / pageSize));
  const currentPage = Math.min(page, pages - 1);
  const visible = jobs.slice(currentPage * pageSize, (currentPage + 1) * pageSize);
  const nodes: Station[] = [];
  const edges: Edge[] = [];
  const available = rows.filter(
    (r) =>
      r.status === "ready" &&
      r.current_job_id == null &&
      !r.drain_mode &&
      !r.pending_operator_release &&
      !r.maintenance,
  );
  const running = rows.filter((r) => r.status === "printing" || r.status === "paused");
  const activeRoute = running.length > 0;
  const queueLanes = visible.length + Number(activeRoute);
  const rowCount = Math.max(1, queueLanes, rows.length);
  const visibleLanes = Math.max(1, queueLanes);
  const stationCount = visibleLanes * 2 + rows.length;

  useEffect(() => {
    if (manualCamera.current) return;
    // React Flow measures new stations after committing them to the DOM.
    let frame: number | undefined;
    function fitMeasuredStations() {
      const { nodeLookup, width, height, minZoom, panZoom } = store.getState();
      if (!width || !height || !panZoom || nodeLookup.size !== stationCount) return;
      if (
        !Array.from(nodeLookup.values()).every(({ measured }) => measured.width && measured.height)
      )
        return;
      unsubscribe();
      frame = requestAnimationFrame(() => {
        if (manualCamera.current) return;
        const bounds = flow.getNodesBounds(flow.getNodes());
        void flow.setViewport(
          getViewportForBounds(
            bounds,
            width,
            height,
            minZoom,
            fitOptions.maxZoom,
            fitOptions.padding,
          ),
        );
      });
    }
    const unsubscribe = store.subscribe(fitMeasuredStations);
    fitMeasuredStations();
    return () => {
      unsubscribe();
      if (frame !== undefined) cancelAnimationFrame(frame);
    };
  }, [flow, stationCount, store]);

  const ypos = (index: number, count: number) => ((rowCount - count) / 2 + index) * 142;
  function node(
    id: string,
    x: number,
    y: number,
    data: Omit<Station["data"], "select" | "chosen">,
  ) {
    nodes.push({
      id,
      type: "station",
      position: { x, y },
      data: {
        ...data,
        chosen: id === selected,
        select: () => {
          if (id === "running") onJobs("all", true);
          else if (id === "inlet" && onAdd) onAdd();
          else setSelected(id);
        },
      },
    });
  }
  function edge(
    source: string,
    target: string,
    registered = false,
    related = false,
    flowing = false,
  ) {
    edges.push({
      id: source + ":" + target,
      source,
      target,
      type: "default",
      className: flowing
        ? "routing-link-printing"
        : registered
          ? "routing-link-available"
          : related || source === selected || target === selected
            ? "routing-link-selected"
            : "routing-link-assigned",
    });
  }
  if (activeRoute || !visible.length) {
    const input = activeRoute ? "running" : "inlet";
    const y = ypos(0, visibleLanes);
    node(input, 0, y, {
      kind: "inlet",
      label: uiText("Queue input"),
      title: activeRoute ? uiText("Active jobs") : uiText("No queued jobs"),
      detail: activeRoute
        ? uiText("{count} running", { count: running.length })
        : uiText("Add a saved Revision"),
    });
    node("dispatcher", 325, y, {
      kind: "rule",
      label: uiText("Routing"),
      title: uiText("Farm dispatcher"),
      detail: activeRoute ? uiText("Dispatched to printer") : uiText("Waiting for queued work"),
    });
    edge(
      input,
      "dispatcher",
      !activeRoute,
      false,
      running.some((row) => row.status === "printing"),
    );
    for (const row of visible.length > 0 ? running : rows)
      edge(
        "dispatcher",
        "printer-" + row.printer_id,
        !running.includes(row),
        false,
        row.status === "printing",
      );
  }
  visible.forEach((job, index) => {
    const id = "job-" + job.id;
    const rule = "rule-" + job.id;
    const y = ypos(index + Number(activeRoute), queueLanes);
    const target = "printer-" + job.printer_id;
    const chosen = selected === id || selected === rule || selected === target;
    node(id, 0, y, {
      kind: "job",
      label: uiText("Queued job") + " · #" + job.id,
      title: dispatchName(job.remote_filename),
      detail: uiText("Priority") + " · " + knownUiText(job.priority ?? "normal"),
    });
    node(rule, 325, y, {
      kind: "rule",
      label: uiText("Routing rule"),
      title: strategyLabel(job.routing_strategy),
      detail: job.blocked_reason
        ? knownUiText(job.blocked_reason)
        : job.target_group || uiText("All groups"),
      held: !!job.blocked_reason,
    });
    edge(id, rule, false, chosen);
    if (rows.some((r) => r.printer_id === job.printer_id)) edge(rule, target, false, chosen);
    else if (job.printer_id == null && job.routing_strategy !== "manual") {
      for (const row of rows)
        if (!job.target_group || row.group === job.target_group)
          edge(rule, "printer-" + row.printer_id, true);
    }
  });
  rows.forEach((row, index) => {
    const printer = printers.find((p) => p.id === row.printer_id);
    const active = row.status === "printing" || row.status === "paused";
    node("printer-" + row.printer_id, 650, ypos(index, rows.length), {
      kind: "printer",
      printerStatus: row.status,
      label: machineState(row),
      title: row.name,
      detail: row.current_job_name
        ? dispatchName(row.current_job_name)
        : printer
          ? providerLabel(printer)
          : "",
      image:
        artwork === "off"
          ? undefined
          : printerArtwork(artwork === "model" ? printer?.model_name : null).imageUrl,
      held: !!(row.maintenance || row.pending_operator_release || row.drain_mode),
      printing: row.status === "printing",
      progress: active ? row.progress : undefined,
      remaining: active ? row.remaining_minutes : undefined,
    });
  });
  const selectedJob = visible.find(
    (job) => selected === "job-" + job.id || selected === "rule-" + job.id,
  );
  const selectedPrinter = rows.find((row) => selected === "printer-" + row.printer_id);
  const selectedMachine = printers.find((p) => p.id === selectedJob?.printer_id);
  const canConnect = (c: Pick<Connection, "source" | "target">) =>
    visible.some((j) => "job-" + j.id === c.source) &&
    printers.some((p) => p.access.can_print && "printer-" + p.id === c.target);
  function connect(c: Connection) {
    if (!canConnect(c)) return;
    const job = visible.find((j) => "job-" + j.id === c.source);
    const printer = printers.find((p) => "printer-" + p.id === c.target);
    if (job && printer) onEdit(job, printer);
  }
  const overview = !selectedJob && !selectedPrinter;
  const InspectorIcon = selectedPrinter ? Printer3D : selectedJob ? FileBox : CircuitBoard;
  return (
    <div className="routing-workspace">
      <div className="routing-grid">
        <div className="routing-stage">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            fitView
            fitViewOptions={fitOptions}
            minZoom={0.18}
            maxZoom={2}
            nodesDraggable={false}
            nodesFocusable={false}
            edgesFocusable={false}
            elementsSelectable={false}
            deleteKeyCode={null}
            zoomOnScroll={false}
            panOnScroll={false}
            preventScrolling={false}
            onConnect={connect}
            isValidConnection={canConnect}
            onMoveStart={(event) => {
              if (event) manualCamera.current = true;
            }}
            onPaneClick={() => setSelected("")}
          >
            <Background gap={19} size={1} color="var(--border)" />
          </ReactFlow>
          <div className="routing-key">
            <div>
              <i />
              {uiText("Assigned route")}
            </div>
            <div>
              <i />
              {uiText("Registered connection")}
            </div>
          </div>

          <div className="routing-camera" role="group" aria-label={uiText("Canvas controls")}>
            <Button
              variant="ghost"
              size="icon"
              aria-label={uiText("Zoom out")}
              onClick={() => {
                manualCamera.current = true;
                void flow.zoomOut();
              }}
            >
              <Minus />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              aria-label={uiText("Fit map")}
              onClick={() => {
                manualCamera.current = false;
                void flow.fitView(fitOptions);
              }}
            >
              <Focus />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              aria-label={uiText("Zoom in")}
              onClick={() => {
                manualCamera.current = true;
                void flow.zoomIn();
              }}
            >
              <Plus />
            </Button>
          </div>
          {pages > 1 && (
            <div className="routing-pager">
              <Button
                variant="ghost"
                size="icon"
                aria-label={uiText("Previous")}
                disabled={!currentPage}
                onClick={() => setPage(currentPage - 1)}
              >
                <ChevronLeft />
              </Button>
              <span>
                {currentPage + 1} / {pages}
              </span>
              <Button
                variant="ghost"
                size="icon"
                aria-label={uiText("Next")}
                disabled={currentPage + 1 >= pages}
                onClick={() => setPage(currentPage + 1)}
              >
                <ChevronRight />
              </Button>
            </div>
          )}
        </div>
        <aside className="routing-inspector" aria-label={uiText("Route inspector")}>
          {!overview && (
            <Button size="xs" variant="ghost" onClick={() => setSelected("")}>
              <ChevronLeft />
              {uiText("Farm dispatcher")}
            </Button>
          )}
          <div className="routing-inspector-title">
            <InspectorIcon size={19} />
            <h3>
              {selectedPrinter?.name ??
                (selectedJob
                  ? dispatchName(selectedJob.remote_filename)
                  : uiText("Farm dispatcher"))}
            </h3>
          </div>
          {selectedJob ? (
            <>
              <dl>
                <dt>{uiText("Routing rule")}</dt>
                <dd>{strategyLabel(selectedJob.routing_strategy)}</dd>
                <dt>{uiText("Destination")}</dt>
                <dd>{selectedMachine?.name ?? uiText("Unassigned")}</dd>
                <dt>{uiText("Priority")}</dt>
                <dd>{knownUiText(selectedJob.priority ?? "normal")}</dd>
                {selectedJob.blocked_reason && (
                  <>
                    <dt>{uiText("Status")}</dt>
                    <dd>{knownUiText(selectedJob.blocked_reason)}</dd>
                  </>
                )}
              </dl>
              <Button variant="outline" size="sm" onClick={() => onEdit(selectedJob)}>
                {uiText("Edit routing")}
              </Button>
            </>
          ) : selectedPrinter ? (
            <>
              <dl>
                <dt>{uiText("Routing")}</dt>
                <dd data-printer-status={selectedPrinter.status}>
                  {machineState(selectedPrinter)}
                </dd>
                <dt>{uiText("Current job")}</dt>
                <dd>
                  {selectedPrinter.current_job_name
                    ? dispatchName(selectedPrinter.current_job_name)
                    : uiText("No active print")}
                </dd>
                <dt>{uiText("Next job")}</dt>
                <dd>
                  {selectedPrinter.next_job_name
                    ? dispatchName(selectedPrinter.next_job_name)
                    : uiText("No queued job")}
                </dd>
              </dl>
              <Link
                className="routing-detail-link"
                href={"/printers/" + selectedPrinter.printer_id}
              >
                {uiText("Open printer")}
                <ArrowRight size={15} />
              </Link>
              <Button
                variant="outline"
                size="sm"
                onClick={() =>
                  onJobs(String(selectedPrinter.printer_id), !!selectedPrinter.current_job_id)
                }
              >
                {uiText("Filter jobs")}
              </Button>
            </>
          ) : (
            <>
              <div className="routing-capacity">
                <div>
                  <span>{uiText("Available capacity")}</span>
                  <strong>
                    {available.length} <small>/ {rows.length}</small>
                  </strong>
                </div>
                <div className="routing-capacity-meter" aria-hidden="true">
                  {rows.map((row) => (
                    <i
                      key={row.printer_id}
                      data-available={available.includes(row)}
                      data-running={running.includes(row)}
                    />
                  ))}
                </div>
                <p>
                  {jobs.length === 0
                    ? uiText("Queue is empty")
                    : jobs.length === 1
                      ? uiText("1 item in queue")
                      : uiText("{count} items in queue", { count: jobs.length })}
                  <span> · </span>
                  {uiText("{count} running", { count: running.length })}
                </p>
              </div>
              <div className="routing-directory" aria-label={uiText("Printers")}>
                {rows.map((row) => (
                  <button
                    key={row.printer_id}
                    type="button"
                    onClick={() => setSelected("printer-" + row.printer_id)}
                  >
                    <Printer3D size={16} />
                    <span>
                      <strong>{row.name}</strong>
                      <small data-printer-status={row.status}>{machineState(row)}</small>
                    </span>
                    <ChevronRight size={15} />
                  </button>
                ))}
              </div>
            </>
          )}
        </aside>
      </div>
      <div className="routing-foot">
        <Cable size={16} />
        {uiText("Drag a queued job’s port to a printer, or select the job and edit its routing.")}
      </div>
    </div>
  );
}
export function FleetRoutingCanvas(props: Props) {
  return (
    <ReactFlowProvider>
      <RoutingMap {...props} />
    </ReactFlowProvider>
  );
}
