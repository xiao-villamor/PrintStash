/** Derived project facts follow the selected Artifact, including a delayed first read. */
import { act, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { setEventSocketFactory, type EventSocket } from "@/lib/events";
import { useProjectProfile } from "../project-profile";
import { aProjectProfile } from "@/test-support/factories";
import { json, renderApp } from "@/test-support/render";

class FakeSocket implements EventSocket {
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  send = vi.fn<(data: string) => void>();
  close = vi.fn<() => void>();
}
let socket: FakeSocket;
beforeEach(() => {
  socket = new FakeSocket();
  setEventSocketFactory(async () => socket);
});
const READY = {
  state: "ready",
  profile: aProjectProfile({ printer: "Saved printer" }),
  failure_reason: null,
};
const PENDING = { state: "pending", profile: null, failure_reason: null };
function Probe({ fileId }: { fileId: number | null }) {
  const query = useProjectProfile(3, fileId);
  return <p>{query.data?.profile?.printer ?? query.data?.state ?? "unknown"}</p>;
}
function notice(fileId = 7) {
  act(() =>
    socket.onmessage?.({
      data: JSON.stringify({
        type: "derivative",
        model_id: 3,
        file_id: fileId,
        kind: "project_profile",
        state: "ready",
      }),
    }),
  );
}
describe("useProjectProfile", () => {
  it("does not read without a selected project", () => {
    const view = renderApp(<Probe fileId={null} />);
    expect(screen.getByText("unknown")).toBeVisible();
    expect(view.requests()).toEqual([]);
  });
  it("refreshes the selected Artifact when analysis finishes", async () => {
    const view = renderApp(<Probe fileId={7} />, {
      routes: { "GET /api/v1/files/7/project-profile": json(PENDING) },
    });
    await screen.findByText("pending");
    await waitFor(() => expect(socket.onmessage).not.toBeNull());
    view.route({ "GET /api/v1/files/7/project-profile": json(READY) });
    notice();
    expect(await screen.findByText("Saved printer")).toBeVisible();
  });
  it("replaces a stale first response when the derivative finishes during that read", async () => {
    let release: ((response: Response) => void) | undefined;
    const pending = new Promise<Response>((resolve) => {
      release = resolve;
    });
    const view = renderApp(<Probe fileId={7} />, {
      routes: { "GET /api/v1/files/7/project-profile": () => pending },
    });
    await waitFor(() => expect(view.requests()).toHaveLength(1));
    await waitFor(() => expect(socket.onmessage).not.toBeNull());
    view.route({ "GET /api/v1/files/7/project-profile": json(READY) });
    notice();
    expect(await screen.findByText("Saved printer")).toBeVisible();
    await act(async () => release?.(Response.json(PENDING)));
    expect(screen.getByText("Saved printer")).toBeVisible();
  });
  it("ignores another Artifact's notice", async () => {
    const view = renderApp(<Probe fileId={7} />, {
      routes: { "GET /api/v1/files/7/project-profile": json(PENDING) },
    });
    await screen.findByText("pending");
    await waitFor(() => expect(socket.onmessage).not.toBeNull());
    notice(8);
    expect(view.requests()).toHaveLength(1);
    expect(screen.getByText("pending")).toBeVisible();
  });
});
