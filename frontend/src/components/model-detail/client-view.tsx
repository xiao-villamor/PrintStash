"use client";

import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

import { Loader2 } from "lucide-react";

import { useModelDetail } from "@/features/library/model-detail";
import { Button } from "@/components/ui/button";
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
      <div className="flex h-full flex-col items-center justify-center gap-3 text-center px-6">
        <p className="text-lg font-semibold text-on-surface">
          {notFound
            ? uiText("Model not found")
            : needsAuth
              ? uiText("Sign in to view this model")
              : uiText("Couldn’t load this model")}
        </p>
        <p className="text-sm text-on-surface-variant">
          {notFound
            ? uiText("This model doesn’t exist or has been deleted.")
            : needsAuth
              ? uiText("This model lives in a collection you need access to.")
              : uiText("A server error occurred. Reload to try again.")}
        </p>
        {!notFound && !needsAuth && (
          <Button variant="outline" onClick={() => void query.refetch()} loading={query.isFetching}>
            {uiText("Retry")}
          </Button>
        )}
      </div>
    );
  }

  return (
    <div className="flex h-full items-center justify-center">
      <Loader2 className="h-8 w-8 animate-spin text-on-surface-variant" />
    </div>
  );
}
