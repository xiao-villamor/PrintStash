/** Search commands publish authoritative DTOs only in the original session incarnation. */
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useQuery } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { clearLogin } from "@/lib/auth-store";
import { getSessionVersion } from "@/lib/session-transport";
import {
  searchKeys,
  searchPreferencesOptions,
  searchSettingsOptions,
  useSearchCommands,
} from "@/lib/queries/search";
import { searchConfiguration, searchPreferences, searchSettings } from "@/test-support/search";
import { json, renderApp } from "@/test-support/render";
function SettingsCommand() {
  const settings = useQuery(searchSettingsOptions());
  const commands = useSearchCommands();
  return (
    <>
      <p>{settings.data ? (settings.data.settings.enabled ? "Enabled" : "Disabled") : "Loading"}</p>
      <button
        onClick={() =>
          commands.settings.mutate({
            payload: searchSettings({ enabled: false }),
            session: getSessionVersion(),
          })
        }
      >
        Save search
      </button>
    </>
  );
}
function renderCommand() {
  return renderApp(<SettingsCommand />, {
    routes: {
      "GET /api/v1/config/ai-search": json(
        searchConfiguration({ settings: searchSettings({ enabled: true }) }),
      ),
      "PUT /api/v1/config/ai-search": json(
        searchConfiguration({ settings: searchSettings({ enabled: false }) }),
      ),
    },
  });
}
afterEach(() => vi.restoreAllMocks());
describe("Search command lifetime", () => {
  it("never dispatches a retired gesture", async () => {
    const app = renderCommand();
    await screen.findByText("Enabled");
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Save search" }));
      app.unmount();
      clearLogin();
    });
    expect(app.requestsWithMethod("PUT")).toEqual([]);
    expect(app.client.getQueriesData({ queryKey: searchKeys.all })).toEqual([]);
  });
  it("discards an acknowledgement after delayed publication retires", async () => {
    const app = renderCommand();
    await screen.findByText("Enabled");
    let resume!: () => void;
    const paused = new Promise<void>((resolve) => {
      resume = resolve;
    });
    const cancel = app.client.cancelQueries.bind(app.client);
    const spy = vi
      .spyOn(app.client, "cancelQueries")
      .mockImplementationOnce(cancel)
      .mockImplementationOnce(() => paused);
    await userEvent.click(screen.getByRole("button", { name: "Save search" }));
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
    await act(async () => {
      app.unmount();
      clearLogin();
      resume();
      await paused;
    });
    expect(app.requestsWithMethod("PUT")).toHaveLength(1);
    expect(app.client.getQueriesData({ queryKey: searchKeys.all })).toEqual([]);
  });
  it("publishes acknowledged settings without a second settings GET", async () => {
    const app = renderCommand();
    await screen.findByText("Enabled");
    await userEvent.click(screen.getByRole("button", { name: "Save search" }));
    expect(await screen.findByText("Disabled")).toBeVisible();
    expect(app.requestsWithMethod("PUT")).toHaveLength(1);
    expect(
      app
        .requests()
        .filter(
          (request) => request.url === "/api/v1/config/ai-search" && request.method === "GET",
        ),
    ).toHaveLength(1);
  });
});

function PreferencesCommand() {
  const preference = useQuery(searchPreferencesOptions(1));
  const { preferences } = useSearchCommands();
  return (
    <>
      <p>{preference.data ? "Preferences loaded" : "Loading"}</p>
      <button
        onClick={() =>
          preferences.mutate({
            payload: { timezone: "Europe/Madrid" },
            userId: 1,
            session: getSessionVersion(),
          })
        }
      >
        Save preference
      </button>
    </>
  );
}
function renderPreferences() {
  return renderApp(<PreferencesCommand />, {
    routes: {
      "GET /api/v1/search/preferences": json(searchPreferences()),
      "PATCH /api/v1/search/preferences": json(searchPreferences({ timezone: "Europe/Madrid" })),
    },
  });
}
describe("Search preference command lifetime", () => {
  it("never dispatches a retired preference gesture", async () => {
    const app = renderPreferences();
    await screen.findByText("Preferences loaded");
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Save preference" }));
      app.unmount();
      clearLogin();
    });
    expect(app.requestsWithMethod("PATCH")).toEqual([]);
    expect(app.client.getQueriesData({ queryKey: ["search-preferences"] })).toEqual([]);
  });
  it("discards a preference acknowledgement after delayed publication retires", async () => {
    const app = renderPreferences();
    await screen.findByText("Preferences loaded");
    let resume!: () => void;
    const paused = new Promise<void>((resolve) => {
      resume = resolve;
    });
    const cancel = app.client.cancelQueries.bind(app.client);
    const spy = vi
      .spyOn(app.client, "cancelQueries")
      .mockImplementationOnce(cancel)
      .mockImplementationOnce(() => paused);
    await userEvent.click(screen.getByRole("button", { name: "Save preference" }));
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
    await act(async () => {
      app.unmount();
      clearLogin();
      resume();
      await paused;
    });
    expect(app.requestsWithMethod("PATCH")).toHaveLength(1);
    expect(app.client.getQueriesData({ queryKey: ["search-preferences"] })).toEqual([]);
  });
});
