"use client";

import { Link } from "@/lib/navigation";
import { usePathname } from "@/lib/navigation";
import { Box, SlidersHorizontal, FolderTree, LogIn, LogOut, Printer, Settings, User } from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { BrandMark } from "@/components/brand-mark";

const mainItems = [
  { href: "/", label: "Vault", icon: Box },
  { href: "/printers", label: "Printers", icon: Printer, adminOnly: true },
  { href: "/profiles", label: "Profiles", icon: SlidersHorizontal },
  { href: "/organize", label: "Catalog", icon: FolderTree },
];

const bottomItems = [
  { href: "/settings", label: "Settings", icon: Settings },
];

export function SidebarNav() {
  const pathname = usePathname();
  const { user, logout } = useAuth();
  const visibleMainItems = mainItems.filter((item) => !item.adminOnly || user?.is_superuser);

  return (
    <nav className="bg-card border-r border-border h-screen w-64 fixed left-0 top-0 flex-col py-6 px-4 z-50 hidden md:flex">
      <div className="flex items-center gap-4 mb-10 px-1">
        <div className="w-10 h-10 rounded bg-blue-600 dark:bg-orange-600 flex items-center justify-center text-white flex-shrink-0">
          <BrandMark className="h-7 w-7" />
        </div>
        <div>
          <h1 className="text-xl font-bold text-foreground leading-tight tracking-tight">
            PrintStash
          </h1>
          <p className="text-[11px] text-muted-foreground font-mono">
            Your prints, organized
          </p>
        </div>
      </div>

      <div className="flex flex-col gap-1 flex-1">
        {visibleMainItems.map((item) => {
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
                  ? "text-blue-700 dark:text-orange-400 bg-blue-50"
                  : "text-muted-foreground hover:bg-muted"
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
        {user ? (
          <div className="flex items-center gap-3 px-3 py-2 mb-1">
            <div className="w-7 h-7 rounded-full bg-slate-800 flex items-center justify-center text-white">
              <User className="h-3.5 w-3.5" />
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-xs font-mono text-foreground truncate">{user.username}</p>
            </div>
            <button
              type="button"
              onClick={logout}
              className="text-muted-foreground hover:text-red-500 transition-colors p-1"
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
                ? "text-blue-700 dark:text-orange-400 bg-blue-50"
                : "text-muted-foreground hover:bg-muted"
            }`}
          >
            <LogIn className="h-5 w-5" />
            <span className="font-mono text-xs tracking-wider uppercase">Sign in</span>
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
                  ? "text-blue-700 dark:text-orange-400 bg-blue-50"
                  : "text-muted-foreground hover:bg-muted"
              }`}
            >
              <item.icon className="h-5 w-5" />
              <span className="font-mono text-xs tracking-wider uppercase">{item.label}</span>
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
