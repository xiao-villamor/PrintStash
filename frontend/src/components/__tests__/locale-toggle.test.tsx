/*
 * The one control that switches the app's language.
 *
 * It is a single icon button, which makes its accessible name the entire label:
 * a screen-reader user has no glyph to interpret, so "Language: English" is the
 * only thing telling them what the button is and what state it is in. Losing that
 * leaves an unlabelled button in the header.
 *
 * It is also a toggle rather than a picker, so the *next* locale has to be
 * derived from the current one. A toggle that always sets Spanish is a one-way
 * door, and the user cannot get back to English without clearing storage.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { LocaleToggle } from "@/components/locale-toggle";
import { I18nProvider } from "@/lib/i18n";

function renderToggle() {
  return render(
    <I18nProvider>
      <LocaleToggle />
    </I18nProvider>,
  );
}

beforeEach(() => {
  localStorage.setItem("printstash.locale", "en");
});

describe("LocaleToggle", () => {
  it("names itself with the language currently in use", () => {
    renderToggle();

    expect(screen.getByRole("button", { name: /Language: English/ })).toBeInTheDocument();
  });

  it("switches to Spanish from English", async () => {
    const user = userEvent.setup();
    renderToggle();

    await user.click(screen.getByRole("button", { name: /Language/ }));

    expect(screen.getByRole("button", { name: /Idioma: Español/ })).toBeInTheDocument();
  });

  it("switches back to English from Spanish", async () => {
    localStorage.setItem("printstash.locale", "es");
    const user = userEvent.setup();
    renderToggle();

    await user.click(screen.getByRole("button", { name: /Idioma/ }));

    expect(screen.getByRole("button", { name: /Language: English/ })).toBeInTheDocument();
  });

  it("remembers the choice for the next visit", () => {
    localStorage.setItem("printstash.locale", "es");

    renderToggle();

    expect(screen.getByRole("button", { name: /Idioma/ })).toBeInTheDocument();
  });
});
