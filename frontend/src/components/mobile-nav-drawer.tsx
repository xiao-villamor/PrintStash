"use client";

import { useEffect } from "react";
import { Link } from "@/lib/navigation";
import { usePathname } from "@/lib/navigation";
import { Box, SlidersHorizontal, FolderTree, LogIn, LogOut, Printer, Settings, User, X } from "lucide-react";
import { useAuth } from "@/lib/auth-context";

interface MobileNavDrawerProps {
  open: boolean;
  onClose: () => void;
}

const mainItems = [
  { href: "/", label: "Vault", icon: Box },
  { href: "/printers", label: "Printers", icon: Printer, adminOnly: true },
  { href: "/profiles", label: "Profiles", icon: SlidersHorizontal },
  { href: "/organize", label: "Catalog", icon: FolderTree },
];

const bottomItems = [
  { href: "/settings", label: "Settings", icon: Settings },
];

export function MobileNavDrawer({ open, onClose }: MobileNavDrawerProps) {
  const pathname = usePathname();
  const { user, logout } = useAuth();
  const visibleMainItems = mainItems.filter((item) => !item.adminOnly || user?.is_superuser);

  useEffect(() => {
    if (open) {
      document.body.style.overflow = "hidden";
    } else {
      document.body.style.overflow = "";
    }
    return () => {
      document.body.style.overflow = "";
    };
  }, [open]);

  if (!open) return null;

  return (
    <div className="md:hidden fixed inset-0 z-50">
      <div
        className="absolute inset-0 bg-black/30 backdrop-blur-sm"
        onClick={onClose}
      />
      <div className="absolute left-0 top-0 bottom-0 w-[280px] max-w-[85vw] bg-[var(--surface-container-low)] shadow-xl slide-in-left flex flex-col">
        <div className="flex items-center justify-between p-4 border-b border-[var(--outline-variant)]">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded bg-[var(--primary-container)] flex items-center justify-center text-[var(--on-primary-container)]">
              <Box className="h-5 w-5" />
            </div>
            <div>
              <h2 className="text-lg font-bold text-[var(--primary)] leading-tight">
                PrintStash
              </h2>
              <p className="text-[10px] text-[var(--on-surface-variant)] font-mono">
                Your prints, organized
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-[var(--on-surface-variant)] hover:text-[var(--on-surface)] p-1 rounded-full hover:bg-[var(--surface-container-high)] transition-colors"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <nav className="flex flex-col gap-1 flex-1 p-3">
          {visibleMainItems.map((item) => {
            const isActive =
              item.href === "/"
                ? pathname === "/"
                : pathname.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                onClick={onClose}
                className={`flex items-center gap-4 px-3 py-2.5 rounded text-sm font-medium transition-all active:scale-95 ${
                  isActive
                    ? "text-[var(--primary)] bg-[var(--secondary-container)]"
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
        </nav>

        <div className="p-3 border-t border-[var(--outline-variant)]">
          {user ? (
            <div className="flex items-center gap-3 px-3 py-2 mb-1">
              <div className="w-8 h-8 rounded-full bg-[var(--primary-container)] flex items-center justify-center text-[var(--on-primary-container)]">
                <User className="h-4 w-4" />
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-sm font-mono text-[var(--on-surface)] truncate">
                  {user.username}
                </p>
              </div>
              <button
                type="button"
                onClick={() => {
                  logout();
                  onClose();
                }}
                className="text-[var(--on-surface-variant)] hover:text-[var(--error)] transition-colors p-1"
                title="Sign out"
              >
                <LogOut className="h-4 w-4" />
              </button>
            </div>
          ) : (
            <Link
              href="/login"
              onClick={onClose}
              className={`flex items-center gap-4 px-3 py-2.5 rounded text-sm font-medium transition-all active:scale-95 ${
                pathname.startsWith("/login")
                  ? "text-[var(--primary)] bg-[var(--secondary-container)]"
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
                onClick={onClose}
                className={`flex items-center gap-4 px-3 py-2.5 mt-1 rounded text-sm font-medium transition-all active:scale-95 ${
                  isActive
                    ? "text-[var(--primary)] bg-[var(--secondary-container)]"
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
      </div>
    </div>
  );
}
