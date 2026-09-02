"use client";

import { useMemo, useRef, useState } from "react";
import {
  ArrowDown,
  ArrowUp,
  Boxes,
  ChevronRight,
  FileText,
  Pencil,
  Plus,
  Search,
  Trash2,
  Upload,
} from "lucide-react";

import { Modal } from "@/components/ui/modal";
import { EmptyState } from "@/components/ui/empty-state";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card } from "@/components/ui/card";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { useI18n } from "@/lib/i18n";
import { useMultipartModels } from "@/lib/queries";
import {
  createMultipartModel,
  deleteDocument,
  deleteMultipartModel,
  saveMultipartModel,
  uploadDocument,
} from "@/lib/api";
import { useMultipartModel, useMultipartModelCandidates } from "@/lib/queries";
import { useAuthenticatedAssetUrl } from "@/lib/use-authenticated-asset-url";
import { useAuth } from "@/lib/auth-context";
import { useRouter, useSearchParams } from "@/lib/navigation";
import { Link } from "@/lib/link";
import { useParams } from "react-router-dom";
import type {
  CollectionRead,
  MultipartModelCandidate,
  MultipartModelRead,
  MultipartPartRead,
} from "@/types";
import { toast } from "@/lib/toast";
import { cn } from "@/lib/utils";
import { PageContainer } from "@/components/ui/page-container";
import { PageHeader } from "@/components/ui/page-header";
import { parseApiError } from "@/lib/errors";

/**
 * Keep server details behind the same error translation seam as the rest of
 * the app. Multipart endpoints also have a few domain-specific detail codes;
 * those should get useful, localised copy instead of exposing the code itself.
 */
function multipartError(
  cause: unknown,
  t: ReturnType<typeof useI18n>["t"],
  fallback: Parameters<ReturnType<typeof useI18n>["t"]>[0],
): string {
  const parsed = parseApiError(cause);
  // Multipart operations need copy from the active catalog. The global
  // userMessage seam intentionally stays locale-neutral, so map the boundary
  // categories here instead of exposing a server detail code or English copy
  // in a Spanish vault.
  if (parsed.code === "network_unreachable") return t("multipart.networkError");
  if (parsed.status === 403 || parsed.code.endsWith("_permission_denied")) {
    return t("multipart.permissionError");
  }
  if (parsed.status === 404 || parsed.code.endsWith("_not_found")) {
    return t("multipart.notFoundError");
  }
  if (parsed.code === "unknown" || parsed.code === "offline") return t(fallback);
  if (parsed.code.startsWith("multipart_") || parsed.code.startsWith("part_")) {
    return t(fallback);
  }
  if (parsed.code.endsWith("_failed")) return t(fallback);
  // Unknown details must remain operation-specific and localized. Do not fall
  // through to userMessage(), whose generic catalog is deliberately English.
  return t(fallback);
}

function Count({ count, one, many }: { count: number; one: string; many: string }) {
  return <span>{(count === 1 ? one : many).replace("{count}", String(count))}</span>;
}

function Cover({ src, alt }: { src: string | null; alt: string }) {
  const url = useAuthenticatedAssetUrl(src);
  return url ? (
    <img src={url} alt={alt} className="h-full w-full object-contain" />
  ) : (
    <div className="flex h-full w-full items-center justify-center bg-muted text-muted-foreground">
      <Boxes className="h-10 w-10" aria-hidden />
    </div>
  );
}

