/*
 * How a failure reaches the user.
 *
 * `toast.error` is handed two different kinds of thing, and they need opposite
 * treatment. A thrown `ApiError` carries a machine code that has to be
 * translated into a sentence somebody can act on. But a dozen call sites pass a
 * literal message they wrote themselves — "Enter a temperature between 0 and
 * 500." — and translating *that* destroys it: free text matches no HTTP envelope
 * and no detail code, so the parser falls back to `unknown` and the user is told
 * the server is unreachable because they typed a letter into a number field.
 *
 * A detail code is a single snake_case token by construction, which is what
 * makes the two separable at all.
 *
 * These render the real `Toaster`, so what is asserted is the text on screen
 * rather than the argument some library function received.
 */

import "@testing-library/jest-dom/vitest";
import { act, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/errors";
import { toast } from "@/lib/toast";
import { renderApp } from "@/test-support/render";

/** Mount the toaster, then raise one error through it. */
function raise(cause: unknown) {
  renderApp(<p>the page</p>);
  act(() => {
    toast.error(cause);
  });
}

const NETWORK_COPY = /Check that PrintStash is running/;

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("toast.error", () => {
  describe("a message the app wrote", () => {
    it("shows it as written", async () => {
      // Translating it destroys the only information the user can act on.
      raise("Enter a temperature between 0 and 500.");

      expect(await screen.findByText("Enter a temperature between 0 and 500.")).toBeInTheDocument();
    });

    it("does not replace it with the generic network copy", async () => {
      raise("Name and folder path are required.");

      await screen.findByText("Name and folder path are required.");
      expect(screen.queryByText(NETWORK_COPY)).toBeNull();
    });
  });

  describe("a code the server sent", () => {
    it("translates a bare detail code into a sentence", async () => {
      // Background jobs report a bare code with no HTTP envelope around it.
      raise("model_not_found");

      expect(await screen.findByText("This model no longer exists.")).toBeInTheDocument();
    });

    it("translates a thrown ApiError", async () => {
      raise(new ApiError(404, "model_not_found", "not found"));

      expect(await screen.findByText("This model no longer exists.")).toBeInTheDocument();
    });

    it("humanises a code this build has no wording for", async () => {
      // A blank toast is worse than an awkward one: the user learns nothing.
      raise("root_path_missing");

      expect(await screen.findByText("Root path missing.")).toBeInTheDocument();
    });
  });

  describe("something else entirely", () => {
    it("falls back to the network copy for a thrown non-Error", async () => {
      raise({ nope: true });

      expect(await screen.findByText(NETWORK_COPY)).toBeInTheDocument();
    });
  });
});
