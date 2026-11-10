"use client";

import { usePathname } from "@/lib/navigation";
import { useEffect, useState } from "react";
import { useRouter } from "@/lib/navigation";

import { BottomNavBar } from "@/components/bottom-nav-bar";
import { CollectionsPaneProvider, CollectionsPaneHost } from "@/components/collections-pane";
import { SidebarNav } from "@/components/sidebar-nav";
import { Toaster } from "@/components/toaster";
import { TopBar } from "@/components/top-bar";
import { MobileFilterProvider } from "@/lib/mobile-filter-provider";
import { NavigationShellProvider } from "@/lib/navigation-shell-provider";
import { LibraryStartupProvider } from "@/lib/library-startup-provider";
import { PreviewActivityTracker } from "@/components/preview-activity-tracker";
import { useAuth } from "@/lib/auth-context";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { Localized } from "@/components/ui/localized";
import { DeferredDialog } from "@/components/deferred-dialog";
import { lazyImport } from "@/lib/lazy-component";
import { subscribeArchiveReviewRequests } from "@/lib/archive-review-events";
import { subscribeImportJobCompletions } from "@/lib/task-center";
import { toast } from "@/lib/toast";
import { uiText } from "@/lib/locale";
import { refreshVaultAfterIngest } from "@/lib/query-client";
import { useSystemTheme } from "@/lib/theme";
import { KeyboardShortcuts } from "@/components/keyboard-shortcuts";
import "./app-shell.css";

const ArchiveReviewDialog = lazyImport(() =>
  import("@/components/archive-review").then((module) => ({ default: module.ArchiveReviewDialog })),
);

const CHROMELESS_PREFIXES = ["/setup", "/login"];

export function AppShell({ children }: { children: React.ReactNode }) {
  useSystemTheme();
  const pathname = usePathname();
  const router = useRouter();
  const { user, loading } = useAuth();
  const { t } = useI18n();
  const chromeless = CHROMELESS_PREFIXES.some((p) => pathname.startsWith(p));
  const isVault = pathname === "/";
  const [archiveJobId, setArchiveJobId] = useState<string | null>(null);
  useEffect(() => subscribeArchiveReviewRequests(setArchiveJobId), []);

  useEffect(
    () =>
      subscribeImportJobCompletions((job) => {
        if (job.kind === "ingestion.archive_selection" && job.state === "completed") {
          void refreshVaultAfterIngest();
        }
        if (job.kind === "ingestion.archive_inspect" && job.state === "failed") {
          toast.error(job.error ?? uiText("ZIP preparation failed."));
          return;
        }
        if (job.kind !== "ingestion.archive_inspect" || job.state !== "completed") return;
        toast.successAction(
          uiText("ZIP ready. Choose which files to add."),
          uiText("Choose files"),
          () => setArchiveJobId(job.job_id),
        );
      }),
    [],
  );

  useEffect(() => {
    const titleKey: MessageKey | null =
      pathname === "/"
        ? "nav.vault"
        : pathname.startsWith("/inbox")
          ? "inbox.title"
          : pathname.startsWith("/builds")
            ? "build.title"
            : pathname.startsWith("/search")
              ? "Search"
              : pathname === "/getting-started"
                ? "nav.gettingStarted"
                : pathname.startsWith("/models/")
                  ? "nav.model"
                  : pathname.startsWith("/library/similar")
                    ? "similarity.title"
                    : pathname.startsWith("/multipart-models/")
                      ? "nav.multipart"
                      : pathname.startsWith("/documents/")
                        ? "nav.document"
                        : pathname.startsWith("/printers/")
                          ? "nav.printer"
                          : pathname.startsWith("/printers")
                            ? "nav.printers"
                            : pathname.startsWith("/statistics")
                              ? "nav.statistics"
                              : pathname.startsWith("/settings")
                                ? "nav.settings"
                                : pathname.startsWith("/profiles")
                                  ? "nav.profiles"
                                  : pathname.startsWith("/login")
                                    ? "nav.signIn"
                                    : pathname.startsWith("/setup")
                                      ? "nav.setup"
                                      : null;
    document.title = titleKey ? `${t(titleKey)} · PrintStash` : "PrintStash";
  }, [pathname, t]);

  useEffect(() => {
    if (!chromeless && !loading && !user) {
      router.replace("/login");
      return;
    }
    if (
      !chromeless &&
      !loading &&
      user &&
      !user.is_superuser &&
      (pathname.startsWith("/printers") || pathname.startsWith("/statistics"))
    ) {
      router.replace("/");
    }
  }, [chromeless, loading, pathname, router, user]);

  if (!chromeless && loading) {
    return <div className="min-h-screen w-full bg-background" aria-busy="true" />;
  }

  if (!chromeless && !loading && !user) {
    return (
      <Localized>
        <Toaster />
      </Localized>
    );
  }

  return (
    <Localized>
      <>
        <Toaster />
        {chromeless ? (
          children
        ) : (
          <LibraryStartupProvider key={user?.id ?? "signed-out"} active={isVault}>
            <CollectionsPaneProvider>
              <NavigationShellProvider>
                <MobileFilterProvider>
                  <KeyboardShortcuts />
                  <div className="flex h-dvh overflow-hidden">
                    <SidebarNav />
                    <CollectionsPaneHost />
                    <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
                      <TopBar />
                      <PreviewActivityTracker enabled={!!user?.is_superuser} />
                      <div className="shell-route-viewport flex min-h-0 flex-1 overflow-hidden">
                        {isVault ? (
                          children
                        ) : (
                          <main className="min-w-0 flex-1 overflow-hidden bg-background">
                            {children}
                          </main>
                        )}
                      </div>
                      <BottomNavBar />
                    </div>
                    {archiveJobId && (
                      <DeferredDialog
                        open
                        title={uiText("ZIP ready. Choose which files to add.")}
                        onClose={() => setArchiveJobId(null)}
                      >
                        <ArchiveReviewDialog
                          key={archiveJobId}
                          jobId={archiveJobId}
                          onClose={() => setArchiveJobId(null)}
                        />
                      </DeferredDialog>
                    )}
                  </div>
                </MobileFilterProvider>
              </NavigationShellProvider>
            </CollectionsPaneProvider>
          </LibraryStartupProvider>
        )}
      </>
    </Localized>
  );
}
