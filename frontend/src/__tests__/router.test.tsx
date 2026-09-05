/** Manufacturing links resolve both list and historical detail routes in the real application router. */
import { afterAll, describe, expect, it } from "vitest";
import { matchRoutes } from "react-router-dom";
import { router } from "../router";

afterAll(() => router.dispose());

describe("router", () => {
  it("resolves a manufacturing history deep link with its build identity", () => {
    const matches = matchRoutes(router.routes, "/builds/42");
    expect(matches?.at(-1)?.route.path).toBe("builds/:id?");
    expect(matches?.at(-1)?.params.id).toBe("42");
  });
  it("resolves the build list with a source composition query", () => {
    const matches = matchRoutes(router.routes, "/builds?multipart=7");
    expect(matches?.at(-1)?.route.path).toBe("builds/:id?");
  });
});