export function NewMultipartModelModal({
  open,
  onClose,
  collectionId,
}: {
  open: boolean;
  onClose: () => void;
  collectionId: number | null;
}) {
  const { t } = useI18n();
  const router = useRouter();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!name.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      const created = await createMultipartModel({
        name: name.trim(),
        description: description.trim() || null,
        collection_id: collectionId,
      });
      setName("");
      setDescription("");
      onClose();
      router.push(`/multipart-models/${created.id}`);
    } catch (cause) {
      setError(multipartError(cause, t, "multipart.createError"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={busy ? () => undefined : onClose}
      title={t("multipart.new")}
      className="max-w-lg"
    >
      <form onSubmit={submit} className="space-y-5">
        <p className="text-sm text-muted-foreground">{t("multipart.linkedNotice")}</p>
        <label className="block space-y-1.5">
          <span className="text-sm font-medium">{t("multipart.name")}</span>
          <Input
            autoFocus
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder={t("multipart.namePlaceholder")}
            maxLength={200}
            required
          />
        </label>
        <label className="block space-y-1.5">
          <span className="text-sm font-medium">{t("multipart.descriptionLabel")}</span>
          <textarea
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            placeholder={t("multipart.descriptionPlaceholder")}
            rows={3}
            className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
        </label>
        {error && (
          <p
            role="alert"
            className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive"
          >
            {error}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={onClose} disabled={busy}>
            {t("multipart.cancel")}
          </Button>
          <Button type="submit" loading={busy} disabled={!name.trim()}>
            {t("multipart.create")}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

export function MultipartModelBrowser({
  collection,
  collections,
  canCreate,
}: {
  collection: string | null;
  collections: CollectionRead[];
  canCreate: boolean;
}) {
  const { t } = useI18n();
  const { user } = useAuth();
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<"updated" | "name" | "parts">("updated");
  const [createOpen, setCreateOpen] = useState(false);
  const collectionId = collections.find((item) => item.path === collection)?.id ?? null;
  const {
    data: items = [],
    isLoading,
    error,
  } = useMultipartModels({
    collection: collection ?? undefined,
    direct: true,
    q: query || undefined,
    limit: 100,
  });
  const canWrite = canCreate || !!user?.is_superuser;
  const sortedItems = useMemo(() => {
    const rows = [...items];
    if (sort === "name") return rows.sort((a, b) => a.name.localeCompare(b.name));
    if (sort === "parts") return rows.sort((a, b) => b.part_count - a.part_count);
    return rows.sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  }, [items, sort]);

  return (
    <section className="flex-1 px-4 py-5 sm:px-6 sm:py-7">
      <NewMultipartModelModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        collectionId={collectionId}
      />
      <div className="w-full space-y-6">
        <header className="flex flex-col gap-4 border-b border-border pb-5 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h1 className="text-2xl font-bold tracking-tight text-foreground">
              {t("multipart.title")}
            </h1>
            <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
              {t("multipart.description")}
            </p>
          </div>
          <Button
            onClick={() => setCreateOpen(true)}
            disabled={!canWrite}
            title={canWrite ? undefined : t("multipart.editAccess")}
          >
            <Plus className="h-4 w-4" /> {t("multipart.new")}
          </Button>
        </header>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex w-full max-w-xl items-center gap-2">
            <Search className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
            <Input
              aria-label={t("multipart.searchModels")}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={t("multipart.searchModels")}
            />
          </div>
          <label className="flex items-center gap-2 text-sm text-muted-foreground">
            <span>{t("multipart.sort")}</span>
            <select
              value={sort}
              onChange={(event) => {
                const value = event.target.value;
                if (value === "updated" || value === "name" || value === "parts") {
                  setSort(value);
                }
              }}
              className="h-10 rounded-md border border-input bg-background px-3 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <option value="updated">{t("multipart.sortUpdated")}</option>
              <option value="name">{t("multipart.sortName")}</option>
              <option value="parts">{t("multipart.sortParts")}</option>
            </select>
          </label>
        </div>
        {error && (
          <p
            role="alert"
            className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive"
          >
            {multipartError(error, t, "multipart.loadError")}
          </p>
        )}
        {isLoading ? (
          <div className="space-y-3" aria-busy="true">
            {[1, 2, 3].map((item) => (
              <Card key={item} className="h-32 animate-pulse bg-muted/40" />
            ))}
          </div>
        ) : items.length === 0 ? (
          <EmptyState
            title={query ? t("multipart.noCandidates") : t("multipart.empty")}
            description={query ? undefined : t("multipart.emptyDescription")}
            action={
              !query && canWrite ? (
                <Button onClick={() => setCreateOpen(true)}>{t("multipart.new")}</Button>
              ) : undefined
            }
          />
        ) : (
          <div className="space-y-3">
            {sortedItems.map((item) => (
              <Link
                key={item.id}
                href={`/multipart-models/${item.id}`}
                className="group flex min-h-32 overflow-hidden rounded-lg border border-border bg-card text-card-foreground transition-[border-color,transform] duration-press hover:-translate-y-0.5 hover:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <div className="w-28 shrink-0 border-r border-border bg-muted/40 sm:w-40">
                  <Cover src={item.cover_thumbnail_url} alt="" />
                </div>
                <div className="flex min-w-0 flex-1 flex-col justify-center space-y-2 p-4">
                  <div className="flex items-start justify-between gap-2">
                    <h3 className="min-w-0 break-words font-semibold">{item.name}</h3>
                    <ChevronRight
                      className="h-4 w-4 shrink-0 text-muted-foreground transition-transform duration-press group-hover:translate-x-0.5"
                      aria-hidden
                    />
                  </div>
                  <p className="text-sm text-muted-foreground">
                    <Count
                      count={item.part_count}
                      one={t("multipart.part")}
                      many={t("multipart.parts")}
                    />{" "}
                    ·{" "}
                    <Count
                      count={item.model_count}
                      one={t("multipart.model")}
                      many={t("multipart.models")}
                    />
                    {item.guide_count > 0 && (
                      <>
                        {" "}
                        · {item.guide_count}{" "}
                        {item.guide_count === 1 ? t("multipart.guide") : t("multipart.guides")}
                      </>
                    )}
                  </p>
                  {item.collection && (
                    <p className="truncate text-xs text-muted-foreground">{item.collection}</p>
                  )}
                </div>
              </Link>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

function modelLabel(model: MultipartModelCandidate, unavailable: string): string {
  return model.available && model.name ? model.name : unavailable;
}

function ModelPicker({
  open,
  onClose,
  aggregateId,
  usedIds,
  onSelect,
}: {
  open: boolean;
  onClose: () => void;
  aggregateId: number;
  usedIds: Set<number>;
  onSelect: (model: MultipartModelCandidate) => void;
}) {
  const { t } = useI18n();
  const [query, setQuery] = useState("");
  const { data: candidates = [], isLoading } = useMultipartModelCandidates(aggregateId, query, {
    enabled: open,
  });
  return (
    <Modal open={open} onClose={onClose} title={t("multipart.modelPicker")} className="max-w-xl">
      <div className="space-y-4">
        <div className="flex items-center gap-2">
          <Search className="h-4 w-4 text-muted-foreground" aria-hidden />
          <Input
            autoFocus
            aria-label={t("multipart.searchModels")}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t("multipart.searchModels")}
          />
        </div>
        <ul
          className="max-h-80 overflow-y-auto rounded-md border border-border"
          role="list"
          aria-label={t("multipart.modelPicker")}
        >
          {isLoading && (
            <li className="p-4 text-sm text-muted-foreground">{t("multipart.loadingModels")}</li>
          )}
          {!isLoading && candidates.length === 0 && (
            <li className="p-4 text-sm text-muted-foreground">{t("multipart.noCandidates")}</li>
          )}
          {candidates.map((candidate) => {
            const alreadyAdded = usedIds.has(candidate.id);
            const unavailable = !candidate.available;
            return (
              <li key={candidate.id} role="listitem" className="last:border-b-0">
                <button
                  key={candidate.id}
                  type="button"
                  disabled={alreadyAdded || unavailable}
                  aria-disabled={alreadyAdded || unavailable}
                  onClick={() => {
                    onSelect(candidate);
                    onClose();
                  }}
                  className="flex w-full items-center gap-3 border-b border-border p-3 text-left hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <span className="h-12 w-12 shrink-0 overflow-hidden rounded border border-border bg-muted/40">
                    <Cover src={candidate.thumbnail_url} alt="" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium">
                      {modelLabel(candidate, t("multipart.unavailable"))}
                    </span>
                    <span className="block text-xs text-muted-foreground">
                      <Count
                        count={candidate.source_file_count}
                        one={t("multipart.sourceFile")}
                        many={t("multipart.sourceFiles")}
                      />{" "}
                      ·{" "}
                      <Count
                        count={candidate.gcode_revision_count}
                        one={t("multipart.gcodeRevision")}
                        many={t("multipart.gcodeRevisions")}
                      />
                    </span>
                    {alreadyAdded && (
                      <span className="block text-xs text-muted-foreground">
                        {t("multipart.alreadyAdded")}
                      </span>
                    )}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </div>
    </Modal>
  );
}

function MemberRow({ model }: { model: MultipartModelCandidate }) {
  const { t } = useI18n();
  const label = modelLabel(model, t("multipart.unavailable"));
  const legacyLabel = model.available ? model.legacy_label : null;
  const content = (
    <div className="flex min-w-0 items-center gap-3">
      <span className="h-10 w-10 shrink-0 overflow-hidden rounded border border-border bg-muted/40">
        <Cover src={model.thumbnail_url} alt="" />
      </span>
      <span className="min-w-0">
        <span
          className={cn(
            "block truncate text-sm font-medium",
            !model.available && "text-muted-foreground",
          )}
        >
          {label}
        </span>
        {legacyLabel && (
          <span className="block truncate text-xs text-muted-foreground">{legacyLabel}</span>
        )}
        <span className="block text-xs text-muted-foreground">
          <Count
            count={model.source_file_count}
            one={t("multipart.sourceFile")}
            many={t("multipart.sourceFiles")}
          />{" "}
          ·{" "}
          <Count
            count={model.gcode_revision_count}
            one={t("multipart.gcodeRevision")}
            many={t("multipart.gcodeRevisions")}
          />
        </span>
      </span>
    </div>
  );
  return model.available ? (
    <Link
      href={`/models/${model.id}`}
      className="min-w-0 flex-1 rounded-sm hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      {content}
    </Link>
  ) : (
    <div className="min-w-0 flex-1">{content}</div>
  );
}

function MultipartMemberCard({ model }: { model: MultipartModelCandidate }) {
  const { t } = useI18n();
  const label = modelLabel(model, t("multipart.unavailable"));
  const content = (
    <>
      <div className="h-3/5 shrink-0 overflow-hidden border-b border-border bg-muted/40">
        <Cover src={model.thumbnail_url} alt={model.available ? label : ""} />
      </div>
      <div className="flex min-h-0 flex-1 flex-col p-3">
        <h3
          className={cn(
            "line-clamp-2 text-sm font-bold uppercase tracking-tight",
            !model.available && "text-muted-foreground",
          )}
        >
          {label}
        </h3>
        {model.legacy_label && (
          <p className="mt-1 truncate text-xs text-muted-foreground">{model.legacy_label}</p>
        )}
        <div className="mt-auto flex flex-wrap gap-1.5 pt-3">
          <span className="rounded border border-border bg-muted px-2 py-0.5 font-mono text-2xs font-semibold uppercase text-muted-foreground">
            <Count
              count={model.source_file_count}
              one={t("multipart.sourceFile")}
              many={t("multipart.sourceFiles")}
            />
          </span>
          <span className="rounded border border-border bg-muted px-2 py-0.5 font-mono text-2xs font-semibold uppercase text-muted-foreground">
            <Count
              count={model.gcode_revision_count}
              one={t("multipart.gcodeRevision")}
              many={t("multipart.gcodeRevisions")}
            />
          </span>
        </div>
      </div>
    </>
  );

  return (
    <article className="group aspect-square w-full max-w-72 overflow-hidden rounded border border-border bg-card text-card-foreground transition-[border-color,transform] duration-press hover:border-primary active:scale-[0.99]">
      {model.available ? (
        <Link
          href={`/models/${model.id}`}
          className="flex h-full flex-col focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
        >
          {content}
        </Link>
      ) : (
        <div className="flex h-full flex-col">{content}</div>
      )}
    </article>
  );
}

function MultipartOverview({ model }: { model: MultipartModelRead }) {
  const { t } = useI18n();
  const cover =
    model.cover_thumbnail_url ??
    model.parts.flatMap((part) => part.models).find((member) => member.thumbnail_url)
      ?.thumbnail_url ??
    null;

  return (
    <div className="grid items-start gap-8 lg:grid-cols-[minmax(280px,420px)_minmax(0,1fr)]">
      <aside className="space-y-5 lg:sticky lg:top-0">
        <div className="aspect-[4/3] overflow-hidden rounded-lg border border-border bg-card">
          <Cover src={cover} alt={model.name} />
        </div>
        <div className="min-w-0">
          <div className="flex flex-wrap gap-2">
            <span className="rounded border border-border bg-muted px-2.5 py-1 font-mono text-xs font-semibold uppercase text-muted-foreground">
              <Count
                count={model.part_count}
                one={t("multipart.part")}
                many={t("multipart.parts")}
              />
            </span>
            <span className="rounded border border-border bg-muted px-2.5 py-1 font-mono text-xs font-semibold uppercase text-muted-foreground">
              <Count
                count={model.model_count}
                one={t("multipart.model")}
                many={t("multipart.models")}
              />
            </span>
            {model.guide_count > 0 && (
              <span className="rounded border border-border bg-muted px-2.5 py-1 font-mono text-xs font-semibold uppercase text-muted-foreground">
                {model.guide_count}{" "}
                {model.guide_count === 1 ? t("multipart.guide") : t("multipart.guides")}
              </span>
            )}
          </div>
          <p className="mt-5 max-w-3xl text-sm leading-6 text-muted-foreground">
            {model.description || t("multipart.workspaceHelp")}
          </p>
          {model.collection && (
            <p className="mt-4 text-xs font-medium text-muted-foreground">{model.collection}</p>
          )}
          {model.guides.length > 0 && (
            <div className="mt-6 border-t border-border pt-4">
              <h2 className="text-sm font-semibold">{t("multipart.guidesHeading")}</h2>
              <ul className="mt-2 flex flex-wrap gap-2">
                {model.guides.map((guide) => (
                  <li key={guide.id}>
                    <Link
                      href={`/documents/${guide.id}`}
                      className="inline-flex items-center gap-2 rounded-md border border-border bg-card px-3 py-2 text-sm font-medium transition-colors duration-press hover:border-primary hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    >
                      <FileText className="h-4 w-4 text-muted-foreground" aria-hidden />
                      {guide.name}
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </aside>

      <section className="min-w-0 space-y-5">
        <div>
          <h2 className="text-lg font-semibold">{t("multipart.partsHeading")}</h2>
          <p className="mt-1 text-sm text-muted-foreground">{t("multipart.workspaceHelp")}</p>
        </div>
        {model.parts.length === 0 ? (
          <EmptyState title={t("multipart.noParts")} description={t("multipart.noPartsHelp")} />
        ) : (
          <div className="space-y-8">
            {model.parts.map((part) => (
              <section key={part.id} aria-labelledby={`multipart-overview-part-${part.id}`}>
                <div className="mb-3 flex items-baseline gap-3 border-b border-border pb-2">
                  <h3 id={`multipart-overview-part-${part.id}`} className="font-semibold">
                    {part.name}
                  </h3>
                  <span className="text-xs text-muted-foreground">
                    {part.models.length > 1 ? t("multipart.variants") : t("multipart.fixedModel")}
                  </span>
                </div>
                <div className="flex flex-wrap gap-4">
                  {part.models.map((member) => (
                    <MultipartMemberCard
                      key={member.choice_id ?? `${member.id}-${member.source_file_id ?? "member"}`}
                      model={member}
                    />
                  ))}
                </div>
              </section>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

function PartEditorRow({
  part,
  index,
  onName,
  onRemoveModel,
  onRemovePart,
  onOpenPicker,
  onMoveUp,
  onMoveDown,
  canMoveDown,
}: {
  part: MultipartPartRead;
  index: number;
  onName: (name: string) => void;
  onRemoveModel: (choiceId: number | undefined, modelId: number) => void;
  onRemovePart: () => void;
  onOpenPicker: () => void;
  onMoveUp: () => void;
  onMoveDown: () => void;
  canMoveDown: boolean;
}) {
  const { t } = useI18n();
  return (
    <fieldset
      className="overflow-hidden rounded-lg border border-border bg-card"
      aria-labelledby={`multipart-part-${part.id}`}
    >
      <legend className="sr-only">{part.name}</legend>
      <div className="flex items-center gap-3 border-b border-border bg-muted/30 px-4 py-3">
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary">
          {index + 1}
        </span>
        <div className="min-w-0 flex-1">
          <label
            htmlFor={`multipart-part-${part.id}`}
            className="text-xs font-mono uppercase tracking-wider text-muted-foreground"
          >
            {t("multipart.partName")}
          </label>
          <Input
            id={`multipart-part-${part.id}`}
            value={part.name}
            onChange={(event) => onName(event.target.value)}
            placeholder={t("multipart.partNamePlaceholder")}
            className="mt-1"
          />
        </div>
        <div className="flex items-center gap-1">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={onMoveUp}
            disabled={index === 0}
            aria-label={`${t("multipart.moveUp")}: ${part.name}`}
          >
            <ArrowUp className="h-4 w-4" />
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={onMoveDown}
            disabled={!canMoveDown}
            aria-label={`${t("multipart.moveDown")}: ${part.name}`}
          >
            <ArrowDown className="h-4 w-4" />
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={onRemovePart}
            aria-label={`${t("multipart.removePart")}: ${index + 1}`}
          >
            <Trash2 className="h-4 w-4 text-destructive" />
          </Button>
        </div>
      </div>
      <div className="flex items-center justify-between gap-3 px-4 pt-4">
        <div>
          <h3 className="text-sm font-semibold">
            {part.models.length > 1 ? t("multipart.variants") : t("multipart.fixedModel")}
          </h3>
          <p className="text-xs text-muted-foreground">
            {part.models.length > 1 ? t("multipart.chooseOne") : t("multipart.fixedModelHelp")}
          </p>
        </div>
        <Button type="button" variant="outline" size="sm" onClick={onOpenPicker}>
          <Plus className="h-4 w-4" /> {t("multipart.addVariant")}
        </Button>
      </div>
      <div className="mx-4 my-3 divide-y divide-border rounded-md border border-border">
        {part.models.map((model) => (
          <div
            key={model.choice_id ?? `${model.id}-${model.source_file_id ?? "new"}`}
            className="flex items-center gap-3 p-3"
          >
            <MemberRow model={model} />
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => onRemoveModel(model.choice_id, model.id)}
              aria-label={`${t("multipart.removeModel")}: ${model.available ? modelLabel(model, t("multipart.unavailable")) : t("multipart.unavailable")}`}
            >
              <Trash2 className="h-4 w-4 text-muted-foreground" />
            </Button>
          </div>
        ))}
      </div>
    </fieldset>
  );
}

export function MultipartModelDetailPage() {
  const params = useParams<{ id: string }>();
  const id = Number(params.id);
  const { t } = useI18n();
  const { user } = useAuth();
  const router = useRouter();
  const searchParams = useSearchParams();
  const {
    data: serverModel,
    isLoading,
    error,
  } = useMultipartModel(Number.isFinite(id) ? id : null);
  const [persistedModel, setPersistedModel] = useState<MultipartModelRead | null>(null);
  const [draft, setDraft] = useState<MultipartModelRead | null>(null);
  const [isEditing, setIsEditing] = useState(false);
  const [pickerPart, setPickerPart] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [guideBusy, setGuideBusy] = useState(false);
  const guideInput = useRef<HTMLInputElement>(null);
  const savedModel = persistedModel ?? serverModel ?? null;
  const model = draft ?? savedModel;
  const canEdit =
    !!user?.is_superuser || model?.effective_role === "edit" || model?.effective_role === "admin";
  const usedIds = useMemo(
    () => new Set((model?.parts ?? []).flatMap((part) => part.models.map((member) => member.id))),
    [model?.parts],
  );
  function beginEditing() {
    if (!savedModel || !canEdit) return;
    setDraft(savedModel);
    setSaveError(null);
    setIsEditing(true);
  }
  function cancelEditing() {
    setDraft(null);
    setSaveError(null);
    setPickerPart(null);
    setIsEditing(false);
  }
  function updatePart(index: number, update: (part: MultipartPartRead) => MultipartPartRead) {
    setDraft((current) => {
      const base = current ?? savedModel;
      if (!base) return current;
      return {
        ...base,
        parts: base.parts.map((part, partIndex) => (partIndex === index ? update(part) : part)),
      };
    });
  }
  function addPart(candidate: MultipartModelCandidate) {
    setDraft((current) => {
      const base = current ?? savedModel;
      if (!base) return current;
      return {
        ...base,
        parts: [
          ...base.parts,
          {
            id: -Date.now(),
            name: t("multipart.partNumber", { number: String(base.parts.length + 1) }),
            sort_order: base.parts.length,
            models: [candidate],
          },
        ],
      };
    });
  }
  function addAlternative(candidate: MultipartModelCandidate) {
    if (pickerPart === null) return;
    updatePart(pickerPart, (part) => ({ ...part, models: [...part.models, candidate] }));
  }
  function movePart(index: number, direction: -1 | 1) {
    const target = index + direction;
    if (!model || target < 0 || target >= model.parts.length) return;
    const parts = [...model.parts];
    [parts[index], parts[target]] = [parts[target], parts[index]];
    setDraft({
      ...model,
      parts: parts.map((part, sort_order) => ({ ...part, sort_order })),
    });
  }
  async function uploadGuide(file: File) {
    if (!model || !canEdit || guideBusy) return;
    setGuideBusy(true);
    setSaveError(null);
    try {
      const guide = await uploadDocument(file, model.collection_id, undefined, model.id);
      setDraft({
        ...model,
        guides: [guide, ...model.guides],
        guide_count: model.guide_count + 1,
      });
      setPersistedModel((current) => {
        const base = current ?? serverModel;
        if (!base) return current;
        return {
          ...base,
          guides: [guide, ...base.guides],
          guide_count: base.guide_count + 1,
        };
      });
      toast.success(t("multipart.guideUploaded"));
    } catch (cause) {
      setSaveError(multipartError(cause, t, "multipart.guideUploadError"));
    } finally {
      setGuideBusy(false);
    }
  }
  async function removeGuide(guideId: number) {
    if (!model || !canEdit || guideBusy) return;
    setGuideBusy(true);
    try {
      await deleteDocument(guideId);
      setDraft({
        ...model,
        guides: model.guides.filter((guide) => guide.id !== guideId),
        guide_count: Math.max(0, model.guide_count - 1),
      });
      setPersistedModel((current) => {
        const base = current ?? serverModel;
        if (!base) return current;
        return {
          ...base,
          guides: base.guides.filter((guide) => guide.id !== guideId),
          guide_count: Math.max(0, base.guide_count - 1),
        };
      });
    } catch (cause) {
      setSaveError(multipartError(cause, t, "multipart.guideDeleteError"));
    } finally {
      setGuideBusy(false);
    }
  }
  async function save() {
    if (!model || !canEdit || busy) return;
    setBusy(true);
    setSaveError(null);
    try {
      const saved = await saveMultipartModel(model.id, {
        name: model.name,
        description: model.description ?? null,
        parts: model.parts.map((part) => ({
          name: part.name.trim(),
          choices: part.models.map((member) => {
            return { model_id: member.id, choice_id: member.choice_id ?? undefined };
          }),
        })),
      });
      setPersistedModel(saved);
      setDraft(null);
      setIsEditing(false);
      toast.success(t("multipart.saved"));
    } catch (cause) {
      setSaveError(multipartError(cause, t, "multipart.saveError"));
    } finally {
      setBusy(false);
    }
  }
  async function remove() {
    if (!model || busy) return;
    setBusy(true);
    try {
      await deleteMultipartModel(model.id);
      toast.success(t("multipart.deleted"));
      router.push(
        `/?v=multipart${searchParams.get("c") ? `&c=${encodeURIComponent(searchParams.get("c") ?? "")}` : ""}`,
      );
    } catch (cause) {
      setSaveError(multipartError(cause, t, "multipart.deleteError"));
    } finally {
      setBusy(false);
      setDeleteOpen(false);
    }
  }
  if (isLoading)
    return (
      <PageContainer>
        <Card className="h-96 animate-pulse bg-muted/40" />
      </PageContainer>
    );
  if (!model)
    return (
      <PageContainer>
        <EmptyState
          title={
            error ? multipartError(error, t, "multipart.detailError") : t("multipart.notFound")
          }
        />
      </PageContainer>
    );
  return (
    <PageContainer>
      <ConfirmModal
        open={deleteOpen}
        onClose={() => setDeleteOpen(false)}
        onConfirm={() => void remove()}
        busy={busy}
        title={t("multipart.deleteTitle")}
        description={t("multipart.deleteDescription")}
        confirmLabel={t("multipart.deleteConfirm")}
      />
      <nav className="text-sm text-muted-foreground">
        <button
          type="button"
          onClick={() => router.push("/?v=multipart")}
          className="hover:text-foreground"
        >
          {t("multipart.title")}
        </button>
        <ChevronRight className="mx-1 inline h-3 w-3" aria-hidden />
        {model.name}
      </nav>
      <PageHeader
        title={model.name}
        description={isEditing ? t("multipart.linkedNotice") : undefined}
        actions={
          isEditing ? (
            <div className="flex flex-wrap gap-2">
              <Button type="button" variant="outline" onClick={cancelEditing} disabled={busy}>
                {t("multipart.cancel")}
              </Button>
              <Button
                type="button"
                variant="outline"
                onClick={() => setDeleteOpen(true)}
                disabled={!canEdit || busy}
              >
                <Trash2 className="h-4 w-4" /> {t("multipart.delete")}
              </Button>
            </div>
          ) : canEdit ? (
            <Button type="button" onClick={beginEditing}>
              <Pencil className="h-4 w-4" /> {t("multipart.edit")}
            </Button>
          ) : undefined
        }
      />
      {isEditing ? (
        <>
          <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,2fr)_minmax(320px,1fr)]">
            <section className="space-y-4">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
                <div>
                  <h2 className="text-lg font-semibold">{t("multipart.partsHeading")}</h2>
                  <p className="mt-1 text-sm text-muted-foreground">{t("multipart.partsHelp")}</p>
                </div>
                <Button type="button" onClick={() => setPickerPart(-1)} disabled={!canEdit}>
                  <Plus className="h-4 w-4" />{" "}
                  {model.parts.length === 0 ? t("multipart.addFirst") : t("multipart.addAnother")}
                </Button>
              </div>
              {model.parts.length === 0 && (
                <EmptyState
                  title={t("multipart.noParts")}
                  description={t("multipart.noPartsHelp")}
                  action={
                    canEdit ? (
                      <Button onClick={() => setPickerPart(-1)}>{t("multipart.addFirst")}</Button>
                    ) : undefined
                  }
                />
              )}
              {model.parts.map((part, index) => (
                <PartEditorRow
                  key={part.id}
                  part={part}
                  index={index}
                  onName={(name) => updatePart(index, (current) => ({ ...current, name }))}
                  onMoveUp={() => movePart(index, -1)}
                  onMoveDown={() => movePart(index, 1)}
                  canMoveDown={index < model.parts.length - 1}
                  onRemovePart={() =>
                    setDraft({
                      ...model,
                      parts: model.parts.filter((_, partIndex) => partIndex !== index),
                    })
                  }
                  onRemoveModel={(choiceId, modelId) =>
                    setDraft({
                      ...model,
                      parts:
                        model.parts[index]?.models.length === 1
                          ? model.parts.filter((_, partIndex) => partIndex !== index)
                          : model.parts.map((current, partIndex) =>
                              partIndex === index
                                ? {
                                    ...current,
                                    models: current.models.filter((member) =>
                                      choiceId != null
                                        ? member.choice_id !== choiceId
                                        : member.id !== modelId,
                                    ),
                                  }
                                : current,
                            ),
                    })
                  }
                  onOpenPicker={() => setPickerPart(index)}
                />
              ))}
            </section>
            <aside className="space-y-4 xl:sticky xl:top-0">
              <section className="space-y-3 rounded-lg border border-border bg-card p-4">
                <div>
                  <h2 className="font-semibold">{t("multipart.detailsHeading")}</h2>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {t("multipart.linkedNotice")}
                  </p>
                </div>
                <label className="block space-y-1.5">
                  <span className="text-sm font-medium">{t("multipart.name")}</span>
                  <Input
                    value={model.name}
                    onChange={(event) => setDraft({ ...model, name: event.target.value })}
                    disabled={!canEdit}
                  />
                </label>
                <label className="block space-y-1.5">
                  <span className="text-sm font-medium">{t("multipart.descriptionLabel")}</span>
                  <textarea
                    value={model.description ?? ""}
                    onChange={(event) => setDraft({ ...model, description: event.target.value })}
                    disabled={!canEdit}
                    rows={3}
                    className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
                  />
                </label>
              </section>
              <section className="space-y-3 rounded-lg border border-border bg-card p-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <h2 className="font-semibold">{t("multipart.guidesHeading")}</h2>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {t("multipart.guidesHelp")}
                    </p>
                  </div>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => guideInput.current?.click()}
                    disabled={!canEdit || guideBusy}
                    loading={guideBusy}
                  >
                    <Upload className="h-4 w-4" /> {t("multipart.uploadGuide")}
                  </Button>
                  <input
                    ref={guideInput}
                    type="file"
                    accept=".pdf,.md,.markdown,.txt,.png,.jpg,.jpeg,.gif,.webp"
                    className="hidden"
                    onChange={(event) => {
                      const file = event.target.files?.[0];
                      event.target.value = "";
                      if (file) void uploadGuide(file);
                    }}
                  />
                </div>
                {model.guides.length === 0 ? (
                  <div className="rounded-md border border-dashed border-border p-5 text-center">
                    <FileText className="mx-auto h-6 w-6 text-muted-foreground" aria-hidden />
                    <p className="mt-2 text-sm font-medium">{t("multipart.noGuides")}</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {t("multipart.guideFormats")}
                    </p>
                  </div>
                ) : (
                  <ul className="divide-y divide-border rounded-md border border-border">
                    {model.guides.map((guide) => (
                      <li key={guide.id} className="flex items-center gap-3 p-3">
                        <FileText className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
                        <Link
                          href={`/documents/${guide.id}`}
                          className="min-w-0 flex-1 truncate text-sm font-medium hover:text-primary"
                        >
                          {guide.name}
                        </Link>
                        <span className="text-xs uppercase text-muted-foreground">
                          {guide.kind}
                        </span>
                        {canEdit && (
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            onClick={() => void removeGuide(guide.id)}
                            aria-label={`${t("multipart.removeGuide")}: ${guide.name}`}
                          >
                            <Trash2 className="h-4 w-4" />
                          </Button>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            </aside>
          </div>
          {saveError && (
            <p
              role="alert"
              className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive"
            >
              {saveError}
            </p>
          )}
          <div className="sticky bottom-16 flex justify-end border-t border-border bg-background/95 py-3 md:bottom-0">
            <Button
              type="button"
              onClick={() => void save()}
              loading={busy}
              disabled={!canEdit || !model.name.trim()}
            >
              {t("multipart.save")}
            </Button>
          </div>
          <ModelPicker
            open={pickerPart !== null}
            onClose={() => setPickerPart(null)}
            aggregateId={model.id}
            usedIds={usedIds}
            onSelect={(candidate) => {
              if (pickerPart === -1) addPart(candidate);
              else addAlternative(candidate);
            }}
          />
        </>
      ) : (
        <MultipartOverview model={model} />
      )}
    </PageContainer>
  );
}
