import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";

import { Button } from "@/components/ui/button";
import {
  useSavedRevision,
  revisionLabel,
  isSlicedRevision,
  printFormat,
  type RevisionSelection,
} from "@/features/printers/saved-revision";
import { SavedRevisionPicker } from "@/components/saved-revision-picker";
import { Check, FileBox, GitBranch, Layers3, Printer, ShieldCheck, Star } from "lucide-react";
import { providerLabel } from "@/lib/printer-providers";
import { knownUiText } from "@/lib/locale";
import "./fleet-queue-workspace.css";
import { Modal } from "@/components/ui/modal";
import { refreshFleet } from "@/features/printers/queries";
import { checkFleetCompatibility, enqueueFleetJob } from "@/lib/api/fleet";
import { onAuthChange } from "@/lib/auth-store";
import { useAuth } from "@/lib/auth-context";
import { userMessage } from "@/lib/errors";
import { useUiLocale } from "@/lib/i18n";
import { uiText } from "@/lib/locale";
import { gcodePlatesOptions } from "@/lib/queries/file-plates";
import { getSessionVersion, requireSessionVersion } from "@/lib/session-transport";
import { toast } from "@/lib/toast";
import type {
  CompatibilityPolicy,
  JobPriority,
  PrinterRead,
  PrintJobRead,
  QueueJobCreate,
  RoutingStrategy,
} from "@/types";

const selectClass =
  "h-[38px] w-full border border-input bg-card px-3 text-[13px] text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50";

interface RepeatRevision extends RevisionSelection {
  plateIndex: number | null;
}

export function FleetQueueAgainAction({
  job,
  printer,
}: {
  job: PrintJobRead;
  printer: PrinterRead;
}) {
  const { user } = useAuth();
  if (
    !user ||
    !printer.access.can_print ||
    job.printer_id !== printer.id ||
    job.state !== "completed" ||
    // Upload-only transfers also report completed, without a print start.
    job.started_at === null ||
    job.source !== "vault" ||
    job.artifact_evidence !== "vault" ||
    // Build attempts are fleet batch copies with separate unit accounting.
    job.batch_id != null ||
    job.model_id <= 0 ||
    job.file_id <= 0
  )
    return null;

  return (
    <QueueAgainButton
      printer={printer}
      revision={{ modelId: job.model_id, fileId: job.file_id, plateIndex: job.plate_index }}
    />
  );
}

function QueueAgainButton({
  printer,
  revision,
}: {
  printer: PrinterRead;
  revision: RepeatRevision;
}) {
  const session = useSyncExternalStore(onAuthChange, getSessionVersion, getSessionVersion);
  const [openedInSession, setOpenedInSession] = useState<number | null>(null);
  const { file } = useSavedRevision(revision, true);
  const canRepeat =
    file &&
    isSlicedRevision(file) === (revision.plateIndex !== null) &&
    printer.capabilities.accepted_print_formats.includes(printFormat(file));

  return (
    <>
      <Button
        size="xs"
        variant="outline"
        disabled={!canRepeat}
        onClick={() => setOpenedInSession(session)}
      >
        {uiText("Queue again")}
      </Button>
      {openedInSession === session && (
        <FleetAddJobDialog
          open
          onClose={() => setOpenedInSession(null)}
          printers={[printer]}
          fixedPrinterId={printer.id}
          repeatRevision={revision}
        />
      )}
    </>
  );
}

/** Fleet owns the resulting queue; Library Query owners supply selectable revisions. */
export function FleetAddJobDialog({
  open,
  onClose,
  printers,
  fixedPrinterId,
  repeatRevision,
}: {
  open: boolean;
  onClose: () => void;
  printers: readonly PrinterRead[];
  fixedPrinterId?: number;
  repeatRevision?: RepeatRevision;
}) {
  const { user } = useAuth();
  const session = useSyncExternalStore(onAuthChange, getSessionVersion, getSessionVersion);
  if (!open || !user) return null;
  return (
    <AddJobForm
      key={session}
      session={session}
      onClose={onClose}
      printers={printers}
      administrator={user.is_superuser}
      fixedPrinterId={fixedPrinterId}
      repeatRevision={repeatRevision}
    />
  );
}

