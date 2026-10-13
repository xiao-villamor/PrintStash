/*
 * The G-code preview, reachable by keyboard and honest about failure.
 *
 * The layer control is a slider a user scrubs, so it carries a real accessible
 * name and value: without them the only way to change layers is dragging, and the
 * viewer becomes mouse-only. The travel and bed toggles expose pressed state for
 * the same reason — a toggle whose state is only a colour is a toggle a screen
 * reader reports as a plain button.
 *
 * The 401 case is the security half. A download token can expire while the viewer
 * is open, and the raw failure carries the request URL and its token. What the
 * user must see is a sentence about signing in again; what must not reach the DOM
 * is the credential.
 */

import "@testing-library/jest-dom/vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setEventSocketFactory, type EventSocket, type EventSocketFactory } from "@/lib/events";
import { storeLogin } from "@/lib/auth-store";
import type { getDerivedText } from "@/lib/api/request";
import { ApiError } from "@/lib/errors";
import { parseGcode } from "@/lib/gcode";

import { GcodeViewer } from "@/components/gcode-viewer";
import { I18nProvider } from "@/lib/i18n";

const fetchMock = vi.fn<typeof fetch>();

vi.stubGlobal("fetch", fetchMock);

function TestCanvas(_props: { children: React.ReactNode }) {
  return <div data-testid="canvas" />;
}

const TOOLPATH = "G90\nM82\nG1 Z0.2\nG1 X10 Y0 E0.1\nG1 X20 Y0 E0.2\n";

function textResponse(text: string, status = 200): Response {
  return new Response(text, { status });
}

beforeEach(() => {
  setEventSocketFactory(async () => ({
    onopen: null,
    onclose: null,
    onmessage: null,
    send() {},
    close() {},
  }));
  fetchMock.mockReset();
  window.localStorage.clear();
});

