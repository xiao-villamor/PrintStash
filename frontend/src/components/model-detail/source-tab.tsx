import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import { useRef, useState, type ReactNode } from "react";
import { ExternalLink } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  sourceApi,
  useSourceEditing,
  type SourceCommand,
  type SourceApi,
} from "@/features/library/provenance";
import { useOptionalI18n, type MessageKey } from "@/lib/i18n";
import { toast } from "@/lib/toast";
import { useAuthenticatedAssetUrl } from "@/lib/use-authenticated-asset-url";
import { safeHttpUrl } from "@/components/model-detail/source-url";
import type {
  ModelProvenanceRead,
  ModelSourceCoverRead,
  ProvenanceFieldRead,
  ProvenanceSourceRead,
} from "@/types";
import { provenanceOriginKey, type ProvenanceOrigin } from "@printstash/domain";

type SubmitSource = ReturnType<typeof useSourceEditing>["submit"];

const LABELS = {
  title: "source.field.title",
  description: "source.field.description",
  instructions: "source.field.instructions",
  creator_name: "source.field.creatorName",
  creator_id: "source.field.creatorId",
  creator_url: "source.field.creatorUrl",
  license_code: "source.field.licenseCode",
  license_url: "source.field.licenseUrl",
  license_text: "source.field.licenseText",
  attribution_text: "source.field.attributionText",
  published_at: "source.published",
  updated_at: "source.updated",
} satisfies Record<ProvenanceFieldRead["field_name"], MessageKey>;

const ORIGIN_LABELS = {
  confirmed: "source.origin.confirmed",
  inferred: "source.origin.inferred",
  user: "source.origin.user",
} satisfies Record<ProvenanceOrigin, MessageKey>;

function provenanceOriginLabel(
  origin: ProvenanceOrigin,
  i18n: ReturnType<typeof useOptionalI18n>,
): string {
  const key = ORIGIN_LABELS[provenanceOriginKey(origin)];
  return i18n?.t(key) ?? uiText(key);
}

