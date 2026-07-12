"use client";

import { Plus } from "lucide-react";

export function FAB({ onClick }: { onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="md:hidden fixed bottom-[88px] right-4 z-50 w-14 h-14 bg-primary text-primary-foreground rounded-full flex items-center justify-center shadow-lg active:scale-95 transition-transform duration-press"
      aria-label="Upload"
    >
      <Plus className="h-7 w-7" />
    </button>
  );
}
