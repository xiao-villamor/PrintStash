/*
 * Choosing storage after signing in, for an owner created from VAULT_SETUP_ADMIN_*.
 *
 * That owner never saw the wizard's storage step, so this is where the choice
 * happens — and it happens on a live account, where a refused choice must leave the
 * form in place to correct rather than strand the installation. The local defaults
 * come from the deployment's own configuration, because a blank path would silently
 * mean "whatever the server was started with".
 */
import "@testing-library/jest-dom/vitest";
import { act, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { clearLogin } from "@/lib/auth-store";
import { setLocale } from "@/lib/locale";
import { queryKeys } from "@/lib/query-client";
import { SetupStorageChoice } from "@/components/setup-storage-choice";
import { aVaultConfig } from "@/test-support/factories";
import { json, renderApp, type RouteTable } from "@/test-support/render";
import { storageProviderCatalogue } from "@/test-support/storage-provider-catalogue";

const PREPARED = { ready: true, storage_provider: "local", checks: [] };

function renderChoice(routes: RouteTable = {}, onPrepared = vi.fn<() => void>()) {
  return renderApp(<SetupStorageChoice onPrepared={onPrepared} />, {
    routes: {
      "GET /api/v1/storage/providers": json(storageProviderCatalogue),
      "GET /api/v1/config": json(
        aVaultConfig({ data_dir: "/data/files", thumb_dir: "/data/thumbs" }),
      ),
      "POST /api/v1/setup/prepare-storage": json(PREPARED),
      ...routes,
    },
  });
}

describe("SetupStorageChoice", () => {
  it("retains a storage draft when locale changes", async () => {
    const choice = renderChoice();
    const root = await screen.findByLabelText("Models directory");
    await userEvent.clear(root);
    await userEvent.type(root, "/my/models");
    await act(async () => setLocale("es"));
    expect(root).toHaveValue("/my/models");
    expect(choice.requestsWithMethod("GET").filter((r) => r.url === "/api/v1/config")).toHaveLength(
      1,
    );
  });
  it("retires a pending storage choice on unmount", async () => {
    const response = Promise.withResolvers<Response>();
    let signal: AbortSignal | null | undefined;
    const onPrepared = vi.fn<() => void>();
    const choice = renderChoice(
      {
        "POST /api/v1/setup/prepare-storage": (_url, init) => {
          signal = init?.signal;
          return response.promise;
        },
      },
      onPrepared,
    );
    await screen.findByLabelText("Models directory");
    await userEvent.click(screen.getByRole("button", { name: "Use this storage" }));
    choice.unmount();
    await act(async () => response.resolve(json(PREPARED)));
    expect(signal?.aborted).toBe(true);
    expect(onPrepared).not.toHaveBeenCalled();
  });
  it("shares configuration with the setup storage picker", async () => {
    const choice = renderApp(<SetupStorageChoice onPrepared={() => {}} />, {
      seed: [[queryKeys.vaultConfig, aVaultConfig({ data_dir: "/cached/models" })]],
      routes: { "GET /api/v1/storage/providers": json(storageProviderCatalogue) },
    });
    expect(await screen.findByLabelText("Models directory")).toHaveValue("/cached/models");
    expect(choice.requestsWithMethod("GET").some((r) => r.url === "/api/v1/config")).toBe(false);
  });

  it("retires a storage choice when the session changes", async () => {
    const response = Promise.withResolvers<Response>();
    let signal: AbortSignal | null | undefined;
    const onPrepared = vi.fn<() => void>();
    const choice = renderChoice(
      {
        "POST /api/v1/setup/prepare-storage": (_url, init) => {
          signal = init?.signal;
          return response.promise;
        },
      },
      onPrepared,
    );
    await screen.findByLabelText("Models directory");
    await userEvent.click(screen.getByRole("button", { name: "Use this storage" }));
    expect(choice.client.getMutationCache().getAll()).toHaveLength(0);
    await act(async () => clearLogin());
    await act(async () => response.resolve(json(PREPARED)));
    expect(signal?.aborted).toBe(true);
    expect(onPrepared).not.toHaveBeenCalled();
  });
  it("retains a storage draft during config refresh", async () => {
    const choice = renderChoice();
    const root = await screen.findByLabelText("Models directory");
    await userEvent.clear(root);
    await userEvent.type(root, "/my/models");
    choice.route({ "GET /api/v1/config": json(aVaultConfig({ data_dir: "/new/default" })) });
    await act(async () => {
      await choice.client.invalidateQueries({ queryKey: queryKeys.vaultConfig });
    });
    expect(root).toHaveValue("/my/models");
  });

  it("starts from the deployment's own local roots", async () => {
    renderChoice();

    expect(await screen.findByLabelText("Models directory")).toHaveValue("/data/files");
  });

  it("sends the chosen storage", async () => {
    const choice = renderChoice();
    await screen.findByLabelText("Models directory");

    await userEvent.click(screen.getByRole("button", { name: "Use this storage" }));

    await vi.waitFor(() =>
      expect(JSON.parse(choice.requestsWithMethod("POST").at(-1)?.body ?? "{}")).toEqual({
        storage_provider: "local",
        storage_provider_config: {
          provider: "local",
          // The catalogue default the onboarding picker hides, as the wizard sends it.
          root: "vault-data",
          data_dir: "/data/files",
          thumb_dir: "/data/thumbs",
        },
      }),
    );
  });

  it("continues once the storage is prepared", async () => {
    const onPrepared = vi.fn<() => void>();
    renderChoice({}, onPrepared);
    await screen.findByLabelText("Models directory");

    await userEvent.click(screen.getByRole("button", { name: "Use this storage" }));

    await vi.waitFor(() => expect(onPrepared).toHaveBeenCalledOnce());
  });

  it("explains a refused choice without leaving the form", async () => {
    renderChoice({
      "POST /api/v1/setup/prepare-storage": json({ detail: "data_dir_not_empty" }, 400),
    });
    await screen.findByLabelText("Models directory");

    await userEvent.click(screen.getByRole("button", { name: "Use this storage" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This folder already contains files",
    );
  });

  it("reports a configuration it could not load", async () => {
    renderChoice({ "GET /api/v1/config": json({ detail: "boom" }, 500) });

    expect(await screen.findByRole("alert")).toHaveTextContent("Could not complete this step");
  });
});
