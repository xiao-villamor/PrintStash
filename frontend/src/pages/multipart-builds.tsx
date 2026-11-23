import {
  ArrowLeft,
  ArrowRight,
  Boxes,
  CheckCircle2,
  Clock3,
  History,
  Plus,
  RefreshCw,
} from "lucide-react";
import { Card } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import { Cover, Count } from "@/components/multipart-model-presentation";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import { useState, useSyncExternalStore } from "react";
import { Navigate, useParams } from "@tanstack/react-router";
import { Link } from "@/lib/link";
import { useSearchParams } from "@/lib/navigation";
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

export default function MultipartBuildsPage(): React.ReactNode {
  useUiLocale();
  const { id } = useParams({ strict: false });
  const search = useSearchParams();
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
  const search = useSearchParams();
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
  if (created) return <Navigate to="/builds/$id" params={{ id: String(created) }} />;
  return (
    <PageContainer>
      <PageHeader
        title={t(compositionId > 0 ? "build.create" : "build.history")}
        description={
          <>
            {t("build.help")}
            {compositionId <= 0 && <span className="block">{t("build.startHelp")}</span>}
          </>
        }
        actions={
          compositionId > 0 ? (
            <Button asChild variant="outline">
              <Link href={`/multipart-models/${compositionId}`}>
                <ArrowLeft className="h-4 w-4" />
                {t("build.backToSet")}
              </Link>
            </Button>
          ) : (
            <Button asChild>
              <Link href="/?type=multipart">
                <Plus className="h-4 w-4" />
                {t("build.chooseMultipart")}
              </Link>
            </Button>
          )
        }
      />
      {displayedError && (
        <p
          role="alert"
          className="rounded-md border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive"
        >
          {t(displayedError)}
        </p>
      )}
      <Card className="overflow-hidden">
        {compositionId > 0 && !denied(composition.error) && (
          <form
            className="border-b border-border"
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
            <div className="flex items-center gap-3 border-b px-4 py-4 sm:px-5">
              <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-muted">
                <Boxes className="h-4 w-4" aria-hidden />
              </div>
              <div className="min-w-0">
                <h2 className="text-sm font-semibold">{t("build.plan")}</h2>
                <p className="text-xs text-muted-foreground">{t("build.planHelp")}</p>
              </div>
            </div>
            <div className="grid lg:grid-cols-2">
              <div className="space-y-5 p-4 sm:p-5">
                <label className="block space-y-2 text-sm font-medium">
                  {t("build.name")}
                  <Input
                    required
                    maxLength={255}
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                  />
                </label>
                <label className="block space-y-2 text-sm font-medium">
                  {t("build.objects")}
                  <Input
                    type="number"
                    required
                    min={1}
                    max={10000}
                    value={quantity}
                    onChange={(event) => setQuantity(Number(event.target.value))}
                    className="max-w-40"
                  />
                </label>
                <p className="text-sm leading-relaxed text-muted-foreground">
                  {t("build.snapshotHelp")}
                </p>
              </div>
              <section
                aria-label={t("build.sourceSet")}
                className="border-t bg-muted/30 p-4 sm:p-5 lg:border-l lg:border-t-0"
              >
                <h3 className="mb-3 text-xs font-semibold text-muted-foreground">
                  {t("build.sourceSet")}
                </h3>
                {composition.data ? (
                  <>
                    <div className="flex min-w-0 items-center gap-4">
                      <div className="h-20 w-20 shrink-0 overflow-hidden rounded-md border border-border bg-muted">
                        <Cover src={composition.data.cover_thumbnail_url} alt="" />
                      </div>
                      <div className="min-w-0">
                        <Link
                          href={`/multipart-models/${compositionId}`}
                          className="break-words text-sm font-semibold hover:underline"
                        >
                          {composition.data.name}
                        </Link>
                        <p className="mt-1 text-xs text-muted-foreground">
                          <Count
                            count={composition.data.part_count}
                            one={t("multipart.part")}
                            many={t("multipart.parts")}
                          />
                          {" · "}
                          <Count
                            count={composition.data.model_count}
                            one={t("multipart.model")}
                            many={t("multipart.models")}
                          />
                        </p>
                      </div>
                    </div>
                    {composition.data.parts.length > 0 && (
                      <ul className="mt-4 max-h-48 divide-y divide-border overflow-y-auto">
                        {composition.data.parts.map((part) => (
                          <li
                            key={part.id}
                            className="flex items-center justify-between gap-3 py-2 text-sm"
                          >
                            <span className="min-w-0 break-words">{part.name}</span>
                            <span className="shrink-0 font-mono text-xs text-muted-foreground">
                              × {part.quantity}
                            </span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </>
                ) : (
                  <div role="status" aria-label={t("build.loading")} className="space-y-3">
                    <Skeleton className="h-20 w-20" />
                    <Skeleton className="h-4 w-48" />
                  </div>
                )}
              </section>
            </div>
            <div className="flex flex-wrap items-center justify-end gap-2 border-t bg-muted/30 p-3">
              <Button asChild variant="outline">
                <Link href={`/multipart-models/${compositionId}`}>{uiText("Cancel")}</Link>
              </Button>
              <Button
                type="submit"
                loading={busy}
                disabled={
                  !composition.data ||
                  composition.isError ||
                  (composition.data.effective_role !== "edit" &&
                    composition.data.effective_role !== "admin")
                }
              >
                <Plus className="h-4 w-4" />
                {t("build.create")}
              </Button>
            </div>
          </form>
        )}
        <section aria-label={t("build.history")}>
          <div className="flex flex-wrap items-center justify-between gap-3 border-b bg-muted/30 p-3">
            <div className="flex items-center gap-2 px-1">
              <History className="h-4 w-4 text-muted-foreground" aria-hidden />
              <h2 className="text-sm font-semibold">{t("build.history")}</h2>
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <label className="flex items-center gap-2 text-sm">
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
                size="sm"
                variant="outline"
                onClick={() => {
                  void history.refetch();
                  if (compositionId > 0) void composition.refetch();
                }}
              >
                <RefreshCw className="h-4 w-4" />
                {t("build.refresh")}
              </Button>
            </div>
          </div>
          {history.isPending ? (
            <div role="status" aria-label={t("build.loading")} className="space-y-4 p-5">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : rows.length === 0 ? (
            history.isSuccess ? (
              <EmptyState
                icon={History}
                title={t("build.empty")}
                description={archived ? t("build.archivedEmptyHelp") : undefined}
                className="py-14"
              />
            ) : null
          ) : (
            <ul className="divide-y divide-border">
              {rows.map((build) => (
                <li key={build.id}>
                  <Link
                    href={`/builds/${build.id}`}
                    aria-label={build.name}
                    className="flex flex-wrap items-center gap-3 px-4 py-4 transition-colors duration-press hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring sm:px-5"
                  >
                    <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-muted">
                      <Boxes className="h-4 w-4 text-muted-foreground" aria-hidden />
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="break-words text-sm font-semibold">{build.name}</p>
                      <p className="mt-1 break-words text-xs text-muted-foreground">
                        {build.composition_name}
                        {" · "}
                        {t("build.objectCount", { count: String(build.object_quantity) })}
                      </p>
                    </div>
                    <span className="flex w-full items-center gap-1.5 pl-12 text-xs text-muted-foreground sm:w-auto sm:pl-0">
                      {build.completed ? (
                        <CheckCircle2 className="h-4 w-4 text-success" aria-hidden />
                      ) : (
                        <Clock3 className="h-4 w-4" aria-hidden />
                      )}
                      {t(
                        build.archived_at
                          ? "build.archived"
                          : build.completed
                            ? "build.complete"
                            : "build.inProgress",
                      )}
                    </span>
                    <ArrowRight
                      className="hidden h-4 w-4 shrink-0 text-muted-foreground sm:block"
                      aria-hidden
                    />
                  </Link>
                </li>
              ))}
            </ul>
          )}
          {(offset > 0 || rows.length >= 50) && (
            <div className="flex justify-end gap-2 border-t bg-muted/30 p-3">
              <Button
                size="sm"
                variant="outline"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - 50))}
              >
                {t("build.previous")}
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={rows.length < 50}
                onClick={() => setOffset(offset + 50)}
              >
                {t("build.next")}
              </Button>
            </div>
          )}
        </section>
      </Card>
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
  if (copyId) return <Navigate to="/builds/$id" params={{ id: String(copyId) }} />;
  const canEdit = build?.effective_role === "edit" || build?.effective_role === "admin";
  return (
    <PageContainer>
      <Link className="text-primary underline" href="/builds">
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
