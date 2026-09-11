import { useEffect, useState } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { Input, inputClasses } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { listFamilyMembers } from "@/lib/api/families";
import { getModel } from "@/lib/api/models";
import { useI18n } from "@/lib/i18n";
import { userMessage } from "@/lib/errors";

/** Cover choices use their own paged query, independent of detail-page filters. */
export function FamilyCoverPicker({
  familyId,
  value,
  onChange,
}: {
  familyId: number;
  value: string;
  onChange: (id: string) => void;
}) {
  const { t } = useI18n();
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  useEffect(() => {
    const timer = setTimeout(() => setQ(search.trim()), 200);
    return () => clearTimeout(timer);
  }, [search]);
  const selected = useQuery({
    queryKey: ["models", Number(value)],
    enabled: value !== "",
    queryFn: () => getModel(Number(value)),
  });
  const query = useInfiniteQuery({
    queryKey: ["families", familyId, "cover-members", q],
    initialPageParam: "",
    queryFn: ({ pageParam }) =>
      listFamilyMembers(familyId, {
        q: q || undefined,
        cursor: pageParam || undefined,
        limit: 24,
        sort: "order",
      }),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
  const members = query.data?.pages.flatMap((page) => page.items) ?? [];
  return (
    <div className="space-y-2">
      <Input
        aria-label={t("families.searchModels")}
        value={search}
        onChange={(event) => setSearch(event.target.value)}
        placeholder={t("families.searchModels")}
      />
      <select
        className={inputClasses}
        aria-label={t("families.coverModel")}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">{t("families.canonical")}</option>
        {selected.data && !members.some((member) => String(member.model_id) === value) && (
          <option value={value}>{selected.data.name}</option>
        )}
        {members.map((member) => (
          <option key={member.id} value={member.model_id}>
            {member.model.name}
          </option>
        ))}
      </select>
      {query.isPending && (
        <p role="status" className="text-xs text-muted-foreground">
          {t("families.loading")}
        </p>
      )}
      {query.isError && (
        <div className="space-y-2">
          <p role="alert" className="text-sm text-destructive">
            {userMessage(query.error)}
          </p>
          <Button type="button" size="sm" variant="outline" onClick={() => void query.refetch()}>
            {t("families.retry")}
          </Button>
        </div>
      )}
      {query.hasNextPage && (
        <Button
          type="button"
          size="sm"
          variant="outline"
          loading={query.isFetchingNextPage}
          onClick={() => void query.fetchNextPage()}
        >
          {t("families.loadMore")}
        </Button>
      )}
    </div>
  );
}
