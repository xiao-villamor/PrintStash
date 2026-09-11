import { useInfiniteQuery } from "@tanstack/react-query";
import { Boxes, Star } from "lucide-react";
import { ModelCard } from "@/components/model-card";
import { Button } from "@/components/ui/button";
import { browseFamilies, starFamily } from "@/lib/api/families";
import { userMessage } from "@/lib/errors";
import { useI18n } from "@/lib/i18n";
import { Link } from "@/lib/link";
import { toast } from "@/lib/toast";
import { useAuthenticatedAssetUrl } from "@/lib/use-authenticated-asset-url";
import type { FamilyBrowseParams, FamilyRead } from "@/types/families";

function FamilyCard({ family, onChange }: { family: FamilyRead; onChange: () => void }) {
  const { t } = useI18n();
  const cover = useAuthenticatedAssetUrl(family.cover_thumbnail_url);
  return (
    <article className="overflow-hidden rounded-md border border-border bg-card">
      <Link
        href={`/families/${family.id}`}
        className="group block focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
      >
        <div className="flex h-36 items-center justify-center bg-muted/30 p-3">
          {cover ? (
            <img src={cover} alt="" className="h-full w-full object-contain" />
          ) : (
            <Boxes className="h-10 w-10 text-muted-foreground" aria-hidden />
          )}
        </div>
        <div className="space-y-1 px-3 pt-3">
          <p className="text-2xs font-semibold uppercase tracking-wider text-muted-foreground">
            {t("families.family")}
          </p>
          <h2 className="truncate text-sm font-semibold group-hover:text-primary">{family.name}</h2>
          <p className="text-xs text-muted-foreground">
            {family.matching_visible_members < family.total_visible_members
              ? t("families.matchingCount", {
                  matching: family.matching_visible_members,
                  total: family.total_visible_members,
                })
              : t("families.memberCount", { count: family.member_count })}
          </p>
        </div>
      </Link>
      <div className="flex items-center justify-between gap-2 px-3 pb-2 pt-2">
        <Link href={`/?family_id=${family.id}`} className="text-xs text-primary hover:underline">
          {t("families.viewMembers")}
        </Link>
        <Button
          size="icon-sm"
          variant="ghost"
          aria-label={t(family.starred ? "families.unstar" : "families.star")}
          aria-pressed={family.starred}
          onClick={async () => {
            try {
              await starFamily(family.id, !family.starred);
              onChange();
            } catch (cause) {
              toast.error(userMessage(cause));
            }
          }}
        >
          <Star
            className={`h-4 w-4 ${family.starred ? "fill-current text-primary" : ""}`}
            aria-hidden
          />
        </Button>
      </div>
    </article>
  );
}

/** The server owns the mixed order and page boundary; never regroup a Model page. */
export function FamilyBrowseGrid({ params }: { params: FamilyBrowseParams }) {
  const { t } = useI18n();
  const query = useInfiniteQuery({
    queryKey: ["families", "browse", params],
    initialPageParam: "",
    queryFn: ({ pageParam }) =>
      browseFamilies({
        ...params,
        browse: "families_collapsed",
        limit: 30,
        cursor: pageParam || undefined,
      }),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
  const items = query.data?.pages.flatMap((page) => page.items) ?? [];
  return (
    <section className="space-y-3 p-4 sm:p-6" aria-label={t("families.browseFamilies")}>
      {query.isPending ? (
        <p role="status" className="text-sm text-muted-foreground">
          {t("families.loading")}
        </p>
      ) : (
        <p className="text-xs text-muted-foreground">
          {t("families.browseCount", { count: query.data?.pages[0]?.total ?? 0 })}
        </p>
      )}
      {query.isError && (
        <div>
          <p role="alert" className="text-sm text-destructive">
            {userMessage(query.error)}
          </p>
          <Button size="sm" variant="outline" onClick={() => void query.refetch()}>
            {t("families.retry")}
          </Button>
        </div>
      )}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-[repeat(auto-fill,minmax(280px,1fr))]">
        {items.map((item) =>
          item.kind === "family" ? (
            <FamilyCard
              key={`family-${item.family.id}`}
              family={item.family}
              onChange={() => void query.refetch()}
            />
          ) : (
            <ModelCard key={`model-${item.model.id}`} model={item.model} />
          ),
        )}
      </div>
      {!query.isPending && !query.isError && items.length === 0 && (
        <p className="py-6 text-sm text-muted-foreground">{t("families.noMatches")}</p>
      )}
      {query.hasNextPage && (
        <Button
          size="sm"
          variant="outline"
          loading={query.isFetchingNextPage}
          onClick={() => void query.fetchNextPage()}
        >
          {t("families.loadMore")}
        </Button>
      )}
    </section>
  );
}
