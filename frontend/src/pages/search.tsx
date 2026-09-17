import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, LayoutGrid, List, Search } from "lucide-react";
import { useEffect, useState } from "react";

import { SearchFilterControls } from "@/components/search-filter-controls";
import { SearchPreferences } from "@/components/search-preferences";
import { SearchSavedViews } from "@/components/search-saved-views";
import {
  readSearchFilters,
  writeSearchFilters,
  hasSearchFilters,
  searchSorts,
} from "@/lib/search-filters";
import { SearchModelPreview } from "@/components/search-evidence";
import { SearchImageInput } from "@/components/search-image-input";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { PageContainer } from "@/components/ui/page-container";
import { PageHeader } from "@/components/ui/page-header";
import {
  getSearchStatus,
  getSearchPreferences,
  parseSearch,
  searchImage,
  searchLibrary,
  searchUsingModel,
} from "@/lib/api/search";
import { useAuth } from "@/lib/auth-context";
import { ApiError } from "@/lib/errors";
import { useI18n } from "@/lib/i18n";
import { Link } from "@/lib/link";
import { useRouter, useSearchParams } from "@/lib/navigation";
import type { SearchSubjectType } from "@/types/search";

const subjectTypes: SearchSubjectType[] = ["model", "collection", "multipart_model", "document"];

export default function SearchPage() {
  const params = useSearchParams();
  const { user } = useAuth();
  return (
    <SearchContent
      key={`${user?.id ?? "anonymous"}:${params.get("image") === "1" ? "image" : "text"}`}
    />
  );
}

