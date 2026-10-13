/** Saved-view reads and acknowledged commands share one private session projection. */
import "@testing-library/jest-dom/vitest";
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { useSavedViews } from "../saved-views";
import { clearLogin, storeLogin } from "@/lib/auth-store";
import { adminSession, json, renderApp } from "@/test-support/render";
import type { SavedViewRead } from "@/types";

const SAVED: SavedViewRead = {
  id: 1,
  name: "Workshop",
  filters: { library_view: "all", direct: true, tag: [], favorites: false },
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};
function Probe({ label = "Views" }: { label?: string }) {
  const saved = useSavedViews(true);
  return (
    <>
      <output aria-label={label}>{saved.views.map((view) => view.name).join(",")}</output>
      <button onClick={() => void saved.query.refetch()}>Refresh</button>
      <button
        onClick={() =>
          saved.mutation.mutate({ kind: "create", name: "Created", filters: SAVED.filters })
        }
      >
        Create
      </button>
      <button
        onClick={() =>
          saved.mutation.mutate({ kind: "update", id: 1, payload: { name: "Renamed" } })
        }
      >
        Rename
      </button>
      <button onClick={() => saved.mutation.mutate({ kind: "delete", id: 1 })}>Delete</button>
      {saved.query.isError && <p>Could not read</p>}
      {saved.mutation.isSuccess && <p>Confirmed</p>}
      {saved.mutation.isError && <p>Could not save</p>}
    </>
  );
}

