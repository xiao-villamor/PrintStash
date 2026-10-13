import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import { useState, useSyncExternalStore } from "react";
import { Link, Navigate, useParams, useSearchParams } from "react-router-dom";
import { useAuth } from "@/lib/auth-context";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { onAuthChange } from "@/lib/auth-store";
import { getSessionVersion } from "@/lib/session-transport";
import { ApiError } from "@/lib/errors";
import { usePrinters } from "@/lib/queries";
import { modelDetailOptions } from "@/features/library/model-detail";
import { multipartDetailOptions } from "@/features/library/multipart";
import { buildListOptions, buildDetailOptions, useBuildCommands } from "@/features/library/builds";
import {
  archiveMultipartBuild,
  confirmBuildResult,
  createMultipartBuild,
  duplicateMultipartBuild,
  queueBuildPart,
  selectBuildRevision,
} from "@/lib/api/multipart-builds";
import type {
  MultipartBuild,
  MultipartBuildPart,
  MultipartBuildAttempt,
} from "@/types/multipart-builds";
import type { PrinterRead } from "@/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { PageContainer } from "@/components/ui/page-container";
import { PageHeader } from "@/components/ui/page-header";

const selectClass = "h-10 w-full rounded-md border border-input bg-background px-3 text-sm";
function denied(error: Error | null) {
  return error instanceof ApiError && [401, 403, 404].includes(error.status);
}
function errorKey(message: string): MessageKey {
  if (/version_conflict|idempotency_conflict/.test(message)) return "build.conflict";
  if (/permission|scope/.test(message)) return "build.denied";
  if (/excess/.test(message)) return "build.excessRequired";
  if (/revision|required|unavailable/.test(message)) return "build.revisionRequired";
  if (/batch_quantity/.test(message)) return "build.batchLimit";
  return "build.failed";
}

export default function MultipartBuildsPage() {
  useUiLocale();
  const { id } = useParams();
  const [search] = useSearchParams();
  const [session] = useState(getSessionVersion);
  const currentSession = useSyncExternalStore(onAuthChange, getSessionVersion);
  const { user, loading } = useAuth();
  if (loading || session !== currentSession) return null;
  if (!user) return <Navigate to="/login" replace />;
  return id ? (
    <BuildDetail key={id} id={Number(id)} />
  ) : (
    <BuildList key={search.get("multipart") ?? "history"} />
  );
}

