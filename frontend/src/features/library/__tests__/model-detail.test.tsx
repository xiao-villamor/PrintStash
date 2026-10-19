/** Confirmed Model publication is fenced by identity and the server edit version; a sustained intent warms the detail read. */
import "@testing-library/jest-dom/vitest";
import { act, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  useModelDetail,
  useModelOpenIntent,
  useModelPrintJobs,
  modelPrintJobsOptions,
  type PublishModel,
} from "../model-detail";
import type { ModelPrintJobRead } from "@/types";
import { clearLogin } from "@/lib/auth-store";
import { queryKeys } from "@/lib/query-client";
import { aMetadata, aModel, aRevision } from "@/test-support/factories";
import { json, renderApp } from "@/test-support/render";

const model = aModel({ id: 1, name: "Current", edit_version: 4 });
function Probe({ ready }: { ready: (publish: PublishModel) => void }) {
  const resource = useModelDetail(1, model);
  ready(resource.publish);
  return <p>{resource.data?.name}</p>;
}
describe("useModelDetail", () => {
  it("revalidates derivatives when a confirmed receipt differs from the observed revision", async () => {
    let publish!: PublishModel;
    const fresh = aModel({
      id: 1,
      name: "Confirmed",
      edit_version: 5,
      derivatives_revision: "ready-after-edit",
      files: [aRevision({ metadata: aMetadata({ estimated_time_s: 1812 }) })],
    });
    const view = renderApp(<Probe ready={(value) => (publish = value)} />, {
      routes: { "GET /api/v1/models/1": json(fresh) },
    });
    await screen.findByText("Current");
    // A background read settles before the edit response captured earlier arrives.
    act(() =>
      view.client.setQueryData(
        queryKeys.model(1),
        aModel({
          ...model,
          derivatives_revision: "ready-before-edit",
          files: [aRevision({ metadata: aMetadata({ estimated_time_s: 1812 }) })],
        }),
      ),
    );
    await act(async () => {
      await publish(
        aModel({
          id: 1,
          name: "Confirmed",
          edit_version: 5,
          derivatives_revision: "pending-before-edit",
          files: [aRevision({ metadata: null })],
        }),
      );
    });

    await waitFor(() =>
      expect(view.client.getQueryData(queryKeys.model(1))).toMatchObject({
        name: "Confirmed",
        edit_version: 5,
        derivatives_revision: "ready-after-edit",
        files: [{ metadata: { estimated_time_s: 1812 } }],
      }),
    );
    expect(view.requests().filter((read) => read.url === "/api/v1/models/1")).toHaveLength(1);
  });

  it("keeps a matching derivative receipt without another detail read", async () => {
    let publish!: PublishModel;
    const view = renderApp(<Probe ready={(value) => (publish = value)} />);
    await screen.findByText("Current");

    await act(async () => {
      await publish(aModel({ id: 1, name: "Confirmed", edit_version: 5 }));
    });

    expect(await screen.findByText("Confirmed")).toBeVisible();
    expect(view.requests().filter((read) => read.url === "/api/v1/models/1")).toEqual([]);
  });

  it("ignores a stale edit receipt with a different derivative revision", async () => {
    let publish!: PublishModel;
    const view = renderApp(<Probe ready={(value) => (publish = value)} />);
    await screen.findByText("Current");

    const accepted = await publish(
      aModel({ id: 1, edit_version: 2, derivatives_revision: "stale-background-state" }),
    );

    expect(accepted).toBe(false);
    expect(screen.getByText("Current")).toBeVisible();
    expect(view.requests().filter((read) => read.url === "/api/v1/models/1")).toEqual([]);
  });
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

function IntentProbe({
  enabled = true,
  ready,
}: {
  enabled?: boolean;
  ready: (intend: (id: number | null) => void) => void;
}) {
  ready(useModelOpenIntent(enabled));
  return null;
}
const detailReads = (requests: { method: string; url: string }[]) =>
  requests.filter((request) => request.url.endsWith("/api/v1/models/7")).length;
const sustained = () => new Promise((resolve) => setTimeout(resolve, 200));

describe("useModelOpenIntent", () => {
  it("reads the Model's detail after a sustained intent", async () => {
    let intend!: (id: number | null) => void;
    const view = renderApp(<IntentProbe ready={(value) => (intend = value)} />, {
      routes: { "GET /api/v1/models/7": json(aModel({ id: 7, name: "Warmed" })) },
    });

    act(() => intend(7));

    await waitFor(() =>
      expect(view.client.getQueryData<{ name: string }>(queryKeys.model(7))?.name).toBe("Warmed"),
    );
  });

  it("reads nothing when the intent ends first", async () => {
    let intend!: (id: number | null) => void;
    const view = renderApp(<IntentProbe ready={(value) => (intend = value)} />, {
      routes: { "GET /api/v1/models/7": json(aModel({ id: 7 })) },
    });

    act(() => intend(7));
    act(() => intend(null));
    await act(sustained);

    expect(detailReads(view.requests())).toBe(0);
  });

  it("reads nothing while it is disabled", async () => {
    let intend!: (id: number | null) => void;
    const view = renderApp(<IntentProbe enabled={false} ready={(value) => (intend = value)} />, {
      routes: { "GET /api/v1/models/7": json(aModel({ id: 7 })) },
    });

    act(() => intend(7));
    await act(sustained);

    expect(detailReads(view.requests())).toBe(0);
  });

  it("reuses a detail that is still fresh", async () => {
    let intend!: (id: number | null) => void;
    const view = renderApp(<IntentProbe ready={(value) => (intend = value)} />, {
      routes: { "GET /api/v1/models/7": json(aModel({ id: 7 })) },
    });
    act(() => intend(7));
    await waitFor(() => expect(detailReads(view.requests())).toBe(1));

    act(() => intend(null));
    act(() => intend(7));
    await act(sustained);

    expect(detailReads(view.requests())).toBe(1);
  });
});
