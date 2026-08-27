import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { ThemeToggle } from "@/components/theme-toggle";

const root = resolve(import.meta.dirname, "../../..");

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

describe("theme-aware favicon", () => {
  it("uses current blue brand in dark mode and versioned asset URLs", () => {
    const darkIcon = readFileSync(resolve(root, "public/icon-dark.svg"), "utf8");
    const html = readFileSync(resolve(root, "index.html"), "utf8");
    const toggle = readFileSync(resolve(root, "src/components/theme-toggle.tsx"), "utf8");

    expect(darkIcon).toContain("#2767FF");
    expect(darkIcon).toContain("#0E48F0");
    expect(darkIcon).not.toMatch(/#fb923c|#ea580c/i);
    expect(html).toContain("/icon-dark.svg?v=2");
    expect(toggle).toContain("/icon-dark.svg?v=2");
  });
});
