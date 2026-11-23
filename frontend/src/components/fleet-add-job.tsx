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
import { Input } from "@/components/ui/input";
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
  const repeatedPlate = repeatRevision?.plateIndex ?? null;
  const repeatSliced = !!repeatRevision && !!file && isSlicedRevision(file);
  const plates = useQuery(gcodePlatesOptions(repeatSliced ? file.id : null));
  const plateReady = repeatSliced
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
  return (
    <Modal
      open
      onClose={close}
      title={uiText("Add to queue")}
      description={uiText("Adjust the queue without changing the Model’s recommended Revision.")}
      className="max-w-[640px]"
      bodyClassName="max-h-[70vh] overflow-y-auto"
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
      <form id="fleet-add-job-form" onSubmit={(event) => void submit(event)} className="space-y-5">
        <fieldset disabled={busy} className="min-w-0 space-y-5">
          {repeatRevision ? (
            <div className="grid gap-2">
              <p className="text-xs text-muted-foreground">{uiText("Revision")}</p>
              {file && chosen.data ? (
                <p className="break-words text-sm font-semibold">
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
              {repeatSliced && (
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
              pickerOpen={pickerOpen}
              onOpenChange={setPickerOpen}
              onChoose={(revision) => {
                setSelection(revision);
                setError(null);
              }}
              file={file}
              chosen={chosen}
            />
          )}
          {fixedPrinterId === undefined ? (
            <fieldset className="grid min-w-0 gap-3">
              <legend className="float-left w-full p-0 text-sm font-semibold">
                {uiText("Routing")}
              </legend>
              <label className="mb-4 grid gap-2 text-[13px]">
                <span>{uiText("Routing")}</span>
                <select
                  value={effectiveStrategy}
                  onChange={(event) => {
                    const value = event.target.value;
                    if (
                      value === "manual" ||
                      (administrator && (value === "default" || value === "least_busy"))
                    )
                      setStrategy(value);
                    setError(null);
                  }}
                  className={selectClass}
                >
                  <option value="manual">{uiText("Choose printer")}</option>
                  {administrator && (
                    <>
                      <option value="default">{uiText("Default printer")}</option>
                      <option value="least_busy">{uiText("Least busy")}</option>
                    </>
                  )}
                </select>
              </label>
              {effectiveStrategy === "manual" && (
                <label className="grid gap-2 text-[13px]">
                  <span>{uiText("Printer")}</span>
                  <select
                    value={target?.id ?? ""}
                    onChange={(event) => {
                      setPrinterId(Number(event.target.value));
                      setError(null);
                    }}
                    className={selectClass}
                    required
                  >
                    <option value="">{uiText("Choose printer")}</option>
                    {targets.map((printer) => (
                      <option key={printer.id} value={printer.id}>
                        {printer.name}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <label className="grid gap-2 text-[13px]">
                <span>{uiText("Target group")}</span>
                <select
                  value={group}
                  onChange={(event) => {
                    setGroup(event.target.value);
                    setError(null);
                  }}
                  className={selectClass}
                >
                  <option value="">{uiText("All groups")}</option>
                  {groups.map((name) => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))}
                </select>
              </label>
            </fieldset>
          ) : (
            <div className="space-y-2">
              <p className="text-xs text-muted-foreground">{uiText("Printer")}</p>
              <p className="break-words text-sm font-semibold">{fixedPrinter?.name}</p>
              {file && !target && (
                <p role="alert" className="text-xs text-destructive">
                  {uiText("This Revision cannot be queued on this printer.")}
                </p>
              )}
            </div>
          )}
          <fieldset className="grid min-w-0 gap-3">
            <legend className="float-left w-full p-0 text-sm font-semibold">
              {uiText("Queue settings")}
            </legend>
            <div className="mb-4 grid gap-[18px] sm:grid-cols-2">
              <label className="grid gap-2 text-[13px]">
                <span>{uiText("Priority")}</span>
                <select
                  value={priority}
                  onChange={(event) => {
                    const value = event.target.value;
                    if (value === "low" || value === "normal" || value === "rush")
                      setPriority(value);
                  }}
                  className={selectClass}
                >
                  <option value="low">{uiText("Low")}</option>
                  <option value="normal">{uiText("Normal")}</option>
                  <option value="rush">{uiText("Rush")}</option>
                </select>
              </label>
              <label className="grid gap-2 text-[13px]">
                <span>{uiText("Queue position")}</span>
                <Input
                  readOnly
                  value={uiText("End of queue")}
                  className="h-[38px] max-w-[160px] bg-muted/30 text-[13px] text-muted-foreground"
                />
              </label>
            </div>
            <label className="grid gap-2 text-[13px]">
              <span>{uiText("Material compatibility")}</span>
              <select
                value={policy}
                onChange={(event) => {
                  if (event.target.value === "safe" || event.target.value === "allow_mismatch")
                    setPolicy(event.target.value);
                  setError(null);
                }}
                className={selectClass}
              >
                <option value="safe">{uiText("Require compatible material")}</option>
                <option value="allow_mismatch">{uiText("Allow mismatch")}</option>
              </select>
            </label>
          </fieldset>
        </fieldset>
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
      </form>
    </Modal>
  );
}