function SourceField({
  source,
  field,
  canEdit,
  blocked,
  snapshot,
  submit,
  last = false,
}: {
  source: ProvenanceSourceRead;
  field: ProvenanceFieldRead;
  canEdit: boolean;
  blocked: boolean;
  snapshot: ModelProvenanceRead;
  submit: SubmitSource;
  last?: boolean;
}) {
  useUiLocale();
  const i18n = useOptionalI18n();
  const t = (key: MessageKey, values?: Record<string, string>) =>
    i18n?.t(key, values) ?? uiText(key, values);
  const label = t(LABELS[field.field_name]);
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(field.effective_value);
  const [restoreOpen, setRestoreOpen] = useState(false);
  const [base, setBase] = useState(snapshot);
  const finish = () => {
    setRestoreOpen(false);
    setEditing(false);
  };
  const save = () =>
    submit(
      {
        kind: "override",
        sourceId: source.id,
        payload: {
          overrides: { [field.field_name]: value },
          clear_overrides: [],
        },
      },
      base,
      finish,
    );
  const restore = () => {
    setRestoreOpen(false);
    submit(
      {
        kind: "override",
        sourceId: source.id,
        payload: {
          overrides: {},
          clear_overrides: [field.field_name],
        },
      },
      base,
      finish,
    );
  };
  const isLink = field.field_name === "creator_url" || field.field_name === "license_url";
  const safeLink = isLink ? safeHttpUrl(field.effective_value) : null;
  const applyToModel = () => {
    const payload =
      field.field_name === "title"
        ? { name: field.effective_value }
        : { description: field.effective_value };
    submit({ kind: "apply", payload }, snapshot, () => {});
  };
  return (
    <div
      className={`grid gap-2 px-3 py-3 @xl/source:grid-cols-[minmax(8rem,0.32fr)_minmax(0,1fr)] @xl/source:items-start ${last ? "" : "border-b border-surface-container-high"}`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="font-mono text-2xs uppercase tracking-wider text-on-surface-variant">
          {label}
        </h3>
        <Badge variant="secondary">{provenanceOriginLabel(field.effective_origin, i18n)}</Badge>
      </div>
      {editing ? (
        <div className="space-y-2">
          <Input
            disabled={blocked || !canEdit}
            value={value}
            onChange={(event) => setValue(event.target.value)}
            aria-label={t("source.override", { label })}
          />
          <div className="flex flex-wrap gap-2">
            <Button size="xs" disabled={blocked || !canEdit} onClick={save}>
              {t("source.save")}
            </Button>
            <Button
              size="xs"
              variant="outline"
              disabled={blocked}
              onClick={() => {
                setValue(field.effective_value);
                setEditing(false);
              }}
            >
              {t("source.cancel")}
            </Button>
            {field.user_override_set && (
              <Button
                size="xs"
                variant="ghost"
                disabled={blocked || !canEdit}
                onClick={() => setRestoreOpen(true)}
              >
                {t("source.restore")}
              </Button>
            )}
          </div>
        </div>
      ) : (
        <div className="flex min-w-0 flex-col items-start gap-3 @lg/source:flex-row @lg/source:justify-between">
          <div className="w-full min-w-0 break-words whitespace-pre-wrap text-sm text-muted-foreground">
            {safeLink ? (
              <a
                href={safeLink}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-primary hover:underline"
              >
                {safeLink}
                <ExternalLink className="h-3.5 w-3.5" />
              </a>
            ) : (
              field.effective_value ||
              (field.field_name.startsWith("license")
                ? t("source.notSupplied")
                : t("source.notSuppliedGeneric"))
            )}
          </div>
          {canEdit && (
            <div className="flex shrink-0 flex-wrap gap-1 @lg/source:justify-end">
              {(field.field_name === "title" || field.field_name === "description") && (
                <Button size="xs" variant="outline" onClick={applyToModel}>
                  {field.field_name === "title"
                    ? (i18n?.t("source.useTitle") ?? uiText("Use source title"))
                    : (i18n?.t("source.useDescription") ?? uiText("Use source description"))}
                </Button>
              )}
              {/* Every field renders one of these, so the visible word alone leaves a
                  screen-reader user hearing "Edit" five times with no way to tell them
                  apart — and it is what let a Playwright test drive the wrong one. */}
              <Button
                size="xs"
                variant="ghost"
                aria-label={t("source.editField", { field: label })}
                onClick={() => {
                  setValue(field.effective_value);
                  setBase(snapshot);
                  setEditing(true);
                }}
              >
                {t("source.edit")}
              </Button>
            </div>
          )}
        </div>
      )}
      {field.field_name === "license_text" && (
        <p className="col-span-full mt-1 text-xs text-muted-foreground @xl/source:col-start-2">
          {i18n?.t("source.licenseDisclaimer") ??
            uiText(
              "PrintStash preserves published license text and does not grant, interpret, or expand rights.",
            )}
        </p>
      )}
      <ConfirmModal
        open={restoreOpen}
        onClose={() => setRestoreOpen(false)}
        title={t("source.restoreTitle")}
        description={t("source.restoreDescription")}
        confirmLabel={t("source.restoreConfirm")}
        busy={blocked}
        onConfirm={restore}
      />
    </div>
  );
}

function SourceTags({ tags, last = false }: { tags: string[]; last?: boolean }) {
  useUiLocale();
  const i18n = useOptionalI18n();
  if (tags.length === 0) return null;
  return (
    <div
      className={`flex flex-col gap-2 px-3 py-3 @xl/source:grid @xl/source:grid-cols-[minmax(8rem,0.32fr)_minmax(0,1fr)] @xl/source:items-start ${last ? "" : "border-b border-surface-container-high"}`}
      aria-label={i18n?.t("source.tags") ?? uiText("Source tags")}
    >
      <span className="font-mono text-2xs uppercase tracking-wider text-on-surface-variant">
        {i18n?.t("source.tags") ?? uiText("Source tags")}
      </span>
      <div className="flex flex-wrap gap-1.5">
        {tags.map((tag) => (
          <Badge key={tag} variant="secondary">
            {tag}
          </Badge>
        ))}
      </div>
    </div>
  );
}

const ACCEPTED_COVER_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);
const MAX_COVER_BYTES = 15 * 1024 * 1024;

