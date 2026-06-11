"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Box, SlidersHorizontal, FolderTree, Printer, Settings } from "lucide-react";

const items = [
  { href: "/", label: "Vault", icon: Box },
  { href: "/printers", label: "Printers", icon: Printer },
  { href: "/profiles", label: "Profiles", icon: SlidersHorizontal },
  { href: "/organize", label: "Catalog", icon: FolderTree },
  { href: "/settings", label: "Settings", icon: Settings },
];

export function BottomNavBar() {
  const pathname = usePathname();

  return (
    <nav className="md:hidden fixed bottom-0 left-0 w-full flex justify-around items-center h-[72px] px-4 bg-card border-t border-border z-40 pb-safe">
      {items.map((item) => {
        const isActive =
          item.href === "/"
            ? pathname === "/"
            : pathname.startsWith(item.href);
        return (
          <Link
            key={item.href}
            href={item.href}
            className={`flex flex-col items-center justify-center px-2 py-1 rounded-full active:scale-95 transition-transform duration-150 ${
              isActive
                ? "bg-blue-50 text-blue-700 dark:text-orange-400"
                : "text-muted-foreground hover:bg-muted transition-colors"
            }`}
          >
            <item.icon
              className="h-5 w-5"
              {...(isActive ? { fill: "currentColor" } : {})}
            />
            <span className="font-mono text-[9px] uppercase tracking-wider mt-0.5">
              {item.label}
            </span>
          </Link>
        );
      })}
    </nav>
  );
}