function SearchContent() {
  const { t } = useI18n();
  const { user } = useAuth();
  const router = useRouter();
  const queryClient = useQueryClient();
  const params = useSearchParams();
  const q = params.get("q") ?? "";
  const imageMode = params.get("image") === "1";
  const sourceModel = /^\d+$/.test(params.get("model") ?? "") ? Number(params.get("model")) : 0;
  const modelId = Number.isSafeInteger(sourceModel) && sourceModel > 0 ? sourceModel : 0;
  const [image, setImage] = useState<File | null>(null);
  const [imageVersion, setImageVersion] = useState(0);
  const [view, setView] = useState<"grid" | "list">("grid");
  const mode = params.get("mode") === "lexical" ? "lexical" : "hybrid";
  const types = subjectTypes.filter((type) => params.getAll("type").includes(type));
  const status = useQuery({
    queryKey: ["ai-search", "status", user?.id],
    queryFn: getSearchStatus,
    enabled: !!user,
    refetchInterval: 15000,
  });
  const filters = readSearchFilters(params);
  const filtered = hasSearchFilters(filters);
  const sort = searchSorts.find((value) => value === params.get("sort")) ?? "relevance";
  const wantsParse = params.get("parse") === "1" && !!q.trim() && !imageMode && !modelId;
  const parseFailed = params.get("parse_error") === "1";
  const preference = useQuery({
    queryKey: ["search-preferences", user?.id],
    queryFn: getSearchPreferences,
    enabled: !!user && !imageMode && !modelId,
    retry: false,
  });
  const canParse = !!preference.data?.available && preference.data.nl_filters_enabled;
  const parsed = useQuery({
    queryKey: ["search-parse", user?.id, q],
    queryFn: ({ signal }) => parseSearch(q, signal),
    enabled: wantsParse && canParse,
    retry: false,
    gcTime: 0,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
  useEffect(() => {
    if (!wantsParse || preference.isPending || (canParse && parsed.isPending)) return;
    const result = canParse ? parsed.data : undefined;
    const next = result?.parsed
      ? writeSearchFilters(result.filters, result.residual_query, result.sort)
      : new URLSearchParams(params);
    next.delete("parse");
    if (canParse && (!!parsed.error || !!result?.reason)) next.set("parse_error", "1");
    else next.delete("parse_error");
    router.replace(`/search?${next}`, { scroll: false });
  }, [
    wantsParse,
    preference.isPending,
    canParse,
    parsed.isPending,
    parsed.data,
    parsed.error,
    params,
    router,
  ]);
  const queryKey = [
    "search-results",
    user?.id,
    q,
    mode,
    types,
    imageMode,
    imageVersion,
    modelId,
    filters,
    sort,
  ];
  const results = useInfiniteQuery({
    queryKey,
    queryFn: ({ pageParam, signal }: { pageParam: string | undefined; signal: AbortSignal }) =>
      modelId
        ? searchUsingModel(modelId, pageParam, signal)
        : imageMode && image
          ? searchImage(image, { cursor: pageParam }, signal)
          : searchLibrary(
              {
                q,
                mode,
                cursor: pageParam,
                types: types.length ? types : undefined,
                filters: filtered ? filters : undefined,
                sort,
              },
              signal,
            ),
    initialPageParam: undefined,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    enabled: !!user && !wantsParse && (!!modelId || (imageMode ? !!image : !!q.trim() || filtered)),
    retry: false,
    gcTime: 0,
  });
  function changeType(type: SearchSubjectType) {
    const next = types.includes(type) ? types.filter((item) => item !== type) : [...types, type];
    const query = new URLSearchParams(params);
    query.delete("type");
    next.forEach((item) => query.append("type", item));
    router.push(`/search?${query}`);
  }
  const pages = results.data?.pages ?? [];
  const first = pages[0];
  const items = pages.flatMap((page) => page.items);
  const cursorExpired =
    results.error instanceof ApiError &&
    ["search_cursor_expired", "search_cursor_invalid"].includes(results.error.code);
  const modelPending =
    !!modelId &&
    pages.some((page) => Object.values(page.leg_errors).includes("search_model_index_pending"));
  const visualReady =
    status.data?.legs.some(
      (leg) => leg === "thumbnail" || leg === "multiview" || leg === "point_cloud",
    ) ?? false;
  return (
    <PageContainer>
      <PageHeader
        actions={
          <Button variant="outline" onClick={() => router.push("/")}>
            <ArrowLeft className="h-4 w-4" aria-hidden />
            {t("aiSearch.backToLibrary")}
          </Button>
        }
        title={t("aiSearch.resultsTitle")}
        description={
          modelId
            ? t("aiSearch.relatedModels")
            : imageMode
              ? t("aiSearch.searchByImage")
              : q
                ? t("aiSearch.resultsFor", { query: q })
                : t("aiSearch.startSearch")
        }
      />
      {!imageMode && !modelId && (
        <>
          <SearchFilterControls
            key={q}
            filters={filters}
            q={q}
            sort={sort}
            showQuery={false}
            onChange={(next, query, order) => {
              const updated = writeSearchFilters(next, query, order);
              if (mode === "lexical") updated.set("mode", mode);
              types.forEach((type) => updated.append("type", type));
              router.push(`/search?${updated}`);
            }}
          />
          {wantsParse && (
            <p role="status" className="mb-3 text-sm text-muted-foreground">
              {t("aiSearch.parsing")}
            </p>
          )}
          {parseFailed && (
            <p role="status" className="mb-3 text-sm text-muted-foreground">
              {t("aiSearch.parseFailed")}
            </p>
          )}
        </>
      )}
      {imageMode && (
        <SearchImageInput
          image={image}
          enabled={visualReady}
          onChange={(file) => {
            setImage(file);
            setImageVersion((value) => value + 1);
          }}
        />
      )}
      {imageMode ? (
        <Button variant="ghost" className="mb-4" onClick={() => router.push("/search")}>
          {t("aiSearch.searchWithWords")}
        </Button>
      ) : (
        visualReady && (
          <Button variant="outline" className="mb-4" onClick={() => router.push("/search?image=1")}>
            {t("aiSearch.searchByImage")}
          </Button>
        )
      )}
      {!imageMode && !modelId && (
        <>
          <details className="mb-4 rounded-lg border border-border">
            <summary className="cursor-pointer px-4 py-3 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
              {t("aiSearch.searchOptions")}
            </summary>
            <div className="space-y-3 border-t border-border p-4">
              {user && (
                <div className="mb-4 flex flex-wrap items-center gap-2">
                  <SearchSavedViews
                    userId={user.id}
                    filters={{ ...filters, q, sort }}
                    onSelect={(value) => router.push(`/search?${writeSearchFilters(value)}`)}
                  />
                  {preference.data && (
                    <SearchPreferences userId={user.id} value={preference.data} />
                  )}
                </div>
              )}
              <div
                className="mb-4 flex flex-wrap items-center gap-2"
                aria-label={t("aiSearch.resultTypes")}
              >
                {subjectTypes.map((type) => (
                  <Button
                    key={type}
                    size="sm"
                    variant={types.includes(type) ? "secondary" : "outline"}
                    aria-pressed={types.includes(type)}
                    onClick={() => changeType(type)}
                  >
                    {t(`aiSearch.type.${type}`)}
                  </Button>
                ))}
                <label className="ml-auto flex items-center gap-2 text-sm">
                  {t("aiSearch.searchMode")}
                  <select
                    aria-label={t("aiSearch.searchMode")}
                    value={mode}
                    className="rounded-md border border-input bg-background p-2 text-sm"
                    onChange={(event) => {
                      const next = new URLSearchParams(params);
                      next.set("mode", event.target.value);
                      router.push(`/search?${next}`);
                    }}
                  >
                    <option value="hybrid">{t("aiSearch.hybrid")}</option>
                    <option value="lexical">{t("aiSearch.keywordOnly")}</option>
                  </select>
                </label>
              </div>
            </div>
          </details>
        </>
      )}
      {status.data?.remote_hosts.length ? (
        <p className="mb-3 text-sm text-muted-foreground">
          {t("aiSearch.remoteDisclosure", { hosts: status.data.remote_hosts.join(", ") })}
        </p>
      ) : null}
      {status.data?.backlog && (
        <p role="status" className="mb-3 text-sm text-muted-foreground">
          {t("aiSearch.backlog")}
        </p>
      )}
      {pages.some((page) => page.degraded.length) && (
        <p role="status" className="mb-3 rounded-md bg-warning/10 p-3 text-sm text-foreground">
          {t("aiSearch.degraded")}
        </p>
      )}
      {modelPending && (
        <p role="status" className="mb-3 text-sm text-muted-foreground">
          {t("aiSearch.modelPending")}
        </p>
      )}
      {modelId ? null : imageMode ? (
        !image && <EmptyState icon={Search} title={t("aiSearch.imagePrompt")} />
      ) : !q.trim() && !filtered ? (
        <EmptyState icon={Search} title={t("aiSearch.startSearch")} />
      ) : null}
      {(!!modelId || (imageMode ? !!image : !!q.trim() || filtered)) &&
        (wantsParse ? null : results.isLoading ? (
          <p role="status" className="py-8 text-sm text-muted-foreground">
            {t("aiSearch.searching")}
          </p>
        ) : results.isError && !items.length ? (
          <EmptyState
            title={t(
              results.error instanceof ApiError && results.error.code === "search_timeout"
                ? "aiSearch.timeout"
                : "aiSearch.loadError",
            )}
            action={<Button onClick={() => void results.refetch()}>{t("aiSearch.retry")}</Button>}
          />
        ) : items.length ? (
          <Card className="overflow-hidden">
            <div className="flex items-center justify-between gap-3 border-b border-border bg-muted/30 px-4 py-3 text-sm text-muted-foreground sm:px-5">
              <span role="status">{t("aiSearch.resultsShown", { count: items.length })}</span>
              <div className="flex gap-1" role="group" aria-label={t("aiSearch.resultView")}>
                <Button
                  variant="ghost"
                  className={view === "grid" ? "bg-accent text-accent-foreground" : undefined}
                  size="icon"
                  aria-label={t("aiSearch.gridView")}
                  aria-pressed={view === "grid"}
                  onClick={() => setView("grid")}
                >
                  <LayoutGrid className="h-4 w-4" aria-hidden />
                </Button>
                <Button
                  variant="ghost"
                  className={view === "list" ? "bg-accent text-accent-foreground" : undefined}
                  size="icon"
                  aria-label={t("aiSearch.listView")}
                  aria-pressed={view === "list"}
                  onClick={() => setView("list")}
                >
                  <List className="h-4 w-4" aria-hidden />
                </Button>
              </div>
            </div>
            <ul
              className={
                view === "grid"
                  ? "grid grid-cols-2 gap-x-4 gap-y-6 p-4 lg:grid-cols-3 xl:grid-cols-4 sm:p-5"
                  : "divide-y divide-border"
              }
            >
              {items.map((item) => (
                <li
                  key={`${item.subject_type}:${item.subject_id}`}
                  className={view === "grid" ? "min-w-0" : "flex gap-3 px-4 py-4 sm:px-5"}
                >
                  {view === "list" && <SearchModelPreview path={item.model?.thumbnail_url} />}
                  <div className="min-w-0 flex-1">
                    <div
                      className={
                        view === "grid" ? "space-y-2" : "flex flex-wrap items-center gap-2"
                      }
                    >
                      <Link
                        href={item.href}
                        className="block rounded-md hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      >
                        {view === "grid" && (
                          <SearchModelPreview path={item.model?.thumbnail_url} large />
                        )}
                        <h2
                          className={
                            view === "grid"
                              ? "mt-3 break-words text-sm font-semibold"
                              : "break-words text-base font-semibold"
                          }
                        >
                          {item.name}
                        </h2>
                      </Link>
                      <Badge variant="outline">{t(`aiSearch.type.${item.subject_type}`)}</Badge>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          </Card>
        ) : (
          <EmptyState
            icon={Search}
            title={t(
              first?.outcome === "no_strong_matches"
                ? "aiSearch.noStrongMatches"
                : "aiSearch.noResults",
            )}
            description={t(
              modelId
                ? "aiSearch.tryRelatedAgain"
                : imageMode
                  ? "aiSearch.tryAnotherImage"
                  : "aiSearch.tryAnotherQuery",
            )}
            action={
              modelId ? (
                <Button onClick={() => void results.refetch()}>{t("aiSearch.retry")}</Button>
              ) : undefined
            }
          />
        ))}
      {results.isFetchNextPageError && (
        <p role="alert" className="mt-4 text-sm text-destructive">
          {t(cursorExpired ? "aiSearch.cursorExpired" : "aiSearch.loadError")}
        </p>
      )}
      {cursorExpired ? (
        <Button
          className="mt-4"
          variant="outline"
          onClick={() => void queryClient.resetQueries({ queryKey, exact: true })}
        >
          {t("aiSearch.restartSearch")}
        </Button>
      ) : (
        results.hasNextPage && (
          <Button
            className="mt-4"
            variant="outline"
            loading={results.isFetchingNextPage}
            onClick={() => void results.fetchNextPage()}
          >
            {t("aiSearch.loadMore")}
          </Button>
        )
      )}
      {pages.some((page) => page.truncated) && (
        <p className="mt-4 text-xs text-muted-foreground">{t("aiSearch.boundedResults")}</p>
      )}
    </PageContainer>
  );
}
