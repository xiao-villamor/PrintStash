import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Camera, History, Search, Sparkles, XCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { searchStatusOptions } from "@/lib/queries/search";
import { useAuth } from "@/lib/auth-context";
import { useLibraryStartup } from "@/lib/library-startup-context";
import { useI18n } from "@/lib/i18n";
import { Link } from "@/lib/link";
import { usePathname, useRouter, useSearchParams } from "@/lib/navigation";
import { useMediaQuery } from "@/lib/use-media-query";

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
}: {
  placement?: "header" | "sidebar";
  recent?: readonly string[];
  onSearchCommit?: (query: string) => void;
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
  const showAi = sidebar || aiEnabled;
  const showCamera = !sidebar && visualReady;

  useEffect(() => {
    const next = value.trim();
    if (composing || next === q || (!sidebar && pathname !== "/" && pathname !== "/search")) return;
    publishTimer.current = window.setTimeout(() => {
      publishTimer.current = undefined;
      setPublished(next);
      if (sidebar || pathname === "/search" || aiActive) {
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
    router.push(searchHref(query, nextMode, pathname === "/search" ? params : "", true));
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
    if (sidebar || pathname !== "/" || aiActive) {
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
  const inputPadding = showAi
    ? showCamera
      ? value
        ? "pr-20 sm:pr-28"
        : "pr-20"
      : value
        ? "pr-20"
        : "pr-10"
    : showCamera
      ? value
        ? "pr-12 sm:pr-20"
        : "pr-12"
      : value
        ? "pr-10"
        : "pr-3";
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
        className={`relative min-w-0 ${sidebar ? "w-full shrink-0" : "flex-1"}`}
        role="search"
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <Search
          className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
          aria-hidden
        />
        <Input
          ref={input}
          autoFocus={sidebar ? desktop : pathname === "/search" && !desktop}
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
          className={`block h-9 w-full rounded border border-input bg-card py-2 pl-9 ${inputPadding} text-[13px] text-foreground placeholder:text-muted-foreground focus:border-primary focus:outline-none focus:ring-1 focus:ring-ring [&::-webkit-search-cancel-button]:hidden`}
          placeholder={t(aiActive ? "aiSearch.describeModel" : "Search Models, files and tags")}
          value={value}
          onChange={(event) => changeValue(event.target.value)}
          onCompositionStart={() => setComposing(true)}
          onCompositionEnd={() => setComposing(false)}
        />
        <div className="absolute right-1 top-1/2 flex -translate-y-1/2 items-center">
          {!value && !showAi && !showCamera && (
            <kbd
              aria-hidden
              className="pointer-events-none mr-1 border border-border bg-muted px-1 font-mono text-2xs text-muted-foreground"
            >
              /
            </kbd>
          )}
          {showAi && (
            <Button
              variant="ghost"
              size="icon"
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
              className={`h-8 w-8 bg-transparent hover:bg-transparent ${aiActive ? "text-primary hover:text-primary" : "text-muted-foreground hover:text-foreground"}`}
              onClick={toggleAi}
            >
              <Sparkles className="h-4 w-4" aria-hidden />
            </Button>
          )}
          {showCamera && (
            <Button
              variant="ghost"
              size="icon"
              type="button"
              className={value ? "hidden h-8 w-8 sm:inline-flex" : "h-8 w-8"}
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
              className="h-8 w-8"
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
          <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
            {t(aiActive ? "aiSearch.meaningHelp" : "aiSearch.keywordHelp")}
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
            <div className="mt-3 text-xs leading-relaxed text-muted-foreground">
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
                  className="mt-1 inline-block underline underline-offset-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  {t(aiEnabled ? "aiSearch.viewPreparation" : "AI Search setup")}
                </Link>
              ) : null}
            </div>
          )}
          <div className="mt-5 min-h-0 flex-1 overflow-y-auto border-t border-border pt-4 pb-3">
            <h3 className="mb-2 px-1 text-xs font-medium text-muted-foreground">
              {t("Recent searches")}
            </h3>
            {recent.length ? (
              recent.map((query) => (
                <Button
                  key={query}
                  variant="ghost"
                  size="sm"
                  className="h-8 w-full justify-start gap-2 px-1 text-left text-xs font-normal"
                  onClick={() => {
                    setValue(query);
                    searchFor(query, effectiveMode);
                    onSearchCommit?.(query);
                  }}
                >
                  <History className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
                  <span className="truncate">{query}</span>
                </Button>
              ))
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
