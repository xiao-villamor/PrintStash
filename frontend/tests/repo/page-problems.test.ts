/** Network failures need explicit cancellation evidence, regardless of event order. */
import { describe, expect, it } from "vitest";
import { RequestFailures } from "../e2e/_request-failures";

const url = "https://printstash.test/api/v1/outliner/collections?view=all";

describe("page request problems", () => {
  it.each(["before", "after"])(
    "recognizes explicit cancellation %s the browser failure",
    (order) => {
      const problems: string[] = [];
      const requests = new RequestFailures(problems);
      if (order === "before") requests.cancel("GET", url);
      requests.failed("GET", url, "net::ERR_ABORTED");
      if (order === "after") requests.cancel("GET", url);
      expect(problems).toEqual([]);
    },
  );

  it("reports an uncorrelated abort on the same endpoint", () => {
    const problems: string[] = [];
    const requests = new RequestFailures(problems);
    requests.cancel("GET", url);
    requests.failed("GET", url, "net::ERR_ABORTED");
    requests.failed("GET", url, "net::ERR_ABORTED");
    expect(problems).toEqual([`request failed: ${url} net::ERR_ABORTED`]);
  });

  it.each(["net::ERR_CONNECTION_RESET", "net::ERR_FAILED"])(
    "preserves %s despite cancellation",
    (error) => {
      const problems: string[] = [];
      const requests = new RequestFailures(problems);
      requests.cancel("GET", url);
      requests.failed("GET", url, error);
      expect(problems).toEqual([`request failed: ${url} ${error}`]);
    },
  );

  it("does not confuse another URL or HTTP method with the canceled request", () => {
    const problems: string[] = [];
    const requests = new RequestFailures(problems);
    requests.cancel("GET", url);
    requests.failed("POST", url, "net::ERR_ABORTED");
    requests.failed("GET", `${url}&favorites=true`, "net::ERR_ABORTED");
    expect(problems).toHaveLength(2);
  });
});
