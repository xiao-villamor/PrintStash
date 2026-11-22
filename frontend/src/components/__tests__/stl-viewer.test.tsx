/** The viewer renders persisted failure reasons before mounting native WebGL. */
import { render as renderUi, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactElement, ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/errors";
import type { StlParserWorker, StlParseReply } from "@/lib/stl-parser";
import type { getDerivedBlob } from "@/lib/api/request";
import { STLViewer, type STLPreviewFeedback } from "../stl-viewer";

/** The viewer reads the library's preview look through React Query. */
function render(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return renderUi(ui, {
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  });
}

describe("STLViewer", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("reports unreadable geometry to the owning source", async () => {
    const feedback = vi.fn<(value: STLPreviewFeedback) => void>();
    class FailedParser implements StlParserWorker {
      onmessage: ((event: MessageEvent<StlParseReply>) => void) | null = null;
      onerror: ((event: ErrorEvent) => void) | null = null;
      postMessage() {
        this.onmessage?.(
          new MessageEvent("message", {
            data: { state: "failed", reason: "invalid_stl" } satisfies StlParseReply,
          }),
        );
      }
      terminate() {}
    }
    vi.stubGlobal("Worker", FailedParser);
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockImplementation(async () => new Response(new Uint8Array([1]))),
    );
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:unreadable");
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    const fetcher = vi
      .fn<typeof getDerivedBlob>()
      .mockResolvedValue({ ready: true, blob: new Blob(["invalid"]) });
    render(
      <STLViewer url="/api/v1/files/7/stl" previewFetcher={fetcher} onFailureChange={feedback} />,
    );
    await waitFor(() =>
      expect(feedback).toHaveBeenLastCalledWith({
        url: "/api/v1/files/7/stl",
        reason: "invalid_source",
      }),
    );
    expect(fetcher).toHaveBeenCalledOnce();
  });

  it("reports a file failure without requesting it again", async () => {
    const feedback = vi.fn<(value: STLPreviewFeedback) => void>();
    const fetcher = vi
      .fn<typeof getDerivedBlob>()
      .mockRejectedValue(new ApiError(409, "storage_authentication_failed", "private"));
    render(
      <STLViewer url="/api/v1/files/7/stl" previewFetcher={fetcher} onFailureChange={feedback} />,
    );
    await screen.findByText(/provider rejected authentication/);
    // The failure is reported from an effect that may commit after the text.
    await waitFor(() =>
      expect(feedback).toHaveBeenLastCalledWith({
        url: "/api/v1/files/7/stl",
        reason: "storage_authentication_failed",
      }),
    );
    expect(fetcher).toHaveBeenCalledOnce();
  });

  it("clears the old failure while another source loads", async () => {
    const feedback = vi.fn<(value: STLPreviewFeedback) => void>();
    const pending = Promise.withResolvers<Awaited<ReturnType<typeof getDerivedBlob>>>();
    const fetcher = vi
      .fn<typeof getDerivedBlob>()
      .mockRejectedValueOnce(new ApiError(409, "storage_authentication_failed", "private"))
      .mockReturnValue(pending.promise);
    const view = render(
      <STLViewer url="/api/v1/files/7/stl" previewFetcher={fetcher} onFailureChange={feedback} />,
    );
    await screen.findByText(/provider rejected authentication/);
    view.rerender(
      <STLViewer url="/api/v1/files/8/stl" previewFetcher={fetcher} onFailureChange={feedback} />,
    );
    await waitFor(() =>
      expect(feedback).toHaveBeenLastCalledWith({
        url: "/api/v1/files/8/stl",
        reason: null,
      }),
    );
    expect(screen.queryByText(/provider rejected authentication/)).not.toBeInTheDocument();
    view.unmount();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });
  it("passes an explicit preview fetcher into STL preparation", async () => {
    const request = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", request);
    const fetcher = vi
      .fn<typeof getDerivedBlob>()
      .mockRejectedValue(new ApiError(422, "resource_limit", "limited"));
    // A share page carries its look, so the viewer makes no session request.
    render(<STLViewer url="/public/stl" previewFetcher={fetcher} look="slate" />);
    expect(await screen.findByText("3D preview omitted due to memory limits")).toBeVisible();
    expect(fetcher).toHaveBeenCalledOnce();
    expect(request).not.toHaveBeenCalled();
  });

  it.each([
    ["storage_credentials_expired", /credentials have expired/],
    ["storage_authentication_failed", /provider rejected authentication/],
    ["storage_access_denied", /credentials have permission/],
    ["storage_signature_invalid", /request signature/],
    ["remote_storage_metadata_failed", /storage is reachable/],
    ["unrecognised_failure", /This couldn't be produced/],
  ])("shows safe guidance for %s", async (code, message) => {
    const fetcher = vi
      .fn<typeof getDerivedBlob>()
      .mockRejectedValue(new ApiError(422, code, "PrivateProviderDetails"));
    render(<STLViewer url="/api/v1/files/1/stl" previewFetcher={fetcher} />);
    expect(await screen.findByText(message)).toBeVisible();
    expect(screen.getByText("Failed to load 3D preview")).toBeVisible();
    expect(screen.queryByText(/PrivateProviderDetails/)).toBeNull();
    expect(fetcher).toHaveBeenCalledOnce();
  });

  it("shows a memory refusal", async () => {
    const request = vi
      .fn<typeof fetch>()
      .mockImplementation(
        async () => new Response(JSON.stringify({ detail: "resource_limit" }), { status: 422 }),
      );
    vi.stubGlobal("fetch", request);

    render(<STLViewer url="/api/v1/files/1/stl" />);

    expect(await screen.findByText("3D preview omitted due to memory limits")).toBeVisible();
  });

  it("says when the chosen plate has nothing on it", async () => {
    class ParsedOffPlate implements StlParserWorker {
      onmessage: ((event: MessageEvent<StlParseReply>) => void) | null = null;
      onerror: ((event: ErrorEvent) => void) | null = null;
      postMessage() {
        // One triangle over plate 2 (x 307.2–563.2); plate 1 is empty.
        const positions = new Float32Array([400, 10, 0, 410, 10, 0, 400, 20, 0]);
        this.onmessage?.(
          new MessageEvent("message", {
            data: {
              state: "parsed",
              positions,
              normals: new Float32Array(9),
            } satisfies StlParseReply,
          }),
        );
      }
      terminate() {}
    }
    vi.stubGlobal("Worker", ParsedOffPlate);
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockImplementation(async () => new Response(new Uint8Array([1]))),
    );
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:plates");
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    const fetcher = vi
      .fn<typeof getDerivedBlob>()
      .mockResolvedValue({ ready: true, blob: new Blob(["stl"]) });

    render(
      <STLViewer
        url="/api/v1/files/7/stl"
        previewFetcher={fetcher}
        plate={{ min_x: 0, min_y: 0, max_x: 256, max_y: 256 }}
      />,
    );

    expect(await screen.findByText("Nothing is placed on this plate")).toBeVisible();
  });
});
