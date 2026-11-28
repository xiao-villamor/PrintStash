import type { ReactNode } from "react";

import { FieldErrorContext, type FieldErrorMap } from "@/lib/form-validation";

/** Supplies the errors from `useFormValidation` to every `FormField` inside. */
export function FieldErrors({ errors, children }: { errors: FieldErrorMap; children: ReactNode }) {
  return <FieldErrorContext.Provider value={errors}>{children}</FieldErrorContext.Provider>;
}
