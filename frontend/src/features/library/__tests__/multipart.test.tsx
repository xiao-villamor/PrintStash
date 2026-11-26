/** Multipart receipts preserve server versions and retire older reads and sessions. */
import "@testing-library/jest-dom/vitest";
import { act, screen, waitFor } from "@testing-library/react";
import { useQuery } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";
import {
  deleteLibraryMultipart,
  moveLibraryMultipart,
  multipartDetailOptions,
  useMultipartPublication,
} from "../multipart";
import { getSessionVersion } from "@/lib/session-transport";
import { clearLogin } from "@/lib/auth-store";
import { queryKeys } from "@/lib/query-client";
import { aMultipartModel } from "@/test-support/factories";
import { json, renderApp } from "@/test-support/render";
import type { MultipartModelRead } from "@/types";

const detail: MultipartModelRead = {
  ...aMultipartModel({ id: 7, name: "Current", edit_version: 4 }),
  description: null,
  parts: [],
  guides: [],
};
type Publish = ReturnType<typeof useMultipartPublication>["publish"];
function Probe({ ready }: { ready: (publish: Publish) => void }) {
  const query = useQuery(multipartDetailOptions(7));
  const owner = useMultipartPublication(7);
  ready(owner.publish);
  return <p>{owner.active ? query.data?.name : "Retired"}</p>;
}

describe("Multipart detail owner", () => {
  it("adopts a reviewed Multipart history with a lower counter", async () => {
    let publish!: Publish;
    const view = renderApp(
      <Probe
        ready={(value) => {
          publish = value;
        }}
      />,
      { seed: [[queryKeys.multipartModel(7), detail]] },
    );
    await screen.findByText("Current");
    const restored = {
      ...detail,
      edit_epoch: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      edit_version: 1,
      name: "Restored",
    };
    await act(async () => expect(await publish(restored)).toBe(true));
    expect(await screen.findByText("Restored")).toBeVisible();
    expect(view.client.getQueryData(queryKeys.multipartModel(7))).toEqual(restored);
  });

  it("preserves another history against a late Multipart receipt", async () => {
    let publish!: Publish;
    const view = renderApp(
      <Probe
        ready={(value) => {
          publish = value;
        }}
      />,
      { seed: [[queryKeys.multipartModel(7), detail]] },
    );
    await screen.findByText("Current");
    const previousPublication = publish;
    const restored = {
      ...detail,
      edit_epoch: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      edit_version: 1,
      name: "Restored",
    };
    act(() => view.client.setQueryData(queryKeys.multipartModel(7), restored));
    await screen.findByText("Restored");
    expect(await previousPublication({ ...detail, edit_version: 9 })).toBe(false);
    expect(view.client.getQueryData(queryKeys.multipartModel(7))).toEqual(restored);
  });

  it("retires an older Multipart read before publishing a write", async () => {
    let publish!: Publish;
    let signal: AbortSignal | null | undefined;
    const held = Promise.withResolvers<Response>();
    const view = renderApp(
      <Probe
        ready={(value) => {
          publish = value;
        }}
      />,
      {
        routes: {
          "GET /api/v1/multipart-models/7": (_url, init) => {
            signal = init?.signal;
            return held.promise;
          },
        },
      },
    );
    await waitFor(() => expect(view.requests()).toHaveLength(1));
    await act(async () => {
      expect(await publish({ ...detail, name: "Confirmed", edit_version: 9 })).toBe(true);
    });
    await act(async () => held.resolve(json(detail)));
    expect(signal).toMatchObject({ aborted: true });
    expect(await screen.findByText("Confirmed")).toBeVisible();
    expect(view.client.getQueryData(queryKeys.multipartModel(7))).toMatchObject({
      edit_version: 9,
    });
  });

  it("suppresses Multipart publication after session retirement", async () => {
    let publish!: Publish;
    const view = renderApp(
      <Probe
        ready={(value) => {
          publish = value;
        }}
      />,
      { seed: [[queryKeys.multipartModel(7), detail]] },
    );
    await screen.findByText("Current");
    act(() => clearLogin());
    expect(await publish({ ...detail, name: "Old session", edit_version: 9 })).toBe(false);
    expect(view.client.getQueryData(queryKeys.multipartModel(7))).toBeUndefined();
    expect(screen.getByText("Retired")).toBeVisible();
  });

  it("ignores a publication after its detail view is unmounted", async () => {
    let publish!: Publish;
    const view = renderApp(
      <Probe
        ready={(value) => {
          publish = value;
        }}
      />,
      { seed: [[queryKeys.multipartModel(7), detail]] },
    );
    await screen.findByText("Current");
    view.unmount();
    expect(await publish({ ...detail, name: "Old view", edit_version: 9 })).toBe(false);
    expect(view.client.getQueryData(queryKeys.multipartModel(7))).toEqual(detail);
  });

  it("preserves a newer Multipart receipt against an older acknowledgement", async () => {
    let publish!: Publish;
    const view = renderApp(
      <Probe
        ready={(value) => {
          publish = value;
        }}
      />,
      { seed: [[queryKeys.multipartModel(7), detail]] },
    );
    await screen.findByText("Current");
    expect(await publish({ ...detail, name: "Older", edit_version: 2 })).toBe(false);
    expect(view.client.getQueryData(queryKeys.multipartModel(7))).toEqual(detail);
  });
});

