"use client";

import { usePathname } from "@/lib/navigation";
import { useEffect } from "react";
import { useRouter } from "@/lib/navigation";

import { BottomNavBar } from "@/components/bottom-nav-bar";
import { Toaster } from "@/components/toaster";
import { TopBar } from "@/components/top-bar";
import { MobileFilterProvider } from "@/lib/mobile-filter-context";
import { useAuth } from "@/lib/auth-context";

const CHROMELESS_PREFIXES = ["/setup", "/login"];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { user, loading } = useAuth();
  const chromeless = CHROMELESS_PREFIXES.some((p) => pathname.startsWith(p));
  const isVault = pathname === "/";

  useEffect(() => {
    const title = pathname === "/"
      ? "Vault"
      : pathname.startsWith("/models/")
        ? "Model"
        : pathname.startsWith("/documents/")
          ? "Document"
          : pathname.startsWith("/printers/")
            ? "Printer"
            : pathname.startsWith("/printers")
              ? "Printers"
              : pathname.startsWith("/statistics")
                ? "Statistics"
                : pathname.startsWith("/settings")
                  ? "Settings"
                  : pathname.startsWith("/organize")
                    ? "Catalog"
                    : pathname.startsWith("/profiles")
                      ? "Profiles"
                      : pathname.startsWith("/login")
                        ? "Sign in"
                        : pathname.startsWith("/setup")
                          ? "Setup"
                          : "PrintStash";
    document.title = `${title} · PrintStash`;
  }, [pathname]);

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
    return <Toaster />;
  }

  return (
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
                <main className="flex-1 min-w-0 overflow-hidden bg-background">
                  {children}
                </main>
              )}
            </div>
            <BottomNavBar />
          </div>
        </MobileFilterProvider>
      )}
    </>
  );
}
