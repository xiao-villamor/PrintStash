/** The loading indicator exposes the selected language to assistive technology. */
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { Spinner } from "../spinner";
import { setLocale } from "@/lib/locale";
afterEach(() => setLocale("en"));
describe("Spinner", () => {
  it("localizes its default accessible name", () => {
    setLocale("es");
    render(<Spinner />);
    expect(screen.getByRole("status", { name: "Cargando" })).toBeVisible();
  });
});
