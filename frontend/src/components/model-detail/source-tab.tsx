import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import { useId, useRef, useState } from "react";
import { ChevronLeft, ExternalLink, Image } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { EmptyState } from "@/components/ui/empty-state";
import { DetailTabLoading } from "./tab-loading";
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
import { sourceProviderName } from "./presentation";
import type {
  ModelProvenancePatch,
  ModelProvenanceRead,
  ModelSourceCoverRead,
  ProvenanceFieldName,
  ProvenanceFieldRead,
  ProvenanceSourceRead,
} from "@/types";

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

function SourceTags({ tags }: { tags: string[] }) {
  useUiLocale();
  const i18n = useOptionalI18n();
  if (tags.length === 0) return null;
  const label = i18n?.t("source.tags") ?? uiText("Source tags");
  return (
    <div
      className="grid grid-cols-[minmax(6rem,.4fr)_minmax(0,1fr)] gap-x-4 border-t border-border px-3 py-2.5 text-sm"
      aria-label={label}
    >
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="flex flex-wrap gap-1.5">
        {tags.map((tag) => (
          <span
            key={tag}
            className="min-w-0 max-w-full rounded-md bg-muted px-2 py-0.5 text-xs text-muted-foreground [overflow-wrap:anywhere]"
          >
            {tag}
          </span>
        ))}
      </dd>
    </div>
  );
}

/** The captured fields the Source view lists, in reading order. */
const READ_FIELDS = [
  "title",
  "description",
  "creator_name",
  "creator_id",
  "creator_url",
  "published_at",
  "updated_at",
] as const satisfies readonly ProvenanceFieldName[];

function SourceRow({
  label,
  field,
  last,
}: {
  label: string;
  field: ProvenanceFieldRead | undefined;
  last: boolean;
}) {
  useUiLocale();
  const i18n = useOptionalI18n();
  const notSupplied = i18n?.t("source.notSuppliedGeneric") ?? uiText("source.notSuppliedGeneric");
  const link = field?.field_name === "creator_url" ? safeHttpUrl(field.effective_value) : null;
  return (
    <div
      className={`grid grid-cols-[minmax(6rem,.4fr)_minmax(0,1fr)] gap-x-4 px-3 py-2.5 text-sm ${last ? "" : "border-b border-border"}`}
    >
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words font-medium [overflow-wrap:anywhere]">
        {link ? (
          <a
            href={link}
            target="_blank"
            rel="noreferrer"
            className="inline-flex max-w-full items-center gap-1 text-primary hover:underline"
          >
            <span className="min-w-0">{link}</span>
            <ExternalLink className="size-3.5 shrink-0" aria-hidden />
          </a>
        ) : (
          field?.effective_value || notSupplied
        )}
        {field?.user_override_set && (
          <span className="block text-xs font-normal text-muted-foreground">
            {uiText("Captured: {value1}", { value1: field.captured_value || notSupplied })}
          </span>
        )}
      </dd>
    </div>
  );
}

const EDIT_FIELDS = [
  "title",
  "creator_name",
  "published_at",
  "license_code",
] as const satisfies readonly ProvenanceFieldName[];

