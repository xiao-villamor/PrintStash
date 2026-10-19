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
  inferenceModelsOptions,
  useSearchCommands,
} from "@/lib/queries/search";
import {
  searchConfiguration,
  searchPreferences,
  searchSettings,
  anInferenceEndpoint,
  anInferenceModel,
  aSearchGeneration,
} from "@/test-support/search";
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
            base: searchConfiguration(),
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
        searchConfiguration({ edit_version: 2, settings: searchSettings({ enabled: false }) }),
      ),
    },
  });
}
afterEach(() => vi.restoreAllMocks());
describe("Search command lifetime", () => {
  it("retains a newer accepted query receipt", async () => {
    const app = renderCommand();
    await screen.findByText("Enabled");
    const response = Promise.withResolvers<Response>();
    app.route({ "PUT /api/v1/config/ai-search": () => response.promise });
    await userEvent.click(screen.getByRole("button", { name: "Save search" }));
    await waitFor(() => expect(app.requestsWithMethod("PUT")).toHaveLength(1));
    const newer = searchConfiguration({
      edit_version: 5,
      settings: searchSettings({ enabled: true }),
    });
    await act(async () => {
      app.client.setQueryData(searchKeys.settings, newer);
      response.resolve(
        json(
          searchConfiguration({ edit_version: 2, settings: searchSettings({ enabled: false }) }),
        ),
      );
    });
    await waitFor(() => expect(app.client.isMutating()).toBe(0));
    expect(app.client.getQueryData(searchKeys.settings)).toEqual(newer);
    expect(screen.getByText("Enabled")).toBeVisible();
  });
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
  it("leaves a new same-user read active when a retired acknowledgement resumes", async () => {
    const app = renderCommand();
    await screen.findByText("Enabled");
    const cache = app.client.getMutationCache();
    const previousSuccess = cache.config.onSuccess;
    const previousSettled = cache.config.onSettled;
    const entered = Promise.withResolvers<void>();
    const resume = Promise.withResolvers<void>();
    const settled = Promise.withResolvers<void>();
    cache.config.onSuccess = async () => {
      entered.resolve();
      await resume.promise;
    };
    cache.config.onSettled = () => {
      settled.resolve();
    };
    const currentRead = Promise.withResolvers<Response>();
    let signal: AbortSignal | null | undefined;
    try {
      await userEvent.click(screen.getByRole("button", { name: "Save search" }));
      await entered.promise;
      app.unmount();
      clearLogin();
      renderApp(<SettingsCommand />, {
        routes: {
          "GET /api/v1/config/ai-search": (_url, init) => {
            signal = init?.signal;
            return currentRead.promise;
          },
        },
      });
      await waitFor(() => expect(signal).toBeDefined());
      await act(async () => {
        resume.resolve();
        await settled.promise;
      });
      expect(signal?.aborted).toBe(false);
      await act(async () => {
        currentRead.resolve(
          json(searchConfiguration({ settings: searchSettings({ enabled: true }) })),
        );
      });
      expect(await screen.findByText("Enabled")).toBeVisible();
    } finally {
      cache.config.onSuccess = previousSuccess;
      cache.config.onSettled = previousSettled;
      resume.resolve();
      currentRead.resolve(json(searchConfiguration()));
    }
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
      <p>{preference.data?.timezone}</p>
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
  it("leaves a new same-user preference read active when a retired acknowledgement resumes", async () => {
    const app = renderPreferences();
    await screen.findByText("Preferences loaded");
    const cache = app.client.getMutationCache();
    const previousSuccess = cache.config.onSuccess;
    const previousSettled = cache.config.onSettled;
    const entered = Promise.withResolvers<void>();
    const resume = Promise.withResolvers<void>();
    const settled = Promise.withResolvers<void>();
    cache.config.onSuccess = async () => {
      entered.resolve();
      await resume.promise;
    };
    cache.config.onSettled = () => {
      settled.resolve();
    };
    const currentRead = Promise.withResolvers<Response>();
    let signal: AbortSignal | null | undefined;
    try {
      await userEvent.click(screen.getByRole("button", { name: "Save preference" }));
      await entered.promise;
      app.unmount();
      clearLogin();
      renderApp(<PreferencesCommand />, {
        routes: {
          "GET /api/v1/search/preferences": (_url, init) => {
            signal = init?.signal;
            return currentRead.promise;
          },
        },
      });
      await waitFor(() => expect(signal).toBeDefined());
      await act(async () => {
        resume.resolve();
        await settled.promise;
      });
      expect(signal?.aborted).toBe(false);
      await act(async () => {
        currentRead.resolve(json(searchPreferences({ timezone: "America/New_York" })));
      });
      expect(await screen.findByText("America/New_York")).toBeVisible();
    } finally {
      cache.config.onSuccess = previousSuccess;
      cache.config.onSettled = previousSettled;
      resume.resolve();
      currentRead.resolve(json(searchPreferences()));
    }
  });
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

function CatalogCommands() {
  const commands = useSearchCommands();
  return (
    <>
      <button
        onClick={() =>
          commands.importEndpoint.mutate({ kind: "embedding", session: getSessionVersion() })
        }
      >
        Import endpoint
      </button>
      <button
        onClick={() =>
          commands.validate.mutate({ id: "a".repeat(64), session: getSessionVersion() })
        }
      >
        Validate encoder
      </button>
      <button
        onClick={() => commands.remove.mutate({ id: "a".repeat(64), session: getSessionVersion() })}
      >
        Delete encoder
      </button>
      <button
        onClick={() =>
          commands.generationAction.mutate({
            generation: aSearchGeneration({ state: "building" }),
            action: "activate",
            session: getSessionVersion(),
          })
        }
      >
        Activate index
      </button>
      <button
        onClick={() =>
          commands.endpoint.mutate({
            payload: {
              kind: "embedding",
              base_url: "http://inference.local/v1",
              model: "new-encoder",
              revision: "pinned-v2",
            },
            session: getSessionVersion(),
          })
        }
      >
        Register endpoint
      </button>
    </>
  );
}

describe("Search catalog command receipts", () => {
  it("removes only the acknowledged encoder from the cached catalog", async () => {
    const retained = anInferenceModel({ id: "b".repeat(64), key: "retained-encoder" });
    const app = renderApp(<CatalogCommands />, {
      seed: [[searchKeys.models, [anInferenceModel(), retained]]],
      routes: { [`DELETE /api/v1/inference/models/${"a".repeat(64)}`]: json(null, 204) },
    });

    await userEvent.click(screen.getByRole("button", { name: "Delete encoder" }));

    await waitFor(() => expect(app.client.getQueryData(searchKeys.models)).toEqual([retained]));
  });
  it("publishes activation only into the matching index row", async () => {
    const retained = aSearchGeneration({ id: 2, model: "other-encoder" });
    const accepted = aSearchGeneration({ state: "active", version_token: "b".repeat(32) });
    const app = renderApp(<CatalogCommands />, {
      seed: [[searchKeys.generations, [aSearchGeneration({ state: "building" }), retained]]],
      routes: { "POST /api/v1/config/ai-search/generations/1/activate": json(accepted) },
    });

    await userEvent.click(screen.getByRole("button", { name: "Activate index" }));

    await waitFor(() =>
      expect(app.client.getQueryData(searchKeys.generations)).toEqual([accepted, retained]),
    );
  });
  it.each([
    { label: "new", endpoints: [] },
    { label: "existing", endpoints: [anInferenceEndpoint({ model: "old-encoder" })] },
  ])("publishes an acknowledged $label endpoint", async ({ endpoints }) => {
    const accepted = anInferenceEndpoint({ model: "new-encoder", revision: "pinned-v2" });
    const app = renderApp(<CatalogCommands />, {
      seed: [[searchKeys.settings, searchConfiguration({ endpoints })]],
      routes: { "POST /api/v1/config/ai-search/endpoints": json(accepted) },
    });

    await userEvent.click(screen.getByRole("button", { name: "Register endpoint" }));

    await waitFor(() =>
      expect(app.client.getQueryData(searchKeys.settings)).toMatchObject({ endpoints: [accepted] }),
    );
  });
});

function EndpointImport() {
  const settings = useQuery(searchSettingsOptions());
  return (
    <>
      <CatalogCommands />
      <p>{settings.data?.endpoints.map((endpoint) => endpoint.model).join(",")}</p>
    </>
  );
}
function EncoderValidation() {
  const models = useQuery(inferenceModelsOptions());
  return (
    <>
      <CatalogCommands />
      <p>{models.data?.map((model) => model.key + ":" + model.installed).join(",")}</p>
    </>
  );
}
describe("Search catalog command refresh", () => {
  it("refreshes the canonical catalog after environment endpoint import", async () => {
    const endpoint = anInferenceEndpoint({ model: "imported-encoder" });
    let imported = false;
    renderApp(<EndpointImport />, {
      routes: {
        "GET /api/v1/config/ai-search": () =>
          json(searchConfiguration({ endpoints: imported ? [endpoint] : [] })),
        "POST /api/v1/config/ai-search/endpoints/from-environment/embedding": () => {
          imported = true;
          return json(endpoint);
        },
      },
    });

    await userEvent.click(screen.getByRole("button", { name: "Import endpoint" }));

    expect(await screen.findByText("imported-encoder")).toBeVisible();
  });
  it("refreshes installed status after encoder validation", async () => {
    let validated = false;
    renderApp(<EncoderValidation />, {
      routes: {
        "GET /api/v1/inference/models": () => json([anInferenceModel({ installed: validated })]),
        [`POST /api/v1/inference/models/${"a".repeat(64)}/validate`]: () => {
          validated = true;
          return json({ id: "a".repeat(64), ready: true });
        },
      },
    });
    await screen.findByText("bge-small-en-v1.5:false");

    await userEvent.click(screen.getByRole("button", { name: "Validate encoder" }));

    expect(await screen.findByText("bge-small-en-v1.5:true")).toBeVisible();
  });
});