describe("GcodeViewer", () => {
  it("keeps a public Gcode viewer outside private events", async () => {
    storeLogin("", { id: 1, username: "owner", email: null, is_superuser: true });
    const factory = vi.fn<EventSocketFactory>(async () => ({
      onopen: null,
      onclose: null,
      onmessage: null,
      send() {},
      close() {},
    }));
    setEventSocketFactory(factory);
    const fetcher = vi
      .fn<typeof getDerivedText>()
      .mockResolvedValue({ ready: true, text: TOOLPATH });
    render(
      <GcodeViewer
        url="/public/toolpath"
        toolpathFetcher={fetcher}
        privateEventsEnabled={false}
        canvasRenderer={TestCanvas}
        toolpathParser={async (text) => parseGcode(text)}
      />,
    );
    expect(await screen.findByRole("slider", { name: "Current layer" })).toBeInTheDocument();
    expect(factory).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("hides a toolpath when its fetcher changes", async () => {
    const first = vi.fn<typeof getDerivedText>().mockResolvedValue({ ready: true, text: TOOLPATH });
    const pending = Promise.withResolvers<Awaited<ReturnType<typeof getDerivedText>>>();
    const second = vi.fn<typeof getDerivedText>().mockReturnValue(pending.promise);
    const parser = async (text: string) => parseGcode(text);
    const { rerender, unmount } = render(
      <GcodeViewer
        url="/public/toolpath"
        toolpathFetcher={first}
        privateEventsEnabled={false}
        canvasRenderer={TestCanvas}
        toolpathParser={parser}
      />,
    );
    expect(await screen.findByRole("slider", { name: "Current layer" })).toBeInTheDocument();
    rerender(
      <GcodeViewer
        url="/public/toolpath"
        toolpathFetcher={second}
        privateEventsEnabled={false}
        canvasRenderer={TestCanvas}
        toolpathParser={parser}
      />,
    );
    expect(screen.queryByRole("slider", { name: "Current layer" })).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Loading");
    unmount();
    await act(async () => pending.resolve({ ready: false, state: "running" }));
  });

  it("exposes an accessible layer slider and pressed states for travel/bed toggles", async () => {
    const user = userEvent.setup();
    fetchMock.mockResolvedValue(textResponse(TOOLPATH));

    render(
      <I18nProvider>
        <GcodeViewer
          url="/api/v1/files/7/toolpath-preview"
          printerBedMm={{ x: 220, y: 220 }}
          canvasRenderer={TestCanvas}
          toolpathParser={async (text) => parseGcode(text)}
        />
      </I18nProvider>,
    );

    const slider = await screen.findByRole("slider", { name: "Current layer" });
    expect(slider).toHaveAttribute("max", "0");
    expect(screen.getByText(/Layer 1 \/ 1/)).toBeInTheDocument();

    const travel = screen.getByRole("button", { name: "Show travel moves" });
    const bed = screen.getByRole("button", { name: "Hide build plate" });
    expect(travel).toHaveAttribute("aria-pressed", "false");
    expect(bed).toHaveAttribute("aria-pressed", "true");

    await user.click(travel);
    await user.click(bed);

    expect(screen.getByRole("button", { name: "Hide travel moves" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: "Show build plate" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("localizes controls and presents a human error after a 401 without leaking auth data", async () => {
    localStorage.setItem("printstash.locale", "es");
    localStorage.setItem(
      "printstash.user",
      JSON.stringify({ id: 1, username: "tester", email: null, is_superuser: true }),
    );
    localStorage.setItem("printstash.token", "must-not-leak");
    fetchMock.mockResolvedValue(textResponse('{"detail":"not_authenticated"}', 401));
    const unauthorized = vi.fn<(event: Event) => void>();
    window.addEventListener("printstash:unauthorized", unauthorized);

    render(
      <I18nProvider>
        <GcodeViewer
          url="/api/v1/files/7/toolpath-preview"
          printerBedMm={{ x: 220, y: 220 }}
          canvasRenderer={TestCanvas}
          toolpathParser={async (text) => parseGcode(text)}
        />
      </I18nProvider>,
    );

    expect(
      await screen.findByText("No se pudo cargar la vista previa de la trayectoria."),
    ).toBeInTheDocument();
    expect(unauthorized).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/files/7/toolpath-preview",
      expect.objectContaining({ headers: {} }),
    );
    expect(JSON.stringify(fetchMock.mock.calls[0])).not.toContain("must-not-leak");
    window.removeEventListener("printstash:unauthorized", unauthorized);
  });
  it("retries a busy converter through the same preview", async () => {
    const user = userEvent.setup();
    fetchMock
      .mockResolvedValueOnce(textResponse('{"detail":"toolpath_busy"}', 429))
      .mockResolvedValueOnce(textResponse(TOOLPATH));
    render(
      <I18nProvider>
        <GcodeViewer
          url="/api/v1/files/7/toolpath"
          canvasRenderer={TestCanvas}
          toolpathParser={async (text) => parseGcode(text)}
        />
      </I18nProvider>,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("Other previews are being prepared");
    await user.click(screen.getByRole("button", { name: "Try preview again" }));
    expect(await screen.findByRole("slider", { name: "Current layer" })).toBeVisible();
  });
  it.each([413, 504])("explains a server resource limit (%s)", async (status) => {
    fetchMock.mockResolvedValue(textResponse('{"detail":"toolpath_limit"}', status));
    render(
      <I18nProvider>
        <GcodeViewer url="/api/v1/files/7/toolpath" canvasRenderer={TestCanvas} />
      </I18nProvider>,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("size or time limit");
  });
  it("refuses invalid converted bytes without suggesting a successful preview", async () => {
    fetchMock.mockResolvedValue(textResponse('{"detail":"toolpath_invalid_bgcode"}', 422));
    render(
      <I18nProvider>
        <GcodeViewer url="/api/v1/files/7/toolpath" canvasRenderer={TestCanvas} />
      </I18nProvider>,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("could not be validated");
    expect(screen.queryByRole("slider")).not.toBeInTheDocument();
  });
  it("waits for a binary toolpath still being derived instead of parsing the notice", async () => {
    // The server converts binary G-code in the background and answers 202 with
    // the derivative's state until it is ready; the notice is not G-code.
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ state: "running" }), {
        status: 202,
        headers: { "content-type": "application/json" },
      }),
    );
    render(
      <I18nProvider>
        <GcodeViewer url="/api/v1/files/7/toolpath" canvasRenderer={TestCanvas} />
      </I18nProvider>,
    );

    expect(await screen.findByRole("status")).toHaveTextContent("Preparing toolpath");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
  it("shows the toolpath once its derivative is ready", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      fetchMock
        .mockResolvedValueOnce(
          new Response(JSON.stringify({ state: "pending" }), {
            status: 202,
            headers: { "content-type": "application/json" },
          }),
        )
        .mockResolvedValueOnce(textResponse(TOOLPATH));
      render(
        <I18nProvider>
          <GcodeViewer
            url="/api/v1/files/7/toolpath"
            canvasRenderer={TestCanvas}
            toolpathParser={async (text) => parseGcode(text)}
          />
        </I18nProvider>,
      );
      await screen.findByRole("status");

      await vi.advanceTimersByTimeAsync(3_000);

      expect(await screen.findByRole("slider", { name: "Current layer" })).toBeVisible();
      expect(fetchMock).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("disabled toolpaths", () => {
  it("resumes a disabled viewer on a policy notice or resync", async () => {
    storeLogin("", { id: 1, username: "owner", email: null, is_superuser: true });
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const socket: EventSocket = {
      onopen: null,
      onclose: null,
      onmessage: null,
      send() {},
      close() {},
    };
    setEventSocketFactory(async () => socket);
    const fetcher = vi
      .fn<NonNullable<import("@/components/gcode-viewer").GcodeViewerProps["toolpathFetcher"]>>()
      .mockRejectedValue(new ApiError(409, "derivative_group_disabled", "disabled"));
    render(
      <I18nProvider>
        <GcodeViewer
          url="/toolpath"
          canvasRenderer={TestCanvas}
          toolpathFetcher={fetcher}
          toolpathParser={async (text) => parseGcode(text)}
        />
      </I18nProvider>,
    );
    try {
      expect(await screen.findByRole("status")).toHaveTextContent(
        "Toolpath preview processing is disabled.",
      );
      expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(10_000);
      });
      expect(fetcher).toHaveBeenCalledTimes(1);
      fetcher.mockResolvedValue({ ready: true, text: TOOLPATH });
      await act(async () => {
        socket.onmessage?.({ data: JSON.stringify({ type: "derivative_policy" }) });
      });
      expect(await screen.findByRole("slider", { name: "Current layer" })).toBeVisible();
      await act(async () => {
        socket.onmessage?.({ data: JSON.stringify({ type: "resync" }) });
      });
      await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(3));
    } finally {
      vi.useRealTimers();
    }
  });
});
