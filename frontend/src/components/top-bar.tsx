"use client";

import { useUiLocale } from "@/lib/i18n";

import { Suspense, useEffect, useState, useSyncExternalStore } from "react";
import { useRouter, usePathname, useSearchParams } from "@/lib/navigation";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@/lib/link";
import { ArrowLeft, Bell, ChevronDown, BookOpen, PanelLeft, Upload } from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { useLibraryStartup } from "@/lib/library-startup-context";
import { useCollectionsPane } from "@/lib/collections-pane-context";
import { useCollectionSearch } from "@/lib/queries";
import { modelDetailOptions } from "@/features/library/model-detail";
import { LibraryBackLink } from "@/features/library/navigation";
import { uiText } from "@/lib/locale";
import { userMessage } from "@/lib/errors";
import { lastVaultHref } from "@/lib/last-collection";
import { LibrarySearch } from "@/components/library-search";
import { BrandMark } from "@/components/brand-mark";
import { ThemeToggle } from "@/components/theme-toggle";
import { WorkspaceMenu } from "@/components/workspace-menu";
import { LocaleToggle } from "@/components/locale-toggle";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { TaskList } from "@/components/task-list";
import { subscribeArchiveReviewRequests } from "@/lib/archive-review-events";
import { DropdownMenu } from "@/components/ui/dropdown-menu";
import { Button } from "@/components/ui/button";
import { useMediaQuery } from "@/lib/use-media-query";
import {
  clearCompletedTasks,
  listTasks,
  needsArchiveReview,
  subscribeTasks,
  startImportJobSync,
  TaskItem,
} from "@/lib/task-center";

const WIKI_URL = "https://www.printstash.org/docs/";

function pageTitle(pathname: string): MessageKey | null {
  if (pathname === "/") return "nav.vault";
  if (pathname.startsWith("/inbox")) return "nav.inbox";
  if (pathname.startsWith("/printers")) return "nav.printers";
  if (pathname.startsWith("/profiles")) return "nav.profiles";
  if (pathname.startsWith("/statistics")) return "nav.statistics";
  if (pathname.startsWith("/settings")) return "nav.settings";
  if (pathname.startsWith("/search")) return "Search";
  if (pathname.startsWith("/getting-started")) return "nav.gettingStarted";
  if (pathname.startsWith("/documents")) return "nav.document";
  if (pathname.startsWith("/multipart-models")) return "nav.multipart";
  if (pathname.startsWith("/library/similar")) return "similarity.title";
  if (pathname.startsWith("/builds")) return "build.title";
  if (pathname.startsWith("/models/")) return "nav.model";
  return null;
}

// `lastVaultHref` reads the remembered collection out of localStorage — an
// external store. Same-tab writes are picked up on the next render (a route
// change re-renders the bar); the `storage` event covers other tabs.
function subscribeLastVaultHref(onStoreChange: () => void): () => void {
  window.addEventListener("storage", onStoreChange);
  return () => window.removeEventListener("storage", onStoreChange);
}

