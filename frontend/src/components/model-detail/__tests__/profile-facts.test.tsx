/** Imported estimates retain their source and never become observed print results. */
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ProfileFacts } from "../profile-facts";

describe("profile fact origins", () => {
  it("keeps provider estimates distinct from measured results", () => {
    render(
      <ProfileFacts
        profile={{
          id: "one",
          name: "Shelf",
          origin: "provider",
          author: "Designer",
          estimated_seconds: 30277,
          filament_grams: 433,
          plate_count: 2,
          printer: "P1S",
          materials: ["PLA"],
        }}
      />,
    );
    expect(screen.getByText("Provider estimate")).toBeVisible();
    expect(screen.getByText("505 min")).toBeVisible();
    expect(screen.getByText("433 g")).toBeVisible();
    expect(
      screen.getByText("Imported profile; not a measured or tested print result."),
    ).toBeVisible();
  });
  it("labels file estimates and leaves missing facts unknown", () => {
    render(<ProfileFacts profile={{ id: "two", name: "Second profile", origin: "file" }} />);
    expect(screen.getByText("Estimate included in the file")).toBeVisible();
    expect(screen.getAllByText("Not captured")).toHaveLength(6);
    expect(screen.queryByText(/Provider estimate/)).not.toBeInTheDocument();
  });
});
