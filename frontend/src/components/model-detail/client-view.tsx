"use client";

import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";

import { getModel } from "@/lib/api";
import { ApiError, parseApiError } from "@/lib/errors";
import { ModelRead } from "@/types";

import { ModelDetail } from "./index";

/**
 * Renders the model detail. When the server could not fetch the model (SSR has
 * no auth token — reads now require a logged-in user), `initialModel` is null
 * and we fetch client-side with the browser's stored token.
 */
export function ModelDetailClientView({
  id,
  initialModel,
}: {
  id: number;
  initialModel: ModelRead | null;
}) {
  useUiLocale();
  const [model, setModel] = useState<ModelRead | null>(initialModel);
  const [error, setError] = useState<ApiError | null>(null);

  useEffect(() => {
    if (model) return;
    let alive = true;
    getModel(id)
      .then((m) => {
        if (alive) setModel(m);
      })
      .catch((e) => {
        if (alive) setError(parseApiError(e));
      });
    return () => {
      alive = false;
    };
  }, [id, model]);

  if (model) return <ModelDetail model={model} />;

  if (error) {
    const notFound = error.status === 404;
    const needsAuth = error.status === 401 || error.status === 403;
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
      </div>
    );
  }

  return (
    <div className="flex h-full items-center justify-center">
      <Loader2 className="h-8 w-8 animate-spin text-on-surface-variant" />
    </div>
  );
}
