import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useRef, useState, type FormEvent } from "react";

import {
  isSlicedRevision,
  printFormat,
  useSavedRevision,
  type RevisionSelection,
} from "@/features/printers/saved-revision";
import { SavedRevisionPicker } from "@/components/saved-revision-picker";
import { Button } from "@/components/ui/button";
import { inputClasses } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { printerKeys, refreshFleet } from "@/features/printers/queries";
import { sendToPrinter } from "@/lib/api/printers";
import { userMessage } from "@/lib/errors";
import { uiText } from "@/lib/locale";
import { gcodePlatesOptions } from "@/lib/queries/file-plates";
import { getSessionVersion, requireSessionVersion } from "@/lib/session-transport";
import type { PrinterRead } from "@/types";

export function PrinterLibraryUpload({
  printer,
  onClose,
  onUploaded,
}: {
  printer: PrinterRead;
  onClose: () => void;
  onUploaded: (remoteFilename: string) => void;
}) {
  const client = useQueryClient();
  const formId = useId();
  const submitting = useRef(false);
  const [session] = useState(getSessionVersion);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [selection, setSelection] = useState<RevisionSelection | null>(null);
  const [plate, setPlate] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { query: chosen, file } = useSavedRevision(selection, false);
  const sliced = file !== undefined && isSlicedRevision(file);
  const plates = useQuery(gcodePlatesOptions(sliced ? file.id : null));
  const choices = plates.data?.plates ?? [];
  const plateIndex =
    plate !== null && choices.includes(plate) ? plate : choices.length === 1 ? choices[0] : null;
  const canEdit = chosen.data?.effective_role === "edit" || chosen.data?.effective_role === "admin";
  const formatAccepted =
    file !== undefined && printer.capabilities.accepted_print_formats.includes(printFormat(file));
  const canSubmit =
    printer.access.can_print &&
    printer.capabilities.can_upload &&
    formatAccepted &&
    canEdit &&
    !pickerOpen &&
    (!sliced || (plates.isSuccess && plateIndex !== null));

  function close() {
    if (!submitting.current) onClose();
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!file || !canSubmit || submitting.current) return;
    submitting.current = true;
    setBusy(true);
    setError(null);
    try {
      requireSessionVersion(session);
      const receipt = await sendToPrinter(printer.id, {
        file_id: file.id,
        plate_index: sliced ? (plateIndex ?? undefined) : undefined,
        start_print: false,
      });
      requireSessionVersion(session);
      const keys = [printerKeys.files(printer.id), printerKeys.jobs(printer.id)];
      await Promise.all(keys.map((queryKey) => client.cancelQueries({ queryKey, exact: true })));
      requireSessionVersion(session);
      for (const queryKey of keys) {
        void client.invalidateQueries({ queryKey, exact: true });
      }
      refreshFleet(client, session);
      onUploaded(receipt.remote_filename);
    } catch (cause) {
      if (session === getSessionVersion()) setError(userMessage(cause));
    } finally {
      submitting.current = false;
      if (session === getSessionVersion()) setBusy(false);
    }
  }

  return (
    <Modal
      open
      onClose={close}
      title={uiText("Upload from library")}
      footer={
        <>
          <Button variant="outline" size="sm" disabled={busy} onClick={close}>
            {uiText("Cancel")}
          </Button>
          <Button type="submit" form={formId} size="sm" loading={busy} disabled={!canSubmit}>
            {uiText("Upload file")}
          </Button>
        </>
      }
    >
      <form id={formId} onSubmit={(event) => void submit(event)} className="space-y-4">
        <p className="text-sm">
          <span className="text-muted-foreground">{uiText("Printer")} · </span>
          {printer.name}
        </p>
        <fieldset disabled={busy} className="space-y-4">
          <SavedRevisionPicker
            pickerOpen={pickerOpen}
            onOpenChange={setPickerOpen}
            onChoose={(revision) => {
              setSelection(revision);
              setPlate(null);
              setError(null);
            }}
            file={file}
            chosen={chosen}
          />
          {file && !canEdit && (
            <p role="status" className="text-sm text-muted-foreground">
              {uiText("Model edit access is required to upload this Revision.")}
            </p>
          )}
          {file && !formatAccepted && (
            <p role="status" className="text-sm text-muted-foreground">
              {uiText("This printer does not accept the selected file format.")}
            </p>
          )}
          {sliced &&
            formatAccepted &&
            (plates.isError ? (
              <div className="space-y-2">
                <p role="alert" className="text-sm text-destructive">
                  {userMessage(plates.error)}
                </p>
                <Button
                  type="button"
                  variant="outline"
                  size="xs"
                  onClick={() => void plates.refetch()}
                >
                  {uiText("Retry")}
                </Button>
              </div>
            ) : plates.isPending ? (
              <p role="status" className="text-sm">
                {uiText("Loading sliced plates…")}
              </p>
            ) : choices.length === 0 ? (
              <p role="alert" className="text-sm">
                {uiText(
                  "This 3MF has no sliced G-code. Slice it in Bambu Studio or OrcaSlicer, then upload the sliced file.",
                )}
              </p>
            ) : (
              <label className="grid gap-1.5 text-sm">
                {uiText("Sliced plate")}
                <select
                  className={inputClasses}
                  value={plateIndex ?? ""}
                  onChange={(event) =>
                    setPlate(choices.find((index) => String(index) === event.target.value) ?? null)
                  }
                >
                  <option value="" disabled>
                    {uiText("Choose a plate")}
                  </option>
                  {choices.map((index) => {
                    const number = uiText("Plate {value1}", {
                      value1: String(index).padStart(2, "0"),
                    });
                    const name = plates.data?.identities?.find(
                      (identity) => identity.number === index,
                    )?.name;
                    return (
                      <option value={index} key={index}>
                        {name ? `${number} · ${name}` : number}
                      </option>
                    );
                  })}
                </select>
              </label>
            ))}
        </fieldset>
        {busy && (
          <p role="status" className="text-sm">
            {uiText("Uploading file to printer…")}
          </p>
        )}
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
      </form>
    </Modal>
  );
}
