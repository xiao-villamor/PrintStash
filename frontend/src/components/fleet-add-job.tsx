import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronLeft } from "lucide-react";
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";

import { Button } from "@/components/ui/button";
import { DropdownMenu } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { modelDetailOptions } from "@/features/library/model-detail";
import { refreshFleet } from "@/features/printers/queries";
import { checkFleetCompatibility, enqueueFleetJob } from "@/lib/api/fleet";
import { onAuthChange } from "@/lib/auth-store";
import { useAuth } from "@/lib/auth-context";
import { userMessage } from "@/lib/errors";
import { useUiLocale } from "@/lib/i18n";
import { uiText } from "@/lib/locale";
import { useModelList, type ModelListFilters } from "@/lib/queries";
import { getSessionVersion, requireSessionVersion } from "@/lib/session-transport";
import { toast } from "@/lib/toast";
import type {
  CompatibilityPolicy,
  FileRead,
  JobPriority,
  ModelListItem,
  PrinterRead,
  QueueJobCreate,
  RoutingStrategy,
} from "@/types";

const selectClass =
  "h-[38px] w-full border border-input bg-card px-3 text-[13px] text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50";

/** Fleet owns the resulting queue; Library Query owners supply selectable revisions. */
export function FleetAddJobDialog({
  open,
  onClose,
  printers,
}: {
  open: boolean;
  onClose: () => void;
  printers: readonly PrinterRead[];
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
    />
  );
}

