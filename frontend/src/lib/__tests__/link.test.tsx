/** Internal links preserve Router push/replace history and their complete destination. */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation, useNavigate } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { Link } from "@/lib/link";

function Navigation({ replace }: { replace: boolean }) {
  const location = useLocation();
  const navigate = useNavigate();
  return (
    <>
      <Link href="/models/7?tab=files#preview" replace={replace}>
        Open model
      </Link>
      <button onClick={() => void navigate(-1)}>Back</button>
      <output aria-label="Location">
        {location.pathname}
        {location.search}
        {location.hash}
      </output>
    </>
  );
}

describe("Link", () => {
  it.each([
    {
      replace: false,
      back: "/library",
      behaviour: "follows a library link through client navigation",
    },
    { replace: true, back: "/earlier", behaviour: "replaces the current entry when requested" },
  ])("$behaviour", async ({ replace, back }) => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/earlier", "/library"]}>
        <Navigation replace={replace} />
      </MemoryRouter>,
    );
    expect(screen.getByRole("link", { name: "Open model" })).toHaveAttribute(
      "href",
      "/models/7?tab=files#preview",
    );
    await user.click(screen.getByRole("link", { name: "Open model" }));
    expect(screen.getByLabelText("Location")).toHaveTextContent("/models/7?tab=files#preview");
    await user.click(screen.getByRole("button", { name: "Back" }));
    expect(screen.getByLabelText("Location")).toHaveTextContent(back);
  });
});
