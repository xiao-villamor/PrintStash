import { useCallback, useEffect, useState } from "react";
import { Link, Navigate, useParams, useSearchParams } from "react-router-dom";
import { useAuth } from "@/lib/auth-context";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { getModel, getMultipartModel, listPrinters } from "@/lib/api";
import {
  archiveMultipartBuild,
  confirmBuildResult,
  createMultipartBuild,
  duplicateMultipartBuild,
  getMultipartBuild,
  listMultipartBuilds,
  queueBuildPart,
  selectBuildRevision,
} from "@/lib/api/multipart-builds";
import type {
  MultipartBuild,
  MultipartBuildPart,
  MultipartBuildAttempt,
} from "@/types/multipart-builds";
import type { FileRead, PrinterRead } from "@/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { PageContainer } from "@/components/ui/page-container";
import { PageHeader } from "@/components/ui/page-header";

const selectClass = "h-10 w-full rounded-md border border-input bg-background px-3 text-sm";
function errorKey(message: string): MessageKey {
  if (/version_conflict|idempotency_conflict/.test(message)) return "build.conflict";
  if (/permission|scope/.test(message)) return "build.denied";
  if (/excess/.test(message)) return "build.excessRequired";
  if (/revision|required|unavailable/.test(message)) return "build.revisionRequired";
  if (/batch_quantity/.test(message)) return "build.batchLimit";
  return "build.failed";
}

export default function MultipartBuildsPage() {
  const { id } = useParams();
  const { user, loading } = useAuth();
  if (loading) return null;
  if (!user) return <Navigate to="/login" replace />;
  return id ? <BuildDetail key={id} id={Number(id)} /> : <BuildList />;
}

