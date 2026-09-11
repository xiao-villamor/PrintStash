import { useEffect, useRef, useState } from "react";
import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, MoreHorizontal, Plus, Printer, Star } from "lucide-react";
import { EntityTagsDialog } from "@/components/entity-tags-dialog";
import { SendToButtons } from "@/components/model-detail/send-to-buttons";
import { Button } from "@/components/ui/button";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { DropdownMenu } from "@/components/ui/dropdown-menu";
import { Input, inputClasses } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { PageContainer } from "@/components/ui/page-container";
import { PageHeader } from "@/components/ui/page-header";
import {
  detachFamilyMember,
  getFamily,
  listFamilyMembers,
  starFamily,
  starVisibleFamilyModels,
  trashFamily,
  updateFamily,
} from "@/lib/api/families";
import { getModel, getModelPrinterFiles } from "@/lib/api/models";
import { userMessage } from "@/lib/errors";
import { useI18n } from "@/lib/i18n";
import { Link } from "@/lib/link";
import { cn } from "@/lib/utils";
import { useRouter } from "@/lib/navigation";
import { useTags } from "@/lib/queries";
import { useAuthenticatedAssetUrl } from "@/lib/use-authenticated-asset-url";
import type { FamilyMemberItem, FamilyMemberParams } from "@/types/families";
import { FamilyComparison } from "./comparison";
import { MEMBER_ROLES } from "@/types/families";
import { FamilyMemberCard, type MemberAction } from "./member-card";
import {
  AddFamilyMemberDialog,
  CanonicalFamilyDialog,
  EditFamilyMemberDialog,
} from "./member-dialogs";
import { FamilyBulkDialog, FamilyMetadataDialog } from "./metadata-dialog";

const SORTS: NonNullable<FamilyMemberParams["sort"]>[] = [
  "order",
  "scale-asc",
  "scale-desc",
  "date-asc",
  "date-desc",
  "success-desc",
];
const FORMATS: NonNullable<FamilyMemberParams["file_type"]>[] = [
  "stl",
  "3mf",
  "obj",
  "step",
  "gcode",
];
type Dialog =
  | { kind: MemberAction; member: FamilyMemberItem }
  | { kind: "add" | "metadata" | "trash" | "tags" | "collection" | "compare" }
  | null;