function SourceCover({
  modelId,
  source,
  canEdit,
  blocked,
  snapshot,
  submit,
  api,
}: {
  modelId: number;
  source: ProvenanceSourceRead;
  canEdit: boolean;
  api: SourceApi;
  blocked: boolean;
  snapshot: ModelProvenanceRead;
  submit: SubmitSource;
}) {
  useUiLocale();
  const i18n = useOptionalI18n();
  const t = (
    key:
      | "source.cover"
      | "source.coverUpload"
      | "source.coverReplace"
      | "source.coverDelete"
      | "source.coverReplaceTitle"
      | "source.coverReplaceDescription"
      | "source.coverDeleteTitle"
      | "source.coverDeleteDescription"
      | "source.coverInvalid"
      | "source.coverTooLarge"
      | "source.coverAvailable"
      | "source.coverEmpty"
      | "source.coverUnavailable",
  ) => i18n?.t(key) ?? uiText(key);
  const cover = source.cover;
  const [deleteBase, setDeleteBase] = useState(snapshot);
  const [replaceOpen, setReplaceOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const pendingFile = useRef<{ file: File; base: ModelProvenanceRead } | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const contentPath = api.getCoverContentPath(modelId, source.id);

  const finish = () => {
    pendingFile.current = null;
    setReplaceOpen(false);
    setDeleteOpen(false);
  };
  const upload = (file: File, base: ModelProvenanceRead) => {
    setReplaceOpen(false);
    submit({ kind: "upload", sourceId: source.id, file }, base, finish);
  };
  const selectFile = (file: File | undefined) => {
    if (!file || blocked || !canEdit) return;
    if (!ACCEPTED_COVER_TYPES.has(file.type)) {
      toast.error(t("source.coverInvalid"));
      return;
    }
    if (file.size > MAX_COVER_BYTES) {
      toast.error(t("source.coverTooLarge"));
      return;
    }
    if (cover) {
      pendingFile.current = { file, base: snapshot };
      setReplaceOpen(true);
    } else {
      upload(file, snapshot);
    }
  };
  const remove = () => {
    setDeleteOpen(false);
    submit({ kind: "delete", sourceId: source.id }, deleteBase, finish);
  };

  return (
    <section aria-labelledby={`cover-heading-${source.id}`}>
      <h2
        id={`cover-heading-${source.id}`}
        className="mb-4 border-b border-outline-variant pb-1 text-lg font-semibold text-on-surface"
      >
        {t("source.cover")}
      </h2>
      <div className="overflow-hidden rounded border border-outline-variant bg-surface">
        <div className="flex flex-col gap-2 px-3 py-2.5 @lg/source:flex-row @lg/source:items-center @lg/source:justify-between">
          <p className="text-sm text-on-surface-variant" aria-live="polite" role="status">
            {cover ? t("source.coverAvailable") : t("source.coverEmpty")}
          </p>
          {canEdit && (
            <div className="flex flex-wrap gap-2">
              <input
                ref={inputRef}
                type="file"
                accept="image/jpeg,image/png,image/webp"
                className="sr-only"
                aria-label={cover ? t("source.coverReplace") : t("source.coverUpload")}
                onChange={(event) => {
                  selectFile(event.currentTarget.files?.[0]);
                  event.currentTarget.value = "";
                }}
              />
              <Button
                size="xs"
                variant="outline"
                disabled={blocked}
                onClick={() => inputRef.current?.click()}
              >
                {cover ? t("source.coverReplace") : t("source.coverUpload")}
              </Button>
              {cover && (
                <Button
                  size="xs"
                  variant="destructive"
                  disabled={blocked}
                  onClick={() => {
                    setDeleteBase(snapshot);
                    setDeleteOpen(true);
                  }}
                >
                  {t("source.coverDelete")}
                </Button>
              )}
            </div>
          )}
        </div>
        {cover && (
          <div className="border-t border-surface-container-high bg-surface-container-low p-3">
            <SourceCoverImage
              cover={cover}
              path={contentPath}
              alt={`${t("source.cover")} - ${source.provider}`}
            />
          </div>
        )}
      </div>
      <ConfirmModal
        open={replaceOpen}
        onClose={() => {
          pendingFile.current = null;
          setReplaceOpen(false);
        }}
        title={t("source.coverReplaceTitle")}
        description={t("source.coverReplaceDescription")}
        confirmLabel={t("source.coverReplace")}
        busy={blocked}
        onConfirm={() => {
          if (pendingFile.current) upload(pendingFile.current.file, pendingFile.current.base);
        }}
      />
      <ConfirmModal
        open={deleteOpen}
        onClose={() => setDeleteOpen(false)}
        title={t("source.coverDeleteTitle")}
        description={t("source.coverDeleteDescription")}
        confirmLabel={t("source.coverDelete")}
        busy={blocked}
        onConfirm={remove}
      />
    </section>
  );
}

function SourceIdentityRow({
  label,
  children,
  last = false,
}: {
  label: string;
  children: ReactNode;
  last?: boolean;
}) {
  useUiLocale();
  return (
    <div
      className={`flex flex-col gap-1 px-3 py-2.5 @lg/source:flex-row @lg/source:items-center @lg/source:justify-between ${last ? "" : "border-b border-surface-container-high"}`}
    >
      <dt className="shrink-0 font-mono text-xs uppercase tracking-wider text-on-surface-variant">
        {label}
      </dt>
      <dd className="min-w-0 text-sm text-on-surface @lg/source:text-right">{children}</dd>
    </div>
  );
}

export function SourceTab(props: { modelId: number; canEdit: boolean; api?: SourceApi }) {
  return <SourceTabContent key={props.modelId} {...props} />;
}

function SourceTabContent({
  modelId,
  canEdit,
  api = sourceApi,
}: {
  modelId: number;
  canEdit: boolean;
  api?: SourceApi;
}) {
  useUiLocale();
  const i18n = useOptionalI18n();
  const t = (key: MessageKey, values?: Record<string, string>) =>
    i18n?.t(key, values) ?? uiText(key, values);
  const editor = useSourceEditing(modelId, api);
  const { data, state } = editor;
  if (!editor.active) return null;
  if (editor.denied || (!data && editor.query.isError))
    return (
      <div role="alert" className="space-y-3">
        <p>{uiText("Couldn’t load this model")}</p>
        <Button
          variant="outline"
          loading={editor.reviewing || editor.query.isFetching}
          onClick={() => (state.phase === "blocked" ? void editor.review() : editor.retryRead())}
        >
          {uiText("Retry")}
        </Button>
      </div>
    );
  if (!data)
    return (
      <div className="space-y-3">
        <Skeleton className="h-20 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  const blocked = state.phase !== "idle";
  const editable = canEdit && !editor.editDenied && !blocked;
  const reviewed = state.phase === "blocked" ? state.reviewed : null;
  return (
    <div className="@container/source space-y-8">
      {editor.query.isError && (
        <div role="alert">
          <p>{uiText("Couldn’t load this model")}</p>
          <Button onClick={editor.retryRead}>{uiText("Retry")}</Button>
        </div>
      )}
      {state.phase === "blocked" && (
        <div role="alert" className="space-y-3 rounded border border-border bg-muted p-3 text-sm">
          <p>
            {uiText(
              state.problem === "conflict" ? "library.editConflict" : "library.saveUnconfirmed",
            )}
          </p>
          <Button variant="outline" loading={editor.reviewing} onClick={() => void editor.review()}>
            {uiText("library.reviewLatest")}
          </Button>
          {reviewed && (
            <section aria-label={uiText("library.latestVersion")} className="space-y-3">
              <h3 className="font-semibold">{uiText("library.latestVersion")}</h3>
              <SourceReview
                modelId={modelId}
                api={api}
                command={state.pending.command}
                model={reviewed.model}
                data={reviewed.provenance}
              />
              <div className="flex flex-wrap gap-2">
                <Button
                  variant="outline"
                  disabled={editor.reviewing}
                  onClick={() => void editor.adopt()}
                >
                  {uiText("library.useLatest")}
                </Button>
                <Button
                  disabled={!canEdit || editor.editDenied || editor.reviewing}
                  onClick={editor.retry}
                >
                  {uiText("library.retryDraft")}
                </Button>
              </div>
            </section>
          )}
        </div>
      )}
      {!data.sources.length && (
        <EmptyState title={t("source.emptyTitle")} description={t("source.emptyDescription")} />
      )}
      {data.sources.map((source) => {
        const canonicalUrl = safeHttpUrl(source.canonical_url);
        const fieldCount = source.fields.length + (source.tags?.length ? 1 : 0);
        return (
          <article key={source.id} className="space-y-7">
            <section aria-labelledby={`source-heading-${source.id}`}>
              <h2
                id={`source-heading-${source.id}`}
                className="mb-4 border-b border-outline-variant pb-1 text-lg font-semibold text-on-surface"
              >
                {t("source.title")}
              </h2>
              <dl
                className="overflow-hidden rounded border border-outline-variant bg-surface"
                data-testid="source-identity-panel"
              >
                <SourceIdentityRow label={t("source.provider")}>
                  <span className="inline-flex flex-wrap items-center justify-start gap-2 @lg/source:justify-end">
                    <span className="capitalize">{source.provider}</span>
                    <Badge variant="secondary">{t("source.capturedStatus")}</Badge>
                  </span>
                </SourceIdentityRow>
                <SourceIdentityRow label={t("source.url")}>
                  {canonicalUrl ? (
                    <a
                      href={canonicalUrl}
                      target="_blank"
                      rel="noreferrer"
                      className="flex min-w-0 max-w-full items-start gap-1 text-left text-primary hover:underline @lg/source:justify-end @lg/source:text-right"
                    >
                      <span className="min-w-0 break-words">{canonicalUrl}</span>
                      <ExternalLink className="h-3.5 w-3.5 shrink-0" />
                    </a>
                  ) : (
                    <span className="break-all text-muted-foreground">{source.canonical_url}</span>
                  )}
                </SourceIdentityRow>
                <SourceIdentityRow label={t("source.sourceId")}>
                  {source.source_item_id || t("source.notSuppliedGeneric")}
                </SourceIdentityRow>
                <SourceIdentityRow label={t("source.revision")}>
                  {source.source_revision || t("source.notSuppliedGeneric")}
                </SourceIdentityRow>
                <SourceIdentityRow label={t("source.captured")}>
                  {new Date(source.first_captured_at).toLocaleDateString(i18n?.locale)}
                </SourceIdentityRow>
                <SourceIdentityRow label={t("source.checked")} last>
                  {new Date(source.last_checked_at).toLocaleDateString(i18n?.locale)}
                </SourceIdentityRow>
              </dl>
            </section>
            <SourceCover
              modelId={modelId}
              source={source}
              canEdit={editable}
              blocked={blocked}
              snapshot={data}
              submit={editor.submit}
              api={api}
            />
            {fieldCount > 0 && (
              <section aria-labelledby={`metadata-heading-${source.id}`}>
                <h2
                  id={`metadata-heading-${source.id}`}
                  className="mb-4 border-b border-outline-variant pb-1 text-lg font-semibold text-on-surface"
                >
                  {t("source.metadata")}
                </h2>
                <div className="overflow-hidden rounded border border-outline-variant bg-surface">
                  <SourceTags tags={source.tags ?? []} last={source.fields.length === 0} />
                  {source.fields.map((field, index) => (
                    <SourceField
                      key={field.field_name}
                      source={source}
                      field={field}
                      canEdit={editable}
                      blocked={blocked}
                      snapshot={data}
                      submit={editor.submit}
                      last={index === source.fields.length - 1}
                    />
                  ))}
                </div>
              </section>
            )}
          </article>
        );
      })}
    </div>
  );
}

function SourceReview({
  modelId,
  api,
  command,
  model,
  data,
}: {
  modelId: number;
  api: SourceApi;
  command: SourceCommand;
  model: import("@/types").ModelRead;
  data: ModelProvenanceRead;
}) {
  if (command.kind === "apply")
    return <p>{"name" in command.payload ? model.name : model.description}</p>;
  const source = data.sources.find((item) => item.id === command.sourceId);
  if (!source) return <p>{uiText("source.emptyTitle")}</p>;
  if (command.kind === "override")
    return (
      <dl>
        {source.fields
          .filter(
            (field) =>
              Object.hasOwn(command.payload.overrides, field.field_name) ||
              command.payload.clear_overrides.includes(field.field_name),
          )
          .map((field) => (
            <div key={field.field_name}>
              <dt>{uiText(LABELS[field.field_name])}</dt>
              <dd className="whitespace-pre-wrap">{field.effective_value}</dd>
            </div>
          ))}
      </dl>
    );
  return (
    <div className="space-y-2">
      <p>{uiText(source.cover ? "source.coverAvailable" : "source.coverEmpty")}</p>
      {source.cover && (
        <SourceCoverImage
          cover={source.cover}
          path={api.getCoverContentPath(modelId, source.id)}
          alt={uiText("source.cover")}
        />
      )}
    </div>
  );
}

function SourceCoverImage({
  cover,
  path,
  alt,
}: {
  cover: ModelSourceCoverRead;
  path: string;
  alt: string;
}) {
  // Metadata identifies the displayed bytes. A reviewed replacement must not
  // reuse a blob fetched for the old cover at the same content endpoint.
  const imageUrl = useAuthenticatedAssetUrl(
    `${path}?v=${cover.id}-${encodeURIComponent(cover.updated_at)}`,
  );
  return imageUrl ? (
    <img src={imageUrl} alt={alt} className="max-h-64 w-full rounded object-contain" />
  ) : (
    <p className="text-sm text-muted-foreground">{uiText("source.coverUnavailable")}</p>
  );
}