function BuildList() {
  const { t } = useI18n();
  const [search] = useSearchParams();
  const compositionId = Number(search.get("multipart"));
  const [rows, setRows] = useState<MultipartBuild[]>([]);
  const [archived, setArchived] = useState(false);
  const [offset, setOffset] = useState(0);
  const [name, setName] = useState("");
  const [quantity, setQuantity] = useState(1);
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState<number | null>(null);
  const [error, setError] = useState<MessageKey | null>(null);
  const refresh = useCallback(
    () => listMultipartBuilds(archived, offset).then(setRows),
    [archived, offset],
  );
  useEffect(() => {
    void refresh().catch((reason) =>
      setError(errorKey(reason instanceof Error ? reason.message : "")),
    );
  }, [refresh]);
  useEffect(() => {
    if (!compositionId) return;
    let active = true;
    void getMultipartModel(compositionId).then(
      (model) => {
        if (active) setName(model.name);
      },
      (reason) => {
        if (active) setError(errorKey(reason instanceof Error ? reason.message : ""));
      },
    );
    return () => {
      active = false;
    };
  }, [compositionId]);
  if (created) return <Navigate to={`/builds/${created}`} />;
  return (
    <PageContainer>
      <PageHeader title={t("build.title")} description={t("build.help")} />
      {error && (
        <p role="alert" className="text-destructive">
          {t(error)}
        </p>
      )}
      {compositionId > 0 && (
        <form
          className="max-w-xl space-y-4 rounded-lg border border-border p-5"
          onSubmit={(event) => {
            event.preventDefault();
            setBusy(true);
            setError(null);
            void createMultipartBuild({
              name,
              object_quantity: quantity,
              multipart_model_id: compositionId,
            })
              .then(
                (build) => setCreated(build.id),
                (reason) => setError(errorKey(reason instanceof Error ? reason.message : "")),
              )
              .finally(() => setBusy(false));
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
          onClick={() =>
            void refresh().catch((reason) =>
              setError(errorKey(reason instanceof Error ? reason.message : "")),
            )
          }
        >
          {t("build.refresh")}
        </Button>
      </div>
      {rows.length === 0 ? (
        <p className="text-muted-foreground">{t("build.empty")}</p>
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
  const { t } = useI18n();
  const [build, setBuild] = useState<MultipartBuild | null>(null);
  const [printers, setPrinters] = useState<PrinterRead[]>([]);
  const [error, setError] = useState<MessageKey | null>(null);
  const [copyName, setCopyName] = useState("");
  const [copyId, setCopyId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let active = true;
    const refresh = () =>
      void getMultipartBuild(id).then(
        (value) => {
          if (active)
            setBuild((current) => (current && current.version > value.version ? current : value));
        },
        (reason) => {
          if (active) setError(errorKey(reason instanceof Error ? reason.message : ""));
        },
      );
    refresh();
    void listPrinters().then(
      (items) => {
        if (active) setPrinters(items.filter((printer) => printer.access.can_print));
      },
      (reason) => {
        if (active) setError(errorKey(reason instanceof Error ? reason.message : ""));
      },
    );
    const timer = window.setInterval(refresh, 5000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [id]);
  const mutate = async (operation: () => Promise<MultipartBuild>) => {
    setBusy(true);
    setError(null);
    try {
      const value = await operation();
      setBuild((current) => (current && current.version > value.version ? current : value));
    } catch (reason) {
      setError(errorKey(reason instanceof Error ? reason.message : ""));
    } finally {
      setBusy(false);
    }
  };
  if (copyId) return <Navigate to={`/builds/${copyId}`} />;
  const canEdit = build?.effective_role === "edit" || build?.effective_role === "admin";
  return (
    <PageContainer>
      <Link className="text-primary underline" to="/builds">
        {t("build.title")}
      </Link>
      {error && (
        <p role="alert" className="text-destructive">
          {t(error)}
        </p>
      )}
      {!build ? (
        <p role="status">{t("build.loading")}</p>
      ) : (
        <>
          <PageHeader
            title={build.name}
            description={`${build.composition_name} · ${t("build.objectCount", { count: String(build.object_quantity) })}`}
            actions={
              <Button variant="outline" onClick={() => void mutate(() => getMultipartBuild(id))}>
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
                  setBusy(true);
                  setError(null);
                  void duplicateMultipartBuild(id, copyName)
                    .then(
                      (copy) => setCopyId(copy.id),
                      (reason) => setError(errorKey(reason instanceof Error ? reason.message : "")),
                    )
                    .finally(() => setBusy(false));
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
  mutate: (operation: () => Promise<MultipartBuild>) => Promise<void>;
}) {
  const { t } = useI18n();
  const { user } = useAuth();
  const [files, setFiles] = useState<FileRead[]>([]);
  const [loadError, setLoadError] = useState(false);
  const [units, setUnits] = useState(1);
  const [countOverride, setCountOverride] = useState<number | null>(null);
  const [printer, setPrinter] = useState("");
  const [acceptedExcess, setAcceptedExcess] = useState<number | null>(null);
  const count =
    countOverride ??
    Math.max(1, Math.min(100, Math.ceil(part.unreserved_units / Math.max(1, units))));
  const excess = Math.max(count * units - part.unreserved_units, 0);
  const excessAccepted = acceptedExcess === excess;
  useEffect(() => {
    if (!part.selected_model_id) return;
    let active = true;
    void getModel(part.selected_model_id).then(
      (model) => {
        if (active) {
          setFiles(model.files.filter((file) => file.file_type === "gcode"));
          setLoadError(false);
        }
      },
      () => {
        if (active) {
          setFiles([]);
          setLoadError(true);
        }
      },
    );
    return () => {
      active = false;
    };
  }, [part.selected_model_id]);
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
            onChange={(event) =>
              void mutate(() =>
                selectBuildRevision(build.id, part.id, {
                  version: build.version,
                  choice_id: Number(event.target.value),
                  revision_id: null,
                }),
              )
            }
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
            onChange={(event) =>
              void mutate(() =>
                selectBuildRevision(build.id, part.id, {
                  version: build.version,
                  revision_id: Number(event.target.value) || null,
                }),
              )
            }
          >
            <option value="">{t("build.noRevision")}</option>
            {files
              .filter((file) => file.model_id === part.selected_model_id)
              .map((file) => (
                <option key={file.id} value={file.id}>
                  {file.revision_label || `v${file.version}`} · {file.original_filename}
                </option>
              ))}
          </select>
        </label>
      </div>
      {loadError && <p role="alert">{t("build.revisionRequired")}</p>}
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
              key={`${attempt.id}:${attempt.version}:${attempt.state}`}
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
  mutate: (operation: () => Promise<MultipartBuild>) => Promise<void>;
}) {
  const { t } = useI18n();
  const [valid, setValid] = useState(attempt.valid_units ?? attempt.suggested_valid_units);
  const [key, setKey] = useState(() => crypto.randomUUID());
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
        void mutate(() =>
          confirmBuildResult(buildId, attempt.id, {
            version: attempt.version,
            valid_units: valid,
            idempotency_key: key,
          }),
        );
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
                setValid(Number(event.target.value));
                setKey(crypto.randomUUID());
              }}
            />
          </label>
          <Button type="submit" variant="outline" disabled={disabled}>
            {t(attempt.valid_units === null ? "build.confirm" : "build.correct")}
          </Button>
        </>
      )}
    </form>
  );
}