describe("useSavedViews", () => {
  it("does not read views without an account", () => {
    const app = renderApp(<Probe />, { auth: adminSession({ user: null }) });
    expect(app.requestsWithMethod("GET")).toEqual([]);
    expect(screen.getByLabelText("Views")).toBeEmptyDOMElement();
  });

  it("shares the authorized list across observers", async () => {
    const app = renderApp(
      <>
        <Probe />
        <Probe label="Other views" />
      </>,
      { routes: { "GET /api/v1/saved-views": json([SAVED]) } },
    );
    await waitFor(() => expect(screen.getByLabelText("Other views")).toHaveTextContent("Workshop"));
    expect(screen.getByLabelText("Views")).toHaveTextContent("Workshop");
    expect(app.requestsWithMethod("GET")).toHaveLength(1);
  });
  it("cancels a read when its last observer leaves", async () => {
    const pending = Promise.withResolvers<Response>();
    let signal: AbortSignal | null | undefined;
    const app = renderApp(<Probe />, {
      routes: {
        "GET /api/v1/saved-views": (_url, init) => {
          signal = init?.signal;
          return pending.promise;
        },
      },
    });
    await waitFor(() => expect(signal).toBeDefined());
    app.unmount();
    expect(signal?.aborted).toBe(true);
    pending.resolve(json([SAVED]));
  });
  it("hides private views when the session retires", async () => {
    renderApp(<Probe />, { routes: { "GET /api/v1/saved-views": json([SAVED]) } });
    await screen.findByText("Workshop");
    act(() => clearLogin());
    expect(screen.getByLabelText("Views")).toBeEmptyDOMElement();
  });
  it("isolates a replacement account", async () => {
    const pending = Promise.withResolvers<Response>();
    const app = renderApp(<Probe />, {
      routes: { "GET /api/v1/saved-views": () => pending.promise },
    });
    await waitFor(() => expect(app.requestsWithMethod("GET")).toHaveLength(1));
    app.route({ "GET /api/v1/saved-views": json([{ ...SAVED, name: "New account" }]) });
    act(() => storeLogin("", { id: 2, username: "maker", email: null, is_superuser: false }));
    await screen.findByText("New account");
    await act(async () => pending.resolve(json([SAVED])));
    expect(screen.getByLabelText("Views")).toHaveTextContent(/^New account$/);
  });
  it("ignores a retired mutation acknowledgement", async () => {
    const pending = Promise.withResolvers<Response>();
    const app = renderApp(<Probe />, {
      routes: {
        "GET /api/v1/saved-views": json([SAVED]),
        "PATCH /api/v1/saved-views/1": () => pending.promise,
      },
    });
    await screen.findByText("Workshop");
    await userEvent.click(screen.getByRole("button", { name: "Rename" }));
    await waitFor(() => expect(app.requestsWithMethod("PATCH")).toHaveLength(1));
    act(() => clearLogin());
    await act(async () => pending.resolve(json({ ...SAVED, name: "Renamed" })));
    expect(screen.getByLabelText("Views")).toBeEmptyDOMElement();
    expect(screen.queryByText("Confirmed")).toBeNull();
  });
  it.each([
    {
      label: "created",
      button: "Create",
      route: "POST /api/v1/saved-views",
      response: { ...SAVED, id: 2, name: "Server created" },
      expected: "Server created,Workshop",
    },
    {
      label: "updated",
      button: "Rename",
      route: "PATCH /api/v1/saved-views/1",
      response: { ...SAVED, name: "Server renamed" },
      expected: "Server renamed",
    },
  ])(
    "publishes a $label view from its acknowledgement",
    async ({ button, route, response, expected }) => {
      const app = renderApp(<Probe />, {
        routes: { "GET /api/v1/saved-views": json([SAVED]), [route]: json(response) },
      });
      await screen.findByText("Workshop");
      await userEvent.click(screen.getByRole("button", { name: button }));
      await screen.findByText("Confirmed");
      expect(screen.getByLabelText("Views")).toHaveTextContent(expected);
      expect(app.requestsWithMethod("GET")).toHaveLength(1);
    },
  );
  it("removes a deleted view after acknowledgement", async () => {
    const app = renderApp(<Probe />, {
      routes: {
        "GET /api/v1/saved-views": json([SAVED]),
        "DELETE /api/v1/saved-views/1": json(null, 204),
      },
    });
    await screen.findByText("Workshop");
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    await screen.findByText("Confirmed");
    expect(screen.getByLabelText("Views")).toBeEmptyDOMElement();
    expect(app.requestsWithMethod("GET")).toHaveLength(1);
  });
  it("rejects a read older than the write acknowledgement", async () => {
    const pending = Promise.withResolvers<Response>();
    const app = renderApp(<Probe />, {
      routes: {
        "GET /api/v1/saved-views": json([SAVED]),
        "PATCH /api/v1/saved-views/1": json({ ...SAVED, name: "Confirmed rename" }),
      },
    });
    await screen.findByText("Workshop");
    app.route({ "GET /api/v1/saved-views": () => pending.promise });
    await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(app.requestsWithMethod("GET")).toHaveLength(2));
    await userEvent.click(screen.getByRole("button", { name: "Rename" }));
    await screen.findByText("Confirmed");
    await act(async () => pending.resolve(json([SAVED])));
    expect(screen.getByLabelText("Views")).toHaveTextContent(/^Confirmed rename$/);
  });
  it("retains the list after a failed mutation", async () => {
    renderApp(<Probe />, {
      routes: {
        "GET /api/v1/saved-views": json([SAVED]),
        "PATCH /api/v1/saved-views/1": json({ detail: "saved_view_name_exists" }, 409),
      },
    });
    await screen.findByText("Workshop");
    await userEvent.click(screen.getByRole("button", { name: "Rename" }));
    await screen.findByText("Could not save");
    expect(screen.getByLabelText("Views")).toHaveTextContent(/^Workshop$/);
  });
  it("rejects a queued command after session retirement", async () => {
    const app = renderApp(<Probe />, {
      routes: {
        "GET /api/v1/saved-views": json([SAVED]),
        "DELETE /api/v1/saved-views/1": json(null, 204),
      },
    });
    await screen.findByText("Workshop");
    act(() => {
      fireEvent.click(screen.getByRole("button", { name: "Delete" }));
      clearLogin();
    });
    await screen.findByText("Could not save");
    expect(app.requestsWithMethod("DELETE")).toHaveLength(0);
  });
  it("serializes writes within the authorized list", async () => {
    const first = Promise.withResolvers<Response>();
    let writes = 0;
    const app = renderApp(<Probe />, {
      routes: {
        "GET /api/v1/saved-views": json([SAVED]),
        "PATCH /api/v1/saved-views/1": () =>
          ++writes === 1 ? first.promise : json({ ...SAVED, name: "Latest acknowledgement" }),
      },
    });
    await screen.findByText("Workshop");
    await userEvent.click(screen.getByRole("button", { name: "Rename" }));
    await waitFor(() => expect(app.requestsWithMethod("PATCH")).toHaveLength(1));

    await userEvent.click(screen.getByRole("button", { name: "Rename" }));

    expect(app.requestsWithMethod("PATCH")).toHaveLength(1);
    await act(async () => first.resolve(json({ ...SAVED, name: "Earlier acknowledgement" })));
    await waitFor(() =>
      expect(screen.getByLabelText("Views")).toHaveTextContent(/^Latest acknowledgement$/),
    );
  });

  it("rejects a command before the list is loaded", async () => {
    const pending = Promise.withResolvers<Response>();
    const app = renderApp(<Probe />, {
      routes: { "GET /api/v1/saved-views": () => pending.promise },
    });
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    await screen.findByText("Could not save");
    expect(app.requestsWithMethod("POST")).toHaveLength(0);
    await act(async () => pending.resolve(json([SAVED])));
  });

  it("rejects an acknowledgement for a different identity", async () => {
    renderApp(<Probe />, {
      routes: {
        "GET /api/v1/saved-views": json([SAVED]),
        "PATCH /api/v1/saved-views/1": json({ ...SAVED, id: 2, name: "Wrong identity" }),
      },
    });
    await screen.findByText("Workshop");
    await userEvent.click(screen.getByRole("button", { name: "Rename" }));
    await screen.findByText("Could not save");
    expect(screen.getByLabelText("Views")).toHaveTextContent(/^Workshop$/);
  });
  it("rejects an empty write acknowledgement", async () => {
    renderApp(<Probe />, {
      routes: {
        "GET /api/v1/saved-views": json([SAVED]),
        "POST /api/v1/saved-views": json(null, 204),
      },
    });
    await screen.findByText("Workshop");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    await screen.findByText("Could not save");
    expect(screen.getByLabelText("Views")).toHaveTextContent(/^Workshop$/);
  });
  it("retains the last list after a transient read failure", async () => {
    const app = renderApp(<Probe />, { routes: { "GET /api/v1/saved-views": json([SAVED]) } });
    await screen.findByText("Workshop");
    app.route({ "GET /api/v1/saved-views": json({ detail: "temporarily_unavailable" }, 503) });

    await userEvent.click(screen.getByRole("button", { name: "Refresh" }));

    await screen.findByText("Could not read");
    expect(screen.getByLabelText("Views")).toHaveTextContent(/^Workshop$/);
  });
});
