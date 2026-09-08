/* Dismissing the setup reminder is persistent, user-specific, and shared by its surfaces. */
import { act, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GettingStartedReminder } from "@/components/getting-started-reminder";
import { usePathname } from "@/lib/navigation";
import { adminSession, memberSession, renderApp } from "@/test-support/render";

function Path() {
  return <span data-testid="path">{usePathname()}</span>;
}

beforeEach(() => localStorage.clear());
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("GettingStartedReminder", () => {
  it("opens the guide", async () => {
    renderApp(
      <>
        <GettingStartedReminder />
        <Path />
      </>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Resume the getting-started guide" }));
    expect(screen.getByTestId("path")).toHaveTextContent("/getting-started");
  });

  it("persists dismissal across remounts", async () => {
    const view = renderApp(<GettingStartedReminder />);
    await userEvent.click(screen.getByRole("button", { name: "Don't show again" }));
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    view.unmount();
    renderApp(<GettingStartedReminder />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("keeps dismissal specific to the current user", async () => {
    const view = renderApp(<GettingStartedReminder />);
    await userEvent.click(screen.getByRole("button", { name: "Don't show again" }));
    view.unmount();
    renderApp(<GettingStartedReminder />, {
      auth: adminSession({
        user: { id: 2, username: "another-admin", email: null, is_superuser: true },
      }),
    });
    expect(screen.getByRole("button", { name: "Resume the getting-started guide" })).toBeVisible();
  });

  it("synchronizes visible reminders", async () => {
    renderApp(
      <>
        <GettingStartedReminder />
        <GettingStartedReminder />
      </>,
    );
    await userEvent.click(screen.getAllByRole("button", { name: "Don't show again" })[0]);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("reacts to dismissal in another tab", () => {
    renderApp(<GettingStartedReminder />);
    act(() => {
      localStorage.setItem("printstash.getting-started.dismissed.1", "true");
      window.dispatchEvent(
        new StorageEvent("storage", { key: "printstash.getting-started.dismissed.1" }),
      );
    });
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("hides the reminder when persistent storage is unavailable", async () => {
    const view = renderApp(<GettingStartedReminder />, {
      auth: adminSession({
        user: { id: 803, username: "storage-blocked-admin", email: null, is_superuser: true },
      }),
    });
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    await userEvent.click(screen.getByRole("button", { name: "Don't show again" }));
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    view.rerender(<GettingStartedReminder />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it.each([
    { label: "member", auth: memberSession() },
    { label: "signed out", auth: adminSession({ user: null }) },
  ])("excludes non-administrators: $label", ({ auth }) => {
    renderApp(<GettingStartedReminder />, { auth });
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
