/*
 * The one control that switches the app's language.
 *
 * It is a single icon button, which makes its accessible name the entire label:
 * a screen-reader user has no glyph to interpret, so "Language: English" is the
 * only thing telling them what the button is and what state it is in. Losing that
 * leaves an unlabelled button in the header.
 *
 * Every registered language is a directly selectable, named menu item.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { LocaleToggle } from "@/components/locale-toggle";
import { localeDefinitions, SUPPORTED_LOCALES } from "@/lib/locale";
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
    await user.click(screen.getByRole("menuitemradio", { name: "Español" }));

    expect(screen.getByRole("button", { name: /Idioma: Español/ })).toBeInTheDocument();
  });

  it("switches back to English from Spanish", async () => {
    localStorage.setItem("printstash.locale", "es");
    const user = userEvent.setup();
    renderToggle();

    await user.click(screen.getByRole("button", { name: /Idioma/ }));
    await user.click(screen.getByRole("menuitemradio", { name: "English" }));

    expect(screen.getByRole("button", { name: /Language: English/ })).toBeInTheDocument();
  });

  it("remembers the choice for the next visit", () => {
    localStorage.setItem("printstash.locale", "es");

    renderToggle();

    expect(screen.getByRole("button", { name: /Idioma/ })).toBeInTheDocument();
  });
});

describe("LocaleToggle registry", () => {
  it("renders all registered languages as choices", async () => {
    renderToggle();
    await userEvent.click(screen.getByRole("button", { name: /Language/ }));
    for (const locale of SUPPORTED_LOCALES) {
      expect(
        screen.getByRole("menuitemradio", { name: localeDefinitions[locale].name }),
      ).toHaveAttribute("lang", locale);
    }
  });
});
