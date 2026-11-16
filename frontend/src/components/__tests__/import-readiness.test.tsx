/** Readiness describes checked evidence without triggering work or exposing admin links. */
import "@testing-library/jest-dom/vitest";
import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ImportReadiness } from "../import-readiness";
import { renderApp, memberSession } from "@/test-support/render";

describe("ImportReadiness", () => {
  it("keeps marketplace access unknown despite configured providers", () => {
    const { requestsWithMethod } = renderApp(
      <ImportReadiness
        devices={[]}
        connections={[{ provider: "cults", connected: true, updated_at: "2026-01-01T00:00:00Z" }]}
        checkedAt={Date.parse("2026-01-01T00:00:00Z")}
      />,
    );
    expect(screen.getByText("Not checked")).toBeVisible();
    expect(screen.getByText("Checked when reserving an upload")).toBeVisible();
    expect(screen.getByText("Responded at the last check")).toBeVisible();
    expect(document.querySelector("time")).toHaveAttribute("datetime", "2026-01-01T00:00:00.000Z");
    expect(screen.getByRole("link", { name: "Backups" })).toHaveAttribute(
      "href",
      "/settings?section=backup",
    );
    expect(requestsWithMethod("POST")).toEqual([]);
  });
  it("keeps administrative actions out of member readiness", () => {
    renderApp(<ImportReadiness devices={[]} connections={[]} checkedAt={Date.now()} />, {
      auth: memberSession(),
    });
    expect(screen.queryByRole("link", { name: "Backups" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Audits" })).not.toBeInTheDocument();
  });
});
