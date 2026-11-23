"use client";

import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

import { Loader2 } from "lucide-react";

import { useModelDetail } from "@/features/library/model-detail";
import { PageFailure } from "@/components/page-failure";
import { parseApiError } from "@/lib/errors";
import { ModelRead } from "@/types";

import { ModelDetail } from "./index";

/** Route composition for the authorized Model read and its recovery states. */
export function ModelDetailClientView({
  id,
  initialModel,
}: {
  id: number;
  initialModel: ModelRead | null;
}) {
  useUiLocale();
  const query = useModelDetail(id, initialModel ?? undefined);
  const model = query.data;
  const error = query.error ? parseApiError(query.error) : null;
  const denied = error && [401, 403, 404].includes(error.status);

  if (model && !denied) return <ModelDetail model={model} />;

  if (error || !query.active) {
    const notFound = error?.status === 404;
    const needsAuth = !query.active || error?.status === 401 || error?.status === 403;
    return (
      <PageFailure
        kind={
          notFound
            ? "not-found"
            : error?.status === 403
              ? "forbidden"
              : needsAuth
                ? "authentication"
                : error?.code === "network_unreachable"
                  ? "offline"
                  : "unavailable"
        }
        title={
          notFound
            ? uiText("Model not found")
            : error?.status === 403
              ? uiText("Access restricted")
              : needsAuth
                ? uiText("Sign in to view this model")
                : error?.code === "network_unreachable"
                  ? uiText("Unable to connect")
                  : uiText("Couldn’t load this model")
        }
        description={
          notFound
            ? uiText("This model doesn’t exist or has been deleted.")
            : needsAuth
              ? uiText("This model lives in a collection you need access to.")
              : error?.code === "network_unreachable"
                ? uiText(
                    "Couldn't reach the server. Check that PrintStash is running and try again.",
                  )
                : uiText("A server error occurred. Reload to try again.")
        }
        onRetry={!notFound && !needsAuth ? () => void query.refetch() : undefined}
        retrying={query.isFetching}
      />
    );
  }

  return (
    <div className="flex h-full items-center justify-center">
      <Loader2 className="h-8 w-8 animate-spin text-on-surface-variant" />
    </div>
  );
}
