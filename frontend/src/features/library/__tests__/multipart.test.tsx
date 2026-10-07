/** Multipart receipts preserve server versions and retire older reads and sessions. */
import "@testing-library/jest-dom/vitest";
import { act, screen, waitFor } from "@testing-library/react";
import { useQuery } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";
import { multipartDetailOptions, useMultipartPublication } from "../multipart";
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
