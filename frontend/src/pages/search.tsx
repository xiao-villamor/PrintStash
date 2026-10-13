import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, LayoutGrid, List, Search } from "lucide-react";
import { useEffect, useId, useState } from "react";

import { SearchFilterControls } from "@/components/search-filter-controls";
import { SearchPreferences } from "@/components/search-preferences";
import { SearchSavedViews } from "@/components/search-saved-views";
import {
  readSearchFilters,
  writeSearchFilters,
  hasSearchFilters,
  searchSorts,
} from "@/lib/search-filters";
import { SearchSubjectPreview } from "@/components/search-evidence";
import { SearchImageInput } from "@/components/search-image-input";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { PageHeader } from "@/components/ui/page-header";
import {
  parsedSearchOptions,
  searchPreferencesOptions,
  searchResultsOptions,
  searchStatusOptions,
  type SearchIntent,
} from "@/lib/queries/search";
import { useAuth } from "@/lib/auth-context";
import { ApiError, parseApiError } from "@/lib/errors";
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
  const status = useQuery(searchStatusOptions(user?.id));
  const filters = readSearchFilters(params);
  const filtered = hasSearchFilters(filters);
  const sort = searchSorts.find((value) => value === params.get("sort")) ?? "relevance";
  const wantsParse = params.get("parse") === "1" && !!q.trim() && !imageMode && !modelId;
  const parseFailed = params.get("parse_error") === "1";
  const preference = useQuery(searchPreferencesOptions(user?.id, !imageMode && !modelId));
  const canParse =
    !preference.isError && !!preference.data?.available && preference.data.nl_filters_enabled;
  const parsed = useQuery(parsedSearchOptions(user?.id, q, wantsParse && canParse));
  useEffect(() => {
    if (!wantsParse || preference.isPending || (canParse && parsed.isPending)) return;
    const result = canParse ? parsed.data : undefined;
    const next = result?.parsed
      ? writeSearchFilters(result.filters, result.residual_query, result.sort)
      : new URLSearchParams(params);
    next.delete("parse");
    if (canParse && (!!parsed.error || !!result?.reason)) next.set("parse_error", "1");
    else next.delete("parse_error");
    router.replace(`/search?${next}`);
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
  const imageIdentity = useId();
  const intent: SearchIntent = modelId
    ? { kind: "model", modelId }
    : imageMode
      ? image
        ? { kind: "image", image, identity: `${imageIdentity}:${imageVersion}` }
        : { kind: "idle" }
      : {
          kind: "text",
          query: {
            q,
            mode,
            types: types.length ? types : undefined,
            filters: filtered ? filters : undefined,
            sort,
          },
        };
  const options = searchResultsOptions(
    user?.id,
    intent,
    !wantsParse && (!!modelId || (imageMode ? !!image : !!q.trim() || filtered)),
  );
  const queryKey = options.queryKey;
  const results = useInfiniteQuery(options);
  function changeType(type: SearchSubjectType) {
    const next = types.includes(type) ? types.filter((item) => item !== type) : [...types, type];
    const query = new URLSearchParams(params);
    query.delete("type");
    next.forEach((item) => query.append("type", item));
    router.push(`/search?${query}`);
  }
  const inaccessible =
    results.isError && [401, 403, 404].includes(parseApiError(results.error).status);
  const pages = inaccessible ? [] : (results.data?.pages ?? []);
  const first = pages[0];
  const items = pages.flatMap((page) => page.items);
  const capability =
    status.isError && [401, 403, 404].includes(parseApiError(status.error).status)
      ? undefined
      : status.data;
  const preferencesInaccessible =
    preference.isError && [401, 403, 404].includes(parseApiError(preference.error).status);
  const cursorExpired =
    results.error instanceof ApiError &&
    ["search_cursor_expired", "search_cursor_invalid"].includes(results.error.code);
  const modelPending =
    !!modelId &&
    pages.some((page) => Object.values(page.leg_errors).includes("search_model_index_pending"));
  const visualReady =
    !status.isError &&
    (status.data?.legs.some(
      (leg) => leg === "thumbnail" || leg === "multiview" || leg === "point_cloud",
    ) ??
      false);
  return (
    <div className="h-full overflow-y-auto bg-background pb-24 md:pb-0">
      <div className="border-b border-border px-4 py-5 sm:px-6">
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
      </div>
      {!imageMode && !modelId && (
        <section
          aria-label={t("aiSearch.searchOptions")}
          className="border-b border-border bg-muted/20 px-4 py-2 sm:px-6 sm:py-3"
        >
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
          <div className="mt-2 flex flex-wrap items-center gap-x-5 gap-y-2 sm:mt-3">
            <div
              className="flex flex-wrap items-center gap-1"
              role="group"
              aria-label={t("aiSearch.resultTypes")}
            >
              <span className="sr-only mr-2 text-xs font-medium text-muted-foreground sm:not-sr-only">
                {t("aiSearch.resultTypes")}
              </span>
              {subjectTypes.map((type) => (
                <Button
                  key={type}
                  size="sm"
                  variant="ghost"
                  className={`px-2 text-xs sm:px-3 sm:text-sm ${types.includes(type) ? "bg-accent text-accent-foreground" : ""}`}
                  aria-pressed={types.includes(type)}
                  onClick={() => changeType(type)}
                >
                  {t(`aiSearch.type.${type}`)}
                </Button>
              ))}
            </div>
            {user && (
              <div className="flex flex-wrap items-center gap-1 lg:ml-auto">
                <SearchSavedViews
                  userId={user.id}
                  filters={{ ...filters, q, sort }}
                  onSelect={(value) => router.push(`/search?${writeSearchFilters(value)}`)}
                />
                {preference.data && !preferencesInaccessible && (
                  <SearchPreferences userId={user.id} value={preference.data} />
                )}
              </div>
            )}
          </div>
        </section>
      )}
      <div className="px-4 py-4 sm:px-6">
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
            <Button
              variant="outline"
              className="mb-4"
              onClick={() => router.push("/search?image=1")}
            >
              {t("aiSearch.searchByImage")}
            </Button>
          )
        )}
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
        {status.isError && (
          <div className="mb-3 flex items-center gap-3" role="alert">
            <p>{t("aiSearch.statusError")}</p>
            <Button variant="outline" size="sm" onClick={() => void status.refetch()}>
              {t("aiSearch.retry")}
            </Button>
          </div>
        )}
        {preference.isError && !imageMode && !modelId && (
          <div className="mb-3 flex items-center gap-3" role="alert">
            <p>{t("aiSearch.preferencesLoadError")}</p>
            <Button variant="outline" size="sm" onClick={() => void preference.refetch()}>
              {t("aiSearch.retry")}
            </Button>
          </div>
        )}
        {capability?.remote_hosts.length ? (
          <p className="mb-3 text-sm text-muted-foreground">
            {t("aiSearch.remoteDisclosure", { hosts: capability.remote_hosts.join(", ") })}
          </p>
        ) : null}
        {capability?.backlog && (
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
            <section aria-label={t("aiSearch.resultsTitle")}>
              <div className="mb-4 flex items-center justify-between gap-3 text-sm text-muted-foreground">
                <span role="status">{t("aiSearch.resultsShown", { count: items.length })}</span>
                <div className="flex gap-1" role="group" aria-label={t("aiSearch.resultView")}>
                  <Button
                    variant="ghost"
                    className={view === "grid" ? "bg-accent text-accent-foreground" : undefined}
                    size="icon-sm"
                    aria-label={t("aiSearch.gridView")}
                    aria-pressed={view === "grid"}
                    onClick={() => setView("grid")}
                  >
                    <LayoutGrid className="h-4 w-4" aria-hidden />
                  </Button>
                  <Button
                    variant="ghost"
                    className={view === "list" ? "bg-accent text-accent-foreground" : undefined}
                    size="icon-sm"
                    aria-label={t("aiSearch.listView")}
                    aria-pressed={view === "list"}
                    onClick={() => setView("list")}
                  >
                    <List className="h-4 w-4" aria-hidden />
                  </Button>
                </div>
              </div>
              <ul
                aria-label={t("aiSearch.resultsTitle")}
                className={
                  view === "grid"
                    ? "grid grid-cols-1 gap-3 sm:grid-cols-[repeat(auto-fill,minmax(240px,1fr))] xl:grid-cols-[repeat(auto-fill,minmax(280px,1fr))]"
                    : "divide-y divide-border border-y border-border"
                }
              >
                {items.map((item) => (
                  <li
                    key={`${item.subject_type}:${item.subject_id}`}
                    className={
                      view === "grid"
                        ? "min-w-0 overflow-hidden rounded-md border border-border bg-card transition-colors duration-press hover:border-primary"
                        : "min-w-0"
                    }
                  >
                    <Link
                      href={item.href}
                      aria-label={item.name}
                      aria-describedby={`search-result-type-${item.subject_type}-${item.subject_id}`}
                      className={
                        view === "grid"
                          ? "group block h-full p-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
                          : "group flex min-w-0 items-center gap-3 px-2 py-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
                      }
                    >
                      <SearchSubjectPreview
                        path={item.model?.thumbnail_url}
                        subjectType={item.subject_type}
                        large={view === "grid"}
                      />
                      <div className={view === "grid" ? "min-w-0 px-1 pb-1 pt-3" : "min-w-0"}>
                        <h2 className="break-words text-sm font-semibold leading-snug text-foreground group-hover:text-primary sm:text-base">
                          {item.name}
                        </h2>
                        <p
                          id={`search-result-type-${item.subject_type}-${item.subject_id}`}
                          className="mt-1 text-xs text-muted-foreground"
                        >
                          {t(`aiSearch.type.${item.subject_type}`)}
                        </p>
                      </div>
                    </Link>
                  </li>
                ))}
              </ul>
            </section>
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
        {results.isError && !!items.length && (
          <p role="alert" className="mt-4 text-sm text-destructive">
            {t(cursorExpired ? "aiSearch.cursorExpired" : "aiSearch.loadError")}
          </p>
        )}
        {results.isError && !!items.length && !cursorExpired && (
          <Button
            className="mt-4"
            variant="outline"
            onClick={() =>
              void (results.isFetchNextPageError ? results.fetchNextPage() : results.refetch())
            }
          >
            {t("aiSearch.retry")}
          </Button>
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
          !results.isError &&
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
      </div>
    </div>
  );
}
