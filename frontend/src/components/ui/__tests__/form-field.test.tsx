/*
 * FormField owns the optional-field convention (words, muted) and the error line
 * under the control, so every form marks and reports fields the same way.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { FormField } from "@/components/ui/form-field";
import { Input } from "@/components/ui/input";

describe("FormField", () => {
  it("marks only optional fields, in words", () => {
    render(
      <>
        <FormField label="Name" htmlFor="a">
          <Input id="a" />
        </FormField>
        <FormField label="Note" htmlFor="b" optional>
          <Input id="b" />
        </FormField>
      </>,
    );
    expect(screen.getAllByText(/optional/)).toHaveLength(1);
    expect(screen.getByText("(optional)")).toBeVisible();
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
