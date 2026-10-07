/** Confirmed Model publication is fenced by identity and the server edit version. */
import "@testing-library/jest-dom/vitest";
import { act, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  useModelDetail,
  useModelPrintJobs,
  modelPrintJobsOptions,
  type PublishModel,
} from "../model-detail";
import type { ModelPrintJobRead } from "@/types";
import { clearLogin } from "@/lib/auth-store";
import { queryKeys } from "@/lib/query-client";
import { aModel } from "@/test-support/factories";
import { json, renderApp } from "@/test-support/render";

const model = aModel({ id: 1, name: "Current", edit_version: 4 });
function Probe({ ready }: { ready: (publish: PublishModel) => void }) {
  const resource = useModelDetail(1, model);
  ready(resource.publish);
  return <p>{resource.data?.name}</p>;
}
describe("useModelDetail", () => {
  it("rejects a receipt after a different history has become canonical", async () => {
    let publish!: PublishModel;
    const view = renderApp(
      <Probe
        ready={(value) => {
          publish = value;
        }}
      />,
    );
    await screen.findByText("Current");
    const oldPublication = publish;
    act(() =>
      view.client.setQueryData(
        queryKeys.model(1),
        aModel({
          id: 1,
          edit_epoch: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
          edit_version: 1,
          name: "Restored",
        }),
      ),
    );
    await screen.findByText("Restored");
    const accepted = await oldPublication(
      aModel({ id: 1, edit_version: 5, name: "Previous history ACK" }),
    );
    expect(accepted).toBe(false);
    expect(view.client.getQueryData(queryKeys.model(1))).toMatchObject({
      name: "Restored",
      edit_epoch: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      edit_version: 1,
    });
  });

  it("publishes an explicitly read history with a lower counter", async () => {
    let publish!: PublishModel;
    const view = renderApp(
      <Probe
        ready={(value) => {
          publish = value;
        }}
      />,
    );
    await screen.findByText("Current");
    const accepted = await publish(
      aModel({
        id: 1,
        edit_epoch: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        edit_version: 1,
        name: "Reviewed restoration",
      }),
    );
    expect(accepted).toBe(true);
    expect(view.client.getQueryData(queryKeys.model(1))).toMatchObject({
      name: "Reviewed restoration",
      edit_version: 1,
    });
  });

  it("ignores a confirmed publication after session retirement", async () => {
    let publish!: PublishModel;
    const view = renderApp(
      <Probe
        ready={(value) => {
          publish = value;
        }}
      />,
    );
    await screen.findByText("Current");

    act(() => clearLogin());
    const accepted = await publish(aModel({ id: 1, name: "Old session ACK", edit_version: 5 }));

    expect(accepted).toBe(false);
    expect(view.client.getQueryData(queryKeys.model(1))).toBeUndefined();
    expect(screen.queryByText("Current")).toBeNull();
  });
  it("preserves a newer Model when an older acknowledgement arrives", async () => {
    let publish!: PublishModel;
    const view = renderApp(
      <Probe
        ready={(value) => {
          publish = value;
        }}
      />,
    );
    await screen.findByText("Current");

    const accepted = await publish(aModel({ id: 1, name: "Older", edit_version: 2 }));

    expect(accepted).toBe(false);
    expect(view.client.getQueryData(queryKeys.model(1))).toMatchObject({
      name: "Current",
      edit_version: 4,
    });
    expect(screen.getByText("Current")).toBeVisible();
  });
});

function HistoryProbe({
  ready,
}: {
  ready: (publish: (job: ModelPrintJobRead) => Promise<void>) => void;
}) {
  const resource = useModelPrintJobs(1, true);
  ready(resource.publish);
  return <p>{resource.data?.map((job) => job.id).join(",")}</p>;
}
describe("useModelPrintJobs", () => {
  it("publishes a confirmed print after cancelling an obsolete history read", async () => {
    let publish!: (job: ModelPrintJobRead) => Promise<void>;
    const pending = Promise.withResolvers<Response>();
    const view = renderApp(
      <HistoryProbe
        ready={(value) => {
          publish = value;
        }}
      />,
      {
        routes: { "GET /api/v1/models/1/print-jobs": () => pending.promise },
      },
    );
    await waitFor(() => expect(view.requests()).toHaveLength(1));
    // SAFETY: this owner reads only a print's id; the rest of the HTTP DTO is opaque to it.
    const job = { id: 100 } as ModelPrintJobRead;

    await act(async () => publish(job));
    await act(async () => pending.resolve(json([])));

    expect(await screen.findByText("100")).toBeVisible();
    expect(view.client.getQueryData(modelPrintJobsOptions(1).queryKey)).toEqual([job]);
  });
});
