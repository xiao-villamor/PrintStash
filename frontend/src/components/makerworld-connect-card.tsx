"use client";

import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, KeyRound, Loader2, LogIn, Unplug } from "lucide-react";
import {
  getMakerWorldStatus,
  makerWorldDisconnect,
  makerWorldLogin,
  makerWorldSetToken,
  makerWorldVerify,
} from "@/lib/api";
import type { MakerWorldStatus } from "@/types";
import { userMessage } from "@/lib/errors";
import { useAuth } from "@/lib/auth-context";

// "creds": entering email + password. "code": entering the emailed/app code.
type Step = "creds" | "code";

const INPUT_CLASS =
  "w-full px-2.5 py-1.5 text-sm rounded border border-border bg-background text-foreground placeholder:text-muted-foreground/40 disabled:opacity-50";

export function MakerWorldConnectCard() {
  const { user } = useAuth();
  const canEdit = !!user?.is_superuser;

  const [status, setStatus] = useState<MakerWorldStatus | null>(null);
  const [loading, setLoading] = useState(true);

  const [step, setStep] = useState<Step>("creds");
  const [account, setAccount] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [loginToken, setLoginToken] = useState<string | null>(null);
  const [codeKind, setCodeKind] = useState<"email" | "tfa">("email");
  // Token-paste fallback: for Google-SSO accounts (no password) and as the
  // universal escape hatch when password login won't work.
  const [tokenMode, setTokenMode] = useState(false);
  const [token, setToken] = useState("");

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      setStatus(await getMakerWorldStatus());
    } catch {
      // ignore — treated as not connected
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const resetForm = useCallback(() => {
    setStep("creds");
    setPassword("");
    setCode("");
    setLoginToken(null);
    setToken("");
    setError("");
  }, []);

  const submitToken = useCallback(async () => {
    setBusy(true);
    setError("");
    try {
      await makerWorldSetToken({ token });
      resetForm();
      setTokenMode(false);
      await load();
    } catch (e) {
      setError(userMessage(e));
    } finally {
      setBusy(false);
    }
  }, [token, load, resetForm]);

  const submitCreds = useCallback(async () => {
    setBusy(true);
    setError("");
    try {
      const res = await makerWorldLogin({ account, password });
      if (res.status === "ok") {
        resetForm();
        setAccount("");
        await load();
        return;
      }
      // A verification code is required — move to the code step.
      setLoginToken(res.login_token);
      setCodeKind(res.status === "need_tfa_code" ? "tfa" : "email");
      setStep("code");
      setPassword("");
    } catch (e) {
      setError(userMessage(e));
    } finally {
      setBusy(false);
    }
  }, [account, password, load, resetForm]);

  const submitCode = useCallback(async () => {
    if (!loginToken) return;
    setBusy(true);
    setError("");
    try {
      await makerWorldVerify({ login_token: loginToken, code });
      resetForm();
      setAccount("");
      await load();
    } catch (e) {
      setError(userMessage(e));
    } finally {
      setBusy(false);
    }
  }, [loginToken, code, load, resetForm]);

  const disconnect = useCallback(async () => {
    setBusy(true);
    setError("");
    try {
      await makerWorldDisconnect();
      resetForm();
      await load();
    } catch (e) {
      setError(userMessage(e));
    } finally {
      setBusy(false);
    }
  }, [load, resetForm]);

  const connected = !!status?.connected;

  return (
    <div className="bg-card border border-border rounded overflow-hidden">
      <div className="px-4 sm:px-6 lg:px-8 py-4 sm:py-5 border-b border-border flex items-center justify-between gap-2">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-foreground">MakerWorld account</h3>
          <p className="text-xs text-muted-foreground mt-0.5">
            Connect a MakerWorld (Bambu) account so model &amp; collection imports can download files
          </p>
        </div>
        <span
          className={`font-mono text-3xs uppercase tracking-wider px-2 py-1 rounded border flex-shrink-0 ${
            connected
              ? "text-green-600 dark:text-green-400 border-green-600/40"
              : "text-muted-foreground border-border"
          }`}
        >
          {connected ? "Connected" : "Not connected"}
        </span>
      </div>

      <div className="p-3 sm:p-4 lg:p-6 space-y-4">
        {loading ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : !canEdit ? (
          <p className="text-xs text-muted-foreground italic">
            Only an administrator can connect MakerWorld.
          </p>
        ) : connected ? (
          <div className="space-y-3">
            <div className="flex items-start gap-2 text-sm text-foreground">
              <CheckCircle2 className="h-4 w-4 mt-0.5 text-green-600 dark:text-green-400 flex-shrink-0" />
              <div>
                <p>MakerWorld is connected.</p>
                {status?.updated_at && (
                  <p className="text-2xs text-muted-foreground mt-0.5">
                    Session stored {new Date(status.updated_at).toLocaleString()}
                  </p>
                )}
                <p className="text-2xs text-muted-foreground mt-1">
                  Sessions expire periodically — if imports start failing with a login error, disconnect and connect again.
                </p>
              </div>
            </div>
            <button
              type="button"
              onClick={disconnect}
              disabled={busy}
              className="inline-flex items-center gap-1.5 px-4 py-2 rounded border border-border text-muted-foreground font-mono text-xs uppercase tracking-wider hover:bg-muted disabled:opacity-50 transition-colors"
            >
              {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Unplug className="h-3.5 w-3.5" />}
              Disconnect
            </button>
          </div>
        ) : step === "creds" && tokenMode ? (
          <form
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              submitToken();
            }}
          >
            <div>
              <label className="block text-2xs text-muted-foreground mb-1">Session token</label>
              <textarea
                rows={3}
                value={token}
                onChange={(e) => setToken(e.target.value)}
                placeholder="Paste the value of the makerworld.com 'token' cookie"
                className={`${INPUT_CLASS} font-mono resize-y`}
              />
            </div>
            <p className="text-3xs text-muted-foreground">
              Log in to makerworld.com in your browser (Google sign-in is fine), then copy the{" "}
              <span className="font-mono">token</span> cookie from DevTools → Application → Cookies. This is the way to connect a Google-linked account.
            </p>
            <div className="flex items-center gap-2">
              <button
                type="submit"
                disabled={busy || !token.trim()}
                className="inline-flex items-center gap-1.5 px-4 py-2 rounded bg-primary text-primary-foreground font-mono text-xs uppercase tracking-wider hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed transition-opacity"
              >
                {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <KeyRound className="h-3.5 w-3.5" />}
                {busy ? "Saving…" : "Save token"}
              </button>
              <button
                type="button"
                onClick={() => {
                  setTokenMode(false);
                  setError("");
                }}
                disabled={busy}
                className="px-3 py-2 rounded border border-border text-muted-foreground font-mono text-xs uppercase tracking-wider hover:bg-muted disabled:opacity-50 transition-colors"
              >
                Use password instead
              </button>
            </div>
          </form>
        ) : step === "creds" ? (
          <form
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              submitCreds();
            }}
          >
            <div>
              <label className="block text-2xs text-muted-foreground mb-1">Email</label>
              <input
                type="email"
                autoComplete="username"
                value={account}
                onChange={(e) => setAccount(e.target.value)}
                placeholder="you@example.com"
                className={INPUT_CLASS}
              />
            </div>
            <div>
              <label className="block text-2xs text-muted-foreground mb-1">Password</label>
              <input
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="MakerWorld password"
                className={INPUT_CLASS}
              />
            </div>
            <p className="text-3xs text-muted-foreground">
              Your password is sent once to Bambu to obtain a session token and is never stored. MakerWorld usually emails a verification code next.
            </p>
            <div className="flex items-center gap-2">
              <button
                type="submit"
                disabled={busy || !account || !password}
                className="inline-flex items-center gap-1.5 px-4 py-2 rounded bg-primary text-primary-foreground font-mono text-xs uppercase tracking-wider hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed transition-opacity"
              >
                {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <LogIn className="h-3.5 w-3.5" />}
                {busy ? "Signing in…" : "Connect"}
              </button>
              <button
                type="button"
                onClick={() => {
                  setTokenMode(true);
                  setError("");
                }}
                disabled={busy}
                className="px-3 py-2 rounded border border-border text-muted-foreground font-mono text-xs uppercase tracking-wider hover:bg-muted disabled:opacity-50 transition-colors"
              >
                Google account? Paste token
              </button>
            </div>
          </form>
        ) : (
          <form
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              submitCode();
            }}
          >
            <div className="flex items-start gap-2 text-xs text-muted-foreground">
              <KeyRound className="h-3.5 w-3.5 mt-0.5 flex-shrink-0" />
              <span>
                {codeKind === "tfa"
                  ? "Enter the code from your authenticator app."
                  : "Enter the verification code MakerWorld emailed you."}
              </span>
            </div>
            <div>
              <label className="block text-2xs text-muted-foreground mb-1">Verification code</label>
              <input
                type="text"
                inputMode="numeric"
                autoComplete="one-time-code"
                value={code}
                onChange={(e) => setCode(e.target.value)}
                placeholder="123456"
                className={`${INPUT_CLASS} font-mono tracking-widest`}
              />
            </div>
            <div className="flex items-center gap-2">
              <button
                type="submit"
                disabled={busy || !code}
                className="inline-flex items-center gap-1.5 px-4 py-2 rounded bg-primary text-primary-foreground font-mono text-xs uppercase tracking-wider hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed transition-opacity"
              >
                {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
                {busy ? "Verifying…" : "Verify"}
              </button>
              <button
                type="button"
                onClick={resetForm}
                disabled={busy}
                className="px-3 py-2 rounded border border-border text-muted-foreground font-mono text-xs uppercase tracking-wider hover:bg-muted disabled:opacity-50 transition-colors"
              >
                Cancel
              </button>
            </div>
          </form>
        )}

        {error && <p className="text-xs text-red-600 dark:text-red-400">{error}</p>}
      </div>
    </div>
  );
}
