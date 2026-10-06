/** The viewer renders persisted failure reasons before mounting native WebGL. */
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/errors";
import type { getDerivedBlob } from "@/lib/api/request";
import { STLViewer } from "../stl-viewer";

describe("STLViewer", () => {
  afterEach(() => vi.unstubAllGlobals());
  it("passes an explicit preview fetcher into STL preparation", async () => {
    const request = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", request);
    const fetcher = vi
      .fn<typeof getDerivedBlob>()
      .mockRejectedValue(new ApiError(422, "resource_limit", "limited"));
    render(<STLViewer url="/public/stl" previewFetcher={fetcher} />);
    expect(await screen.findByText("3D preview omitted due to memory limits")).toBeVisible();
    expect(fetcher).toHaveBeenCalledOnce();
    expect(request).not.toHaveBeenCalled();
  });

  it("shows a memory refusal", async () => {
    const request = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        new Response(JSON.stringify({ detail: "resource_limit" }), { status: 422 }),
      );
    vi.stubGlobal("fetch", request);

    render(<STLViewer url="/api/v1/files/1/stl" />);

    expect(await screen.findByText("3D preview omitted due to memory limits")).toBeVisible();
  });
});
