"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Box, LogIn, LogOut, Printer, Settings, User } from "lucide-react";
import { useAuth } from "@/lib/auth-context";

const mainItems = [
  { href: "/", label: "Vault", icon: Box },
  { href: "/printers", label: "Printers", icon: Printer },
];

const bottomItems = [
  { href: "/settings", label: "Settings", icon: Settings },
];

export function SidebarNav() {
  const pathname = usePathname();
  const { user, logout } = useAuth();

  return (
    <nav className="bg-[var(--surface-container-low)] border-r border-[var(--outline-variant)] h-screen w-64 fixed left-0 top-0 flex-col py-6 px-4 z-50 hidden md:flex">
      <div className="flex items-center gap-4 mb-10 px-1">
        <div className="w-10 h-10 rounded bg-[var(--primary-container)] flex items-center justify-center text-[var(--on-primary-container)] flex-shrink-0">
          <Box className="h-5 w-5" />
        </div>
        <div>
          <h1 className="text-xl font-bold text-[var(--primary)] leading-tight">
            PrintStash
          </h1>
          <p className="text-[11px] text-[var(--on-surface-variant)] font-mono">
            Your prints, organized
          </p>
        </div>
      </div>

      <div className="flex flex-col gap-1 flex-1">
        {mainItems.map((item) => {
          const isActive =
            item.href === "/"
              ? pathname === "/"
              : pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`flex items-center gap-4 px-3 py-2 rounded text-sm font-medium transition-all active:scale-95 ${
                isActive
                  ? "text-[var(--primary)] border-r-[3px] border-[var(--primary)] bg-[var(--secondary-container)]"
                  : "text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-high)]"
              }`}
            >
              <item.icon className="h-5 w-5" />
              <span className="font-mono text-xs tracking-wider uppercase">
                {item.label}
              </span>
            </Link>
          );
        })}
      </div>

      {/* Bottom section */}
      <div className="flex flex-col gap-1">
        {/* User section */}
        {user ? (
          <div className="flex items-center gap-3 px-3 py-2 mb-1">
            <div className="w-7 h-7 rounded-full bg-[var(--primary-container)] flex items-center justify-center text-[var(--on-primary-container)]">
              <User className="h-3.5 w-3.5" />
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-xs font-mono text-[var(--on-surface)] truncate">
                {user.username}
              </p>
            </div>
            <button
              type="button"
              onClick={logout}
              className="text-[var(--on-surface-variant)] hover:text-[var(--error)] transition-colors p-1"
              title="Sign out"
            >
              <LogOut className="h-4 w-4" />
            </button>
          </div>
        ) : (
          <Link
            href="/login"
            className={`flex items-center gap-4 px-3 py-2 rounded text-sm font-medium transition-all active:scale-95 mb-1 ${
              pathname.startsWith("/login")
                ? "text-[var(--primary)] border-r-[3px] border-[var(--primary)] bg-[var(--secondary-container)]"
                : "text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-high)]"
            }`}
          >
            <LogIn className="h-5 w-5" />
            <span className="font-mono text-xs tracking-wider uppercase">
              Sign in
            </span>
          </Link>
        )}

        {bottomItems.map((item) => {
          const isActive = pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`flex items-center gap-4 px-3 py-2 rounded text-sm font-medium transition-all active:scale-95 ${
                isActive
                  ? "text-[var(--primary)] border-r-[3px] border-[var(--primary)] bg-[var(--secondary-container)]"
                  : "text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-high)]"
              }`}
            >
              <item.icon className="h-5 w-5" />
              <span className="font-mono text-xs tracking-wider uppercase">
                {item.label}
              </span>
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
