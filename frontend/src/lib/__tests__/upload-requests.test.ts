/** Upload requests reach a mounted Library, and report when none can take them. */
import { describe, expect, it, vi } from "vitest";
import { requestUpload, subscribeUploadRequests } from "../upload-requests";

describe("requestUpload", () => {
  it("opens Upload in the mounted Library", () => {
    const open = vi.fn<() => void>();
    const stop = subscribeUploadRequests(open);
    expect(requestUpload()).toBe(true);
    expect(open).toHaveBeenCalledTimes(1);
    stop();
  });

  it("reports that no Library took the request after it unmounts", () => {
    const open = vi.fn<() => void>();
    subscribeUploadRequests(open)();
    expect(requestUpload()).toBe(false);
    expect(open).not.toHaveBeenCalled();
  });
});
