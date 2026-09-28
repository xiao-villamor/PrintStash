"use client";

import { usePathname } from "@/lib/navigation";
import { useEffect, useState } from "react";
import { useRouter } from "@/lib/navigation";

import { BottomNavBar } from "@/components/bottom-nav-bar";
import { Toaster } from "@/components/toaster";
import { TopBar } from "@/components/top-bar";
import { MobileFilterProvider } from "@/lib/mobile-filter-provider";
import { useAuth } from "@/lib/auth-context";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { Localized } from "@/components/ui/localized";
import { ArchiveReviewDialog } from "@/components/archive-review";
import { subscribeArchiveReviewRequests } from "@/lib/archive-review-events";
import { subscribeImportJobCompletions } from "@/lib/task-center";
import { toast } from "@/lib/toast";
import { uiText } from "@/lib/locale";
import { refreshVaultAfterIngest } from "@/lib/query-client";

const CHROMELESS_PREFIXES = ["/setup", "/login", "/getting-started"];

export function AppShell({ children }: { children: React.ReactNode }) {
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
    document.title = `${titleKey ? t(titleKey) : "PrintStash"} · PrintStash`;
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
          <MobileFilterProvider>
            <div className="flex flex-col h-dvh overflow-hidden">
              <TopBar />
              <div className="flex flex-1 min-h-0 overflow-hidden">
                {isVault ? (
                  children
                ) : (
                  <main className="flex-1 min-w-0 overflow-hidden bg-background">{children}</main>
                )}
              </div>
              <BottomNavBar />
              {archiveJobId && (
                <ArchiveReviewDialog
                  key={archiveJobId}
                  jobId={archiveJobId}
                  onClose={() => setArchiveJobId(null)}
                />
              )}
            </div>
          </MobileFilterProvider>
        )}
      </>
    </Localized>
  );
}