export function TopBar() {
  useUiLocale();
  const { t } = useI18n();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const pane = useCollectionsPane();
  const attachBreadcrumbs = pane?.attachBreadcrumbs;
  const titleKey =
    pathname === "/settings" && searchParams.get("section") === "trash"
      ? "settings.trash"
      : pageTitle(pathname);
  const desktop = useMediaQuery("(min-width: 768px)");
  const router = useRouter();
  const { user, loading, logout } = useAuth();
  const startup = useLibraryStartup();
  const modelMatch = /^\/models\/(\d+)\/?$/.exec(pathname);
  const parsedModelId = modelMatch ? Number(modelMatch[1]) : null;
  const modelId =
    parsedModelId !== null && Number.isSafeInteger(parsedModelId) && parsedModelId > 0
      ? parsedModelId
      : null;
  const detail = useQuery({ ...modelDetailOptions(modelId), enabled: false });
  // Library already owns this exact probe. Off-library the header keeps one
  // observer active so members do not get an upload action without edit access.
  const writableProbe = useCollectionSearch("", "edit", {
    enabled: !!user && !user.is_superuser && pathname !== "/",
  });
  const canUpload =
    !!user &&
    (user.is_superuser ||
      (!writableProbe.isError && (writableProbe.data?.pages[0]?.items.length ?? 0) > 0));
  const activityEnabled = startup.canLoad("activity");
  const [tasks, setTasks] = useState<TaskItem[]>(() => listTasks());
  const [tasksOpen, setTasksOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  // The logo returns to the model browser, restoring the last folder the user
  // was in rather than always resetting to "All Models", so it reflects the
  // remembered collection at click time.
  const homeHref = useSyncExternalStore(subscribeLastVaultHref, lastVaultHref);
  const modelBackHref = detail.data?.collection
    ? `/?c=${encodeURIComponent(detail.data.collection)}`
    : detail.data
      ? "/"
      : homeHref;

  useEffect(() => {
    const unsubscribe = subscribeTasks(() => setTasks(listTasks()));
    const stopSync = activityEnabled ? startImportJobSync() : () => {};
    return () => {
      unsubscribe();
      stopSync();
    };
  }, [activityEnabled]);

  useEffect(() => subscribeArchiveReviewRequests(() => setTasksOpen(false)), []);

  function openUpload() {
    if (!canUpload) return;
    const target = new URL(
      pathname === "/" ? `/?${searchParams}` : homeHref,
      window.location.origin,
    );
    target.searchParams.set("upload", "1");
    router.push(`${target.pathname}?${target.searchParams}`);
  }

  async function handleLogout() {
    await logout();
    setProfileOpen(false);
    router.push("/login");
  }

  return (
    <header className="relative z-dropdown flex h-14 shrink-0 items-center gap-2 border-b border-border bg-card px-2 sm:gap-3 sm:px-3">
      {!desktop && modelId !== null && (
        <LibraryBackLink
          href={modelBackHref}
          aria-label={uiText("Back")}
          className="flex h-8 w-8 shrink-0 items-center justify-center text-muted-foreground hover:bg-muted hover:text-foreground md:hidden"
        >
          <ArrowLeft className="h-4 w-4" aria-hidden />
        </LibraryBackLink>
      )}
      {/* Logo */}
      <Link
        href={homeHref}
        className="flex shrink-0 items-center space-x-2 transition-opacity hover:opacity-80 md:hidden"
      >
        <div className="w-8 h-8 bg-primary rounded flex items-center justify-center flex-shrink-0">
          <BrandMark className="h-6 w-6 text-primary-foreground" />
        </div>
        <span className="text-xl font-bold text-foreground tracking-tight hidden sm:block">
          PrintStash
        </span>
      </Link>

      {pane && (
        <button
          type="button"
          aria-label={uiText("Collections")}
          aria-controls="collections-pane"
          aria-expanded={pane.open}
          onClick={pane.toggle}
          className="hidden h-8 w-8 shrink-0 items-center justify-center text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring md:flex"
        >
          <PanelLeft className="h-4 w-4" aria-hidden />
        </button>
      )}
      {pathname === "/" && pane ? (
        <div ref={attachBreadcrumbs} className="hidden min-w-0 flex-1 md:block" />
      ) : (
        <nav
          aria-label={uiText("Breadcrumbs")}
          aria-hidden={!desktop || undefined}
          className="hidden min-w-0 flex-1 items-center gap-2 overflow-hidden whitespace-nowrap text-sm md:flex"
        >
          {modelId !== null ? (
            <LibraryBackLink
              href={modelBackHref}
              aria-label={uiText("Back")}
              className="shrink-0 text-muted-foreground hover:text-foreground"
            >
              {uiText("Collections")}
            </LibraryBackLink>
          ) : (
            <Link href={homeHref} className="shrink-0 text-muted-foreground hover:text-foreground">
              PrintStash
            </Link>
          )}
          <span aria-hidden className="text-muted-foreground">
            /
          </span>
          <span className="min-w-0 truncate font-medium text-foreground" aria-current="page">
            {modelId !== null && detail.data
              ? detail.data.name
              : titleKey
                ? t(titleKey)
                : t("Page not found")}
          </span>
        </nav>
      )}

      {/* Search */}
      <Suspense fallback={<span className="flex-1" />}>
        <LibrarySearch />
      </Suspense>

      {/* Right Actions & Profile */}
      <div className="flex shrink-0 items-center gap-1 sm:gap-2">
        <Button
          type="button"
          size="sm"
          onClick={openUpload}
          disabled={!canUpload}
          aria-label={uiText("Upload")}
          title={
            writableProbe.isError
              ? userMessage(writableProbe.error)
              : canUpload
                ? uiText("Upload artifacts")
                : uiText("Sign in and get edit access to upload")
          }
        >
          <Upload className="h-4 w-4" aria-hidden />
          <span className="hidden sm:inline">{uiText("Upload")}</span>
        </Button>
        <a
          href={WIKI_URL}
          className="hidden h-8 w-8 items-center justify-center text-muted-foreground transition-colors hover:bg-muted hover:text-foreground sm:flex md:hidden"
        >
          <BookOpen className="h-4 w-4" />
          <span className="sr-only">{t("nav.wiki")}</span>
        </a>
        <div className="flex md:hidden">
          <ThemeToggle />
          <LocaleToggle />
        </div>
        <DropdownMenu
          open={tasksOpen}
          onOpenChange={(open) => {
            if (open) startup.request("activity");
            setTasksOpen(open);
          }}
          align="end"
          role="dialog"
          className="hidden sm:flex"
          trigger={
            <button
              type="button"
              data-menu-trigger
              onClick={() => {
                startup.request("activity");
                setTasksOpen((v) => !v);
              }}
              className="relative flex h-8 w-8 items-center justify-center text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              aria-label={t("nav.notifications")}
              title={t("nav.notifications")}
              aria-haspopup="dialog"
              aria-expanded={tasksOpen}
            >
              <Bell className="h-4 w-4" />
              {tasks.some(
                (t) => t.status === "pending" || t.status === "running" || needsArchiveReview(t),
              ) && (
                <span className="absolute top-0.5 right-0.5 h-2 w-2 rounded-full bg-primary ring-2 ring-background" />
              )}
            </button>
          }
        >
          <TaskList
            onNavigate={() => setTasksOpen(false)}
            tasks={tasks}
            onClear={() => {
              clearCompletedTasks();
              setTasks(listTasks());
            }}
          />
        </DropdownMenu>

        {/* Profile — hidden on mobile, where the bottom nav's "More" sheet owns
            the account actions (the avatar menu only duplicated nav links). */}
        {!loading && !user ? (
          <div className="relative hidden sm:block">
            <Link
              href="/login"
              className="flex items-center px-3 py-1.5 text-sm font-medium text-primary-foreground bg-primary rounded hover:bg-primary-hover transition-colors"
            >
              {t("nav.logIn")}
            </Link>
          </div>
        ) : (
          <DropdownMenu
            open={profileOpen}
            onOpenChange={setProfileOpen}
            align="end"
            role="menu"
            className="hidden sm:block md:hidden"
            trigger={
              <Button
                type="button"
                variant="ghost"
                size="sm"
                data-menu-trigger
                onClick={() => setProfileOpen((v) => !v)}
                className="group"
                aria-haspopup="menu"
                aria-expanded={profileOpen}
              >
                <div
                  aria-hidden="true"
                  className="flex h-7 w-7 items-center justify-center rounded-full border border-input bg-accent text-xs font-semibold text-accent-foreground"
                >
                  {user?.username.charAt(0).toUpperCase() ?? "…"}
                </div>
                <span className="sr-only">{t("nav.myWorkspace")}</span>
                <ChevronDown
                  className={`hidden h-4 w-4 text-muted-foreground transition-transform ${profileOpen ? "rotate-180" : ""}`}
                />
              </Button>
            }
          >
            <WorkspaceMenu
              pathname={pathname}
              username={user?.username ?? "…"}
              printersInQuickAccess={desktop && pathname === "/"}
              isAdmin={!!user?.is_superuser}
              onNavigate={() => setProfileOpen(false)}
              onLogout={handleLogout}
            />
          </DropdownMenu>
        )}
      </div>
    </header>
  );
}
