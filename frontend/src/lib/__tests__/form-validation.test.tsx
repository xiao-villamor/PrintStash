/*
 * Inline form validation replaces the browser's native bubble: the message sits
 * under the field, focus moves to the first invalid control, and nothing invalid
 * is ever submitted.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { FieldErrors } from "@/components/ui/field-errors";
import { FormField } from "@/components/ui/form-field";
import { Input } from "@/components/ui/input";
import { useFormValidation } from "@/lib/form-validation";

function Demo({ onSubmit }: { onSubmit: () => void }) {
  const validation = useFormValidation();
  return (
    <form
      aria-label="demo"
      {...validation.formProps}
      onSubmit={(event) => validation.handle(event, onSubmit)}
    >
      <FieldErrors errors={validation.errors}>
        <FormField label="Name" htmlFor="demo-name">
          <Input id="demo-name" required />
        </FormField>
      </FieldErrors>
      <button type="submit">Save</button>
    </form>
  );
}

describe("useFormValidation", () => {
  it("shows the error under the field, focuses it, blocks submit", () => {
    const submit = vi.fn<() => void>();
    render(<Demo onSubmit={submit} />);
    expect(screen.getByRole("form", { name: "demo" })).toHaveAttribute("novalidate");

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(submit).not.toHaveBeenCalled();
    expect(screen.getByText("This field is required.")).toBeVisible();
    const name = screen.getByLabelText("Name");
    expect(name).toHaveAttribute("aria-invalid", "true");
    expect(name).toHaveFocus();
  });

  it("clears the error as the person types, then submits", () => {
    const submit = vi.fn<() => void>();
    render(<Demo onSubmit={submit} />);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    fireEvent.input(screen.getByLabelText("Name"), { target: { value: "Benchy" } });
    expect(screen.queryByText("This field is required.")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Name")).not.toHaveAttribute("aria-invalid");

    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(submit).toHaveBeenCalledTimes(1);
  });
});
