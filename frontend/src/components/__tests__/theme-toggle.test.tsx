import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { ThemeToggle } from "@/components/theme-toggle";

afterEach(() => {
  document.documentElement.classList.remove("dark");
  localStorage.clear();
});

describe("ThemeToggle", () => {
  it("leaves dark mode on the first click when the page painted dark", async () => {
    document.documentElement.classList.add("dark");

    render(<ThemeToggle />);
    await userEvent.click(screen.getByLabelText("Toggle theme"));

    expect(document.documentElement.classList.contains("dark")).toBe(false);
    expect(localStorage.getItem("printstash.theme")).toBe("light");
  });

  it("enters dark mode on the first click when the page painted light", async () => {
    render(<ThemeToggle />);
    await userEvent.click(screen.getByLabelText("Toggle theme"));

    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(localStorage.getItem("printstash.theme")).toBe("dark");
  });
});
