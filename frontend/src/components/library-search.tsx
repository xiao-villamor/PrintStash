import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Camera,
  Check,
  Folder,
  History,
  Library,
  Search,
  Sparkles,
  X,
  XCircle,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { searchStatusOptions } from "@/lib/queries/search";
import { useAuth } from "@/lib/auth-context";
import { useLibraryStartup } from "@/lib/library-startup-context";
import { useI18n } from "@/lib/i18n";
import { Link } from "@/lib/link";
import { usePathname, useRouter, useSearchParams } from "@/lib/navigation";
import { useMediaQuery } from "@/lib/use-media-query";
import { collectionIdentity, useCollectionName } from "@/features/library/collection-navigation";

type SearchMode = "lexical" | "hybrid";

function searchHref(query: string, mode: SearchMode, base: string, parse = false) {
  const params = new URLSearchParams(base);
  if (query.trim()) params.set("q", query.trim());
  else params.delete("q");
  params.delete("image");
  params.delete("model");
  params.delete("parse_error");
  params.set("mode", mode);
  // Semantic matching updates live. Optional language parsing runs on submission.
  if (parse && mode === "hybrid" && query.trim()) params.set("parse", "1");
  else params.delete("parse");
  return `/search?${params}`;
}

export function LibrarySearch({
  placement = "header",
  recent = [],
  onSearchCommit,
  onRecentRemove,
  onRecentClear,
}: {
  placement?: "header" | "sidebar" | "mobile";
  recent?: readonly string[];
  onSearchCommit?: (query: string) => void;
  onRecentRemove?: (query: string) => void;
  onRecentClear?: () => void;
}) {
  const { t } = useI18n();
  const { user } = useAuth();
  const startup = useLibraryStartup();
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const params = searchParams.toString();
  const q = pathname === "/" || pathname === "/search" ? (searchParams.get("q") ?? "") : "";
  const sidebar = placement === "sidebar";
  const desktop = useMediaQuery("(min-width: 768px)");
  const routeMode: SearchMode =
    pathname === "/search" && searchParams.get("mode") !== "lexical" ? "hybrid" : "lexical";
  const [mode, setMode] = useState<SearchMode>(routeMode);
  const [syncedMode, setSyncedMode] = useState(routeMode);
  if (syncedMode !== routeMode) {
    setSyncedMode(routeMode);
    setMode(routeMode);
  }
  // The sidebar searches where the user is. The collection they came from
  // stays one click away after widening the search to the whole library.
  const libraryRoute = pathname === "/";
  const scopedPath = libraryRoute ? searchParams.get("c") : null;
  const scopedId = libraryRoute ? collectionIdentity(searchParams) : null;
  const [origin, setOrigin] = useState(
    scopedPath === null ? null : { path: scopedPath, id: scopedId },
  );
  if (scopedPath !== null && (origin?.path !== scopedPath || origin.id !== scopedId))
    setOrigin({ path: scopedPath, id: scopedId });
  const originName = useCollectionName(
    sidebar && libraryRoute ? (origin?.path ?? null) : null,
    origin?.id ?? null,
  );
  const scoped = scopedPath !== null;
  function changeScope(next: "collection" | "library") {
    if (!origin || next === (scoped ? "collection" : "library")) return;
    const updated = new URLSearchParams(params);
    updated.delete("scope");
    if (next === "library") {
      updated.delete("c");
      updated.delete("cid");
    } else {
      updated.set("c", origin.path);
      if (origin.id !== null) updated.set("cid", String(origin.id));
    }
    router.replace(`/?${updated}`);
    input.current?.focus();
  }
  const [engaged, setEngaged] = useState(false);
  const [value, setValue] = useState(q);
  const [synced, setSynced] = useState(q);
  const [published, setPublished] = useState(q);
  const [composing, setComposing] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const publishTimer = useRef<number | undefined>(undefined);
  if (synced !== q) {
    setSynced(q);
    // A receipt for our previous draft must not replace newer typing.
    if (q !== published) setValue(q);
  }
  const status = useQuery(
    searchStatusOptions(
      user?.id,
      startup.canLoad("search") &&
        (sidebar || pathname === "/" || pathname === "/search" || engaged),
    ),
  );
  const capability = status.isError ? undefined : status.data;
  const visualReady = capability?.legs.some(
    (leg) => leg === "thumbnail" || leg === "multiview" || leg === "point_cloud",
  );
  const aiEnabled = capability?.enabled === true;
  const aiReady = aiEnabled && capability?.semantic_ready === true;
  const effectiveMode = aiReady ? mode : "lexical";
  const aiActive = effectiveMode === "hybrid";
  const AiModeIcon = aiActive ? Check : Sparkles;
  const showAi = sidebar || aiEnabled;
  const showCamera = !sidebar && visualReady;

  useEffect(() => {
    const next = value.trim();
    if (composing || next === q || (!sidebar && pathname !== "/" && pathname !== "/search")) return;
    publishTimer.current = window.setTimeout(() => {
      publishTimer.current = undefined;
      setPublished(next);
      if (pathname === "/search" || aiActive) {
        const href = searchHref(next, effectiveMode, pathname === "/search" ? params : "");
        if (pathname === "/search") router.replace(href);
        else router.push(href);
      } else {
        const updated = new URLSearchParams(params);
        if (next) updated.set("q", next);
        else updated.delete("q");
        router.replace(updated.size ? `/?${updated}` : "/");
      }
    }, 250);
    return () => {
      if (publishTimer.current !== undefined) window.clearTimeout(publishTimer.current);
      publishTimer.current = undefined;
    };
  }, [value, q, composing, sidebar, pathname, params, aiActive, effectiveMode, router]);

  function cancelPublication() {
    if (publishTimer.current !== undefined) window.clearTimeout(publishTimer.current);
    publishTimer.current = undefined;
  }
  function searchFor(query: string, nextMode: SearchMode) {
    if (!query.trim()) return;
    cancelPublication();
    setPublished(query.trim());
    if (pathname === "/" && nextMode === "lexical") {
      const updated = new URLSearchParams(params);
      updated.set("q", query.trim());
      router.replace(`/?${updated}`);
    } else router.push(searchHref(query, nextMode, pathname === "/search" ? params : "", true));
  }
  function toggleAi() {
    const nextMode = aiActive ? "lexical" : "hybrid";
    cancelPublication();
    setMode(nextMode);
    if (value.trim()) searchFor(value, nextMode);
    input.current?.focus();
  }
  function submit() {
    if (composing) return;
    onSearchCommit?.(value.trim());
    if (pathname !== "/" || aiActive) {
      searchFor(value, effectiveMode);
      return;
    }
    cancelPublication();
    const next = value.trim();
    const updated = new URLSearchParams(params);
    if (next) updated.set("q", next);
    else updated.delete("q");
    setPublished(next);
    router.replace(updated.size ? `/?${updated}` : "/");
  }
  function changeValue(next: string) {
    setValue(next);
    if (next.trim()) return;
    cancelPublication();
    setPublished("");
    if (pathname === "/search") router.replace(searchHref("", effectiveMode, params));
    else {
      const updated = new URLSearchParams(params);
      updated.delete("q");
      router.replace(updated.size ? `/?${updated}` : "/");
    }
  }
  return (
    <div
      className={
        sidebar
          ? "flex min-h-0 min-w-0 flex-1 flex-col px-3"
          : "shell-search ml-auto flex min-w-0 max-w-[382px] flex-1 items-center"
      }
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) onSearchCommit?.(value.trim());
      }}
    >
      <form
        className={`flex h-9 min-w-0 items-center gap-1 rounded-md border border-input bg-field-surface px-1.5 hover:border-border-strong focus-within:border-muted-foreground ${sidebar ? "w-full shrink-0" : "flex-1"}`}
        role="search"
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <Search
          className="pointer-events-none ml-1.5 size-4 shrink-0 text-muted-foreground"
          aria-hidden
        />
        <Input
          ref={input}
          autoFocus={
            placement === "mobile" || (sidebar ? desktop : pathname === "/search" && !desktop)
          }
          onFocus={() => {
            setEngaged(true);
            startup.request("search");
          }}
          data-model-search
          data-shell-panel-search={sidebar ? "" : undefined}
          type="search"
          maxLength={512}
          autoComplete="off"
          spellCheck={false}
          aria-label={t("aiSearch.searchLibrary")}
          className="h-full flex-1 rounded-none border-0 bg-transparent px-1 py-0 text-field focus:ring-0 [&::-webkit-search-cancel-button]:hidden"
          placeholder={t(aiActive ? "aiSearch.describeModel" : "Search Models, files and tags")}
          value={value}
          onChange={(event) => changeValue(event.target.value)}
          onCompositionStart={() => setComposing(true)}
          onCompositionEnd={() => setComposing(false)}
        />
        <div className="flex shrink-0 items-center gap-0.5">
          {!value && !showAi && !showCamera && (
            <kbd
              aria-hidden
              className="pointer-events-none mr-1 rounded-md border border-border bg-muted px-1 font-mono text-2xs text-muted-foreground"
            >
              /
            </kbd>
          )}
          {showAi && (
            <Button
              variant="ghost"
              size="xs"
              type="button"
              disabled={!aiReady}
              aria-label={t("aiSearch.settingsTitle")}
              aria-pressed={aiActive}
              title={t(
                !aiReady
                  ? "aiSearch.notReady"
                  : aiActive
                    ? "aiSearch.turnOffAi"
                    : "aiSearch.submitAi",
              )}
              className="gap-1.5 px-2 text-muted-foreground aria-pressed:bg-accent aria-pressed:text-accent-foreground dark:aria-pressed:bg-surface-bright dark:aria-pressed:hover:bg-surface-bright"
              onClick={toggleAi}
            >
              <AiModeIcon className={aiActive ? "size-3.5 text-success" : "size-3.5"} aria-hidden />
              {t(aiActive ? "aiSearch.activeLabel" : "aiSearch.settingsTitle")}
            </Button>
          )}
          {showCamera && (
            <Button
              variant="ghost"
              size="icon"
              type="button"
              className={value ? "hidden size-7 sm:inline-flex" : "size-7"}
              aria-label={t("aiSearch.searchByImage")}
              title={t("aiSearch.searchByImage")}
              onClick={() => router.push("/search?image=1")}
            >
              <Camera className="h-4 w-4" aria-hidden />
            </Button>
          )}
          {value && (
            <Button
              variant="ghost"
              size="icon"
              type="button"
              className="size-7"
              aria-label={t("nav.clearSearch")}
              onClick={() => {
                changeValue("");
                input.current?.focus();
              }}
            >
              <XCircle className="h-4 w-4" aria-hidden />
            </Button>
          )}
        </div>
      </form>
      {sidebar && (
        <>
          {libraryRoute && origin && (
            <div
              role="group"
              aria-label={t("search.scope")}
              className="mt-2 grid grid-cols-2 rounded-md border border-border"
            >
              {(
                [
                  ["collection", Folder, originName ?? t("search.scopeCollection")],
                  ["library", Library, t("search.scopeLibrary")],
                ] as const
              ).map(([key, Icon, label]) => {
                const pressed = (key === "collection") === scoped;
                return (
                  <button
                    key={key}
                    type="button"
                    aria-pressed={pressed}
                    title={label}
                    onClick={() => changeScope(key)}
                    className={`flex h-8 min-w-0 items-center justify-center gap-1.5 px-2 text-xs transition-colors duration-press focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring ${pressed ? "bg-accent text-accent-foreground" : "text-muted-foreground hover:bg-muted hover:text-foreground"}`}
                  >
                    <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden />
                    <span className="truncate">{label}</span>
                  </button>
                );
              })}
            </div>
          )}
          <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
            {aiActive
              ? t("aiSearch.meaningHelp")
              : scoped && originName
                ? t("search.keywordHelpIn", { value1: originName })
                : t("aiSearch.keywordHelp")}
          </p>
          {visualReady && (
            <Button
              variant="outline"
              size="sm"
              className="mt-3 self-start"
              onClick={() => router.push("/search?image=1")}
            >
              {t("Image search")}
            </Button>
          )}
          {!aiReady && (
            <div className="mt-3 flex items-start gap-2.5 rounded-md border border-border bg-muted/40 px-3 py-2.5 text-xs leading-relaxed text-muted-foreground">
              <Sparkles className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
              <div className="min-w-0 flex-1">
                <p role="status">
                  {t(
                    status.isError
                      ? "aiSearch.statusError"
                      : status.isPending
                        ? "Checking AI Search…"
                        : aiEnabled
                          ? "aiSearch.notReady"
                          : "aiSearch.noActiveIndex",
                  )}
                </p>
                {status.isError ? (
                  <Button
                    variant="outline"
                    size="sm"
                    className="mt-2"
                    loading={status.isFetching}
                    onClick={() => void status.refetch()}
                  >
                    {t("Retry")}
                  </Button>
                ) : user?.is_superuser && !status.isPending ? (
                  <Link
                    href="/settings?section=ai-search"
                    className="mt-1 inline-block font-medium text-foreground underline underline-offset-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    {t(aiEnabled ? "aiSearch.viewPreparation" : "AI Search setup")}
                  </Link>
                ) : null}
              </div>
            </div>
          )}
          <div className="mt-4 min-h-0 flex-1 overflow-y-auto border-t border-border pt-3 pb-3">
            <div className="mb-1 flex h-7 items-center justify-between px-1">
              <h3 className="font-mono text-3xs uppercase tracking-wider text-muted-foreground">
                {t("Recent searches")}
              </h3>
              {recent.length > 0 && onRecentClear && (
                <Button
                  variant="ghost"
                  size="xs"
                  className="px-1.5 text-2xs"
                  onClick={onRecentClear}
                >
                  {t("Clear")}
                </Button>
              )}
            </div>
            {recent.length ? (
              <ul>
                {recent.map((query) => (
                  <li key={query} className="group/recent relative">
                    <Button
                      variant="ghost"
                      size="sm"
                      className="w-full justify-start gap-2 px-1 pr-8 text-left text-xs font-normal"
                      onClick={() => {
                        setValue(query);
                        searchFor(query, effectiveMode);
                        onSearchCommit?.(query);
                      }}
                    >
                      <History className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
                      <span className="truncate">{query}</span>
                    </Button>
                    {onRecentRemove && (
                      <Button
                        variant="ghost"
                        size="icon"
                        className="absolute right-0 top-1/2 w-7 -translate-y-1/2 text-muted-foreground opacity-0 transition-opacity duration-press group-focus-within/recent:opacity-100 group-hover/recent:opacity-100 focus-visible:opacity-100"
                        aria-label={t("search.removeRecent", { value1: query })}
                        onClick={() => onRecentRemove(query)}
                      >
                        <X className="h-3.5 w-3.5" aria-hidden />
                      </Button>
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="px-1 text-xs leading-relaxed text-muted-foreground">
                {t("aiSearch.recentEmpty")}
              </p>
            )}
          </div>
        </>
      )}
    </div>
  );
}
