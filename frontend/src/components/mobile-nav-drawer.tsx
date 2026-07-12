"use client";

import { Link } from "@/lib/navigation";
import { usePathname } from "@/lib/navigation";
import { Box, SlidersHorizontal, FolderTree, LogIn, LogOut, Printer, Settings, User, X } from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { BrandMark } from "@/components/brand-mark";
import { Drawer } from "@/components/ui/drawer";

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

  return (
    <Drawer
      open={open}
      onClose={onClose}
      side="left"
      ariaLabel="Navigation"
      containerClassName="md:hidden"
      className="w-[280px] max-w-[85vw] bg-surface-container-low shadow-xl flex flex-col"
    >
        <div className="flex items-center justify-between p-4 border-b border-outline-variant">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded bg-primary-container flex items-center justify-center text-on-primary-container">
              <BrandMark className="h-7 w-7" />
            </div>
            <div>
              <h2 className="text-lg font-bold text-primary leading-tight">
                PrintStash
              </h2>
              <p className="text-3xs text-on-surface-variant font-mono">
                Your prints, organized
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-on-surface-variant hover:text-on-surface p-1 rounded-full hover:bg-surface-container-high transition-colors"
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
                className={`flex items-center gap-4 px-3 py-2.5 rounded text-sm font-medium transition-[color,background-color,transform] duration-press active:scale-[0.98] ${
                  isActive
                    ? "text-primary bg-secondary-container"
                    : "text-on-surface-variant hover:bg-surface-container-high"
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

        <div className="p-3 border-t border-outline-variant">
          {user ? (
            <div className="flex items-center gap-3 px-3 py-2 mb-1">
              <div className="w-8 h-8 rounded-full bg-primary-container flex items-center justify-center text-on-primary-container">
                <User className="h-4 w-4" />
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-sm font-mono text-on-surface truncate">
                  {user.username}
                </p>
              </div>
              <button
                type="button"
                onClick={() => {
                  logout();
                  onClose();
                }}
                className="text-on-surface-variant hover:text-error transition-colors p-1"
                title="Sign out"
              >
                <LogOut className="h-4 w-4" />
              </button>
            </div>
          ) : (
            <Link
              href="/login"
              onClick={onClose}
              className={`flex items-center gap-4 px-3 py-2.5 rounded text-sm font-medium transition-[color,background-color,transform] duration-press active:scale-[0.98] ${
                pathname.startsWith("/login")
                  ? "text-primary bg-secondary-container"
                  : "text-on-surface-variant hover:bg-surface-container-high"
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
                className={`flex items-center gap-4 px-3 py-2.5 mt-1 rounded text-sm font-medium transition-[color,background-color,transform] duration-press active:scale-[0.98] ${
                  isActive
                    ? "text-primary bg-secondary-container"
                    : "text-on-surface-variant hover:bg-surface-container-high"
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
    </Drawer>
  );
}

