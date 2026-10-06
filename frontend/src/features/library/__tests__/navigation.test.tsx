/** Return links carry view identity without changing native link gestures. */
import "@testing-library/jest-dom/vitest";
import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { LibraryBackLink, LibraryItemLink } from "../navigation";
import { useLibraryEntry } from "../navigation-state";
import { renderApp } from "@/test-support/render";
import { useLocation } from "react-router-dom";

function Library() {
  const location = useLocation();
  const entry = useLibraryEntry("/?type=all&sort=name-asc&tag=Desk", true);
  return (
    <>
      <LibraryItemLink href="/models/1" origin={entry}>
        Open Model
      </LibraryItemLink>
      <span>{location.pathname}</span>
    </>
  );
}

describe("Library navigation", () => {
  it("encodes a complete return view in native item links", () => {
    renderApp(<Library />);
    const link = screen.getByRole("link", { name: "Open Model" });
    expect(link).toHaveAttribute(
      "href",
      "/models/1?return=%2F%3Ftype%3Dall%26sort%3Dname-asc%26tag%3DDesk",
    );
  });

  it.each([{ ctrlKey: true }, { metaKey: true }, { button: 1 }])(
    "preserves modified-link navigation %j",
    (gesture) => {
      renderApp(<Library />);
      const link = screen.getByRole("link", { name: "Open Model" });
      const native = fireEvent.click(link, gesture);
      expect(native).toBe(true);
      expect(screen.getByText("/")).toBeVisible();
    },
  );

  it.each([
    { value: "/?type=multipart&sort=name-asc", target: "/?type=multipart&sort=name-asc" },
    { value: "//outside.example/", target: "/?c=parts" },
    { value: "https://outside.example/", target: "/?c=parts" },
    { value: "/settings", target: "/?c=parts" },
    { value: "", target: "/?c=parts" },
  ])("chooses a safe fallback for direct detail links: $value", ({ value, target }) => {
    renderApp(<LibraryBackLink href="/?c=parts">Back</LibraryBackLink>, {
      at: `/models/1?${new URLSearchParams({ return: value })}`,
    });
    expect(screen.getByRole("link", { name: "Back" })).toHaveAttribute("href", target);
  });
});
