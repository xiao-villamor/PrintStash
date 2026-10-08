/** Protected image ownership preserves visible URLs and bounds authorized work. */
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Cover } from "@/components/multipart-model-presentation";
import { SearchSubjectPreview } from "@/components/search-evidence";
import { ProtectedThumbnail } from "@/components/protected-thumbnail";
import { retirePrivateSessionScope } from "@/lib/auth-store";

beforeEach(() => {
  vi.stubGlobal("IntersectionObserver", undefined);
  vi.stubGlobal("URL", {
    createObjectURL: () => "blob:decoded",
    revokeObjectURL: vi.fn<(url: string) => void>(),
  });
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>(async () => new Response("png")),
  );
});
afterEach(() => {
  cleanup();
  retirePrivateSessionScope();
  vi.unstubAllGlobals();
});
describe("ProtectedThumbnail", () => {
  it("starts an admitted image without another visibility gate", async () => {
    render(
      <ProtectedThumbnail
        path="/admitted/thumbnail"
        alt="Admitted"
        placeholder={<span>Missing</span>}
      />,
    );

    const image = await screen.findByAltText("Admitted");

    expect(image).toHaveAttribute("loading", "eager");
  });

  it("waits for load when early decoding cannot start yet", async () => {
    const decoder = vi
      .fn<() => Promise<void>>()
      .mockRejectedValueOnce(new DOMException("Loading not started", "EncodingError"))
      .mockResolvedValue(undefined);
    const descriptor = Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, "decode");
    Object.defineProperty(HTMLImageElement.prototype, "decode", {
      configurable: true,
      value: decoder,
    });
    try {
      render(
        <ProtectedThumbnail
          path="/pending/decode"
          alt="Pending"
          placeholder={<span>Missing</span>}
        />,
      );
      const image = await screen.findByAltText("Pending");
      await waitFor(() => expect(decoder).toHaveBeenCalledOnce());
      expect(image.parentElement?.getAttribute("data-library-thumbnail")).toBe("pending");
      await act(async () => fireEvent.load(image));
      expect(image.parentElement?.getAttribute("data-library-thumbnail")).toBe("ready");
    } finally {
      if (descriptor) Object.defineProperty(HTMLImageElement.prototype, "decode", descriptor);
      else Reflect.deleteProperty(HTMLImageElement.prototype, "decode");
    }
  });

  it("publishes a decoded cached image without another load event", async () => {
    const decoded = Promise.withResolvers<void>();
    const descriptor = Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, "decode");
    Object.defineProperty(HTMLImageElement.prototype, "decode", {
      configurable: true,
      value: () => decoded.promise,
    });
    try {
      render(
        <ProtectedThumbnail
          path="/cached/decode"
          alt="Cached"
          placeholder={<span>Missing</span>}
        />,
      );
      const image = await screen.findByAltText("Cached");
      expect(image.parentElement?.getAttribute("data-library-thumbnail")).toBe("pending");
      await act(async () => decoded.resolve());
      await waitFor(() =>
        expect(image.parentElement?.getAttribute("data-library-thumbnail")).toBe("ready"),
      );
    } finally {
      if (descriptor) Object.defineProperty(HTMLImageElement.prototype, "decode", descriptor);
      else Reflect.deleteProperty(HTMLImageElement.prototype, "decode");
    }
  });

  it("reports an image ready only after decode", async () => {
    const decoded = Promise.withResolvers<void>();
    render(<ProtectedThumbnail path="/thumbnail" alt="Part" placeholder={<span>Missing</span>} />);
    const image = await screen.findByAltText("Part");
    Object.defineProperty(image, "decode", { value: () => decoded.promise });
    expect(image.parentElement?.getAttribute("data-library-thumbnail")).toBe("pending");
    fireEvent.load(image);
    expect(image.parentElement?.getAttribute("data-library-thumbnail")).toBe("pending");
    await act(async () => decoded.resolve());
    await waitFor(() =>
      expect(image.parentElement?.getAttribute("data-library-thumbnail")).toBe("ready"),
    );
  });
  it("reports failed protected downloads explicitly", async () => {
    vi.mocked(fetch).mockResolvedValue(new Response("unavailable", { status: 503 }));
    render(
      <ProtectedThumbnail path="/failed/download" alt="Part" placeholder={<span>Missing</span>} />,
    );
    await waitFor(() =>
      expect(
        screen.getByText("Missing").parentElement?.getAttribute("data-library-thumbnail"),
      ).toBe("failed"),
    );
    expect(screen.queryByAltText("Part")).toBeNull();
  });
  it("reports image element failures explicitly", async () => {
    render(
      <ProtectedThumbnail
        path="https://example.test/broken.png"
        alt="Part"
        placeholder={<span>Missing</span>}
      />,
    );
    const image = await screen.findByAltText("Part");
    fireEvent.error(image);
    expect(image.parentElement?.getAttribute("data-library-thumbnail")).toBe("failed");
  });
  it("keeps missing image semantics", () => {
    render(<ProtectedThumbnail path={null} alt="Part" placeholder={<span>Missing</span>} />);
    expect(screen.getByText("Missing").parentElement?.getAttribute("data-library-thumbnail")).toBe(
      "missing",
    );
    expect(screen.queryByAltText("Part")).toBeNull();
  });
  it("keeps external covers outside authenticated transport", async () => {
    render(
      <ProtectedThumbnail
        path="https://example.test/cover.png"
        alt="Cover"
        placeholder={<span>Missing</span>}
      />,
    );
    const image = await screen.findByAltText("Cover");
    expect(image.getAttribute("src")).toBe("https://example.test/cover.png");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("leases Multipart covers through viewport admission", async () => {
    render(<Cover src="/multipart/cover" alt="Multipart cover" />);
    const image = await screen.findByAltText("Multipart cover");
    expect(image.getAttribute("src")).toBe("blob:decoded");
    expect(fetch).toHaveBeenCalledOnce();
  });

  it("leases Search previews through viewport admission", async () => {
    render(<SearchSubjectPreview path="/search/preview" subjectType="document" />);
    await waitFor(() =>
      expect(document.querySelector("img")?.getAttribute("src")).toBe("blob:decoded"),
    );
    expect(fetch).toHaveBeenCalledOnce();
  });

  it("reports failed decoding explicitly", async () => {
    render(
      <ProtectedThumbnail path="/failed/decode" alt="Failed" placeholder={<span>Missing</span>} />,
    );
    const image = await screen.findByAltText("Failed");
    Object.defineProperty(image, "decode", {
      value: async () => {
        throw new Error("decode failed");
      },
    });
    await act(async () => fireEvent.load(image));
    expect(image.parentElement?.getAttribute("data-library-thumbnail")).toBe("failed");
  });

  it("ignores a previous URL decode after image reassignment", async () => {
    let next = 0;
    vi.stubGlobal("URL", {
      createObjectURL: () => `blob:switched-${++next}`,
      revokeObjectURL: vi.fn<(url: string) => void>(),
    });
    const old = Promise.withResolvers<void>();
    const page = render(
      <ProtectedThumbnail path="/decode/A" alt="Part" placeholder={<span>Missing</span>} />,
    );
    const first = await screen.findByAltText("Part");
    Object.defineProperty(first, "decode", { value: () => old.promise });
    fireEvent.load(first);
    page.rerender(
      <ProtectedThumbnail path="/decode/B" alt="Part" placeholder={<span>Missing</span>} />,
    );
    await waitFor(() =>
      expect(screen.getByAltText("Part").getAttribute("src")).toBe("blob:switched-2"),
    );
    const current = screen.getByAltText("Part");
    await act(async () => old.resolve());
    expect(current.parentElement?.getAttribute("data-library-thumbnail")).toBe("pending");
    Object.defineProperty(current, "decode", { value: () => Promise.resolve() });
    await act(async () => fireEvent.load(current));
    expect(current.parentElement?.getAttribute("data-library-thumbnail")).toBe("ready");
  });
});
