/*
 * The one load-failure block: announced as an alert, with Retry in a fixed place
 * and never offered when there is nothing to retry.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ErrorState } from "@/components/ui/error-state";

describe("ErrorState", () => {
  it("announces the failure with Retry in the same place every time", () => {
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
