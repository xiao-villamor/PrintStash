/** Recovery pages provide an exit without depending on the failed router. */
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { PageFailure } from "@/components/page-failure";
import NotFound from "@/pages/not-found";
import { RouteError } from "@/components/route-error";

describe("PageFailure", () => {
  it("offers document navigation when a URL is missing", () => {
    render(<NotFound />);
    expect(screen.getByRole("alert", { name: "Page not found" })).toBeVisible();
    expect(screen.getByText("404")).toBeVisible();
    expect(screen.getByRole("link", { name: "Back to vault" })).toHaveAttribute("href", "/");
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
  });
  it("offers sign-in for a missing session", () => {
    render(<PageFailure kind="authentication" />);
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login");
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
  });
  it("distinguishes permissions from missing identity", () => {
    render(<PageFailure kind="forbidden" />);
    expect(screen.getByRole("heading", { name: "Access restricted" })).toBeVisible();
    expect(screen.getByText("403")).toBeVisible();
    expect(screen.queryByText("Sign in required")).not.toBeInTheDocument();
  });
  it("retries the read and prevents duplicate submissions", async () => {
    const retry = vi.fn<() => void>();
    const user = userEvent.setup();
    const view = render(<PageFailure kind="offline" onRetry={retry} />);
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(retry).toHaveBeenCalledTimes(1);
    view.rerender(<PageFailure kind="offline" onRetry={retry} retrying />);
    expect(screen.getByRole("button", { name: "Retry" })).toBeDisabled();
    expect(screen.queryByText("500")).not.toBeInTheDocument();
  });
  it("renders route recovery without router, session or query providers", () => {
    render(<RouteError />);
    expect(screen.getByRole("heading", { name: "Page unavailable" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Reload page" })).toBeEnabled();
    expect(screen.getByRole("link", { name: "Back to vault" })).toHaveAttribute("href", "/");
  });
});
