"use client";

import { useUiLocale } from "@/lib/i18n";

import { useEffect, useState, useSyncExternalStore } from "react";
import { useRouter, usePathname, useSearchParams } from "@/lib/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@/lib/link";
import { ArrowLeft, Bell, PanelLeft, PanelRight, Search, Upload } from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { useLibraryStartup } from "@/lib/library-startup-context";
import { useCollectionsPane } from "@/lib/collections-pane-context";
import {
  collectionLookupByIdOptions,
  collectionLookupOptions,
  useCollectionSearch,
} from "@/lib/queries";
import { collectionIdentity } from "@/features/library/collection-navigation";
import { modelDetailOptions } from "@/features/library/model-detail";
import { multipartDetailOptions } from "@/features/library/multipart";
import { printerDetailOptions } from "@/features/printers/queries";
import { documentDetailOptions } from "@/lib/queries/documents";
import { LibraryBackLink } from "@/features/library/navigation";
import { uiText } from "@/lib/locale";
import { userMessage } from "@/lib/errors";
import { lastVaultHref } from "@/lib/last-collection";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { TaskList } from "@/components/task-list";
import { subscribeArchiveReviewRequests } from "@/lib/archive-review-events";
import { DropdownMenu } from "@/components/ui/dropdown-menu";
import { Button } from "@/components/ui/button";
import { useMediaQuery } from "@/lib/use-media-query";
import { useMobileFilterDrawer } from "@/lib/mobile-filter-context";
import {
  clearCompletedTasks,
  listTasks,
  needsArchiveReview,
  subscribeTasks,
  startImportJobSync,
  TaskItem,
} from "@/lib/task-center";

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
    pathname === "/settings" &&
    searchParams.get("section") === "trash" &&
    searchParams.get("view") !== "settings"
      ? "settings.trash"
      : pageTitle(pathname);
  const desktop = useMediaQuery("(min-width: 768px)");
  const mobileCollections = useMobileFilterDrawer();
  const router = useRouter();
  const { user } = useAuth();
  const startup = useLibraryStartup();
  const queryClient = useQueryClient();
  const collectionPath = pathname === "/" ? searchParams.get("c") : null;
  const collectionId = pathname === "/" ? collectionIdentity(searchParams) : null;
  const collection = useQuery({
    ...(collectionId === null
      ? collectionLookupOptions(collectionPath, queryClient)
      : collectionLookupByIdOptions(collectionId, queryClient)),
    enabled: false,
  });
  const collectionName =
    collectionPath && !collection.isError ? collection.data?.collection.name : null;
  const modelMatch = /^\/models\/(\d+)\/?$/.exec(pathname);
  const parsedModelId = modelMatch ? Number(modelMatch[1]) : null;
  const modelId =
    parsedModelId !== null && Number.isSafeInteger(parsedModelId) && parsedModelId > 0
      ? parsedModelId
      : null;
  const detail = useQuery({ ...modelDetailOptions(modelId), enabled: false });
  const multipartMatch = /^\/multipart-models\/(\d+)\/?$/.exec(pathname);
  const parsedMultipartId = multipartMatch ? Number(multipartMatch[1]) : null;
  const multipartId =
    parsedMultipartId !== null && Number.isSafeInteger(parsedMultipartId) && parsedMultipartId > 0
      ? parsedMultipartId
      : null;
  const multipart = useQuery({ ...multipartDetailOptions(multipartId), enabled: false });
  const documentMatch = /^\/documents\/(\d+)\/?$/.exec(pathname);
  const parsedDocumentId = documentMatch ? Number(documentMatch[1]) : null;
  const documentId =
    parsedDocumentId !== null && Number.isSafeInteger(parsedDocumentId) && parsedDocumentId > 0
      ? parsedDocumentId
      : null;
  const documentDetail = useQuery({ ...documentDetailOptions(documentId), enabled: false });
  const record =
    modelId !== null
      ? detail.isError
        ? null
        : detail.data
      : multipartId !== null
        ? multipart.isError
          ? null
          : multipart.data
        : documentId !== null && !documentDetail.isError
          ? documentDetail.data
          : null;
  const recordRoute = modelId !== null || multipartId !== null || documentId !== null;
  const printerMatch = /^\/printers\/(\d+)\/?$/.exec(pathname);
  const parsedPrinterId = printerMatch ? Number(printerMatch[1]) : null;
  const printerId =
    parsedPrinterId !== null && Number.isSafeInteger(parsedPrinterId) && parsedPrinterId > 0
      ? parsedPrinterId
      : null;
  const printerDetail = useQuery({ ...printerDetailOptions(printerId), enabled: false });
  const printerName =
    printerId !== null && !printerDetail.isError ? printerDetail.data?.name : null;
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
  const [inspector, setInspector] = useState({ available: false, open: false });
  useEffect(() => {
    function update(event: Event) {
      if (!(event instanceof CustomEvent)) return;
      const detail: unknown = event.detail;
      if (!(detail instanceof Object) || !("available" in detail) || !("open" in detail)) return;
      if (
        (detail.available === true || detail.available === false) &&
        (detail.open === true || detail.open === false)
      )
        setInspector({ available: detail.available, open: detail.open });
    }
    window.addEventListener("printstash:library-inspector-state", update);
    return () => window.removeEventListener("printstash:library-inspector-state", update);
  }, []);
  // The logo returns to the model browser, restoring the last folder the user
  // was in rather than always resetting to "All Models", so it reflects the
  // remembered collection at click time.
  const homeHref = useSyncExternalStore(subscribeLastVaultHref, lastVaultHref);
  const modelBackHref = record?.collection
    ? `/?c=${encodeURIComponent(record.collection)}`
    : record
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

  return (
    <header
      data-current-collection={
        recordRoute ? record?.collection : pathname === "/" ? searchParams.get("c") : undefined
      }
      className="shell-topbar relative z-dropdown flex h-[57px] shrink-0 items-center gap-2 border-b border-border bg-card px-2 sm:gap-3 sm:px-3"
    >
      {!desktop && recordRoute && (
        <LibraryBackLink
          data-shell-back
          href={modelBackHref}
          aria-label={uiText("Back")}
          className="flex h-8 w-8 shrink-0 items-center justify-center text-muted-foreground hover:bg-muted hover:text-foreground md:hidden"
        >
          <ArrowLeft className="h-4 w-4" aria-hidden />
        </LibraryBackLink>
      )}
      {!desktop && printerId !== null && (
        <Link
          href="/printers"
          aria-label={uiText("Back")}
          className="flex h-8 w-8 shrink-0 items-center justify-center text-muted-foreground hover:bg-muted hover:text-foreground md:hidden"
        >
          <ArrowLeft className="h-4 w-4" aria-hidden />
        </Link>
      )}
      <span className="min-w-0 flex-1 truncate text-[13px] font-semibold md:hidden">
        {printerName
          ? printerName
          : recordRoute && record
            ? record.name
            : pathname === "/"
              ? searchParams.get("favorites") === "true"
                ? uiText("Favorites")
                : (collectionName ?? uiText("Library"))
              : titleKey
                ? t(titleKey)
                : t("Page not found")}
      </span>

      {pane && (
        <button
          type="button"
          aria-label={uiText("Toggle sidebar")}
          title={uiText("Toggle sidebar")}
          aria-controls={desktop ? "collections-pane" : "mobile-collections-pane"}
          aria-expanded={desktop ? pane.open : mobileCollections.open}
          onClick={
            desktop
              ? pane.toggle
              : mobileCollections.open
                ? mobileCollections.closeDrawer
                : mobileCollections.openDrawer
          }
          className={`${pathname === "/" ? "flex" : "hidden md:flex"} order-first h-8 w-8 shrink-0 items-center justify-center text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring md:order-none ${(desktop ? pane.open : mobileCollections.open) ? "bg-muted text-foreground" : ""}`}
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
          <Link href={homeHref} className="shrink-0 text-muted-foreground hover:text-foreground">
            PrintStash
          </Link>
          {recordRoute && (
            <>
              <span aria-hidden className="text-muted-foreground">
                /
              </span>
              <LibraryBackLink
                data-shell-back
                href={modelBackHref}
                aria-label={uiText("Back")}
                className="shrink-0 text-muted-foreground hover:text-foreground"
              >
                {uiText("Collections")}
              </LibraryBackLink>
            </>
          )}
          {printerId !== null && (
            <>
              <span aria-hidden className="text-muted-foreground">
                /
              </span>
              <Link
                href="/printers"
                aria-label={uiText("Back")}
                className="shrink-0 text-muted-foreground hover:text-foreground"
              >
                {uiText("nav.printers")}
              </Link>
            </>
          )}
          <span aria-hidden className="text-muted-foreground">
            /
          </span>
          <span className="min-w-0 truncate font-medium text-foreground" aria-current="page">
            {printerName
              ? printerName
              : recordRoute && record
                ? record.name
                : titleKey
                  ? t(titleKey)
                  : t("Page not found")}
          </span>
        </nav>
      )}

      {/* Right Actions & Profile */}
      <div className="flex shrink-0 items-center gap-1 sm:gap-2">
        {!desktop && pathname !== "/search" && (
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8 text-muted-foreground"
            aria-label={t("Search")}
            onClick={() => router.push("/search")}
          >
            <Search className="h-4 w-4" aria-hidden />
          </Button>
        )}
        <Button
          type="button"
          size="sm"
          className="h-[34px]"
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
        <DropdownMenu
          open={tasksOpen}
          onOpenChange={(open) => {
            if (open) startup.request("activity");
            setTasksOpen(open);
          }}
          align="end"
          role="dialog"
          className="flex"
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
        {pathname === "/" && inspector.available && (
          <button
            type="button"
            aria-label={uiText("Show or hide details")}
            title={uiText("Show or hide details")}
            aria-controls="model-drawer"
            aria-pressed={inspector.open}
            onClick={() => window.dispatchEvent(new Event("printstash:toggle-library-inspector"))}
            className={`hidden h-8 w-8 items-center justify-center text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring md:inline-flex ${inspector.open ? "bg-muted text-foreground" : ""}`}
          >
            <PanelRight className="h-4 w-4" aria-hidden />
          </button>
        )}
      </div>
    </header>
  );
}
