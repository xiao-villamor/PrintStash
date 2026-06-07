"use client";

import { useEffect, useState, useRef } from "react";
import Link from "next/link";
import { AlertTriangle, X } from "lucide-react";
import {
  isLoggedIn,
  onAuthChange,
  onUnauthorized,
} from "@/lib/auth";
import { toast } from "@/lib/toast";

export function AuthBanner() {
  const [show, setShow] = useState(false);
  const [reason, setReason] = useState<"missing" | "rejected" | "expired">(
    "missing",
  );
  const firstFire = useRef(true);

  useEffect(() => {
    function update() {
      if (show && isLoggedIn()) {
        setShow(false);
      }
    }
    const offAuth = onAuthChange(update);
    const offUnauth = onUnauthorized(() => {
      let r: "missing" | "rejected" | "expired";
      if (isLoggedIn()) {
        r = "expired";
      } else {
        r = "missing";
      }
      setReason(r);
      setShow(true);
      // Toast only on subsequent 401s, not the automatic bootstrap probe.
      if (!firstFire.current) {
        const msgs: Record<string, string> = {
          missing: "Authentication required — sign in to continue.",
          rejected: "Credentials were rejected by the server. Sign in again.",
          expired: "Your session has expired. Please sign in again.",
        };
        toast.warning(msgs[r]);
      }
      firstFire.current = false;
    });
    return () => {
      offAuth();
      offUnauth();
    };
  }, [show]);

  if (!show) return null;

  const messages: Record<string, string> = {
    missing:
      "An action requires authentication. Sign in to continue.",
    rejected:
      "Server rejected the stored credentials. Sign in again.",
    expired:
      "Your session has expired. Please sign in again.",
  };

  return (
    <div className="bg-amber-500/10 border-b border-amber-500/30 px-6 py-2 flex items-center gap-3 text-xs font-mono text-amber-800 dark:text-amber-200">
      <AlertTriangle className="h-4 w-4 flex-shrink-0" />
      <span className="flex-1">{messages[reason]}</span>
      {!isLoggedIn() && (
        <Link
          href="/login"
          className="uppercase tracking-wider underline hover:no-underline"
          onClick={() => setShow(false)}
        >
          Sign in
        </Link>
      )}
      <Link
        href="/settings"
        className="uppercase tracking-wider underline hover:no-underline"
        onClick={() => setShow(false)}
      >
        Settings
      </Link>
      <button
        type="button"
        onClick={() => setShow(false)}
        className="p-1 rounded hover:bg-amber-500/20 transition-colors"
        aria-label="Dismiss"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}
