import { useEffect, useState } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { Modal } from "@/components/ui/modal";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { getModel } from "@/lib/api/models";
import { listFamilyMembers } from "@/lib/api/families";
import { userMessage } from "@/lib/errors";
import { useI18n } from "@/lib/i18n";
import type { FamilyMemberItem } from "@/types/families";
import { ModelThumbnail } from "./model-picker";

export function FamilyChoicesDialog({
  modelId,
  usedIds,
  onClose,
  onSelect,
}: {
  modelId: number;
  usedIds: ReadonlySet<number>;
  onClose: () => void;
  onSelect: (members: FamilyMemberItem[]) => void;
}) {
  const { t } = useI18n();
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  useEffect(() => {
    const timer = setTimeout(() => setQ(search.trim()), 200);
    return () => clearTimeout(timer);
  }, [search]);
  const model = useQuery({ queryKey: ["models", modelId], queryFn: () => getModel(modelId) });
  const familyId = model.data?.family?.id;
  const query = useInfiniteQuery({
    queryKey: ["families", familyId, "choices", q],
    enabled: familyId != null,
    initialPageParam: "",
    queryFn: ({ pageParam }) =>
      listFamilyMembers(familyId!, {
        cursor: pageParam || undefined,
        q: q || undefined,
        limit: 24,
        sort: "order",
      }),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
  const [selected, setSelected] = useState<Map<number, FamilyMemberItem>>(new Map());
  const members = query.data?.pages.flatMap((page) => page.items) ?? [];
  return (
    <Modal open onClose={onClose} title={t("families.addChoices")} className="max-w-xl">
      <div className="space-y-3">
        <p className="text-sm text-muted-foreground">{t("families.choicesHelp")}</p>
        {model.data?.family && <h2 className="text-sm font-semibold">{model.data.family.name}</h2>}
        {(model.isPending || (familyId != null && query.isPending)) && (
          <p role="status" className="text-sm">
            {t("families.loading")}
          </p>
        )}
        {model.data && !model.data.family && (
          <p className="text-sm text-muted-foreground">{t("families.noFamily")}</p>
        )}
        {(model.isError || query.isError) && (
          <div>
            <p role="alert" className="text-sm text-destructive">
              {userMessage(model.error ?? query.error)}
            </p>
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                if (model.isError) void model.refetch();
                else void query.refetch();
              }}
            >
              {t("families.retry")}
            </Button>
          </div>
        )}
        {familyId != null && (
          <Input
            aria-label={t("families.searchModels")}
            placeholder={t("families.searchModels")}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        )}
        {members.length > 0 && (
          <div className="max-h-80 overflow-y-auto rounded-md border border-border">
            {members.map((member) => (
              <label
                key={member.id}
                className="flex items-center gap-3 border-b border-border px-3 py-2 last:border-b-0"
              >
                <Checkbox
                  checked={selected.has(member.model_id)}
                  disabled={usedIds.has(member.model_id)}
                  ariaLabel={t("families.selectModel", { name: member.model.name })}
                  onChange={(checked) =>
                    setSelected((current) => {
                      const next = new Map(current);
                      if (checked) next.set(member.model_id, member);
                      else next.delete(member.model_id);
                      return next;
                    })
                  }
                />
                <ModelThumbnail url={member.model.thumbnail_url} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium">{member.model.name}</span>
                  <span className="text-xs text-muted-foreground">
                    {usedIds.has(member.model_id)
                      ? t("families.choiceExists")
                      : t(`families.role.${member.role}`)}
                  </span>
                </span>
              </label>
            ))}
          </div>
        )}
        {query.hasNextPage && (
          <Button
            variant="outline"
            size="sm"
            loading={query.isFetchingNextPage}
            onClick={() => void query.fetchNextPage()}
          >
            {t("families.loadMore")}
          </Button>
        )}
        <div className="flex justify-end gap-2 border-t border-border pt-3">
          <Button variant="outline" onClick={onClose}>
            {t("families.cancel")}
          </Button>
          <Button
            disabled={selected.size === 0}
            onClick={() => {
              onSelect([...selected.values()]);
              onClose();
            }}
          >
            {t("families.addSelectedChoices", { count: selected.size })}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