function AddJobForm({
  session,
  onClose,
  printers,
  administrator,
}: {
  session: number;
  onClose: () => void;
  printers: readonly PrinterRead[];
  administrator: boolean;
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
  const [query, setQuery] = useState("");
  const [settledQuery, setSettledQuery] = useState("");
  useEffect(() => {
    const timer = window.setTimeout(() => setSettledQuery(query), 250);
    return () => window.clearTimeout(timer);
  }, [query]);
  const filters = useMemo<ModelListFilters>(
    () => ({ q: settledQuery.trim() || undefined, file_type: ["gcode"], direct: false }),
    [settledQuery],
  );
  const models = useModelList(filters, 24, "date-desc", pickerOpen);
  const rows = models.data?.pages.flatMap((page) => page.items) ?? [];
  const [browsedModel, setBrowsedModel] = useState<ModelListItem | null>(null);
  const [selection, setSelection] = useState<{ modelId: number; fileId: number } | null>(null);
  const browsed = useQuery({
    ...modelDetailOptions(browsedModel?.id ?? null),
    enabled: pickerOpen && browsedModel !== null,
  });
  const chosen = useQuery({
    ...modelDetailOptions(selection?.modelId ?? null),
    enabled: selection !== null,
  });
  const revisions = browsed.isError
    ? []
    : (browsed.data?.files.filter((file) => file.file_type === "gcode") ?? []);
  const file = chosen.isError
    ? undefined
    : chosen.data?.files.find(
        (value) => value.id === selection?.fileId && value.file_type === "gcode",
      );
  const [strategy, setStrategy] = useState<RoutingStrategy>("manual");
  const effectiveStrategy = administrator ? strategy : "manual";
  const [printerId, setPrinterId] = useState<number | null>(null);
  const [group, setGroup] = useState("");
  const [priority, setPriority] = useState<JobPriority>("normal");
  const [policy, setPolicy] = useState<CompatibilityPolicy>("safe");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const format = file?.original_filename.toLowerCase().endsWith(".bgcode")
    ? "bgcode_binary"
    : "gcode_text";
  const printable = printers.filter((printer) => printer.access.can_print);
  const groups = Array.from(
    new Set(printable.flatMap((printer) => (printer.group ? [printer.group] : []))),
  ).sort();
  const targets = printable.filter(
    (printer) =>
      (!group || printer.group === group) &&
      (!file || printer.capabilities.accepted_print_formats.includes(format)),
  );
  const target =
    printerId === null
      ? (targets.find((printer) => printer.is_default) ?? targets[0])
      : targets.find((printer) => printer.id === printerId);
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
  function revisionLabel(revision: FileRead) {
    return (
      revision.revision_label ||
      `${uiText("Revision")} ${revision.gcode_revision_number ?? revision.version}`
    );
  }
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (submitting.current || !file || pickerOpen || (effectiveStrategy === "manual" && !target))
      return;
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
            disabled={!file || pickerOpen || (effectiveStrategy === "manual" && !target)}
          >
            {uiText("Save job")}
          </Button>
        </>
      }
    >
      <form id="fleet-add-job-form" onSubmit={(event) => void submit(event)} className="space-y-5">
        <fieldset disabled={busy} className="min-w-0 space-y-5">
          <div className="grid gap-2">
            <label className="text-[13px]" htmlFor="fleet-queue-revision">
              {uiText("Revision")}
            </label>
            <DropdownMenu
              open={pickerOpen}
              onOpenChange={(value) => {
                setPickerOpen(value);
                if (!value) setBrowsedModel(null);
              }}
              portal
              align="start"
              role="dialog"
              aria-label={uiText("Revision")}
              className="w-full"
              contentClassName="w-[36rem] max-w-[calc(100vw-2rem)] overflow-hidden border border-border bg-popover text-popover-foreground shadow-lg"
              trigger={
                <Button
                  id="fleet-queue-revision"
                  type="button"
                  variant="outline"
                  className="h-[38px] w-full justify-between border-input text-left text-[13px] font-normal"
                  aria-haspopup="dialog"
                  aria-expanded={pickerOpen}
                  onClick={() => setPickerOpen(!pickerOpen)}
                >
                  <span className="min-w-0 truncate">
                    {file && chosen.data
                      ? `${chosen.data.name} · ${revisionLabel(file)}`
                      : uiText("Select")}
                  </span>
                  <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
                </Button>
              }
            >
              {browsedModel ? (
                <div className="max-h-[60vh] overflow-y-auto p-3">
                  <Button
                    type="button"
                    variant="ghost"
                    size="xs"
                    onClick={() => setBrowsedModel(null)}
                  >
                    <ChevronLeft className="h-3.5 w-3.5" />
                    {uiText("Models")}
                  </Button>
                  <h3 className="my-3 break-words text-sm font-semibold">
                    {browsed.data?.name ?? browsedModel.name}
                  </h3>
                  {browsed.isPending ? (
                    <p role="status" className="py-4 text-xs text-muted-foreground">
                      {uiText("Loading...")}
                    </p>
                  ) : browsed.isError ? (
                    <div role="alert" className="space-y-2 text-sm">
                      <p>{userMessage(browsed.error)}</p>
                      <Button
                        type="button"
                        size="xs"
                        variant="outline"
                        onClick={() => void browsed.refetch()}
                      >
                        {uiText("Retry")}
                      </Button>
                    </div>
                  ) : revisions.length ? (
                    <div className="divide-y divide-border">
                      {revisions.map((revision) => (
                        <button
                          key={revision.id}
                          type="button"
                          className="block w-full px-2 py-3 text-left text-sm hover:bg-popover-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                          onClick={() => {
                            setSelection({ modelId: browsedModel.id, fileId: revision.id });
                            setBrowsedModel(null);
                            setPickerOpen(false);
                            setError(null);
                          }}
                        >
                          <span className="font-medium">
                            {revisionLabel(revision)}
                            {revision.is_recommended ? ` ★ ${uiText("Recommended")}` : ""}
                          </span>
                          <span className="mt-1 block break-words text-xs text-muted-foreground">
                            {revision.original_filename}
                          </span>
                        </button>
                      ))}
                    </div>
                  ) : (
                    <p className="py-4 text-sm text-muted-foreground">
                      {uiText("No G-code revision yet")}
                    </p>
                  )}
                </div>
              ) : (
                <>
                  <div className="border-b border-border p-3">
                    <Input
                      autoFocus
                      value={query}
                      onChange={(event) => setQuery(event.target.value)}
                      maxLength={512}
                      aria-label={uiText("Search models")}
                      placeholder={uiText("Search models")}
                    />
                  </div>
                  <div className="max-h-64 overflow-y-auto p-1">
                    {models.isError ? (
                      <div role="alert" className="space-y-2 p-3 text-sm">
                        <p>{userMessage(models.error)}</p>
                        <Button
                          type="button"
                          size="xs"
                          variant="outline"
                          onClick={() => void models.refetch()}
                        >
                          {uiText("Retry")}
                        </Button>
                      </div>
                    ) : models.isPending ? (
                      <p role="status" className="p-3 text-xs text-muted-foreground">
                        {uiText("Loading...")}
                      </p>
                    ) : rows.length ? (
                      rows.map((model) => (
                        <button
                          key={model.id}
                          type="button"
                          disabled={models.isPlaceholderData}
                          onClick={() => setBrowsedModel(model)}
                          className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm hover:bg-popover-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
                        >
                          <span className="min-w-0 break-words">{model.name}</span>
                          <ChevronDown className="h-3.5 w-3.5 shrink-0 -rotate-90 text-muted-foreground" />
                        </button>
                      ))
                    ) : (
                      <p className="p-3 text-sm text-muted-foreground">
                        {uiText("No models found")}
                      </p>
                    )}
                  </div>
                  {models.hasNextPage && (
                    <div className="border-t border-border p-2">
                      <Button
                        type="button"
                        variant="outline"
                        size="xs"
                        className="w-full"
                        loading={models.isFetchingNextPage}
                        onClick={() => void models.fetchNextPage()}
                      >
                        {uiText("Load more")}
                      </Button>
                    </div>
                  )}
                </>
              )}
            </DropdownMenu>
            {chosen.isError && (
              <p role="alert" className="text-xs text-destructive">
                {userMessage(chosen.error)}
              </p>
            )}
          </div>
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
