/** Shared account controls retain their real desktop/mobile navigation owners. */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SidebarNav } from "@/components/sidebar-nav";
import { BottomNavBar } from "@/components/bottom-nav-bar";
import { KeyboardShortcuts } from "@/components/keyboard-shortcuts";
import {
  adminSession,
  memberSession,
  renderApp,
  json,
  type RenderAppOptions,
} from "@/test-support/render";
import { usePathname } from "@/lib/navigation";

function Location() {
  return <output aria-label="Location">{usePathname()}</output>;
}
function renderMenu(options: RenderAppOptions & { mobile?: boolean } = {}) {
  const { mobile = false, ...rest } = options;
  return renderApp(
    <>
      {mobile ? <BottomNavBar /> : <SidebarNav />}
      <KeyboardShortcuts />
      <Location />
    </>,
    { routes: { "GET /api/v1/inbox": json([]) }, ...rest },
  );
}
async function openMenu(mobile = false) {
  await userEvent.click(screen.getByRole("button", { name: mobile ? "More" : "My workspace" }));
  return screen.getByRole("menu");
}
beforeEach(() => {
  localStorage.clear();
  document.documentElement.classList.remove("dark");
});
describe("WorkspaceMenu", () => {
  it("identifies the current account", async () => {
    renderMenu();
    const menu = await openMenu();
    expect(menu).toHaveTextContent("admin");
    expect(menu).toHaveTextContent("Administrator");
  });
  it("keeps desktop account destinations compact", async () => {
    renderMenu();
    const menu = await openMenu();
    expect(
      within(menu)
        .getAllByRole("menuitem")
        .map((x) => x.textContent),
    ).toEqual(["Statistics", "Settings", "Docsprintstash.org", "Log out"]);
  });
  it.each([
    { label: "administrator", auth: adminSession() },
    { label: "member", auth: memberSession() },
  ])("opens the official documentation for a $label", async ({ auth }) => {
    renderMenu({ auth });
    const menu = await openMenu();
    expect(within(menu).getByRole("menuitem", { name: /Docs/ })).toHaveAttribute(
      "href",
      "https://www.printstash.org/docs/",
    );
  });
  it("keeps all mobile destinations reachable", async () => {
    renderMenu({ mobile: true });
    const menu = await openMenu(true);
    for (const name of [
      "Favorites",
      "Profiles",
      "Similar models",
      "Trash",
      "Statistics",
      "Settings",
    ])
      expect(within(menu).getByRole("menuitem", { name })).toBeVisible();
    expect(screen.getByRole("link", { name: "Printers" })).toHaveAttribute("href", "/printers");
  });
  it("omits privileged account destinations for a member", async () => {
    renderMenu({ mobile: true, auth: memberSession() });
    const menu = await openMenu(true);
    expect(within(menu).queryByRole("menuitem", { name: "Statistics" })).not.toBeInTheDocument();
    expect(within(menu).queryByRole("menuitem", { name: "Trash" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Profiles" })).toHaveAttribute("href", "/profiles");
  });
  it("navigates through the client router", async () => {
    renderMenu();
    const menu = await openMenu();
    await userEvent.click(within(menu).getByRole("menuitem", { name: "Settings" }));
    await waitFor(() =>
      expect(screen.getByRole("status", { name: "Location" })).toHaveTextContent("/settings"),
    );
  });
  it("returns focus to the account trigger after Escape", async () => {
    renderMenu();
    await openMenu();
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("menu")).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: "My workspace" })).toHaveFocus();
  });
  it("uses the shared explicit theme preference", async () => {
    renderMenu();
    const menu = await openMenu();
    await userEvent.click(within(menu).getByRole("menuitemradio", { name: "Dark" }));
    expect(localStorage.getItem("printstash.theme")).toBe("dark");
    expect(document.documentElement).toHaveClass("dark");
  });
  it("retains the system theme preference", async () => {
    renderMenu();
    const menu = await openMenu();
    await userEvent.click(within(menu).getByRole("menuitemradio", { name: "System" }));
    expect(localStorage.getItem("printstash.theme")).toBe("system");
  });
  it("switches the real locale immediately", async () => {
    renderMenu();
    const menu = await openMenu();
    await userEvent.click(within(menu).getByRole("menuitemradio", { name: "Español" }));
    expect(await screen.findByRole("menuitem", { name: "Cerrar sesión" })).toBeVisible();
  });
  it("opens the shared keyboard help from mobile More", async () => {
    renderMenu({ mobile: true });
    const menu = await openMenu(true);
    await userEvent.click(within(menu).getByRole("menuitem", { name: "Keyboard shortcuts" }));
    expect(screen.getByRole("dialog", { name: "Keyboard shortcuts" })).toHaveTextContent(
      "Find and move around",
    );
  });
  it("logs out through the authenticated shell owner", async () => {
    const logout = vi.fn<() => Promise<void>>().mockResolvedValue();
    renderMenu({ auth: adminSession({ logout }) });
    const menu = await openMenu();
    await userEvent.click(within(menu).getByRole("menuitem", { name: "Log out" }));
    expect(logout).toHaveBeenCalledOnce();
    await waitFor(() =>
      expect(screen.getByRole("status", { name: "Location" })).toHaveTextContent("/login"),
    );
  });
  it("offers sign in without a private session", () => {
    renderMenu({ auth: adminSession({ user: null }) });
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login");
  });
});