function AddJobForm({
  session,
  onClose,
  printers,
  administrator,
  fixedPrinterId,
  repeatRevision,
}: {
  session: number;
  onClose: () => void;
  printers: readonly PrinterRead[];
  administrator: boolean;
  fixedPrinterId?: number;
  repeatRevision?: RepeatRevision;
}) {
  useUiLocale();
  const client = useQueryClient();
  const alive = useRef(true);
  const submitting = useRef(false);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [selection, setSelection] = useState<RevisionSelection | null>(null);
  const { query: chosen, file } = useSavedRevision(repeatRevision ?? selection, !!repeatRevision);
  const [chosenPlate, setChosenPlate] = useState<number | null>(null);
  const repeatedPlate = repeatRevision ? repeatRevision.plateIndex : chosenPlate;
  const sliced = !!file && isSlicedRevision(file);
  const plates = useQuery(gcodePlatesOptions(sliced ? file.id : null));
  const plateReady = sliced
    ? repeatedPlate !== null && plates.isSuccess && plates.data.plates.includes(repeatedPlate)
    : repeatedPlate === null;
  const [strategy, setStrategy] = useState<RoutingStrategy>("manual");
  const fixedPrinter = printers.find((printer) => printer.id === fixedPrinterId);
  const effectiveStrategy = administrator && fixedPrinterId === undefined ? strategy : "manual";
  const [printerId, setPrinterId] = useState<number | null>(null);
  const [group, setGroup] = useState("");
  const [priority, setPriority] = useState<JobPriority>("normal");
  const [policy, setPolicy] = useState<CompatibilityPolicy>("safe");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const printable = printers.filter((printer) => printer.access.can_print);
  const groups = Array.from(
    new Set(printable.flatMap((printer) => (printer.group ? [printer.group] : []))),
  ).sort();
  const targets = printable.filter(
    (printer) =>
      (!group || printer.group === group) &&
      (!file || printer.capabilities.accepted_print_formats.includes(printFormat(file))),
  );
  const requestedPrinterId = fixedPrinterId ?? printerId;
  const target =
    requestedPrinterId === null
      ? (targets.find((printer) => printer.is_default) ?? targets[0])
      : targets.find((printer) => printer.id === requestedPrinterId);
  const enqueue = useMutation({
    retry: false,
    mutationFn: async (command: { payload: QueueJobCreate; session: number }) => {
      requireSessionVersion(command.session);
      const result = await enqueueFleetJob(command.payload);
      requireSessionVersion(command.session);
      return result;
    },
    onSuccess: (_result, command) => refreshFleet(client, command.session),
  });
  function close() {
    if (!submitting.current) onClose();
  }
  const canSubmit = file && plateReady && !pickerOpen && (effectiveStrategy !== "manual" || target);
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (submitting.current || !canSubmit) return;
    submitting.current = true;
    setBusy(true);
    setError(null);
    try {
      requireSessionVersion(session);
      if (effectiveStrategy === "manual" && target && policy === "safe") {
        const report = await checkFleetCompatibility(file.id, [target.id]);
        requireSessionVersion(session);
        const assessment = report.printers.find((printer) => printer.printer_id === target.id);
        if (report.file_id !== file.id || !assessment)
          throw new Error(uiText("Compatibility check failed"));
        if (assessment.verdict === "mismatch") {
          setError(
            uiText(
              "The selected G-code material or nozzle does not match the printer’s known loaded state. Continuing records an audited override. Color differences alone do not block printing.",
            ),
          );
          return;
        }
      }
      await enqueue.mutateAsync({
        session,
        payload: {
          file_id: file.id,
          plate_index: repeatedPlate ?? undefined,
          strategy: effectiveStrategy,
          printer_id: effectiveStrategy === "manual" ? target?.id : undefined,
          target_group: group || null,
          priority,
          compatibility_policy: policy,
        },
      });
      requireSessionVersion(session);
      if (alive.current) {
        toast.success(uiText("Added to fleet queue"));
        onClose();
      }
    } catch (cause) {
      if (session === getSessionVersion() && alive.current) setError(userMessage(cause));
    } finally {
      submitting.current = false;
      if (session === getSessionVersion() && alive.current) setBusy(false);
    }
  }
  const destination =
    effectiveStrategy === "manual"
      ? (target?.name ?? uiText("Choose printer"))
      : effectiveStrategy === "default"
        ? uiText("Default printer")
        : uiText("Least busy");
  return (
    <Modal
      open
      onClose={close}
      title={uiText("Add to queue")}
      className="queue-compose-dialog"
      bodyClassName="queue-compose-body"
      footer={
        <>
          <Button type="button" variant="outline" size="sm" disabled={busy} onClick={close}>
            {uiText("Cancel")}
          </Button>
          <Button
            form="fleet-add-job-form"
            type="submit"
            size="sm"
            loading={busy}
            disabled={!canSubmit}
          >
            {uiText("Save job")}
          </Button>
        </>
      }
    >
      <form
        id="fleet-add-job-form"
        onSubmit={(event) => void submit(event)}
        className="queue-compose"
      >
        <fieldset disabled={busy} className="queue-compose-fields">
          <div className="queue-revision-choice">
            <FileBox size={23} />
            <div>
              {repeatRevision ? (
                <div className="grid gap-2">
                  <p className="text-xs text-muted-foreground">{uiText("Revision")}</p>
                  {file && chosen.data ? (
                    <p className="wrap-anywhere text-sm font-semibold">
                      {chosen.data.name} · {revisionLabel(file)}
                    </p>
                  ) : chosen.isFetching ? (
                    <p role="status" className="text-sm">
                      {uiText("Loading...")}
                    </p>
                  ) : (
                    <p role="alert" className="text-xs text-destructive">
                      {chosen.isError
                        ? userMessage(chosen.error)
                        : uiText("This Revision cannot be queued on this printer.")}
                    </p>
                  )}
                  {sliced && (
                    <>
                      <p className="text-sm">
                        {uiText("Sliced plate")}: {repeatedPlate}
                      </p>
                      {plates.isPending ? (
                        <p role="status" className="text-sm">
                          {uiText("Loading sliced plates…")}
                        </p>
                      ) : (
                        !plateReady && (
                          <p role="alert" className="text-xs text-destructive">
                            {plates.isError
                              ? userMessage(plates.error)
                              : uiText("This Revision cannot be queued on this printer.")}
                          </p>
                        )
                      )}
                    </>
                  )}
                </div>
              ) : (
                <SavedRevisionPicker
                  emptyLabel={uiText("Browse saved Revisions")}
                  pickerOpen={pickerOpen}
                  onOpenChange={setPickerOpen}
                  onChoose={(revision) => {
                    setSelection(revision);
                    setChosenPlate(null);
                    setError(null);
                  }}
                  file={file}
                  chosen={chosen}
                />
              )}
            </div>
          </div>
          {!repeatRevision && sliced && (
            <fieldset className="queue-compose-group">
              <legend>{uiText("Sliced plate")}</legend>
              {plates.isError ? (
                <div role="alert">
                  <span>{userMessage(plates.error)}</span>{" "}
                  <Button
                    type="button"
                    size="xs"
                    variant="outline"
                    onClick={() => void plates.refetch()}
                  >
                    {uiText("Retry")}
                  </Button>
                </div>
              ) : plates.isPending ? (
                <p role="status">{uiText("Loading sliced plates…")}</p>
              ) : (
                <div className="queue-options queue-plates">
                  {plates.data.plates.map((plate) => (
                    <label key={plate}>
                      <input
                        type="radio"
                        name="queue-plate"
                        checked={chosenPlate === plate}
                        onChange={() => setChosenPlate(plate)}
                      />
                      <span>{uiText("Plate {plate}", { plate })}</span>
                    </label>
                  ))}
                </div>
              )}
            </fieldset>
          )}
          {fixedPrinterId === undefined && (
            <>
              {administrator && (
                <fieldset className="queue-compose-group">
                  <legend>{uiText("Routing")}</legend>
                  <div className="queue-options">
                    {(["manual", "default", "least_busy"] as const).map((value) => {
                      const Icon =
                        value === "manual" ? Printer : value === "default" ? Star : GitBranch;
                      const label =
                        value === "manual"
                          ? uiText("Choose printer")
                          : value === "default"
                            ? uiText("Default printer")
                            : uiText("Least busy");
                      return (
                        <label key={value}>
                          <input
                            type="radio"
                            name="queue-routing"
                            checked={strategy === value}
                            onChange={() => {
                              setStrategy(value);
                              setError(null);
                            }}
                          />
                          <span>
                            <Icon size={15} />
                            {label}
                          </span>
                        </label>
                      );
                    })}
                  </div>
                </fieldset>
              )}
              {!!groups.length && (
                <label className="queue-group-filter">
                  {uiText("Target group")}
                  <select
                    className={selectClass}
                    value={group}
                    onChange={(e) => {
                      setGroup(e.target.value);
                      setPrinterId(null);
                      setError(null);
                    }}
                  >
                    <option value="">{uiText("All groups")}</option>
                    {groups.map((name) => (
                      <option key={name}>{name}</option>
                    ))}
                  </select>
                </label>
              )}
              {effectiveStrategy === "manual" && (
                <fieldset className="queue-compose-group">
                  <legend>{uiText("Destination")}</legend>
                  <div className="queue-printer-choices">
                    {targets.map((printer) => (
                      <label key={printer.id}>
                        <input
                          type="radio"
                          name="queue-destination"
                          checked={target?.id === printer.id}
                          onChange={() => {
                            setPrinterId(printer.id);
                            setError(null);
                          }}
                        />
                        <span>
                          <Printer size={17} />
                          <span>
                            <b>{printer.name}</b>
                            <small>{providerLabel(printer)}</small>
                          </span>
                          <Check size={15} />
                        </span>
                      </label>
                    ))}
                    {!targets.length && <p>{uiText("No compatible printers")}</p>}
                  </div>
                </fieldset>
              )}
            </>
          )}
          <fieldset className="queue-compose-group">
            <legend>{uiText("Priority")}</legend>
            <div className="queue-options queue-priority">
              {(["low", "normal", "rush"] as const).map((value) => (
                <label key={value}>
                  <input
                    type="radio"
                    name="queue-priority"
                    checked={priority === value}
                    onChange={() => setPriority(value)}
                  />
                  <span>
                    {value === "low"
                      ? uiText("Low")
                      : value === "normal"
                        ? uiText("Normal")
                        : uiText("Rush")}
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
          <label className="queue-material-policy">
            <input
              type="checkbox"
              checked={policy === "allow_mismatch"}
              onChange={(e) => {
                setPolicy(e.target.checked ? "allow_mismatch" : "safe");
                setError(null);
              }}
            />
            {uiText("Allow material or nozzle mismatch")}
          </label>
        </fieldset>
        <aside className="queue-ticket" aria-label={uiText("Dispatch details")}>
          <h3>
            <Layers3 size={17} />
            {uiText("Dispatch details")}
          </h3>
          <div className="queue-ticket-route">
            <FileBox size={15} />
            <div>
              <small>{uiText("Revision")}</small>
              <b>{chosen.data && file ? chosen.data.name : uiText("Select a saved Revision")}</b>
              {file && <small>{revisionLabel(file)}</small>}
            </div>
            <Printer size={15} />
            <div>
              <small>{uiText("Destination")}</small>
              <b>{fixedPrinter?.name ?? destination}</b>
              {target && effectiveStrategy === "manual" && (
                <small>{target.drain_mode ? uiText("Draining") : knownUiText(target.status)}</small>
              )}
            </div>
          </div>
          <dl>
            <dt>{uiText("Queue position")}</dt>
            <dd>{uiText("End of queue")}</dd>
            <dt>{uiText("Priority")}</dt>
            <dd>
              {priority === "rush"
                ? uiText("Rush")
                : priority === "low"
                  ? uiText("Low")
                  : uiText("Normal")}
            </dd>
            {repeatedPlate !== null && (
              <>
                <dt>{uiText("Sliced plate")}</dt>
                <dd>{repeatedPlate}</dd>
              </>
            )}
          </dl>
          <p>
            <ShieldCheck size={16} />
            {policy === "safe" ? uiText("Require compatible material") : uiText("Allow mismatch")}
          </p>
          {fixedPrinterId !== undefined && file && !target && (
            <p role="alert">{uiText("This Revision cannot be queued on this printer.")}</p>
          )}
        </aside>
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
      </form>
    </Modal>
  );
}