export function FamilyDetail({ id }: { id: number }) {
  const { t } = useI18n();
  const router = useRouter();
  const cache = useQueryClient();
  const tags = useTags();
  const familyQuery = useQuery({ queryKey: ["families", id], queryFn: () => getFamily(id) });
  const family = familyQuery.data;
  const [filters, setFilters] = useState<FamilyMemberParams>({ sort: "order" });
  const [search, setSearch] = useState("");
  useEffect(() => {
    const timer = setTimeout(
      () => setFilters((current) => ({ ...current, q: search.trim() || undefined })),
      200,
    );
    return () => clearTimeout(timer);
  }, [search]);
  const membersQuery = useInfiniteQuery({
    queryKey: ["families", id, "members", filters],
    enabled: !!family,
    initialPageParam: "",
    queryFn: ({ pageParam }) =>
      listFamilyMembers(id, { ...filters, cursor: pageParam || undefined, limit: 24 }),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
  const members = membersQuery.data?.pages.flatMap((page) => page.items) ?? [];
  const [selected, setSelected] = useState<Map<number, FamilyMemberItem>>(new Map());
  const [dialog, setDialog] = useState<Dialog>(null);
  const [menu, setMenu] = useState(false);
  const menuTrigger = useRef<HTMLButtonElement>(null);
  const [send, setSend] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const cover = useAuthenticatedAssetUrl(family?.cover_thumbnail_url ?? null);
  const canonicalId = family?.canonical_model_id;
  const canonical = useQuery({
    queryKey: ["models", canonicalId],
    enabled: canonicalId != null,
    queryFn: () => getModel(canonicalId!),
  });
  const printerFiles = useQuery({
    queryKey: ["models", canonicalId, "printer-files"],
    enabled: canonicalId != null && send,
    queryFn: () => getModelPrinterFiles(canonicalId!),
  });
  const gcode = canonical.data?.files.filter((file) => file.file_type === "gcode") ?? [];
  const closeDialog = () => {
    setDialog(null);
    setError(null);
  };
  const refresh = () => {
    setSelected(new Map());
    void cache.invalidateQueries({ queryKey: ["families"] });
    void cache.invalidateQueries({ queryKey: ["models"] });
  };
  const mutate = async <T,>(operation: () => Promise<T>) => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await operation();
      refresh();
    } catch (cause) {
      setError(userMessage(cause));
    } finally {
      setBusy(false);
    }
  };
  if (!family)
    return (
      <PageContainer>
        <Link href="/" className="text-sm text-primary hover:underline">
          {t("families.back")}
        </Link>
        {familyQuery.isError ? (
          <div>
            <p role="alert" className="text-sm text-destructive">
              {userMessage(familyQuery.error)}
            </p>
            <Button variant="outline" onClick={() => void familyQuery.refetch()}>
              {t("families.retry")}
            </Button>
          </div>
        ) : (
          <p role="status">{t("families.loading")}</p>
        )}
      </PageContainer>
    );
  const editable = family.effective_role !== "view";
  const pair = [...selected.values()].map(
    (member) => members.find((current) => current.id === member.id) ?? member,
  );
  return (
    <PageContainer className="space-y-5">
      <Link
        href="/"
        className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-primary"
      >
        <ArrowLeft className="h-4 w-4" aria-hidden />
        {t("families.back")}
      </Link>
      <PageHeader
        title={family.name}
        description={
          <>
            {t("families.memberCount", { count: family.member_count })}
            {family.collection && ` · ${family.collection}`}
          </>
        }
        actions={
          <>
            <Button
              variant="ghost"
              size="icon-sm"
              disabled={busy}
              aria-label={t(family.starred ? "families.unstar" : "families.star")}
              aria-pressed={family.starred}
              onClick={() => void mutate(() => starFamily(id, !family.starred))}
            >
              <Star
                className={`h-4 w-4 ${family.starred ? "fill-current text-primary" : ""}`}
                aria-hidden
              />
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={canonicalId == null || canonical.isPending || gcode.length === 0}
              onClick={() => setSend(true)}
            >
              <Printer className="h-4 w-4" aria-hidden />
              {t("families.sendCanonical")}
            </Button>
            {editable && (
              <Button size="sm" onClick={() => setDialog({ kind: "add" })}>
                <Plus className="h-4 w-4" aria-hidden />
                {t("families.addMembers")}
              </Button>
            )}
            <DropdownMenu
              open={menu}
              onOpenChange={setMenu}
              contentClassName="rounded-md border border-border bg-card shadow-lg"
              trigger={
                <Button
                  ref={menuTrigger}
                  onClick={() => setMenu(!menu)}
                  aria-expanded={menu}
                  aria-haspopup="menu"
                  data-menu-trigger
                  variant="ghost"
                  size="icon-sm"
                  aria-label={t("families.actions")}
                >
                  <MoreHorizontal className="h-4 w-4" aria-hidden />
                </Button>
              }
            >
              <div className="min-w-52 p-1">
                {editable &&
                  (["metadata", "tags", "collection"] as const).map((kind) => (
                    <button
                      role="menuitem"
                      key={kind}
                      className="block w-full rounded px-3 py-2 text-left text-sm hover:bg-accent focus-visible:bg-accent focus-visible:outline-none"
                      onClick={() => {
                        setMenu(false);
                        menuTrigger.current?.focus();
                        setDialog({ kind });
                      }}
                    >
                      {t(
                        kind === "metadata"
                          ? "families.edit"
                          : kind === "tags"
                            ? "families.bulkTags"
                            : "families.bulkCollection",
                      )}
                    </button>
                  ))}
                <button
                  role="menuitem"
                  disabled={busy}
                  className="block w-full rounded px-3 py-2 text-left text-sm hover:bg-accent focus-visible:bg-accent focus-visible:outline-none"
                  onClick={() => {
                    setMenu(false);
                    void mutate(() => starVisibleFamilyModels(id, family.version));
                  }}
                >
                  {t("families.bulkStar")}
                </button>
                {editable && (
                  <button
                    role="menuitem"
                    className="block w-full rounded px-3 py-2 text-left text-sm text-destructive hover:bg-accent focus-visible:bg-accent focus-visible:outline-none"
                    onClick={() => {
                      setMenu(false);
                      menuTrigger.current?.focus();
                      setDialog({ kind: "trash" });
                    }}
                  >
                    {t("families.archive")}
                  </button>
                )}
              </div>
            </DropdownMenu>
          </>
        }
      />
      <div className="flex items-start gap-3 rounded-md border border-border bg-card p-3">
        {cover && (
          <img
            src={cover}
            alt=""
            className="h-16 w-16 shrink-0 rounded object-contain sm:h-20 sm:w-20"
          />
        )}
        <div className="min-w-0 flex-1 space-y-2">
          <div>
            <p className="text-xs font-medium text-muted-foreground">{t("families.canonical")}</p>
            {canonicalId != null ? (
              <Link
                href={`/models/${canonicalId}`}
                className="break-words text-sm font-semibold text-primary hover:underline"
              >
                {canonical.data?.name ?? t("families.viewModel")}
              </Link>
            ) : (
              <>
                <p className="text-sm font-semibold">{t("families.vacancy")}</p>
                <p className="text-xs text-muted-foreground">{t("families.vacancyHelp")}</p>
              </>
            )}
          </div>
          {family.description && (
            <p className="whitespace-pre-wrap break-words text-sm leading-relaxed text-muted-foreground">
              {family.description}
            </p>
          )}
          <EntityTagsDialog
            entityLabel={family.name}
            tags={family.tags}
            availableTags={tags.data ?? []}
            canEdit={editable}
            help={t("families.familyTagsHelp")}
            onSave={async (next) => {
              await updateFamily(id, { version: family.version, tags: next });
              refresh();
            }}
          />
          {canonicalId != null &&
            !canonical.isPending &&
            !canonical.isError &&
            gcode.length === 0 && (
              <p className="text-xs text-muted-foreground">{t("families.noGcode")}</p>
            )}
          {canonical.isError && (
            <p className="text-xs text-destructive">{userMessage(canonical.error)}</p>
          )}
        </div>
      </div>
      {!editable && <p className="text-xs text-muted-foreground">{t("families.readOnly")}</p>}
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      <section className="space-y-3" aria-label={t("families.members")}>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="text-base font-semibold">{t("families.members")}</h2>
            <p className="text-xs text-muted-foreground">{t("families.compareHelp")}</p>
          </div>
          <Button
            size="sm"
            variant="outline"
            disabled={selected.size !== 2}
            onClick={() => setDialog({ kind: "compare" })}
          >
            {t("families.compare")} {selected.size > 0 && `(${selected.size}/2)`}
          </Button>
        </div>
        <div className="space-y-2">
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            <Input
              aria-label={t("families.searchModels")}
              placeholder={t("families.searchModels")}
              className="col-span-2 h-11 sm:col-span-1 sm:h-9"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
            <select
              aria-label={t("families.role")}
              className={cn(inputClasses, "h-11 min-w-0 text-xs sm:h-9")}
              value={filters.role ?? ""}
              onChange={(event) =>
                setFilters({
                  ...filters,
                  role: (["canonical", ...MEMBER_ROLES] as const).find(
                    (role) => role === event.target.value,
                  ),
                })
              }
            >
              <option value="">{t("families.allRoles")}</option>
              {(["canonical", ...MEMBER_ROLES] as const).map((role) => (
                <option key={role} value={role}>
                  {t(`families.role.${role}`)}
                </option>
              ))}
            </select>
            <select
              aria-label={t("families.sort")}
              className={cn(inputClasses, "h-11 min-w-0 text-xs sm:h-9")}
              value={filters.sort}
              onChange={(event) =>
                setFilters({ ...filters, sort: SORTS.find((sort) => sort === event.target.value) })
              }
            >
              {SORTS.map((sort) => (
                <option key={sort} value={sort}>
                  {t(`families.sort.${sort}`)}
                </option>
              ))}
            </select>
          </div>
          <details>
            <summary className="cursor-pointer text-xs font-medium text-muted-foreground hover:text-foreground">
              {t("families.moreFilters")}{" "}
              {[filters.file_type, filters.known_good, filters.has_revisions, filters.source].some(
                (value) => value !== undefined,
              ) &&
                `(${[filters.file_type, filters.known_good, filters.has_revisions, filters.source].filter((value) => value !== undefined).length})`}
            </summary>
            <div className="mt-2 grid grid-cols-2 gap-2 lg:grid-cols-4">
              <select
                aria-label={t("families.formats")}
                className={cn(inputClasses, "h-11 min-w-0 text-xs sm:h-9")}
                value={filters.file_type ?? ""}
                onChange={(event) =>
                  setFilters({
                    ...filters,
                    file_type: FORMATS.find((format) => format === event.target.value),
                  })
                }
              >
                <option value="">{t("families.allFormats")}</option>
                {FORMATS.map((format) => (
                  <option key={format} value={format}>
                    {format.toUpperCase()}
                  </option>
                ))}
              </select>
              <select
                aria-label={t("families.knownGood")}
                className={cn(inputClasses, "h-11 min-w-0 text-xs sm:h-9")}
                value={filters.known_good === undefined ? "" : String(filters.known_good)}
                onChange={(event) =>
                  setFilters({
                    ...filters,
                    known_good: event.target.value ? event.target.value === "true" : undefined,
                  })
                }
              >
                <option value="">
                  {t("families.knownGood")}: {t("families.any")}
                </option>
                <option value="true">{t("families.hasKnownGood")}</option>
                <option value="false">{t("families.noKnownGood")}</option>
              </select>
              <select
                aria-label={t("families.revisions")}
                className={cn(inputClasses, "h-11 min-w-0 text-xs sm:h-9")}
                value={filters.has_revisions === undefined ? "" : String(filters.has_revisions)}
                onChange={(event) =>
                  setFilters({
                    ...filters,
                    has_revisions: event.target.value ? event.target.value === "true" : undefined,
                  })
                }
              >
                <option value="">
                  {t("families.revisions")}: {t("families.any")}
                </option>
                <option value="true">{t("families.hasRevisions")}</option>
                <option value="false">{t("families.noRevisions")}</option>
              </select>
              <select
                aria-label={t("families.source")}
                className={cn(inputClasses, "h-11 min-w-0 text-xs sm:h-9")}
                value={filters.source ?? ""}
                onChange={(event) =>
                  setFilters({
                    ...filters,
                    source:
                      event.target.value === "vault" || event.target.value === "external"
                        ? event.target.value
                        : undefined,
                  })
                }
              >
                <option value="">
                  {t("families.source")}: {t("families.any")}
                </option>
                <option value="vault">{t("families.vault")}</option>
                <option value="external">{t("families.external")}</option>
              </select>
            </div>
          </details>
        </div>
        <p className="text-xs text-muted-foreground">
          {t("families.matchingCount", {
            matching: membersQuery.data?.pages[0]?.total ?? 0,
            total: family.total_visible_members,
          })}
        </p>
        {membersQuery.isPending && (
          <p role="status" className="text-sm text-muted-foreground">
            {t("families.loading")}
          </p>
        )}
        {membersQuery.isError && (
          <div>
            <p role="alert" className="text-sm text-destructive">
              {userMessage(membersQuery.error)}
            </p>
            <Button size="sm" variant="outline" onClick={() => void membersQuery.refetch()}>
              {t("families.retry")}
            </Button>
          </div>
        )}
        <div className="grid gap-3 lg:grid-cols-2 xl:grid-cols-3">
          {members.map((member) => (
            <FamilyMemberCard
              key={member.id}
              member={member}
              editable={editable}
              selected={selected.has(member.id)}
              selectionFull={selected.size === 2}
              onAction={(kind) => setDialog({ kind, member })}
              onToggle={() =>
                setSelected((current) => {
                  const next = new Map(current);
                  if (next.has(member.id)) next.delete(member.id);
                  else if (next.size < 2) next.set(member.id, member);
                  return next;
                })
              }
            />
          ))}
        </div>
        {!membersQuery.isPending && !membersQuery.isError && members.length === 0 && (
          <p className="py-5 text-center text-sm text-muted-foreground">
            {t("families.noMatches")}
          </p>
        )}
        {membersQuery.hasNextPage && (
          <Button
            variant="outline"
            loading={membersQuery.isFetchingNextPage}
            onClick={() => void membersQuery.fetchNextPage()}
          >
            {t("families.loadMore")}
          </Button>
        )}
      </section>
      {dialog?.kind === "add" && (
        <AddFamilyMemberDialog family={family} onClose={closeDialog} onSaved={refresh} />
      )}
      {dialog?.kind === "metadata" && (
        <FamilyMetadataDialog family={family} onClose={closeDialog} onSaved={refresh} />
      )}
      {dialog?.kind === "edit-member" && (
        <EditFamilyMemberDialog
          family={family}
          member={dialog.member}
          onClose={closeDialog}
          onSaved={refresh}
        />
      )}
      {dialog?.kind === "canonical" && (
        <CanonicalFamilyDialog
          family={family}
          member={dialog.member}
          onClose={closeDialog}
          onSaved={refresh}
        />
      )}
      {(dialog?.kind === "tags" || dialog?.kind === "collection") && (
        <FamilyBulkDialog
          family={family}
          mode={dialog.kind}
          onClose={closeDialog}
          onSaved={refresh}
        />
      )}
      {dialog?.kind === "compare" && pair.length === 2 && (
        <Modal open onClose={closeDialog} title={t("families.compare")} className="max-w-6xl">
          <FamilyComparison members={[pair[0], pair[1]]} />
        </Modal>
      )}
      {(dialog?.kind === "trash" || dialog?.kind === "detach") && (
        <ConfirmModal
          open
          onClose={closeDialog}
          busy={busy}
          title={t(dialog.kind === "trash" ? "families.archive" : "families.detach")}
          description={`${t(dialog.kind === "trash" ? "families.archiveHelp" : "families.detachHelp")}${error ? `\n\n${error}` : ""}`}
          confirmLabel={t(dialog.kind === "trash" ? "families.archive" : "families.detach")}
          onConfirm={() =>
            void mutate(async () => {
              if (dialog.kind === "trash") {
                await trashFamily(id, family.version);
                router.push("/");
              } else if (dialog.kind === "detach")
                await detachFamilyMember(id, dialog.member.id, family.version);
              closeDialog();
            })
          }
        />
      )}
      {send && canonical.data && (
        <SendToButtons
          gcodeFiles={gcode}
          printerFiles={printerFiles.data ?? []}
          open
          onOpenChange={setSend}
        />
      )}
    </PageContainer>
  );
}
