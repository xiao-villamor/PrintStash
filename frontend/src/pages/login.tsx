"use client";

import { useState } from "react";
import { Navigate } from "react-router-dom";
import { useRouter } from "@/lib/navigation";
import { Box, Loader2 } from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { ThemeToggle } from "@/components/theme-toggle";

export default function LoginPage() {
  const { login, user } = useAuth();
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (user) {
    return <Navigate to="/" replace />;
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await login(username, password);
      router.replace("/");
    } catch (err: any) {
      if (err.message?.includes("401")) {
        setError("Invalid username or password.");
      } else {
        setError(err.message || "Login failed.");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-[var(--surface-container-lowest)] relative">
      <div className="absolute top-4 right-4">
        <ThemeToggle />
      </div>
      <div className="w-full max-w-sm mx-auto space-y-8">
        <div className="text-center">
          <div className="w-14 h-14 mx-auto rounded bg-blue-600 dark:bg-orange-600 flex items-center justify-center text-white mb-4">
            <Box className="h-7 w-7" />
          </div>
          <h1 className="text-2xl font-bold text-[var(--on-surface)]">
            PrintStash
          </h1>
          <p className="text-sm text-[var(--on-surface-variant)] mt-1">
            Sign in to manage your vault
          </p>
        </div>

        <form
          onSubmit={handleSubmit}
          className="bg-[var(--surface-container-low)] border border-[var(--outline-variant)] rounded p-6 space-y-4"
        >
          <div>
            <label
              htmlFor="username"
              className="block text-xs font-mono uppercase tracking-wider text-[var(--on-surface-variant)] mb-1.5"
            >
              Username
            </label>
            <input
              id="username"
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              autoFocus
              required
              className="w-full h-10 bg-[var(--surface-container-lowest)] text-[var(--on-surface)] font-mono text-sm border border-[var(--outline-variant)] rounded px-3 focus:outline-none focus:ring-2 focus:ring-[var(--primary)] focus:border-transparent"
            />
          </div>

          <div>
            <label
              htmlFor="password"
              className="block text-xs font-mono uppercase tracking-wider text-[var(--on-surface-variant)] mb-1.5"
            >
              Password
            </label>
            <input
              id="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
              className="w-full h-10 bg-[var(--surface-container-lowest)] text-[var(--on-surface)] font-mono text-sm border border-[var(--outline-variant)] rounded px-3 focus:outline-none focus:ring-2 focus:ring-[var(--primary)] focus:border-transparent"
            />
          </div>

          {error && (
            <div className="text-xs text-[var(--error)] font-mono">{error}</div>
          )}

          <button
            type="submit"
            disabled={busy}
            className="w-full h-10 rounded bg-[var(--primary)] text-[var(--primary-foreground)] font-mono text-xs uppercase tracking-wider hover:opacity-90 transition-opacity disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
          >
            {busy && <Loader2 className="h-4 w-4 animate-spin" />}
            Sign in
          </button>
        </form>
      </div>
    </div>
  );
}
