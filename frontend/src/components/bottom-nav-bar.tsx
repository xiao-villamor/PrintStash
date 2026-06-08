"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Box, FolderTree, Printer, Settings } from "lucide-react";

const items = [
  { href: "/", label: "Vault", icon: Box },
  { href: "/printers", label: "Printers", icon: Printer },
  { href: "/organize", label: "Catalog", icon: FolderTree },
  { href: "/settings", label: "Settings", icon: Settings },
];

export function BottomNavBar() {
  const pathname = usePathname();

  return (
    <nav className="md:hidden fixed bottom-0 left-0 w-full flex justify-around items-center h-[72px] px-4 bg-[var(--surface)] border-t border-[var(--outline-variant)] z-40 pb-safe">
      {items.map((item) => {
        const isActive =
          item.href === "/"
            ? pathname === "/"
            : pathname.startsWith(item.href);
        return (
          <Link
            key={item.href}
            href={item.href}
            className={`flex flex-col items-center justify-center px-3 py-1 rounded-full active:scale-95 transition-transform duration-150 ${
              isActive
                ? "bg-[var(--secondary-container)] text-[var(--on-secondary-container)]"
                : "text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-low)] transition-colors"
            }`}
          >
            <item.icon
              className="h-5 w-5"
              {...(isActive ? { fill: "currentColor" } : {})}
            />
            <span className="font-mono text-[10px] uppercase tracking-wider mt-0.5">
              {item.label}
            </span>
          </Link>
        );
      })}
    </nav>
  );
}
