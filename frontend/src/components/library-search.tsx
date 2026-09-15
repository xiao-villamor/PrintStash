import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Camera, Search, Sparkles, XCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import { DropdownMenu } from "@/components/ui/dropdown-menu";
import { getSearchStatus, searchLibrary } from "@/lib/api/search";
import { useAuth } from "@/lib/auth-context";
import { useI18n } from "@/lib/i18n";
import { Link } from "@/lib/link";
import { usePathname, useRouter, useSearchParams } from "@/lib/navigation";

export function LibrarySearch() {
  const { t } = useI18n();
  const { user } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const q = searchParams.get("q") ?? "";
  const [value, setValue] = useState(q);
  const [synced, setSynced] = useState(q);
  const [published, setPublished] = useState(q);
  const [debounced, setDebounced] = useState(q);
  const [open, setOpen] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const wrapper = useRef<HTMLDivElement>(null);
  const visible = pathname === "/" || pathname === "/search";
  if (synced !== q) {
    setSynced(q);
    // An earlier debounced browse update can land after a new keystroke.
    // Only navigation from outside this input replaces the current draft.
    if (q !== published) setValue(q);
  }
  const status = useQuery({
    queryKey: ["ai-search", "status", user?.id],
    queryFn: getSearchStatus,
    enabled: visible && !!user,
    refetchInterval: 15000,
    retry: false,
  });
  const suggestions = useQuery({
    queryKey: ["search-suggestions", user?.id, debounced],
    queryFn: ({ signal }) =>
      searchLibrary({ q: debounced, mode: "lexical", instant: true, limit: 5 }, signal),
    enabled: !!user && visible && open && !!debounced && debounced === value.trim(),
    gcTime: 0,
    retry: false,
  });
  useEffect(() => {
    if (!visible) return;
    function focus(event: KeyboardEvent) {
      const target = event.target instanceof HTMLElement ? event.target : null;
      if (
        event.key !== "/" ||
        event.metaKey ||
        event.ctrlKey ||
        event.altKey ||
        target?.matches("input, textarea, select, [contenteditable='true']")
      )
        return;
      event.preventDefault();
      input.current?.focus();
    }
    window.addEventListener("keydown", focus);
    return () => window.removeEventListener("keydown", focus);
  }, [visible]);
  const params = searchParams.toString();
  useEffect(() => {
    if (!visible) return;
    const timer = window.setTimeout(() => {
      const next = value.trim();
      setDebounced(next);
      if (pathname !== "/" || next === q) return;
      const updated = new URLSearchParams(params);
      if (next) updated.set("q", next);
      else updated.delete("q");
      setPublished(next);
      router.replace(updated.size ? `/?${updated}` : "/", { scroll: false });
    }, 250);
    return () => window.clearTimeout(timer);
  }, [pathname, params, q, router, value, visible]);
  function submit() {
    if (!value.trim()) return;
    setOpen(false);
    router.push(`/search?${new URLSearchParams({ q: value.trim(), parse: "1" })}`);
  }
  function changeValue(next: string) {
    setValue(next);
    setOpen(!!next.trim());
    if (next.trim()) return;
    setDebounced("");
    setPublished("");
    if (pathname === "/search") {
      router.replace("/", { scroll: false });
    } else {
      const updated = new URLSearchParams(params);
      updated.delete("q");
      router.replace(updated.size ? `/?${updated}` : "/", { scroll: false });
    }
  }
  if (!visible) return <span className="flex-1" />;
  const current = debounced === value.trim();
  const visualReady = status.data?.legs.some(
    (leg) => leg === "thumbnail" || leg === "multiview" || leg === "point_cloud",
  );
  return (
    <div ref={wrapper} className="mx-3 min-w-0 max-w-2xl flex-1 sm:mx-8">
      <DropdownMenu
        open={open && !!value.trim()}
        onOpenChange={setOpen}
        role="dialog"
        align="start"
        contentClassName="w-full rounded-lg border border-border bg-popover text-popover-foreground shadow-lg"
        trigger={
          <form
            className="relative"
            role="search"
            onSubmit={(event) => {
              event.preventDefault();
              submit();
            }}
          >
            {status.data?.semantic_ready ? (
              <Button
                variant="ghost"
                size="icon"
                type="submit"
                className="absolute left-1 top-1/2 h-8 w-8 -translate-y-1/2"
                aria-label={t("aiSearch.submitAi")}
                title={t("aiSearch.submitAi")}
              >
                <Sparkles className="h-4 w-4 text-primary" />
              </Button>
            ) : (
              <Search
                className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
                aria-hidden
              />
            )}
            <input
              ref={input}
              data-model-search
              data-menu-trigger
              type="search"
              maxLength={512}
              autoComplete="off"
              aria-label={t("aiSearch.searchLibrary")}
              aria-haspopup="dialog"
              aria-expanded={open && !!value.trim()}
              className={`block w-full rounded-lg border border-border bg-muted py-2 pl-9 ${visualReady ? "pr-20" : "pr-10"} text-sm text-foreground placeholder:text-muted-foreground focus:border-primary focus:bg-background focus:outline-none focus:ring-1 focus:ring-ring [&::-webkit-search-cancel-button]:hidden`}
              placeholder={t("nav.search")}
              value={value}
              onClick={() => setOpen(!!value.trim())}
              onChange={(event) => {
                changeValue(event.target.value);
              }}
              onKeyDown={(event) => {
                if (event.key === "ArrowDown" && open) {
                  event.preventDefault();
                  wrapper.current
                    ?.querySelector<HTMLAnchorElement>("[data-search-suggestion]")
                    ?.focus();
                }
              }}
            />
            <div className="absolute right-1 top-1/2 flex -translate-y-1/2 items-center">
              {visualReady && (
                <Button
                  variant="ghost"
                  size="icon"
                  type="button"
                  aria-label={t("aiSearch.searchByImage")}
                  title={t("aiSearch.searchByImage")}
                  onClick={() => {
                    setOpen(false);
                    router.push("/search?image=1");
                  }}
                >
                  <Camera className="h-4 w-4" />
                </Button>
              )}
              {value && (
                <Button
                  variant="ghost"
                  size="icon"
                  type="button"
                  aria-label={t("nav.clearSearch")}
                  onClick={() => {
                    changeValue("");
                    input.current?.focus();
                  }}
                >
                  <XCircle className="h-4 w-4" />
                </Button>
              )}
            </div>
          </form>
        }
      >
        <div className="p-2">
          <p className="px-2 py-1 text-xs text-muted-foreground">
            {t("aiSearch.keywordSuggestions")}
          </p>
          {!current || suggestions.isFetching ? (
            <p role="status" className="p-2 text-sm">
              {t("aiSearch.searching")}
            </p>
          ) : suggestions.isError ? (
            <p role="alert" className="p-2 text-sm text-destructive">
              {t("aiSearch.suggestionsError")}
            </p>
          ) : (
            <ul>
              {suggestions.data?.items.map((item) => (
                <li key={`${item.subject_type}:${item.subject_id}`}>
                  <Link
                    data-search-suggestion
                    href={item.href}
                    className="block rounded-md px-2 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring hover:bg-muted"
                    onClick={() => setOpen(false)}
                  >
                    <span className="block truncate font-medium">{item.name}</span>
                    <span className="text-xs text-muted-foreground">
                      {t(`aiSearch.type.${item.subject_type}`)}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
          <Button variant="ghost" className="mt-1 w-full justify-start" onClick={submit}>
            {t("aiSearch.allResults")}
          </Button>
          {status.data?.backlog && (
            <p role="status" className="px-2 py-1 text-xs text-muted-foreground">
              {t("aiSearch.backlog")}
            </p>
          )}
        </div>
      </DropdownMenu>
    </div>
  );
}
