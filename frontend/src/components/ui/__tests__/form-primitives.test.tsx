/*
 * The shared form and feedback primitives from the UI audit: one validation
 * mechanism (inline text under the field, never the browser bubble), one
 * optional-field convention, one load-failure block and one password reveal.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ErrorState } from "@/components/ui/error-state";
import { FieldErrors } from "@/components/ui/field-errors";
import { FormField } from "@/components/ui/form-field";
import { Input } from "@/components/ui/input";
import { PasswordToggle } from "@/components/ui/password-toggle";
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
        <FormField label="Note" htmlFor="demo-note" optional>
          <Input id="demo-note" />
        </FormField>
      </FieldErrors>
      <button type="submit">Save</button>
    </form>
  );
}

describe("useFormValidation", () => {
  it("shows the error under the field, focuses it, and never submits an invalid form", () => {
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

  it("clears the error as the person types and then submits", () => {
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

describe("FormField", () => {
  it("marks only optional fields, in words", () => {
    render(<Demo onSubmit={() => {}} />);
    expect(screen.getByText("(optional)")).toBeVisible();
    expect(screen.getAllByText(/optional/)).toHaveLength(1);
  });

  it("renders an explicit error as an alert under the control", () => {
    render(
      <FormField label="Confirm" htmlFor="c" error="Passwords do not match." errorId="c-error">
        <Input id="c" />
      </FormField>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Passwords do not match.");
  });
});

describe("ErrorState", () => {
  it("announces the failure and offers Retry in the same place every time", () => {
    const retry = vi.fn<() => void>();
    render(<ErrorState message="Could not load." onRetry={retry} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Could not load.");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(retry).toHaveBeenCalledTimes(1);
  });

  it("omits Retry when there is nothing to retry", () => {
    render(<ErrorState message="Wrong password." />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});

describe("PasswordToggle", () => {
  it("reports its state and controlled field", () => {
    const toggle = vi.fn<() => void>();
    render(
      <div className="relative">
        <PasswordToggle visible={false} onToggle={toggle} label="Show password" controls="pw" />
      </div>,
    );
    const button = screen.getByRole("button", { name: "Show password" });
    expect(button).toHaveAttribute("aria-pressed", "false");
    expect(button).toHaveAttribute("aria-controls", "pw");
    fireEvent.click(button);
    expect(toggle).toHaveBeenCalledTimes(1);
  });
});