/** One form, one save: every correction is kept next to the captured value. */
function SourceEditForm({
  source,
  snapshot,
  submit,
  blocked,
  onCancel,
}: {
  source: ProvenanceSourceRead;
  snapshot: ModelProvenanceRead;
  submit: SubmitSource;
  blocked: boolean;
  onCancel: () => void;
}) {
  useUiLocale();
  const i18n = useOptionalI18n();
  const t = (key: MessageKey, values?: Record<string, string>) =>
    i18n?.t(key, values) ?? uiText(key, values);
  const id = useId();
  const fieldOf = (name: ProvenanceFieldName) =>
    source.fields.find((field) => field.field_name === name);
  const [base] = useState(snapshot);
  const [draft, setDraft] = useState<Record<string, string>>(() =>
    Object.fromEntries(EDIT_FIELDS.map((name) => [name, fieldOf(name)?.effective_value ?? ""])),
  );
  const [restored, setRestored] = useState<ProvenanceFieldName[]>([]);
  const overrides: ModelProvenancePatch["overrides"] = {};
  for (const name of EDIT_FIELDS) {
    if (restored.includes(name)) continue;
    if (draft[name] !== (fieldOf(name)?.effective_value ?? "")) overrides[name] = draft[name];
  }
  const changed = Object.keys(overrides).length > 0 || restored.length > 0;
  const save = () =>
    submit(
      {
        kind: "override",
        sourceId: source.id,
        payload: { overrides, clear_overrides: restored },
      },
      base,
      onCancel,
    );
  const notSupplied = t("source.notSuppliedGeneric");
  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (changed && !blocked) save();
      }}
    >
      <div className="space-y-1">
        <h2 className="text-base font-semibold">{uiText("Edit source")}</h2>
        <p className="text-sm text-muted-foreground">
          {uiText("Your corrections are kept next to the captured value.")}
        </p>
      </div>
      <div className="space-y-1.5">
        <label htmlFor={`${id}-link`} className="block text-sm font-medium">
          {uiText("Source link")}
        </label>
        <Input id={`${id}-link`} readOnly value={source.canonical_url} />
      </div>
      {EDIT_FIELDS.map((name) => {
        const field = fieldOf(name);
        const label = name === "license_code" ? uiText("License") : t(LABELS[name]);
        const canRestore = field?.user_override_set && !restored.includes(name);
        return (
          <div key={name} className="space-y-1.5">
            <label htmlFor={`${id}-${name}`} className="block text-sm font-medium">
              {label}
            </label>
            <Input
              id={`${id}-${name}`}
              value={draft[name]}
              disabled={blocked}
              placeholder={name === "published_at" ? notSupplied : undefined}
              onChange={(event) => {
                const value = event.target.value;
                setDraft((current) => ({ ...current, [name]: value }));
                setRestored((current) => current.filter((item) => item !== name));
              }}
            />
            {name === "license_code" && (
              <p className="text-xs text-muted-foreground">
                {uiText("Search known licenses or type the published name.")}
              </p>
            )}
            {canRestore && (
              <p className="flex flex-wrap items-center gap-x-3 text-xs text-muted-foreground">
                <span className="wrap-anywhere">
                  {uiText("Captured: {value1}", { value1: field.captured_value || notSupplied })}
                </span>
                <button
                  type="button"
                  className="text-primary hover:underline"
                  onClick={() => {
                    setRestored((current) => [...current, name]);
                    setDraft((current) => ({ ...current, [name]: field.captured_value }));
                  }}
                >
                  {uiText("Restore captured value")}
                </button>
              </p>
            )}
          </div>
        );
      })}
      <div className="flex justify-end gap-2 border-t border-border pt-4">
        <Button type="button" variant="outline" onClick={onCancel}>
          {uiText("Cancel")}
        </Button>
        <Button type="submit" disabled={!changed || blocked}>
          {uiText("Save source")}
        </Button>
      </div>
    </form>
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
    <section
      aria-labelledby={`cover-heading-${source.id}`}
      className="flex flex-wrap items-center gap-4 border-b border-border py-4"
    >
      <div className="flex h-24 w-32 shrink-0 items-center justify-center bg-muted/40">
        {cover ? (
          <SourceCoverImage
            compact
            cover={cover}
            path={contentPath}
            alt={`${t("source.cover")} - ${source.provider}`}
          />
        ) : (
          <Image className="h-6 w-6 text-muted-foreground" aria-hidden />
        )}
      </div>
      <div className="min-w-0 flex-1 space-y-2">
        <h3 id={`cover-heading-${source.id}`} className="text-sm font-semibold">
          {t("source.cover")}
        </h3>
        <p className="text-sm text-muted-foreground" aria-live="polite" role="status">
          {cover ? t("source.coverAvailable") : t("source.coverEmpty")}
        </p>
        {canEdit && (
          <div className="flex flex-wrap gap-2">
            <input
              ref={inputRef}
              type="file"
              accept="image/jpeg,image/png,image/webp"
              tabIndex={-1}
              className="hidden"
              aria-label={cover ? t("source.coverReplace") : t("source.coverUpload")}
              onChange={(event) => {
                selectFile(event.currentTarget.files?.[0]);
                event.currentTarget.value = "";
              }}
            />
            <Button
              size="xs"
              variant="ghost"
              disabled={blocked}
              onClick={() => inputRef.current?.click()}
            >
              {cover ? t("source.coverReplace") : t("source.coverUpload")}
            </Button>
            {cover && (
              <Button
                size="xs"
                variant="ghost"
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

export function SourceTab(props: {
  modelId: number;
  canEdit: boolean;
  api?: SourceApi;
  /** Returns to the Overview; shown beside Edit source when given. */
  onBack?: () => void;
}) {
  return <SourceTabContent key={props.modelId} {...props} />;
}

function SourceTabContent({
  modelId,
  canEdit,
  api = sourceApi,
  onBack,
}: {
  modelId: number;
  canEdit: boolean;
  api?: SourceApi;
  onBack?: () => void;
}) {
  useUiLocale();
  const i18n = useOptionalI18n();
  const t = (key: MessageKey, values?: Record<string, string>) =>
    i18n?.t(key, values) ?? uiText(key, values);
  const editor = useSourceEditing(modelId, api);
  const { data, state } = editor;
  const [editingSource, setEditingSource] = useState(false);
  if (!editor.active) return null;
  if (editor.denied || (!data && editor.query.isError))
    return (
      <div role="alert" className="space-y-3">
        <p>{uiText("Couldn’t load source information.")}</p>
        <Button
          variant="outline"
          loading={editor.reviewing || editor.query.isFetching}
          onClick={() => (state.phase === "blocked" ? void editor.review() : editor.retryRead())}
        >
          {uiText("Retry")}
        </Button>
      </div>
    );
  const blocked = state.phase !== "idle";
  const canEditSource = canEdit && !editor.editDenied;
  const editable = canEditSource && editingSource && !blocked;
  const reviewed = state.phase === "blocked" ? state.reviewed : null;
  const feedback = state.phase === "blocked" && (
    <div role="alert" className="space-y-3 rounded border border-border bg-muted p-3 text-sm">
      <p>
        {uiText(state.problem === "conflict" ? "library.editConflict" : "library.saveUnconfirmed")}
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
  );
  return (
    <DetailTabLoading pending={!data} variant="source">
      {data && (
        <div className="@container/source space-y-6">
          {editor.query.isError && (
            <div role="alert">
              <p>{uiText("Couldn’t load source information.")}</p>
              <Button onClick={editor.retryRead}>{uiText("Retry")}</Button>
            </div>
          )}
          {(onBack || (canEditSource && data.sources.length > 0)) && (
            <div className="flex items-center justify-between gap-2">
              {onBack ? (
                <Button size="sm" variant="ghost" className="-ms-2" onClick={onBack}>
                  <ChevronLeft className="size-4 shrink-0" aria-hidden />
                  {uiText("Overview")}
                </Button>
              ) : (
                <span />
              )}
              {canEditSource && data.sources.length > 0 && !editingSource && (
                <Button size="sm" variant="outline" onClick={() => setEditingSource(true)}>
                  {uiText("Edit source")}
                </Button>
              )}
            </div>
          )}
          {feedback}
          {!data.sources.length && (
            <EmptyState title={t("source.emptyTitle")} description={t("source.emptyDescription")} />
          )}
          {data.sources.map((source) => {
            const canonicalUrl = safeHttpUrl(source.canonical_url);
            const fieldByName = (name: ProvenanceFieldName) =>
              source.fields.find((item) => item.field_name === name);
            const titleField = fieldByName("title");
            const creatorField = fieldByName("creator_name");
            const licenseField = fieldByName("license_code");
            const licenseUrl = safeHttpUrl(fieldByName("license_url")?.effective_value ?? "");
            const site = sourceProviderName(source.provider);
            if (editingSource && canEditSource)
              return (
                <article key={source.id} className="min-w-0 space-y-6">
                  <SourceCover
                    modelId={modelId}
                    source={source}
                    canEdit={editable}
                    blocked={blocked}
                    snapshot={data}
                    submit={editor.submit}
                    api={api}
                  />
                  <SourceEditForm
                    source={source}
                    snapshot={data}
                    submit={editor.submit}
                    blocked={blocked}
                    onCancel={() => setEditingSource(false)}
                  />
                </article>
              );
            return (
              <article key={source.id} className="min-w-0 space-y-5">
                <section aria-labelledby={`source-heading-${source.id}`}>
                  <h2 id={`source-heading-${source.id}`} className="sr-only">
                    {uiText("Source information")}
                  </h2>
                  <div className="flex min-w-0 items-center gap-4 rounded-lg border border-border p-3">
                    <div
                      aria-hidden={source.cover ? undefined : true}
                      className="flex size-16 shrink-0 items-center justify-center overflow-hidden rounded-md bg-muted text-muted-foreground"
                    >
                      {source.cover ? (
                        <SourceCoverImage
                          compact
                          cover={source.cover}
                          path={api.getCoverContentPath(modelId, source.id)}
                          alt={`${uiText("source.cover")} - ${source.provider}`}
                        />
                      ) : (
                        <Image className="size-5" aria-hidden />
                      )}
                    </div>
                    <div className="min-w-0 space-y-1">
                      <p className="break-words text-base font-semibold [overflow-wrap:anywhere]">
                        {titleField?.effective_value || site}
                      </p>
                      <p className="break-words text-sm">
                        {canonicalUrl ? (
                          <a
                            href={canonicalUrl}
                            target="_blank"
                            rel="noreferrer"
                            title={canonicalUrl}
                            className="inline-flex max-w-full items-center gap-1 text-primary hover:underline"
                          >
                            <span className="min-w-0 [overflow-wrap:anywhere]">
                              {site}
                              {creatorField?.effective_value
                                ? ` · ${creatorField.effective_value}`
                                : ""}
                            </span>
                            <ExternalLink className="size-3.5 shrink-0" aria-hidden />
                            <span className="sr-only">{uiText("Opens in a new tab")}</span>
                          </a>
                        ) : (
                          source.canonical_url
                        )}
                      </p>
                    </div>
                  </div>
                </section>
                {licenseField?.effective_value && (
                  <section aria-labelledby={`license-heading-${source.id}`}>
                    <h2
                      id={`license-heading-${source.id}`}
                      className="mb-2 text-sm font-medium text-muted-foreground"
                    >
                      {uiText("License")}
                    </h2>
                    <div className="space-y-1 rounded-lg border border-border p-3">
                      <p className="text-sm font-semibold">{licenseField.effective_value}</p>
                      <p className="text-sm text-muted-foreground">
                        {uiText("source.licenseNote", { site })}
                      </p>
                      {(licenseUrl ?? canonicalUrl) && (
                        <a
                          href={licenseUrl ?? canonicalUrl ?? undefined}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-1 text-sm text-primary hover:underline"
                        >
                          {uiText("source.licenseTerms", { site })}
                          <ExternalLink className="size-3.5 shrink-0" aria-hidden />
                          <span className="sr-only">{uiText("Opens in a new tab")}</span>
                        </a>
                      )}
                    </div>
                  </section>
                )}
                <section aria-labelledby={`metadata-heading-${source.id}`}>
                  <h2
                    id={`metadata-heading-${source.id}`}
                    className="mb-2 text-sm font-medium text-muted-foreground"
                  >
                    {uiText("Captured fields")}
                  </h2>
                  <dl className="overflow-hidden rounded-lg border border-border">
                    {READ_FIELDS.map((name, index) => (
                      <SourceRow
                        key={name}
                        label={t(LABELS[name])}
                        field={fieldByName(name)}
                        last={index === READ_FIELDS.length - 1 && !source.tags?.length}
                      />
                    ))}
                    <SourceTags tags={source.tags ?? []} />
                  </dl>
                </section>
                <p className="text-sm text-muted-foreground" data-testid="source-identity-panel">
                  {t("source.captured")}{" "}
                  {new Date(source.first_captured_at).toLocaleDateString(i18n?.locale)} ·{" "}
                  {t("source.checked")}{" "}
                  {new Date(source.last_checked_at).toLocaleDateString(i18n?.locale)} ·{" "}
                  {t("source.sourceId")} {source.source_item_id || t("source.notSuppliedGeneric")}
                  {source.source_revision
                    ? ` · ${t("source.revision")} ${source.source_revision}`
                    : ""}
                </p>
              </article>
            );
          })}
        </div>
      )}
    </DetailTabLoading>
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
  compact = false,
}: {
  cover: ModelSourceCoverRead;
  path: string;
  alt: string;
  compact?: boolean;
}) {
  // Metadata identifies the displayed bytes. A reviewed replacement must not
  // reuse a blob fetched for the old cover at the same content endpoint.
  const imageUrl = useAuthenticatedAssetUrl(
    `${path}?v=${cover.id}-${encodeURIComponent(cover.updated_at)}`,
  );
  return imageUrl ? (
    <img
      src={imageUrl}
      alt={alt}
      className={compact ? "h-full w-full object-contain" : "max-h-64 w-full object-contain"}
    />
  ) : (
    <p className="text-sm text-muted-foreground">{uiText("source.coverUnavailable")}</p>
  );
}
