"use client";

import { usePathname } from "next/navigation";

import { AuthBanner } from "@/components/auth-banner";
import { BottomNavBar } from "@/components/bottom-nav-bar";
import { Toaster } from "@/components/toaster";
import { TopBar } from "@/components/top-bar";
import { MobileFilterProvider } from "@/lib/mobile-filter-context";

const CHROMELESS_PREFIXES = ["/setup", "/login"];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const chromeless = CHROMELESS_PREFIXES.some((p) => pathname.startsWith(p));
  const isVault = pathname === "/";

  return (
    <>
      <Toaster />
      {chromeless ? (
        children
      ) : (
        <MobileFilterProvider>
          <div className="flex flex-col min-h-screen max-h-screen">
            <TopBar />
            <AuthBanner />
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
