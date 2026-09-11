import { useEffect, useId, useState } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { Box } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { listModelPage } from "@/lib/api/models";
import { userMessage } from "@/lib/errors";
import { useI18n } from "@/lib/i18n";
import { useAuth } from "@/lib/auth-context";
import { useAuthenticatedAssetUrl } from "@/lib/use-authenticated-asset-url";
import type { ModelListItem } from "@/types/models";

export type FamilyPickerModel = Pick<
  ModelListItem,
  "id" | "name" | "collection" | "effective_role" | "thumbnail_url" | "family"
>;

export function ModelThumbnail({ url }: { url: string | null }) {
  const src = useAuthenticatedAssetUrl(url);
  return (
    <span className="flex h-10 w-10 shrink-0 items-center justify-center overflow-hidden rounded bg-muted">
      {src ? (
        <img src={src} alt="" className="h-full w-full object-contain" />
      ) : (
        <Box className="h-5 w-5 text-muted-foreground" aria-hidden />
      )}
    </span>
  );
}

/** Server pages stay independent of the selection retained by the parent. */
export function FamilyModelPicker({
  selected,
  onToggle,
  allowGrouped = false,
  excluded = [],
  excludedFamilyId,
  editableOnly = true,
}: {
  selected: ReadonlyMap<number, FamilyPickerModel>;
  onToggle: (model: FamilyPickerModel) => void;
  allowGrouped?: boolean;
  excluded?: readonly number[];
  excludedFamilyId?: number;
  editableOnly?: boolean;
}) {
  const { t } = useI18n();
  const { user } = useAuth();
  const id = useId();
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  useEffect(() => {
    const timer = setTimeout(() => setQuery(search.trim()), 200);
    return () => clearTimeout(timer);
  }, [search]);
  const models = useInfiniteQuery({
    queryKey: ["models", "family-picker", query],
    initialPageParam: "",
    queryFn: ({ pageParam }) =>
      listModelPage({
        q: query || undefined,
        cursor: pageParam || undefined,
        limit: 24,
        sort: "name-asc",
      }),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
  const items = models.data?.pages.flatMap((page) => page.items) ?? [];
  return (
    <div className="space-y-2">
      <label htmlFor={id} className="text-sm font-medium">
        {t("families.findModels")}
      </label>
      <Input
        id={id}
        value={search}
        onChange={(event) => setSearch(event.target.value)}
        placeholder={t("families.searchModels")}
      />
      <div
        className="max-h-64 overflow-y-auto rounded-md border border-border"
        aria-busy={models.isFetching}
      >
        {items.map((model) => {
          const unavailable =
            excluded.includes(model.id) ||
            (!!model.family && (!allowGrouped || model.family.id === excludedFamilyId)) ||
            (editableOnly &&
              !user?.is_superuser &&
              !["edit", "admin"].includes(model.effective_role ?? ""));
          return (
            <label
              key={model.id}
              className="flex min-h-14 items-center gap-3 border-b border-border px-3 py-2 last:border-b-0 hover:bg-muted/50"
            >
              <Checkbox
                checked={selected.has(model.id)}
                disabled={unavailable}
                onChange={() => onToggle(model)}
                ariaLabel={t("families.selectModel", { name: model.name })}
              />
              <ModelThumbnail url={model.thumbnail_url} />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium">{model.name}</span>
                <span className="block truncate text-xs text-muted-foreground">
                  {model.family
                    ? t("families.alreadyMember", { name: model.family.name })
                    : (model.collection ?? t("families.unfiled"))}
                </span>
              </span>
            </label>
          );
        })}
        {models.isPending && (
          <p role="status" className="p-3 text-sm text-muted-foreground">
            {t("families.loading")}
          </p>
        )}
        {!models.isPending && !models.isError && items.length === 0 && (
          <p className="p-3 text-sm text-muted-foreground">{t("families.noMatches")}</p>
        )}
        {models.isError && (
          <div className="p-3">
            <p role="alert" className="text-sm text-destructive">
              {userMessage(models.error)}
            </p>
            <Button type="button" size="sm" variant="outline" onClick={() => void models.refetch()}>
              {t("families.retry")}
            </Button>
          </div>
        )}
        {models.hasNextPage && (
          <div className="p-2">
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="w-full"
              loading={models.isFetchingNextPage}
              onClick={() => void models.fetchNextPage()}
            >
              {t("families.loadMore")}
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
