import { createContext, useCallback, useContext, useMemo, useState, type FormEvent } from "react";

import { uiText } from "@/lib/locale";

export type FieldErrorMap = Readonly<Record<string, string>>;

export const FieldErrorContext = createContext<FieldErrorMap>({});

/** The error recorded for the control with this id, if the last submit found one. */
export function useFieldError(id: string | undefined): string | undefined {
  const errors = useContext(FieldErrorContext);
  return id ? errors[id] : undefined;
}

function messageFor(control: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement): string {
  const { validity } = control;
  if (validity.valueMissing) return uiText("This field is required.");
  if (validity.customError && control.validationMessage) return control.validationMessage;
  if (validity.typeMismatch && control instanceof HTMLInputElement) {
    if (control.type === "email") return uiText("Enter a valid email address.");
    if (control.type === "url") return uiText("Enter a valid URL.");
  }
  if (validity.patternMismatch && control.title) return control.title;
  if (validity.typeMismatch || validity.patternMismatch) return uiText("Enter a valid value.");
  if (validity.tooShort || validity.tooLong) return uiText("Check the length of this value.");
  if (validity.rangeUnderflow || validity.rangeOverflow || validity.stepMismatch)
    return uiText("Enter a value within the allowed range.");
  return uiText("Enter a valid value.");
}

/**
 * Replaces the browser's native validation bubble with the app's own error
 * under the field. Spread `formProps` on the `<form>`, call `handle(event, submit)`
 * from `onSubmit`, wrap its fields in `<FieldErrors>`, and `FormField` shows each message beside its control. The
 * control gets `aria-invalid` and focus moves to the first invalid one.
 */
export function useFormValidation() {
  const [errors, setErrors] = useState<FieldErrorMap>({});

  const validate = useCallback((form: HTMLFormElement): boolean => {
    const next: Record<string, string> = {};
    let first: HTMLElement | null = null;
    for (const element of Array.from(form.elements)) {
      if (
        !(
          element instanceof HTMLInputElement ||
          element instanceof HTMLSelectElement ||
          element instanceof HTMLTextAreaElement
        )
      )
        continue;
      if (element.willValidate && !element.checkValidity()) {
        if (element.id) next[element.id] = messageFor(element);
        element.setAttribute("aria-invalid", "true");
        first ??= element;
      } else if (element.getAttribute("aria-invalid") === "true" && !element.dataset.invalid) {
        element.removeAttribute("aria-invalid");
      }
    }
    setErrors(next);
    first?.focus();
    return first === null;
  }, []);

  const onInput = useCallback((event: FormEvent<HTMLFormElement>) => {
    const target = event.target;
    if (!(target instanceof HTMLElement) || !target.id) return;
    setErrors((current) => {
      if (!(target.id in current)) return current;
      // Only a mark this hook set is cleared; a control the component marks
      // itself keeps the attribute React renders.
      target.removeAttribute("aria-invalid");
      const { [target.id]: _removed, ...rest } = current;
      return rest;
    });
  }, []);

  /** Submit handler body: validates first and only then calls `submit`. */
  const handle = useCallback(
    (
      event: FormEvent<HTMLFormElement>,
      submit: (event: FormEvent<HTMLFormElement>) => void | Promise<void>,
    ) => {
      if (!validate(event.currentTarget)) {
        event.preventDefault();
        return;
      }
      return submit(event);
    },
    [validate],
  );

  return useMemo(
    () => ({
      errors,
      handle,
      /** Validate imperatively (e.g. a save button outside the form's submit event). */
      validate,
      /** Spread on the form: no native bubbles, errors clear as the user types. */
      formProps: { noValidate: true, onInput } as const,
    }),
    [errors, handle, onInput, validate],
  );
}
