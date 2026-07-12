"use client";

import { useState } from "react";
import { Navigate } from "react-router-dom";
import { useRouter } from "@/lib/navigation";
import { Loader2 } from "lucide-react";
import { BrandMark } from "@/components/brand-mark";
import { useAuth } from "@/lib/auth-context";
import { ThemeToggle } from "@/components/theme-toggle";
import { consumeSessionExpired } from "@/lib/auth";

export default function LoginPage() {
  const { login, user } = useAuth();
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [remember_me, setremember_me] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [sessionExpired] = useState(consumeSessionExpired);

  if (user) {
    return <Navigate to="/" replace />;
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await login(username, password, remember_me);
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
    <div className="min-h-screen flex items-center justify-center bg-surface-container-lowest relative">
      <div className="absolute top-4 right-4">
        <ThemeToggle />
      </div>
      <div className="w-full max-w-sm mx-auto space-y-8">
        <div className="text-center">
          <div className="w-14 h-14 mx-auto rounded bg-primary flex items-center justify-center text-primary-foreground mb-4">
            <BrandMark className="h-10 w-10" />
          </div>
          <h1 className="text-2xl font-bold text-on-surface">
            PrintStash
          </h1>
          <p className="text-sm text-on-surface-variant mt-1">
            Sign in to manage your vault
          </p>
        </div>

        <form
          onSubmit={handleSubmit}
          className="bg-surface-container-low border border-outline-variant rounded p-6 space-y-4"
        >
          {sessionExpired && (
            <div role="status" className="rounded border border-warning/30 bg-warning/10 px-3 py-2 text-sm text-on-surface">
              Session expired. You were signed out; sign in again to continue.
            </div>
          )}
          <div>
            <label
              htmlFor="username"
              className="block text-xs font-mono uppercase tracking-wider text-on-surface-variant mb-1.5"
            >
              Username
            </label>
            <input
              id="username"
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              aria-invalid={!!error}
              aria-describedby={error ? "login-error" : undefined}
              autoComplete="username"
              autoFocus
              required
              className="w-full h-10 bg-surface-container-lowest text-on-surface font-mono text-sm border border-outline-variant rounded px-3 focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent"
            />
          </div>

          <div>
            <label
              htmlFor="password"
              className="block text-xs font-mono uppercase tracking-wider text-on-surface-variant mb-1.5"
            >
              Password
            </label>
            <input
              id="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              aria-invalid={!!error}
              aria-describedby={error ? "login-error" : undefined}
              autoComplete="current-password"
              required
              className="w-full h-10 bg-surface-container-lowest text-on-surface font-mono text-sm border border-outline-variant rounded px-3 focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent"
            />
            <label
              htmlFor="remember_me"
              className="mt-3 flex items-center gap-2 cursor-pointer select-none text-xs font-mono uppercase tracking-wider text-on-surface-variant"
            >
              <input
                id="remember_me"
                type="checkbox"
                checked={remember_me}
                onChange={(e) => setremember_me(e.target.checked)}
                className="h-4 w-4 rounded accent-primary cursor-pointer"
              />
              Remember me
            </label>
          </div>

          {error && (
            <div id="login-error" role="alert" className="text-sm text-error font-mono">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={busy}
            className="w-full h-10 rounded bg-primary text-primary-foreground font-mono text-xs uppercase tracking-wider hover:opacity-90 transition-opacity disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
          >
            {busy && <Loader2 className="h-4 w-4 animate-spin" />}
            Sign in
          </button>
        </form>
      </div>
    </div>
  );
}
