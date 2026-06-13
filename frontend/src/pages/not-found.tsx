import { Link } from "@/lib/navigation";

export default function NotFound() {
  return (
    <div className="flex h-full min-h-[60vh] flex-col items-center justify-center gap-3 text-center px-6">
      <p className="text-2xl font-bold text-[var(--on-surface)]">404</p>
      <p className="text-sm text-[var(--on-surface-variant)]">
        This page doesn’t exist.
      </p>
      <Link
        href="/"
        className="mt-2 h-9 px-4 inline-flex items-center rounded bg-[var(--primary)] text-[var(--primary-foreground)] font-mono text-xs uppercase tracking-wider hover:opacity-90"
      >
        Back to vault
      </Link>
    </div>
  );
}