function BuildList() {
  useUiLocale();
  const { t } = useI18n();
  const [search] = useSearchParams();
  const compositionId = Number(search.get("multipart"));
  const [archived, setArchived] = useState(false);
  const [offset, setOffset] = useState(0);
  const [nameDraft, setName] = useState<string | null>(null);
  const [quantity, setQuantity] = useState(1);
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState<number | null>(null);
  const [error, setError] = useState<MessageKey | null>(null);
  const history = useQuery(buildListOptions(archived, offset));
  const composition = useQuery(multipartDetailOptions(compositionId > 0 ? compositionId : null));
  const commands = useBuildCommands();
  const name = nameDraft ?? composition.data?.name ?? "";
  const rows = denied(history.error) ? [] : (history.data ?? []);
  const readError = history.error ?? composition.error;
  const displayedError = error ?? (readError ? errorKey(readError.message) : null);
  if (created) return <Navigate to={`/builds/${created}`} />;
  return (
    <PageContainer>
      <PageHeader title={t("build.title")} description={t("build.help")} />
      {displayedError && (
        <p role="alert" className="text-destructive">
          {t(displayedError)}
        </p>
      )}
      {compositionId > 0 && !denied(composition.error) && (
        <form
          className="max-w-xl space-y-4 rounded-lg border border-border p-5"
          onSubmit={(event) => {
            event.preventDefault();
            if (!commands.isCurrent()) return;
            setBusy(true);
            setError(null);
            void commands
              .run(() =>
                createMultipartBuild({
                  name,
                  object_quantity: quantity,
                  multipart_model_id: compositionId,
                }),
              )
              .then(
                (build) => {
                  if (build && commands.isCurrent()) setCreated(build.id);
                },
                (reason) => {
                  if (commands.isCurrent())
                    setError(errorKey(reason instanceof Error ? reason.message : ""));
                },
              )
              .finally(() => {
                if (commands.isCurrent()) setBusy(false);
              });
          }}
        >
          <h2 className="font-semibold">{t("build.create")}</h2>
          <label className="block space-y-2">
            {t("build.name")}
            <Input
              required
              maxLength={255}
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </label>
          <label className="block space-y-2">
            {t("build.objects")}
            <Input
              type="number"
              required
              min={1}
              max={10000}
              value={quantity}
              onChange={(event) => setQuantity(Number(event.target.value))}
            />
          </label>
          <p className="text-sm text-muted-foreground">{t("build.snapshotHelp")}</p>
          <Button type="submit" loading={busy}>
            {t("build.create")}
          </Button>
        </form>
      )}
      <div className="flex flex-wrap items-center gap-4">
        <label className="flex items-center gap-2">
          <Checkbox
            checked={archived}
            onChange={(value) => {
              setArchived(value === true);
              setOffset(0);
            }}
          />
          {t("build.showArchived")}
        </label>
        <Button
          variant="outline"
          onClick={() => {
            void history.refetch();
            if (compositionId > 0) void composition.refetch();
          }}
        >
          {t("build.refresh")}
        </Button>
      </div>
      {history.isPending ? (
        <p role="status">{t("build.loading")}</p>
      ) : rows.length === 0 ? (
        history.isSuccess ? (
          <p className="text-muted-foreground">{t("build.empty")}</p>
        ) : null
      ) : (
        <ul className="divide-y divide-border rounded-lg border border-border">
          {rows.map((build) => (
            <li key={build.id} className="flex flex-wrap items-center justify-between gap-3 p-4">
              <Link className="font-medium text-primary underline" to={`/builds/${build.id}`}>
                {build.name}
              </Link>
              <span className="text-sm text-muted-foreground">
                {build.composition_name} ·{" "}
                {t(build.completed ? "build.complete" : "build.inProgress")}
              </span>
            </li>
          ))}
        </ul>
      )}
      <div className="flex gap-3">
        <Button
          variant="outline"
          disabled={offset === 0}
          onClick={() => setOffset(Math.max(0, offset - 50))}
        >
          {t("build.previous")}
        </Button>
        <Button
          variant="outline"
          disabled={rows.length < 50}
          onClick={() => setOffset(offset + 50)}
        >
          {t("build.next")}
        </Button>
      </div>
    </PageContainer>
  );
}

