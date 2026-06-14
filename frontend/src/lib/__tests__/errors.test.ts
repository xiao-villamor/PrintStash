import { describe, expect, it } from "vitest";

import {
  ApiError,
  getErrorMessage,
  parseApiError,
  userMessage,
} from "@/lib/errors";

describe("ApiError", () => {
  it("flags 401 as an auth error", () => {
    expect(new ApiError(401, "not_authenticated", "").isAuthError).toBe(true);
    expect(new ApiError(500, "boom", "").isAuthError).toBe(false);
  });

  it("flags the 'already_configured' 409 as unconfigured", () => {
    expect(new ApiError(409, "already_configured", "").isUnconfigured).toBe(true);
    expect(new ApiError(409, "duplicate_slug", "").isUnconfigured).toBe(false);
    expect(new ApiError(400, "already_configured", "").isUnconfigured).toBe(false);
  });
});

describe("parseApiError", () => {
  it("returns the same instance when given an ApiError", () => {
    const original = new ApiError(404, "model_not_found", "body");
    expect(parseApiError(original)).toBe(original);
  });

  it("extracts status and the FastAPI detail code from an HTTP message", () => {
    const err = parseApiError(
      new Error('HTTP 404: {"detail":"model_not_found"}'),
    );
    expect(err.status).toBe(404);
    expect(err.code).toBe("model_not_found");
  });

  it("falls back to the status string when the body is not JSON", () => {
    const err = parseApiError(new Error("HTTP 502: upstream is down"));
    expect(err.status).toBe(502);
    expect(err.code).toBe("502");
    expect(err.detail).toBe("upstream is down");
  });

  it("returns a status-0 'unknown' error for unrecognised input", () => {
    const err = parseApiError("a plain string");
    expect(err.status).toBe(0);
    expect(err.code).toBe("unknown");
    expect(err.detail).toBe("a plain string");
  });

  it("handles non-error, non-string values", () => {
    const err = parseApiError({ weird: true });
    expect(err.code).toBe("unknown");
    expect(err.detail).toBe("Unknown error");
  });
});

describe("getErrorMessage / userMessage", () => {
  it("maps known codes to friendly copy", () => {
    expect(getErrorMessage("invalid_credentials")).toBe(
      "Invalid username or password.",
    );
    expect(getErrorMessage("collection_not_empty")).toMatch(/still has models/);
  });

  it("humanises unknown codes by replacing underscores", () => {
    expect(getErrorMessage("some_new_code")).toBe("some new code");
  });

  it("userMessage parses then maps in one step", () => {
    expect(
      userMessage(new Error('HTTP 401: {"detail":"invalid_credentials"}')),
    ).toBe("Invalid username or password.");
  });
});