describe("deleteLibraryMultipart", () => {
  it("ignores a deletion receipt after the session changes", async () => {
    const pending = Promise.withResolvers<Response>();
    const app = renderApp(<p>Library</p>, {
      routes: { "DELETE /api/v1/multipart-models/7": () => pending.promise },
    });
    const command = deleteLibraryMultipart(app.client, 7, getSessionVersion());
    const outcome = command.then(
      () => null,
      (error: Error) => error.message,
    );
    await waitFor(() => expect(app.requestsWithMethod("DELETE")).toHaveLength(1));
    act(() => clearLogin());
    app.client.setQueryData(queryKeys.multipartModel(7), { ...detail, name: "New session" });
    pending.resolve(json(null, 204));
    expect(await outcome).toBe("request_session_changed");
    expect(app.client.getQueryData(queryKeys.multipartModel(7))).toMatchObject({
      name: "New session",
    });
  });

  it("retires a confirmed deleted grouping from the cache", async () => {
    const app = renderApp(<p>Library</p>, {
      seed: [[queryKeys.multipartModel(7), detail]],
      routes: { "DELETE /api/v1/multipart-models/7": json(null, 204) },
    });
    await deleteLibraryMultipart(app.client, 7, getSessionVersion());
    expect(app.client.getQueryData(queryKeys.multipartModel(7))).toBeUndefined();
    expect(app.requestsWithMethod("DELETE").map((r) => r.url)).toEqual([
      "/api/v1/multipart-models/7",
    ]);
  });
  it("preserves a grouping after a rejected deletion", async () => {
    const app = renderApp(<p>Library</p>, {
      seed: [[queryKeys.multipartModel(7), detail]],
      routes: { "DELETE /api/v1/multipart-models/7": json({ detail: "permission_denied" }, 403) },
    });
    await expect(deleteLibraryMultipart(app.client, 7, getSessionVersion())).rejects.toThrow(
      "permission_denied",
    );
    expect(app.client.getQueryData(queryKeys.multipartModel(7))).toEqual(detail);
  });
  it("refuses deletion under a retired session", async () => {
    const app = renderApp(<p>Library</p>);
    const previous = getSessionVersion();
    act(() => clearLogin());
    await expect(deleteLibraryMultipart(app.client, 7, previous)).rejects.toThrow(
      "request_session_changed",
    );
    expect(app.requestsWithMethod("DELETE")).toHaveLength(0);
  });
});

describe("moveLibraryMultipart", () => {
  const source = { id: 7, edit_epoch: detail.edit_epoch, edit_version: detail.edit_version };
  const moved = (over: Partial<MultipartModelRead> = {}): MultipartModelRead => ({
    ...detail,
    edit_version: detail.edit_version + 1,
    collection_id: 3,
    collection: "toys",
    ...over,
  });

  it("sends only the destination, keeping the acknowledged detail", async () => {
    const app = renderApp(<p>Library</p>, {
      seed: [[queryKeys.multipartModel(7), detail]],
      routes: { "PATCH /api/v1/multipart-models/7": json(moved()) },
    });
    await moveLibraryMultipart(app.client, source, 3, getSessionVersion());
    const [request] = app.requestsWithMethod("PATCH");
    expect(request?.url).toBe("/api/v1/multipart-models/7");
    expect(JSON.parse(request!.body)).toEqual({ collection_id: 3 });
    expect(app.client.getQueryData(queryKeys.multipartModel(7))).toMatchObject({
      collection_id: 3,
      edit_version: detail.edit_version + 1,
    });
  });

  it("moves a set to the Vault with a null destination", async () => {
    const app = renderApp(<p>Library</p>, {
      routes: {
        "PATCH /api/v1/multipart-models/7": json(moved({ collection_id: null, collection: null })),
      },
    });
    await moveLibraryMultipart(app.client, source, null, getSessionVersion());
    expect(JSON.parse(app.requestsWithMethod("PATCH")[0]!.body)).toEqual({ collection_id: null });
  });

  it("preserves the detail after a rejected move", async () => {
    const app = renderApp(<p>Library</p>, {
      seed: [[queryKeys.multipartModel(7), detail]],
      routes: {
        "PATCH /api/v1/multipart-models/7": json({ detail: "collection_permission_denied" }, 403),
      },
    });
    await expect(moveLibraryMultipart(app.client, source, 3, getSessionVersion())).rejects.toThrow(
      "collection_permission_denied",
    );
    expect(app.client.getQueryData(queryKeys.multipartModel(7))).toEqual(detail);
  });

  it("rejects a receipt that does not advance the version", async () => {
    const app = renderApp(<p>Library</p>, {
      seed: [[queryKeys.multipartModel(7), detail]],
      routes: {
        "PATCH /api/v1/multipart-models/7": json(moved({ edit_version: detail.edit_version })),
      },
    });
    await expect(moveLibraryMultipart(app.client, source, 3, getSessionVersion())).rejects.toThrow(
      "Invalid Multipart acknowledgement",
    );
    expect(app.client.getQueryData(queryKeys.multipartModel(7))).toEqual(detail);
  });

  it("refuses a move under a retired session", async () => {
    const app = renderApp(<p>Library</p>);
    const previous = getSessionVersion();
    act(() => clearLogin());
    await expect(moveLibraryMultipart(app.client, source, 3, previous)).rejects.toThrow(
      "request_session_changed",
    );
    expect(app.requestsWithMethod("PATCH")).toHaveLength(0);
  });
});
