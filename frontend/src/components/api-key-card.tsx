"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, KeyRound } from "lucide-react";
import {
  clearStoredApiKey,
  getStoredApiKey,
  onAuthChange,
  setStoredApiKey,
} from "@/lib/auth";

export function ApiKeyCard() {
  const [key, setKey] = useState("");
  const [stored, setStored] = useState<string | null>(null);
  const [reveal, setReveal] = useState(false);

  useEffect(() => {
    setStored(getStoredApiKey());
    return onAuthChange(() => setStored(getStoredApiKey()));
  }, []);

  function save() {
    setStoredApiKey(key);
    setKey("");
  }

  function clear() {
    clearStoredApiKey();
  }

  const masked = stored
    ? stored.length <= 8
      ? "•".repeat(stored.length)
      : `${stored.slice(0, 3)}${"•".repeat(stored.length - 6)}${stored.slice(-3)}`
    : null;

  return (
    <div className="bg-[var(--surface-container-lowest)] border border-[var(--outline-variant)] rounded overflow-hidden">
      <div className="px-4 sm:px-6 lg:px-8 py-4 sm:py-5 border-b border-[var(--outline-variant)] flex items-center gap-2 sm:gap-3">
        <div className="w-9 h-9 rounded bg-[var(--surface-container)] flex items-center justify-center text-[var(--on-surface-variant)] flex-shrink-0">
          <KeyRound className="h-4 w-4" />
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="text-sm font-semibold text-[var(--on-surface)]">
            Vault API key
          </h3>
          <p className="text-xs text-[var(--on-surface-variant)] mt-0.5">
            Stored in your browser. Required for uploads, deletes, and printer
            control. Replaced by per-user auth in Stage 4.
          </p>
        </div>
      </div>

      <div className="p-3 sm:p-4 lg:p-6 space-y-4">
        {stored ? (
          <div className="flex items-center justify-between gap-2 rounded border border-[var(--outline-variant)] bg-[var(--surface-container)] px-3 py-2 flex-wrap">
            <div className="flex items-center gap-2 min-w-0">
              <CheckCircle2 className="h-4 w-4 text-emerald-500 flex-shrink-0" />
              <span className="font-mono text-xs text-[var(--on-surface)] truncate max-w-[180px] sm:max-w-none">
                {reveal ? stored : masked}
              </span>
            </div>
            <div className="flex items-center gap-2 flex-shrink-0 ml-auto">
              <button
                type="button"
                onClick={() => setReveal((r) => !r)}
                className="font-mono text-[10px] uppercase tracking-wider text-[var(--on-surface-variant)] hover:text-[var(--on-surface)] transition-colors"
              >
                {reveal ? "Hide" : "Show"}
              </button>
              <button
                type="button"
                onClick={clear}
                className="font-mono text-[10px] uppercase tracking-wider text-[var(--error)] hover:opacity-80 transition-opacity"
              >
                Clear
              </button>
            </div>
          </div>
        ) : (
          <div className="rounded border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-300 font-mono">
            No key stored. Write operations will fail until one is saved.
          </div>
        )}

        <div className="flex items-end gap-2">
          <div className="flex-1">
            <label className="block font-mono text-xs text-[var(--on-surface-variant)] tracking-wider uppercase mb-2">
              {stored ? "Replace key" : "Set key"}
            </label>
            <input
              type="password"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              placeholder="VAULT_API_KEY"
              autoComplete="off"
              className="w-full h-10 bg-[var(--surface-container-lowest)] text-[var(--on-surface)] font-mono text-sm border border-[var(--outline-variant)] rounded px-3 focus:outline-none focus:ring-2 focus:ring-[var(--primary)] focus:border-transparent"
            />
          </div>
          <button
            type="button"
            onClick={save}
            disabled={!key.trim()}
            className="h-10 px-4 rounded bg-[var(--primary)] text-[var(--primary-foreground)] font-mono text-xs uppercase tracking-wider hover:opacity-90 transition-opacity disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Save
          </button>
        </div>
      </div>
    </div>
  );
}
