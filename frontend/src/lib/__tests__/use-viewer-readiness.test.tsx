/**
 * The 3D loader can resolve synchronously from its geometry cache. These tests
 * pin URL-specific readiness so a late reset cannot cover an already-rendered
 * model with a permanent loading indicator.
 */
import { useEffect } from "react";
import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useViewerReadiness } from "@/lib/use-viewer-readiness";

function CachedLoader({ onLoaded }: { onLoaded: (loaded: boolean) => void }) {
  useEffect(() => onLoaded(true), [onLoaded]);
  return null;
}

function ViewerHarness({ activeUrl }: { activeUrl: string }) {
  const readiness = useViewerReadiness(activeUrl);
  return (
    <>
      <output aria-label="preview readiness">{readiness.loaded ? "ready" : "loading"}</output>
      <CachedLoader onLoaded={readiness.setLoaded} />
    </>
  );
}

describe("useViewerReadiness", () => {
  it("marks the active preview as loaded", () => {
    const { result } = renderHook(() => useViewerReadiness("/files/1/stl"));

    act(() => result.current.setLoaded(true));

    expect(result.current.loaded).toBe(true);
  });

  it("keeps a synchronously cached preview ready after mount", async () => {
    render(<ViewerHarness activeUrl="/files/1/stl" />);

    await waitFor(() => {
      expect(screen.getByRole("status", { name: "preview readiness" })).toHaveTextContent("ready");
    });
  });

  it("clears readiness when a different preview becomes active", () => {
    const { result, rerender } = renderHook(({ activeUrl }) => useViewerReadiness(activeUrl), {
      initialProps: { activeUrl: "/files/1/stl" },
    });
    act(() => result.current.setLoaded(true));

    rerender({ activeUrl: "/files/2/stl" });

    expect(result.current.loaded).toBe(false);
  });

  it("clears readiness when the active preview reports a failure", () => {
    const { result } = renderHook(() => useViewerReadiness("/files/1/stl"));
    act(() => result.current.setLoaded(true));

    act(() => result.current.setLoaded(false));

    expect(result.current.loaded).toBe(false);
  });
});
