import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";

import { Button } from "@/components/ui/button";
import { OptionPicker } from "@/components/ui/option-picker";
import { RadioOption } from "@/components/ui/radio-option";
import {
  useSavedRevision,
  revisionLabel,
  isSlicedRevision,
  printFormat,
  type RevisionSelection,
} from "@/features/printers/saved-revision";
import { SavedRevisionPicker } from "@/components/saved-revision-picker";
import {
  QueueCompatibilityField,
  QueueDialogActions,
  QueueDispatchDetails,
  QueuePriorityField,
  QueueRevisionPanel,
  QueueRoutingField,
  QueueTargetGroupField,
} from "@/components/queue-composer";
import { providerLabel } from "@/lib/printer-providers";
import { knownUiText } from "@/lib/locale";
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
  const canSubmit =
    file !== undefined && plateReady && !pickerOpen && (effectiveStrategy !== "manual" || !!target);
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
      dismissible={!busy}
      className="print-task-dialog max-w-240"
      bodyClassName="queue-compose-body flex-1 p-0"
      footer={
        <QueueDialogActions
          busy={busy}
          canSubmit={canSubmit}
          onClose={close}
          submit={{ form: "fleet-add-job-form" }}
        />
      }
    >
      <form
        id="fleet-add-job-form"
        onSubmit={(event) => void submit(event)}
        className="queue-compose min-h-full"
      >
        <fieldset disabled={busy} className="queue-compose-fields">
          <QueueRevisionPanel>
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
          </QueueRevisionPanel>
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
                    <RadioOption
                      key={plate}
                      name="queue-plate"
                      checked={chosenPlate === plate}
                      onChange={() => setChosenPlate(plate)}
                    >
                      {uiText("Plate {plate}", { plate })}
                    </RadioOption>
                  ))}
                </div>
              )}
            </fieldset>
          )}
          {fixedPrinterId === undefined && (
            <>
              {administrator && (
                <QueueRoutingField
                  value={strategy}
                  onChange={(value) => {
                    setStrategy(value);
                    setError(null);
                  }}
                />
              )}
              <QueueTargetGroupField
                groups={groups}
                value={group}
                onChange={(value) => {
                  setGroup(value);
                  setPrinterId(null);
                  setError(null);
                }}
              />
              {effectiveStrategy === "manual" && (
                <OptionPicker
                  label={uiText("Destination")}
                  searchPlaceholder={uiText("Search printers")}
                  placeholder={uiText(targets.length ? "Choose printer" : "No compatible printers")}
                  value={target?.id ?? null}
                  options={targets.map((printer) => ({
                    value: printer.id,
                    label: printer.name,
                    description: providerLabel(printer),
                  }))}
                  onChange={(value) => {
                    setPrinterId(value);
                    setError(null);
                  }}
                />
              )}
            </>
          )}
          <QueuePriorityField value={priority} onChange={setPriority} />
          <QueueCompatibilityField
            checked={policy === "allow_mismatch"}
            onChange={(checked) => {
              setPolicy(checked ? "allow_mismatch" : "safe");
              setError(null);
            }}
          />
        </fieldset>
        <QueueDispatchDetails
          model={file ? chosen.data?.name : undefined}
          revision={file ? revisionLabel(file) : undefined}
          destination={fixedPrinter?.name ?? destination}
          destinationStatus={
            target && effectiveStrategy === "manual"
              ? target.drain_mode
                ? uiText("Draining")
                : knownUiText(target.status)
              : undefined
          }
          priority={priority}
          policy={policy}
          plate={repeatedPlate}
        >
          {fixedPrinterId !== undefined && file && !target && (
            <p role="alert">{uiText("This Revision cannot be queued on this printer.")}</p>
          )}
        </QueueDispatchDetails>
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
      </form>
    </Modal>
  );
}
