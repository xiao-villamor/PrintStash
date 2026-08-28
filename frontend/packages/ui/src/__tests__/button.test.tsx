import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";

import { Button, buttonVariants } from "../components/button";

it("merges variants and caller classes", () => {
  render(<Button className="px-8">Save</Button>);
  expect(screen.getByRole("button", { name: "Save" })).toHaveClass("px-8", "bg-primary");
  expect(buttonVariants({ size: "sm" })).toContain("h-9");
});

it("disables and labels a loading button without replacing its content", () => {
  render(<Button loading>Upload</Button>);
  const button = screen.getByRole("button", { name: "Upload" });
  expect(button).toBeDisabled();
  expect(button.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
});

it("composes styling onto an asChild link", () => {
  render(
    <Button asChild variant="link">
      <a href="/models">Models</a>
    </Button>,
  );
  expect(screen.getByRole("link", { name: "Models" })).toHaveClass("text-primary");
});
