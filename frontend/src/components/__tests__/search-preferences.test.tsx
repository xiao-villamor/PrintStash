/** Personal consent and timezone updates stay scoped to the authenticated user's preference endpoint. */
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { SearchPreferences } from "@/components/search-preferences";
import { json, renderApp } from "@/test-support/render";
import type { SearchPreferences as Preferences } from "@/types/search";

const value: Preferences = {
  nl_filters_enabled: false,
  available: true,
  timezone: null,
  effective_timezone: "UTC",
  endpoint_host: "local-chat",
};
describe("Search preferences", () => {
  it.each<{ locale: "en" | "es"; title: string; optIn: string }>([
    {
      locale: "en",
      title: "Natural-language search",
      optIn: "Interpret my searches as editable filters",
    },
    {
      locale: "es",
      title: "Búsqueda en lenguaje natural",
      optIn: "Interpretar mis búsquedas como filtros editables",
    },
  ])("supports accessible consent in $locale", async ({ locale, title, optIn }) => {
    const user = userEvent.setup();
    const app = renderApp(<SearchPreferences value={value} userId={1} />, { locale });
    const trigger = screen.getByRole("button", { name: title });
    await user.click(trigger);
    const dialog = screen.getByRole("dialog", { name: title });
    expect(dialog).toHaveTextContent("local-chat");
    expect(screen.getByRole("checkbox", { name: optIn })).not.toBeChecked();
    expect(dialog.contains(document.activeElement)).toBe(true);
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await waitFor(() => expect(trigger).toHaveFocus());
    expect(app.requestsWithMethod("PATCH")).toHaveLength(0);
  });
  it("hides the option when the instance has no available parser", () => {
    renderApp(<SearchPreferences value={{ ...value, available: false }} userId={1} />);
    expect(screen.queryByRole("button", { name: "Natural-language search" })).toBeNull();
  });
  it("keeps the draft after a rejected timezone", async () => {
    const app = renderApp(<SearchPreferences value={value} userId={1} />, {
      routes: { "PATCH /api/v1/search/preferences": json({}, 422) },
    });
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Natural-language search" }));
    const timezone = screen.getByRole("textbox", {
      name: "Your timezone (blank uses instance setting)",
    });
    await user.type(timezone, "Invalid/Zone");
    await user.click(screen.getByRole("button", { name: "Save preferences" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Check the IANA timezone");
    expect(timezone).toHaveValue("Invalid/Zone");
    expect(JSON.parse(app.lastBody())).toEqual({
      nl_filters_enabled: false,
      timezone: "Invalid/Zone",
    });
  });
  it("sends only personal preferences", async () => {
    const app = renderApp(<SearchPreferences value={value} userId={1} />, {
      routes: {
        "PATCH /api/v1/search/preferences": json({
          ...value,
          nl_filters_enabled: true,
          timezone: "Europe/Madrid",
        }),
      },
    });
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Natural-language search" }));
    await user.click(
      screen.getByRole("checkbox", { name: "Interpret my searches as editable filters" }),
    );
    await user.type(
      screen.getByRole("textbox", { name: "Your timezone (blank uses instance setting)" }),
      "Europe/Madrid",
    );
    await user.click(screen.getByRole("button", { name: "Save preferences" }));
    expect(JSON.parse(app.lastBody())).toEqual({
      nl_filters_enabled: true,
      timezone: "Europe/Madrid",
    });
    expect(app.requests().every((request) => request.url.endsWith("/search/preferences"))).toBe(
      true,
    );
  });
});
