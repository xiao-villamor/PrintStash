"use client";

import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

import { useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  ChevronRight,
  Folder,
  CheckSquare,
  Square,
  ArrowLeft,
  ArrowDown,
  ArrowUp,
  FileText,
  Pencil,
  Plus,
  Search,
  Star,
  Trash2,
  Upload,
} from "lucide-react";

import { Modal } from "@/components/ui/modal";
import { EmptyState } from "@/components/ui/empty-state";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { CollectionPicker } from "@/components/collection-picker";
import { DropdownMenu } from "@/components/ui/dropdown-menu";
import { Card } from "@/components/ui/card";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { EntityTagsDialog } from "@/components/entity-tags-dialog";
import { useI18n } from "@/lib/i18n";
import { useCollectionChildren, useCollectionLookup, useTags } from "@/lib/queries";
import {
  deleteDocument,
  deleteMultipartModel,
  deleteMultipartModelCover,
  replaceMultipartModelTags,
  saveMultipartModel,
  starMultipartModel,
  unstarMultipartModel,
  uploadDocument,
  uploadMultipartModelCover,
} from "@/lib/api";
import {
  MULTIPART_CANDIDATE_PAGE_SIZE,
  useMultipartModel,
  useMultipartModelCandidates,
} from "@/lib/queries";
import { useAuth } from "@/lib/auth-context";
import { useRouter, useSearchParams } from "@/lib/navigation";
import { Link } from "@/lib/link";
import { useParams } from "react-router-dom";
import type {
  CollectionNodeRead,
  MultipartModelCandidate,
  MultipartModelListItem,
  MultipartModelRead,
  MultipartPartRead,
  TagRead,
} from "@/types";
import { toast } from "@/lib/toast";
import { cn } from "@/lib/utils";
import { queryKeys } from "@/lib/query-client";

import { Count, Cover } from "@/components/multipart-model-presentation";
import { multipartError, detailHref } from "@/lib/multipart-model-presentation";

