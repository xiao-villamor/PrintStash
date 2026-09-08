import { useSyncExternalStore } from "react";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth-context";
import { useI18n } from "@/lib/i18n";
import { useRouter } from "@/lib/navigation";

const CHANGE_EVENT = "printstash:getting-started-reminder-changed";
// If browser storage is blocked, respect dismissal for the current page session.
const sessionDismissals = new Set<number>();
const preferenceKey = (userId: number) => `printstash.getting-started.dismissed.${userId}`;

function isDismissed(userId: number | undefined): boolean {
  if (userId === undefined) return false;
  if (sessionDismissals.has(userId)) return true;
  try {
    return localStorage.getItem(preferenceKey(userId)) === "true";
  } catch {
    return false;
  }
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener(CHANGE_EVENT, onChange);
  window.addEventListener("storage", onChange);
  return () => {
    window.removeEventListener(CHANGE_EVENT, onChange);
    window.removeEventListener("storage", onChange);
  };
}

export function GettingStartedReminder() {
  const { user } = useAuth();
  const { t } = useI18n();
  const router = useRouter();
  const dismissed = useSyncExternalStore(
    subscribe,
    () => isDismissed(user?.id),
    () => false,
  );
  if (!user?.is_superuser || dismissed) return null;

  function dismiss() {
    if (!user) return;
    try {
      localStorage.setItem(preferenceKey(user.id), "true");
    } catch {
      sessionDismissals.add(user.id);
    }
    window.dispatchEvent(new Event(CHANGE_EVENT));
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button variant="outline" onClick={() => router.push("/getting-started")}>
        {t("setup.resume")}
      </Button>
      <Button variant="ghost" onClick={dismiss}>
        {t("setup.dismissReminder")}
      </Button>
    </div>
  );
}
