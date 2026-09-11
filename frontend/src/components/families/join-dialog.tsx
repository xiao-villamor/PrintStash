import { useEffect, useState } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { addFamilyMember, getFamily, listFamilies } from "@/lib/api/families";
import { userMessage } from "@/lib/errors";
import { useI18n } from "@/lib/i18n";
import type { FamilyRead, MemberRole } from "@/types/families";
import { MemberRoleSelect } from "./member-dialogs";

/** Joining never changes the destination's human-selected canonical Model. */
export function JoinFamilyDialog({
  modelId,
  onClose,
  onJoined,
}: {
  modelId: number;
  onClose: () => void;
  onJoined: (family: FamilyRead) => void;
}) {
  const { t } = useI18n();
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<FamilyRead | null>(null);
  const [role, setRole] = useState<MemberRole>("identical");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const timer = setTimeout(() => setQ(search.trim()), 200);
    return () => clearTimeout(timer);
  }, [search]);
  const families = useInfiniteQuery({
    queryKey: ["families", "join", q],
    initialPageParam: "",
    queryFn: ({ pageParam }) =>
      listFamilies({ q: q || undefined, limit: 24, cursor: pageParam || undefined }),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
  const items = families.data?.pages.flatMap((page) => page.items) ?? [];
  return (
    <Modal
      open
      onClose={() => {
        if (!busy) onClose();
      }}
      title={t("families.join")}
      className="max-w-lg"
    >
      <form
        className="space-y-3"
        onSubmit={async (event) => {
          event.preventDefault();
          if (!selected || busy) return;
          setBusy(true);
          setError(null);
          try {
            const current = await getFamily(selected.id);
            if (current.effective_role === "view") {
              setError(t("families.readOnly"));
              return;
            }
            await addFamilyMember(current.id, current.version, { model_id: modelId, role });
            onJoined(current);
          } catch (cause) {
            setError(userMessage(cause));
          } finally {
            setBusy(false);
          }
        }}
      >
        <fieldset disabled={busy} className="space-y-3">
          <Input
            aria-label={t("families.searchFamilies")}
            placeholder={t("families.searchFamilies")}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            autoFocus
          />
          {selected && (
            <p className="break-words text-sm font-medium">
              {t("families.selectedFamily", { name: selected.name })}
            </p>
          )}
          {families.isPending && (
            <p role="status" className="text-sm text-muted-foreground">
              {t("families.loading")}
            </p>
          )}
          {families.isError && (
            <div className="space-y-2">
              <p role="alert" className="text-sm text-destructive">
                {userMessage(families.error)}
              </p>
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => void families.refetch()}
              >
                {t("families.retry")}
              </Button>
            </div>
          )}
          <div className="max-h-64 overflow-y-auto rounded-md border border-border">
            {items.map((family) => (
              <label
                key={family.id}
                className="flex items-center gap-3 border-b border-border px-3 py-2.5 last:border-b-0"
              >
                <input
                  type="radio"
                  name="family"
                  checked={selected?.id === family.id}
                  disabled={family.effective_role === "view"}
                  onChange={() => setSelected(family)}
                  className="accent-primary"
                />
                <span className="min-w-0 flex-1">
                  <span className="block break-words text-sm font-medium">{family.name}</span>
                  <span className="text-xs text-muted-foreground">
                    {t("families.memberCount", { count: family.member_count })}
                    {family.effective_role === "view" && ` · ${t("families.viewOnly")}`}
                  </span>
                </span>
              </label>
            ))}
            {!families.isPending && !families.isError && !items.length && (
              <p className="p-3 text-sm text-muted-foreground">{t("families.noFamilyMatches")}</p>
            )}
          </div>
          {families.hasNextPage && (
            <Button
              type="button"
              size="sm"
              variant="outline"
              loading={families.isFetchingNextPage}
              onClick={() => void families.fetchNextPage()}
            >
              {t("families.loadMore")}
            </Button>
          )}
          <MemberRoleSelect value={role} onChange={setRole} label={t("families.role")} />
        </fieldset>
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        <div className="flex justify-end gap-2 border-t border-border pt-3">
          <Button type="button" variant="outline" disabled={busy} onClick={onClose}>
            {t("families.cancel")}
          </Button>
          <Button type="submit" loading={busy} disabled={!selected}>
            {t("families.join")}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