export function MultipartModelCard({
  item,
  collectionLabel,
  returnTo,
  availableTags = [],
  onDataChange,
}: {
  item: MultipartModelListItem;
  collectionLabel?: string | null;
  returnTo?: string;
  availableTags?: TagRead[];
  onDataChange?: () => void;
}) {
  useUiLocale();
  const { t } = useI18n();
  const [starOverride, setStarOverride] = useState<{
    base: boolean;
    value: boolean;
  } | null>(null);
  const [starBusy, setStarBusy] = useState(false);
  const starred =
    starOverride !== null && starOverride.base === item.starred ? starOverride.value : item.starred;
  const canEditTags = item.effective_role === "edit" || item.effective_role === "admin";

  async function toggleStar() {
    if (starBusy) return;
    const next = !starred;
    setStarOverride({ base: item.starred, value: next });
    setStarBusy(true);
    try {
      await (next ? starMultipartModel(item.id) : unstarMultipartModel(item.id));
      onDataChange?.();
    } catch (cause) {
      setStarOverride({ base: item.starred, value: !next });
      toast.error(multipartError(cause, t, "multipart.favoriteError"));
    } finally {
      setStarBusy(false);
    }
  }

  async function saveCardTags(nextTags: string[]) {
    try {
      await replaceMultipartModelTags(item.id, nextTags);
      onDataChange?.();
      toast.success(t("multipart.tagsSaved"));
    } catch (cause) {
      toast.error(multipartError(cause, t, "multipart.tagsSaveError"));
      throw cause;
    }
  }

  return (
    <article className="animate-card-in group relative flex aspect-square h-full min-w-0 max-w-full flex-col overflow-hidden rounded border border-border bg-card text-card-foreground transition-[border-color,box-shadow,transform] duration-fast hover:-translate-y-0.5 hover:border-primary active:scale-[0.99]">
      <button
        type="button"
        onClick={() => void toggleStar()}
        disabled={starBusy}
        aria-label={
          starred
            ? uiText("Remove {value1} from favorites", { value1: String(item.name) })
            : uiText("Add {value1} to favorites", { value1: String(item.name) })
        }
        className="absolute right-2 top-2 z-10 rounded bg-card/90 p-2 text-muted-foreground shadow-sm transition-[color,background-color,transform] duration-press ease-out hover:bg-card hover:text-primary active:scale-[0.98] disabled:opacity-50"
      >
        <Star className={cn("h-4 w-4", starred && "fill-current text-primary")} />
      </button>
      {canEditTags && (
        <div className="absolute right-2 top-12 z-10">
          <EntityTagsDialog
            entityLabel={item.name}
            tags={item.tags}
            availableTags={availableTags}
            canEdit
            help={t("multipart.tagsHelp")}
            onSave={saveCardTags}
            triggerMode="icon"
            triggerClassName="bg-card/90 text-muted-foreground shadow-sm hover:bg-card hover:text-primary"
          />
        </div>
      )}
      <Link
        href={detailHref(item.id, returnTo)}
        aria-label={item.name}
        className="flex h-full min-w-0 flex-col overflow-hidden focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
      >
        <div className="relative h-3/5 shrink-0 overflow-hidden border-b border-border bg-muted/40">
          <Cover src={item.cover_thumbnail_url} alt="" />
          <span className="absolute left-2 top-2 max-w-[calc(100%-6rem)] truncate rounded border border-primary/30 bg-card/90 px-2 py-1 font-mono text-3xs font-semibold uppercase tracking-wider text-primary shadow-sm">
            {t("multipart.badge")}
          </span>
        </div>
        <div className="flex min-h-0 min-w-0 flex-1 flex-col p-3">
          <h3
            title={item.name}
            className="line-clamp-2 break-words text-sm font-bold tracking-tight"
          >
            {item.name}
          </h3>
          <p className="mt-1 truncate text-xs text-muted-foreground">
            <Count count={item.part_count} one={t("multipart.part")} many={t("multipart.parts")} />
            {" · "}
            <Count
              count={item.model_count}
              one={t("multipart.model")}
              many={t("multipart.models")}
            />
            {item.guide_count > 0 && (
              <>
                {" · "}
                {item.guide_count}{" "}
                {item.guide_count === 1 ? t("multipart.guide") : t("multipart.guides")}
              </>
            )}
          </p>
          <div className="mt-auto flex min-w-0 items-center gap-1 overflow-hidden border-t border-border pt-2">
            <div className="flex min-w-0 flex-1 gap-1 overflow-hidden">
              {item.tags.slice(0, 2).map((tag) => (
                <span
                  key={tag}
                  title={tag}
                  className="max-w-24 truncate rounded border border-primary-soft bg-accent px-1.5 py-0.5 font-mono text-3xs font-semibold tracking-wider text-accent-foreground"
                >
                  {tag}
                </span>
              ))}
              {item.tags.length > 2 && (
                <span className="shrink-0 text-3xs text-muted-foreground">
                  +{item.tags.length - 2}
                </span>
              )}
            </div>
            {collectionLabel && (
              <span
                title={collectionLabel}
                className="max-w-[45%] shrink truncate text-xs text-muted-foreground"
              >
                {collectionLabel}
              </span>
            )}
          </div>
        </div>
      </Link>
    </article>
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
  variants,
  onSelect,
}: {
  open: boolean;
  onClose: () => void;
  aggregateId: number;
  usedIds: Set<number>;
  variants: boolean;
  onSelect: (models: MultipartModelCandidate[]) => void;
}) {
  const { t } = useI18n();
  const [query, setQuery] = useState("");
  const [collection, setCollection] = useState<CollectionNodeRead | null>(null);
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<Map<number, MultipartModelCandidate>>(new Map());
  const children = useCollectionChildren(collection?.id ?? null, { enabled: open });
  const lookup = useCollectionLookup(collection?.path ?? null);
  const {
    data: candidates = [],
    isLoading,
    isError,
    refetch,
  } = useMultipartModelCandidates(aggregateId, query, {
    enabled: open,
    collection: collection?.path,
    direct: collection !== null,
    offset,
  });
  const ancestors = lookup.data
    ? [...lookup.data.ancestors, lookup.data.collection]
    : collection
      ? [collection]
      : [];
  const folders = children.data?.pages.flatMap((page) => page.items) ?? [];
  function navigate(next: CollectionNodeRead | null) {
    setCollection(next);
    setOffset(0);
  }
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={t("multipart.modelPicker")}
      className="flex max-h-[90dvh] max-w-5xl flex-col"
    >
      <div className="flex min-h-0 flex-col gap-4">
        <p className="text-sm text-muted-foreground">
          {t(variants ? "multipart.pickVariantsHelp" : "multipart.pickPartsHelp")}
        </p>
        <div className="flex items-center gap-2">
          <Search className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
          <Input
            autoFocus
            aria-label={t("multipart.searchModels")}
            value={query}
            maxLength={128}
            onChange={(event) => {
              setQuery(event.target.value);
              setOffset(0);
            }}
            placeholder={t("multipart.searchModels")}
          />
        </div>
        <nav
          aria-label={t("multipart.browseCollections")}
          className="flex flex-wrap items-center gap-1 text-sm"
        >
          <Button
            variant="ghost"
            size="sm"
            onClick={() => navigate(null)}
            aria-current={collection === null ? "page" : undefined}
          >
            {t("multipart.allModels")}
          </Button>
          {ancestors.map((item) => (
            <span key={item.id} className="flex min-w-0 items-center gap-1">
              <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
              <Button
                variant="ghost"
                size="sm"
                className="h-auto whitespace-normal break-words text-left"
                onClick={() => navigate(item)}
                aria-current={item.id === collection?.id ? "page" : undefined}
              >
                {item.name}
              </Button>
            </span>
          ))}
        </nav>
        <div className="min-h-0 overflow-y-auto overscroll-contain pr-1">
          {children.isError && (
            <div role="alert" className="mb-3 flex flex-wrap items-center gap-2 text-sm">
              <span>{t("multipart.collectionsError")}</span>
              <Button variant="outline" size="sm" onClick={() => void children.refetch()}>
                {t("multipart.retry")}
              </Button>
            </div>
          )}
          {folders.length > 0 && (
            <ul
              aria-label={t("multipart.browseCollections")}
              className="mb-4 grid grid-cols-1 gap-2 sm:grid-cols-3"
            >
              {folders.map((folder) => (
                <li key={folder.id}>
                  <Button
                    variant="outline"
                    className="h-auto min-h-11 w-full justify-start whitespace-normal break-words text-left"
                    onClick={() => navigate(folder)}
                  >
                    <Folder className="mr-2 h-4 w-4 shrink-0" aria-hidden />
                    {folder.name}
                  </Button>
                </li>
              ))}
            </ul>
          )}
          {children.hasNextPage && (
            <Button
              variant="outline"
              size="sm"
              loading={children.isFetchingNextPage}
              onClick={() => void children.fetchNextPage()}
            >
              {uiText("Show more folders")}
            </Button>
          )}
          {isLoading && (
            <p role="status" className="py-8 text-sm text-muted-foreground">
              {t("multipart.loadingModels")}
            </p>
          )}
          {isError && (
            <div role="alert" className="flex flex-wrap items-center gap-3 py-8 text-sm">
              <span>{t("multipart.candidatesError")}</span>
              <Button variant="outline" onClick={() => void refetch()}>
                {t("multipart.retry")}
              </Button>
            </div>
          )}
          {!isLoading && !isError && candidates.length === 0 && (
            <p className="py-8 text-sm text-muted-foreground">{t("multipart.noCandidates")}</p>
          )}
          <ul
            aria-label={t("multipart.modelPicker")}
            className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4"
          >
            {!isError &&
              candidates.slice(0, MULTIPART_CANDIDATE_PAGE_SIZE).map((candidate) => {
                const alreadyAdded = usedIds.has(candidate.id);
                const checked = selected.has(candidate.id);
                return (
                  <li key={candidate.id} className="min-w-0">
                    <button
                      type="button"
                      disabled={alreadyAdded || !candidate.available}
                      aria-pressed={checked}
                      aria-label={modelLabel(candidate, t("multipart.unavailable"))}
                      onClick={() =>
                        setSelected((current) => {
                          const next = new Map(current);
                          if (next.has(candidate.id)) next.delete(candidate.id);
                          else next.set(candidate.id, candidate);
                          return next;
                        })
                      }
                      className={cn(
                        "relative flex h-full w-full flex-col overflow-hidden rounded-lg border border-border text-left transition-transform duration-press active:scale-[0.99] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50",
                        checked
                          ? "bg-accent text-accent-foreground"
                          : "bg-card text-card-foreground hover:bg-muted",
                      )}
                    >
                      <span className="aspect-[4/3] w-full overflow-hidden bg-muted">
                        <Cover src={candidate.thumbnail_url} alt="" />
                      </span>
                      <span
                        className="absolute right-2 top-2 rounded bg-background p-1 text-foreground"
                        aria-hidden
                      >
                        {checked ? (
                          <CheckSquare className="h-5 w-5" />
                        ) : (
                          <Square className="h-5 w-5" />
                        )}
                      </span>
                      <span className="flex w-full flex-1 flex-col gap-1 p-3">
                        <span className="break-words text-sm font-medium">
                          {modelLabel(candidate, t("multipart.unavailable"))}
                        </span>
                        <span className="text-xs text-muted-foreground">
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
                          <span className="text-xs text-muted-foreground">
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
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-3">
          <p role="status" className="text-sm text-muted-foreground">
            {t("multipart.selectedCount", { count: String(selected.size) })}
          </p>
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={offset === 0 || isLoading}
              onClick={() => setOffset(offset - MULTIPART_CANDIDATE_PAGE_SIZE)}
            >
              {t("multipart.previousPage")}
            </Button>
            <span className="text-sm tabular-nums">
              {t("multipart.pageNumber", {
                number: String(offset / MULTIPART_CANDIDATE_PAGE_SIZE + 1),
              })}
            </span>
            <Button
              variant="outline"
              size="sm"
              disabled={candidates.length <= MULTIPART_CANDIDATE_PAGE_SIZE || isLoading || isError}
              onClick={() => setOffset(offset + MULTIPART_CANDIDATE_PAGE_SIZE)}
            >
              {t("multipart.nextPage")}
            </Button>
          </div>
          <div className="flex w-full items-center justify-end gap-2 sm:w-auto">
            <Button variant="ghost" onClick={onClose}>
              {t("multipart.cancel")}
            </Button>
            <Button
              disabled={!open || selected.size === 0}
              onClick={() => {
                onSelect([...selected.values()]);
                onClose();
              }}
            >
              {t(variants ? "multipart.addSelectedVariants" : "multipart.addSelectedParts", {
                count: String(selected.size),
              })}
            </Button>
          </div>
        </div>
      </div>
    </Modal>
  );
}

function MemberRow({ model }: { model: MultipartModelCandidate }) {
  useUiLocale();
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
  useUiLocale();
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
            "line-clamp-2 text-sm font-bold tracking-tight",
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

function MultipartOverview({
  model,
  onAddFirst,
}: {
  model: MultipartModelRead;
  onAddFirst?: () => void;
}) {
  useUiLocale();
  const { t } = useI18n();
  const members = model.parts.flatMap((part) => part.models);
  const coverMember =
    members.find((member) => member.id === model.cover_model_id) ??
    members.find((member) => member.thumbnail_url) ??
    members[0] ??
    null;
  const cover = model.cover_thumbnail_url ?? coverMember?.thumbnail_url ?? null;

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-x-hidden overflow-y-auto pb-24 md:flex-row md:overflow-hidden md:pb-0">
      <section className="relative m-2 flex min-h-80 min-w-0 flex-1 items-center justify-center overflow-hidden rounded border border-outline-variant bg-surface-container-low md:m-4 md:min-h-0">
        <div className="h-full w-full p-8 sm:p-12 lg:p-16">
          <Cover src={cover} alt={model.name} />
        </div>
        <div className="absolute bottom-4 left-4 max-w-[calc(100%-2rem)] rounded border border-outline-variant bg-surface-container-lowest/95 px-3 py-2">
          <p className="truncate text-sm font-medium text-on-surface">
            {model.cover_image_uploaded
              ? t("multipart.uploadedCover")
              : model.cover_image_url
                ? t("multipart.customCover")
                : coverMember
                  ? modelLabel(coverMember, t("multipart.unavailable"))
                  : model.name}
          </p>
          <p className="font-mono text-3xs uppercase tracking-wider text-on-surface-variant">
            {t("multipart.coverBadge")}
          </p>
        </div>
      </section>

      <aside className="min-h-0 min-w-0 max-w-full shrink-0 border-t border-outline-variant bg-surface-container-lowest md:h-full md:w-[clamp(24rem,44vw,47.5rem)] md:border-l md:border-t-0">
        <div className="min-w-0 md:h-full md:overflow-y-auto">
          <section className="space-y-4 border-b border-outline-variant p-5">
            <h2 className="text-lg font-semibold text-on-surface">{t("multipart.contents")}</h2>
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
            {model.tags.length > 0 && (
              <div className="flex flex-wrap gap-1.5" aria-label={t("multipart.tagsLabel")}>
                {model.tags.map((tag) => (
                  <span
                    key={tag}
                    className="rounded-full border border-outline-variant bg-surface-container-low px-2 py-0.5 font-mono text-3xs tracking-wider text-on-surface-variant"
                  >
                    {tag}
                  </span>
                ))}
              </div>
            )}
            <dl className="divide-y divide-border rounded-md border border-border">
              <div className="px-3 py-3">
                <dt className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  {t("multipart.descriptionLabel")}
                </dt>
                <dd className="mt-1 whitespace-pre-wrap text-sm leading-6 text-foreground">
                  {model.description || t("multipart.noDescription")}
                </dd>
              </div>
              <div className="px-3 py-3">
                <dt className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  {t("multipart.collectionLabel")}
                </dt>
                <dd className="mt-1 text-sm font-medium text-foreground">
                  {model.collection_label || t("multipart.vaultOnly")}
                </dd>
              </div>
            </dl>
            {model.guides.length > 0 && (
              <div className="border-t border-border pt-4">
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
          </section>

          <section className="space-y-6 p-5">
            <div>
              <h2 className="text-lg font-semibold">{t("multipart.partsHeading")}</h2>
              <p className="mt-1 text-sm text-muted-foreground">{t("multipart.workspaceHelp")}</p>
            </div>
            {model.parts.length === 0 ? (
              <EmptyState
                title={t("multipart.noParts")}
                description={t("multipart.noPartsHelp")}
                action={
                  onAddFirst ? (
                    <Button type="button" size="sm" onClick={onAddFirst}>
                      <Plus className="h-4 w-4" /> {t("multipart.addFirst")}
                    </Button>
                  ) : undefined
                }
                className="py-10"
              />
            ) : (
              <div className="space-y-8">
                {model.parts.map((part, partIndex) => (
                  <section key={part.id} aria-labelledby={`multipart-overview-part-${part.id}`}>
                    <div className="mb-3 flex items-baseline gap-3 border-b border-border pb-2">
                      <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary">
                        {partIndex + 1}
                      </span>
                      <h3 id={`multipart-overview-part-${part.id}`} className="font-semibold">
                        {part.name}
                      </h3>
                      <span className="text-xs text-muted-foreground">
                        {part.models.length > 1
                          ? t("multipart.variants")
                          : t("multipart.fixedModel")}
                      </span>
                    </div>
                    <div className="grid grid-cols-1 gap-4 sm:grid-cols-[repeat(auto-fill,minmax(240px,240px))]">
                      {part.models.map((member) => (
                        <MultipartMemberCard
                          key={
                            member.choice_id ?? `${member.id}-${member.source_file_id ?? "member"}`
                          }
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
      </aside>
    </div>
  );
}

function PartEditorRow({
  part,
  index,
  onName,
  onQuantity,
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
  onQuantity: (quantity: number) => void;
  onRemoveModel: (choiceId: number | undefined, modelId: number) => void;
  onRemovePart: () => void;
  onOpenPicker: () => void;
  onMoveUp: () => void;
  onMoveDown: () => void;
  canMoveDown: boolean;
}) {
  useUiLocale();
  const { t } = useI18n();
  return (
    <fieldset
      className="min-w-0 overflow-hidden rounded-lg border border-border bg-card"
      aria-labelledby={`multipart-part-${part.id}`}
    >
      <legend className="sr-only">{part.name}</legend>
      <div className="grid grid-cols-[auto_minmax(0,1fr)] items-start gap-x-3 gap-y-2 border-b border-border bg-muted/30 px-4 py-3 sm:grid-cols-[auto_minmax(0,1fr)_auto] sm:items-center">
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
        <label className="col-start-2 space-y-1 text-xs text-muted-foreground">
          {t("build.quantity")}
          <Input
            type="number"
            min={1}
            max={10000}
            value={part.quantity}
            onChange={(event) => onQuantity(Number(event.target.value))}
          />
        </label>
        <div className="col-start-2 flex items-center gap-1 sm:col-start-3 sm:row-start-1">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={onMoveUp}
            disabled={index === 0}
            aria-label={`${t("multipart.moveUp")}: ${part.name}`}
            className="h-11 w-11 sm:h-9 sm:w-9"
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
            className="h-11 w-11 sm:h-9 sm:w-9"
          >
            <ArrowDown className="h-4 w-4" />
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={onRemovePart}
            aria-label={`${t("multipart.removePart")}: ${index + 1}`}
            className="h-11 w-11 sm:h-9 sm:w-9"
          >
            <Trash2 className="h-4 w-4 text-destructive" />
          </Button>
        </div>
      </div>
      <div className="flex min-w-0 flex-col items-stretch gap-3 px-4 pt-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold">
            {part.models.length > 1 ? t("multipart.variants") : t("multipart.fixedModel")}
          </h3>
          <p className="text-xs text-muted-foreground">
            {part.models.length > 1 ? t("multipart.chooseOne") : t("multipart.fixedModelHelp")}
          </p>
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={onOpenPicker}
          className="h-11 w-full sm:h-9 sm:w-auto"
        >
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
              className="h-11 w-11 sm:h-9 sm:w-9"
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
  useUiLocale();
  const params = useParams<{ id: string }>();
  const id = Number(params.id);
  const { t } = useI18n();
  const { user } = useAuth();
  const { data: availableTags = [] } = useTags();
  const queryClient = useQueryClient();
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
  const [collectionPickerOpen, setCollectionPickerOpen] = useState(false);
  const [picker, setPicker] = useState({ open: false, part: -1, session: 0 });
  function openPicker(part: number) {
    setPicker((current) => ({ open: true, part, session: current.session + 1 }));
  }
  function closePicker() {
    setPicker((current) => ({ ...current, open: false }));
  }
  const [busy, setBusy] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [guideBusy, setGuideBusy] = useState(false);
  const [coverBusy, setCoverBusy] = useState(false);
  const guideInput = useRef<HTMLInputElement>(null);
  const coverInput = useRef<HTMLInputElement>(null);
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
  function beginAddingFirst() {
    if (!savedModel || !canEdit) return;
    setDraft(savedModel);
    setSaveError(null);
    setIsEditing(true);
    openPicker(-1);
  }
  function cancelEditing() {
    setDraft(null);
    setSaveError(null);
    closePicker();
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
  function addPart(candidates: MultipartModelCandidate[]) {
    setDraft((current) => {
      const base = current ?? savedModel;
      if (!base) return current;
      const firstDraftId =
        base.parts.reduce((smallest, part) => Math.min(smallest, part.id), 0) - 1;
      return {
        ...base,
        parts: [
          ...base.parts,
          ...candidates.map((candidate, index) => ({
            id: firstDraftId - index,
            quantity: 1,
            name: t("multipart.partNumber", { number: String(base.parts.length + index + 1) }),
            sort_order: base.parts.length + index,
            models: [candidate],
          })),
        ],
      };
    });
  }
  function addAlternative(candidates: MultipartModelCandidate[]) {
    if (!picker.open) return;
    updatePart(picker.part, (part) => ({ ...part, models: [...part.models, ...candidates] }));
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
  function applySavedCover(saved: MultipartModelRead) {
    setPersistedModel(saved);
    setDraft((current) =>
      current
        ? {
            ...current,
            cover_image_url: saved.cover_image_url,
            cover_image_uploaded: saved.cover_image_uploaded,
            cover_thumbnail_url: saved.cover_thumbnail_url,
          }
        : current,
    );
    void queryClient.invalidateQueries({ queryKey: queryKeys.multipartModels });
  }
  async function uploadCover(file: File) {
    if (!model || !canEdit || coverBusy) return;
    setCoverBusy(true);
    setSaveError(null);
    try {
      const saved = await uploadMultipartModelCover(model.id, file);
      applySavedCover(saved);
      toast.success(t("multipart.coverUploaded"));
    } catch (cause) {
      setSaveError(multipartError(cause, t, "multipart.coverUploadError"));
    } finally {
      setCoverBusy(false);
    }
  }
  async function removeCover() {
    if (!model || !canEdit || coverBusy) return;
    setCoverBusy(true);
    setSaveError(null);
    try {
      applySavedCover(await deleteMultipartModelCover(model.id));
      toast.success(t("multipart.coverRemoved"));
    } catch (cause) {
      setSaveError(multipartError(cause, t, "multipart.coverRemoveError"));
    } finally {
      setCoverBusy(false);
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
        collection_id: model.collection_id,
        cover_model_id:
          model.cover_model_id !== null && usedIds.has(model.cover_model_id)
            ? model.cover_model_id
            : null,
        cover_image_url: model.cover_image_url,
        parts: model.parts.map((part) => ({
          name: part.name.trim(),
          quantity: part.quantity,
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
  async function saveTags(nextTags: string[]) {
    if (!model || !canEdit) return;
    try {
      const saved = await replaceMultipartModelTags(model.id, nextTags);
      setPersistedModel(saved);
      setDraft((current) => (current ? { ...current, tags: saved.tags } : current));
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.tags }),
        queryClient.invalidateQueries({ queryKey: queryKeys.multipartModels }),
      ]);
      toast.success(t("multipart.tagsSaved"));
    } catch (cause) {
      toast.error(multipartError(cause, t, "multipart.tagsSaveError"));
      throw cause;
    }
  }
  async function remove() {
    if (!model || busy) return;
    setBusy(true);
    try {
      await deleteMultipartModel(model.id);
      toast.success(t("multipart.deleted"));
      router.push(backHref);
    } catch (cause) {
      setSaveError(multipartError(cause, t, "multipart.deleteError"));
    } finally {
      setBusy(false);
      setDeleteOpen(false);
    }
  }
  if (isLoading)
    return (
      <div className="flex h-full items-center justify-center p-6">
        <Card className="h-96 w-full max-w-5xl animate-pulse bg-muted/40" />
      </div>
    );
  if (!model)
    return (
      <div className="flex h-full items-center justify-center p-6">
        <EmptyState
          title={
            error ? multipartError(error, t, "multipart.detailError") : t("multipart.notFound")
          }
        />
      </div>
    );
  const requestedReturn = searchParams.get("return");
  const backHref =
    requestedReturn?.startsWith("/") && !requestedReturn.startsWith("//")
      ? requestedReturn
      : model.collection
        ? `/?c=${encodeURIComponent(model.collection)}`
        : "/";
  return (
    <div className="flex h-full min-h-0 flex-col bg-background">
      <ConfirmModal
        open={deleteOpen}
        onClose={() => setDeleteOpen(false)}
        onConfirm={() => void remove()}
        busy={busy}
        title={t("multipart.deleteTitle")}
        description={t("multipart.deleteDescription")}
        confirmLabel={t("multipart.deleteConfirm")}
      />
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-outline-variant bg-surface-container-lowest px-4 py-3 md:px-6">
        <div className="flex min-w-0 flex-1 items-center gap-3 sm:gap-4">
          <Link
            href={backHref}
            aria-label={t("multipart.title")}
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded text-on-surface-variant transition-colors duration-press hover:bg-surface-container-high"
          >
            <ArrowLeft className="h-5 w-5" />
          </Link>
          <div className="min-w-0">
            <h1 className="truncate text-xl font-semibold leading-tight text-on-surface">
              {model.name}
            </h1>
            <p className="truncate font-mono text-xs text-on-surface-variant">
              <Count
                count={model.part_count}
                one={t("multipart.part")}
                many={t("multipart.parts")}
              />
              {" · "}
              <Count
                count={model.model_count}
                one={t("multipart.model")}
                many={t("multipart.models")}
              />
              {model.guide_count > 0 && (
                <>
                  {" · "}
                  {model.guide_count}{" "}
                  {model.guide_count === 1 ? t("multipart.guide") : t("multipart.guides")}
                </>
              )}
            </p>
          </div>
        </div>
        {isEditing ? (
          <div className="flex w-full flex-wrap gap-2 sm:w-auto">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={cancelEditing}
              disabled={busy}
              className="h-11 sm:h-9"
            >
              {t("multipart.cancel")}
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setDeleteOpen(true)}
              disabled={!canEdit || busy}
              className="h-11 sm:h-9"
            >
              <Trash2 className="h-4 w-4" /> {t("multipart.delete")}
            </Button>
          </div>
        ) : canEdit ? (
          <div className="flex flex-wrap items-center gap-3">
            <Link className="text-sm text-primary underline" href={`/builds?multipart=${model.id}`}>
              {t("build.create")}
            </Link>
            <Button type="button" size="sm" onClick={beginEditing}>
              <Pencil className="h-4 w-4" /> {t("multipart.edit")}
            </Button>
          </div>
        ) : undefined}
      </header>
      {isEditing ? (
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-5 pb-24 sm:px-6 md:pb-6">
          <div className="grid min-w-0 items-start gap-6 xl:grid-cols-[minmax(0,2fr)_minmax(320px,1fr)]">
            <section className="min-w-0 space-y-4">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
                <div>
                  <h2 className="text-lg font-semibold">{t("multipart.partsHeading")}</h2>
                  <p className="mt-1 text-sm text-muted-foreground">{t("multipart.partsHelp")}</p>
                </div>
                <Button
                  type="button"
                  onClick={() => openPicker(-1)}
                  disabled={!canEdit}
                  className="h-11 sm:h-10"
                >
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
                      <Button onClick={() => openPicker(-1)}>{t("multipart.addFirst")}</Button>
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
                  onQuantity={(quantity) =>
                    updatePart(index, (current) => ({ ...current, quantity }))
                  }
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
                  onOpenPicker={() => openPicker(index)}
                />
              ))}
            </section>
            <aside className="min-w-0 space-y-4 xl:sticky xl:top-0">
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
                <div className="space-y-1.5">
                  <span className="block text-sm font-medium">{t("multipart.tagsLabel")}</span>
                  <EntityTagsDialog
                    entityLabel={model.name}
                    tags={model.tags}
                    availableTags={availableTags}
                    canEdit={canEdit}
                    help={t("multipart.tagsHelp")}
                    onSave={saveTags}
                  />
                </div>
                <div className="space-y-1.5">
                  <span className="block text-sm font-medium">
                    {t("multipart.collectionLabel")}
                  </span>
                  <DropdownMenu
                    open={collectionPickerOpen}
                    onOpenChange={setCollectionPickerOpen}
                    role="dialog"
                    align="start"
                    contentClassName="w-80 max-w-[90vw] p-2"
                    trigger={
                      <button
                        type="button"
                        data-menu-trigger
                        onClick={() => setCollectionPickerOpen((open) => !open)}
                        disabled={!canEdit}
                        aria-haspopup="dialog"
                        aria-expanded={collectionPickerOpen}
                        aria-label={t("multipart.collectionLabel")}
                        className="h-10 w-full rounded-md border border-input bg-background px-3 text-left text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
                      >
                        {model.collection_label || t("multipart.vaultOnly")}
                      </button>
                    }
                  >
                    <CollectionPicker
                      minRole="edit"
                      selectedPath={model.collection ?? ""}
                      noneLabel={user?.is_superuser ? t("multipart.vaultOnly") : undefined}
                      emptyLabel={uiText("No editable collections.")}
                      onSelect={(collection) => {
                        setDraft({
                          ...model,
                          collection_id: collection?.id ?? null,
                          collection: collection?.path ?? null,
                          collection_label: collection?.display_path ?? null,
                        });
                        setCollectionPickerOpen(false);
                      }}
                    />
                  </DropdownMenu>
                  <span
                    id="multipart-collection-help"
                    className="block text-xs text-muted-foreground"
                  >
                    {t("multipart.collectionHelp")}
                  </span>
                </div>
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
                <div className="space-y-3">
                  <div>
                    <span className="block text-sm font-medium">{t("multipart.coverImage")}</span>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {t("multipart.coverUploadHelp")}
                    </p>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => coverInput.current?.click()}
                      disabled={!canEdit || coverBusy}
                      loading={coverBusy}
                    >
                      <Upload className="h-4 w-4" />
                      {model.cover_image_uploaded
                        ? t("multipart.replaceCover")
                        : t("multipart.uploadCover")}
                    </Button>
                    {model.cover_image_uploaded && (
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        onClick={() => void removeCover()}
                        disabled={!canEdit || coverBusy}
                        aria-label={t("multipart.removeUploadedCover")}
                      >
                        <Trash2 className="h-4 w-4" /> {t("multipart.removeCover")}
                      </Button>
                    )}
                    <input
                      ref={coverInput}
                      type="file"
                      accept="image/png,image/jpeg,image/webp"
                      className="hidden"
                      aria-label={t("multipart.uploadCover")}
                      onChange={(event) => {
                        const file = event.target.files?.[0];
                        event.target.value = "";
                        if (file) void uploadCover(file);
                      }}
                    />
                  </div>
                  {model.cover_image_uploaded && (
                    <p className="text-xs font-medium text-primary" role="status">
                      {t("multipart.uploadedFromComputer")}
                    </p>
                  )}
                  <div className="space-y-1.5 border-t border-border pt-3">
                    <label htmlFor="multipart-cover-image" className="block text-sm font-medium">
                      {t("multipart.coverImageUrl")}
                    </label>
                    <Input
                      id="multipart-cover-image"
                      type="url"
                      aria-describedby="multipart-cover-image-help"
                      value={model.cover_image_url ?? ""}
                      onChange={(event) =>
                        setDraft({
                          ...model,
                          cover_image_url: event.target.value || null,
                        })
                      }
                      placeholder={t("multipart.coverImagePlaceholder")}
                      disabled={!canEdit || model.cover_image_uploaded}
                    />
                    <span
                      id="multipart-cover-image-help"
                      className="block text-xs text-muted-foreground"
                    >
                      {model.cover_image_uploaded
                        ? t("multipart.coverImageUrlDisabled")
                        : t("multipart.coverImageHelp")}
                    </span>
                  </div>
                </div>
              </section>
              <section className="space-y-3 rounded-lg border border-border bg-card p-4">
                <div className="flex flex-col items-stretch gap-3 sm:flex-row sm:items-start sm:justify-between">
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
          <div className="mt-4 flex justify-end border-t border-border bg-background py-3 xl:sticky xl:bottom-0">
            <Button
              type="button"
              onClick={() => void save()}
              loading={busy}
              disabled={!canEdit || !model.name.trim()}
              className="h-11 sm:h-10"
            >
              {t("multipart.save")}
            </Button>
          </div>
          <ModelPicker
            key={picker.session}
            open={picker.open}
            variants={picker.part !== -1}
            onClose={() => closePicker()}
            aggregateId={model.id}
            usedIds={usedIds}
            onSelect={(candidate) => {
              if (picker.part === -1) addPart(candidate);
              else addAlternative(candidate);
            }}
          />
        </div>
      ) : (
        <MultipartOverview model={model} onAddFirst={canEdit ? beginAddingFirst : undefined} />
      )}
    </div>
  );
}