function BuildDetail({ id }: { id: number }) {
  useUiLocale();
  const { t } = useI18n();
  const client = useQueryClient();
  const detail = useQuery(buildDetailOptions(id, client));
  const printerQuery = usePrinters();
  const printers = denied(printerQuery.error)
    ? []
    : (printerQuery.data ?? []).filter((printer) => printer.access.can_print);
  const build = denied(detail.error) ? undefined : detail.data;
  const commands = useBuildCommands();
  const [error, setError] = useState<MessageKey | null>(null);
  const [copyName, setCopyName] = useState("");
  const [copyId, setCopyId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const readError = detail.error ?? printerQuery.error;
  const displayedError = error ?? (readError ? errorKey(readError.message) : null);
  const mutate = async (operation: () => Promise<MultipartBuild>): Promise<boolean> => {
    if (!commands.isCurrent()) return false;
    setBusy(true);
    setError(null);
    try {
      return (await commands.run(operation, id)) !== null;
    } catch (reason) {
      if (commands.isCurrent()) setError(errorKey(reason instanceof Error ? reason.message : ""));
      return false;
    } finally {
      if (commands.isCurrent()) setBusy(false);
    }
  };
  const refresh = () => {
    setError(null);
    void detail.refetch();
    void printerQuery.refetch();
  };
  if (copyId) return <Navigate to={`/builds/${copyId}`} />;
  const canEdit = build?.effective_role === "edit" || build?.effective_role === "admin";
  return (
    <PageContainer>
      <Link className="text-primary underline" to="/builds">
        {t("build.title")}
      </Link>
      {displayedError && (
        <p role="alert" className="text-destructive">
          {t(displayedError)}
        </p>
      )}
      {!build ? (
        detail.isPending ? (
          <p role="status">{t("build.loading")}</p>
        ) : (
          <Button variant="outline" onClick={refresh}>
            {t("build.refresh")}
          </Button>
        )
      ) : (
        <>
          <PageHeader
            title={build.name}
            description={`${build.composition_name} · ${t("build.objectCount", { count: String(build.object_quantity) })}`}
            actions={
              <Button variant="outline" onClick={refresh}>
                {t("build.refresh")}
              </Button>
            }
          />
          <p role="status" className="font-medium">
            {t(
              build.archived_at
                ? "build.archived"
                : build.completed
                  ? "build.complete"
                  : "build.inProgress",
            )}
          </p>
          <p className="text-sm text-muted-foreground">{t("build.resultHelp")}</p>
          {build.parts.map((part) => (
            <BuildPart
              key={part.id}
              build={build}
              part={part}
              printers={printers}
              disabled={busy || !canEdit || !!build.archived_at}
              mutate={mutate}
            />
          ))}
          {canEdit && (
            <section className="space-y-4 border-t border-border pt-5">
              <form
                className="flex max-w-xl flex-wrap items-end gap-3"
                onSubmit={(event) => {
                  event.preventDefault();
                  if (!commands.isCurrent()) return;
                  setBusy(true);
                  setError(null);
                  void commands
                    .run(() => duplicateMultipartBuild(id, copyName))
                    .then(
                      (copy) => {
                        if (copy && commands.isCurrent()) setCopyId(copy.id);
                      },
                      (reason) => {
                        if (commands.isCurrent())
                          setError(errorKey(reason instanceof Error ? reason.message : ""));
                      },
                    )
                    .finally(() => {
                      if (commands.isCurrent()) setBusy(false);
                    });
                }}
              >
                <label className="grow space-y-2">
                  {t("build.copyName")}
                  <Input
                    required
                    maxLength={255}
                    value={copyName}
                    onChange={(event) => setCopyName(event.target.value)}
                  />
                </label>
                <Button loading={busy} type="submit" variant="outline">
                  {t("build.duplicate")}
                </Button>
              </form>
              <p className="text-sm text-muted-foreground">{t("build.duplicateHelp")}</p>
              <Button
                disabled={busy}
                variant="outline"
                onClick={() =>
                  void mutate(() => archiveMultipartBuild(id, build.version, !build.archived_at))
                }
              >
                {t(build.archived_at ? "build.unarchive" : "build.archive")}
              </Button>
            </section>
          )}
        </>
      )}
    </PageContainer>
  );
}

function BuildPart({
  build,
  part,
  printers,
  disabled,
  mutate,
}: {
  build: MultipartBuild;
  part: MultipartBuildPart;
  printers: PrinterRead[];
  disabled: boolean;
  mutate: (operation: () => Promise<MultipartBuild>) => Promise<boolean>;
}) {
  useUiLocale();
  const { t } = useI18n();
  const { user } = useAuth();
  const model = useQuery(modelDetailOptions(part.selected_model_id));
  const files = model.isError
    ? []
    : (model.data?.files ?? []).filter((file) => file.file_type === "gcode");
  const loadError = model.isError;
  const [units, setUnits] = useState(1);
  const [countOverride, setCountOverride] = useState<number | null>(null);
  const [printer, setPrinter] = useState("");
  const [acceptedExcess, setAcceptedExcess] = useState<number | null>(null);
  const count =
    countOverride ??
    Math.max(1, Math.min(100, Math.ceil(part.unreserved_units / Math.max(1, units))));
  const excess = Math.max(count * units - part.unreserved_units, 0);
  const excessAccepted = acceptedExcess === excess;
  return (
    <section
      className="space-y-5 rounded-lg border border-border bg-card p-5"
      aria-label={part.name}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 className="text-lg font-semibold">{part.name}</h2>
        <p className="font-mono">{t("build.missing", { count: String(part.missing_units) })}</p>
      </div>
      <dl className="grid grid-cols-2 gap-4 text-sm sm:grid-cols-4">
        {(
          [
            ["build.required", part.required_units],
            ["build.valid", part.valid_units],
            ["build.active", part.active_units],
            ["build.unreviewed", part.unreviewed_units],
          ] as const
        ).map(([label, value]) => (
          <div key={label}>
            <dt className="text-muted-foreground">{t(label)}</dt>
            <dd className="mt-1 text-lg font-semibold">{value}</dd>
          </div>
        ))}
      </dl>
      <div className="grid gap-4 sm:grid-cols-2">
        <label className="space-y-2">
          {t("build.choice")}
          <select
            className={selectClass}
            disabled={disabled}
            value={part.selected_choice_id ?? ""}
            onChange={(event) => {
              const choiceId = Number(event.target.value);
              void mutate(() =>
                selectBuildRevision(build.id, part.id, {
                  version: build.version,
                  choice_id: choiceId,
                  revision_id: null,
                }),
              );
            }}
          >
            <option value="">{t("build.unavailable")}</option>
            {part.choices.map((choice, index) => (
              <option
                key={choice.choice_id ?? index}
                value={choice.choice_id ?? ""}
                disabled={!choice.available}
              >
                {choice.name ?? t("build.unavailable")}
              </option>
            ))}
          </select>
        </label>
        <label className="space-y-2">
          {t("build.revision")}
          <select
            className={selectClass}
            disabled={disabled || loadError}
            value={part.revision_id ?? ""}
            onChange={(event) => {
              const revisionId = Number(event.target.value) || null;
              void mutate(() =>
                selectBuildRevision(build.id, part.id, {
                  version: build.version,
                  revision_id: revisionId,
                }),
              );
            }}
          >
            <option value="">{t("build.noRevision")}</option>
            {files
              .filter((file) => file.model_id === part.selected_model_id)
              .map((file) => (
                <option key={file.id} value={file.id}>
                  {file.revision_label || uiText("v{value1}", { value1: String(file.version) })} ·{" "}
                  {file.original_filename}
                </option>
              ))}
          </select>
        </label>
      </div>
      {loadError && (
        <div>
          <p role="alert">{t("build.revisionRequired")}</p>
          <Button variant="outline" onClick={() => void model.refetch()}>
            {t("build.refresh")}
          </Button>
        </div>
      )}
      <p className="text-xs text-muted-foreground">{t("build.revisionHelp")}</p>
      <form
        className="space-y-4 border-t border-border pt-4"
        onSubmit={(event) => {
          event.preventDefault();
          void mutate(() =>
            queueBuildPart(build.id, part.id, {
              version: build.version,
              units_per_job: units,
              job_count: count,
              confirm_excess: excessAccepted,
              routing: {
                strategy: printer ? "manual" : "least_busy",
                printer_id: printer ? Number(printer) : undefined,
              },
            }),
          );
        }}
      >
        <div className="grid gap-4 sm:grid-cols-3">
          <label className="space-y-2">
            {t("build.unitsPerFile")}
            <Input
              type="number"
              min={1}
              max={10000}
              required
              disabled={disabled}
              value={units}
              onChange={(event) => {
                setUnits(Number(event.target.value));
                setCountOverride(null);
                setAcceptedExcess(null);
              }}
            />
          </label>
          <label className="space-y-2">
            {t("build.jobs")}
            <Input
              type="number"
              min={1}
              max={1000}
              required
              disabled={disabled}
              value={count}
              onChange={(event) => {
                setCountOverride(Number(event.target.value));
                setAcceptedExcess(null);
              }}
            />
          </label>
          <label className="space-y-2">
            {t("build.printer")}
            <select
              className={selectClass}
              disabled={disabled}
              value={printer}
              onChange={(event) => setPrinter(event.target.value)}
            >
              <option value="">
                {t(user?.is_superuser ? "build.automatic" : "build.choosePrinter")}
              </option>
              {printers.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          </label>
        </div>
        {excess > 0 && (
          <label className="flex items-center gap-2">
            <Checkbox
              disabled={disabled}
              checked={excessAccepted}
              onChange={(value) => setAcceptedExcess(value === true ? excess : null)}
            />
            {t("build.excess", { count: String(excess) })}
          </label>
        )}
        <Button
          type="submit"
          disabled={
            disabled ||
            !part.queueable ||
            part.unreserved_units === 0 ||
            (!user?.is_superuser && !printer) ||
            (excess > 0 && !excessAccepted)
          }
        >
          {t("build.queue")}
        </Button>
      </form>
      {part.attempts.length > 0 && (
        <div className="space-y-3 border-t border-border pt-4">
          <h3 className="font-medium">{t("build.history")}</h3>
          {part.attempts.map((attempt) => (
            <Result
              key={attempt.id}
              buildId={build.id}
              attempt={attempt}
              disabled={disabled}
              mutate={mutate}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function Result({
  buildId,
  attempt,
  disabled,
  mutate,
}: {
  buildId: number;
  attempt: MultipartBuildAttempt;
  disabled: boolean;
  mutate: (operation: () => Promise<MultipartBuild>) => Promise<boolean>;
}) {
  useUiLocale();
  const { t } = useI18n();
  const [draft, setDraft] = useState<{
    base: MultipartBuildAttempt;
    valid: number;
    key: string;
  } | null>(null);
  const valid = draft?.valid ?? attempt.valid_units ?? attempt.suggested_valid_units;
  const changed = draft !== null && draft.base.version !== attempt.version;
  const terminal = ["completed", "failed", "cancelled", "unavailable"].includes(attempt.state);
  const stateKey: MessageKey =
    attempt.state === "completed"
      ? "build.jobCompleted"
      : attempt.state === "failed"
        ? "build.jobFailed"
        : attempt.state === "cancelled"
          ? "build.jobCancelled"
          : attempt.state === "unavailable"
            ? "build.unavailable"
            : "build.jobActive";
  return (
    <form
      className="flex flex-wrap items-end gap-4 rounded-md bg-muted/30 p-3"
      aria-label={t("build.attempt", { id: String(attempt.historical_job_id) })}
      onSubmit={(event) => {
        event.preventDefault();
        if (disabled || changed) return;
        const submitted = draft ?? { base: attempt, valid, key: crypto.randomUUID() };
        setDraft(submitted);
        void mutate(() =>
          confirmBuildResult(buildId, attempt.id, {
            version: submitted.base.version,
            valid_units: submitted.valid,
            idempotency_key: submitted.key,
          }),
        ).then((confirmed) => {
          if (confirmed) setDraft(null);
        });
      }}
    >
      <div className="grow">
        <p className="text-sm font-medium">
          {t("build.attempt", { id: String(attempt.historical_job_id) })} · {t(stateKey)}
        </p>
        <p className="text-xs text-muted-foreground">
          {t("build.planned", { count: String(attempt.planned_units) })}
        </p>
      </div>
      {terminal && (
        <>
          <label className="space-y-1 text-sm">
            {t("build.valid")}
            <Input
              className="w-24"
              type="number"
              min={0}
              max={attempt.planned_units}
              required
              disabled={disabled}
              value={valid}
              onChange={(event) => {
                setDraft({
                  base: draft?.base ?? attempt,
                  valid: Number(event.target.value),
                  key: crypto.randomUUID(),
                });
              }}
            />
          </label>
          {changed && (
            <div className="space-y-2">
              <p role="alert">{t("build.conflict")}</p>
              <p>
                {t("build.valid")}: {attempt.valid_units ?? attempt.suggested_valid_units}
              </p>
              <Button
                type="button"
                variant="outline"
                disabled={disabled}
                onClick={() => setDraft({ base: attempt, valid, key: crypto.randomUUID() })}
              >
                {t("library.reviewLatest")}
              </Button>
            </div>
          )}
          <Button type="submit" variant="outline" disabled={disabled || changed}>
            {t(attempt.valid_units === null ? "build.confirm" : "build.correct")}
          </Button>
        </>
      )}
    </form>
  );
}
