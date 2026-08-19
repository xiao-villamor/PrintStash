import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import { LocaleToggle } from "@/components/locale-toggle";
import { DomLocalization, Localized } from "@/components/ui/localized";
import { translateUiText } from "@/components/ui/localized";
import { I18nProvider, useI18n } from "@/lib/i18n";

function Probe() {
  const { t } = useI18n();
  return <p>{t("auth.welcome")}</p>;
}

it("defaults a new browser to English regardless of browser language", async () => {
  localStorage.clear();
  vi.spyOn(window.navigator, "language", "get").mockReturnValue("es-ES");

  render(
    <I18nProvider>
      <Probe />
    </I18nProvider>,
  );

  expect(screen.getByText("Welcome back")).toBeInTheDocument();
  await waitFor(() => expect(localStorage.getItem("printstash.locale")).toBe("en"));
  expect(document.documentElement.lang).toBe("en");
});

it("persists locale and switches typed messages", async () => {
  localStorage.setItem("printstash.locale", "en");
  render(
    <I18nProvider>
      <LocaleToggle />
      <Probe />
    </I18nProvider>,
  );

  expect(screen.getByText("Welcome back")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /Language/ }));
  expect(screen.getByText("Te damos la bienvenida")).toBeInTheDocument();
  expect(localStorage.getItem("printstash.locale")).toBe("es");
  expect(document.documentElement.lang).toBe("es");
});

it("localizes page content and accessible labels", () => {
  localStorage.setItem("printstash.locale", "es");
  render(
    <I18nProvider>
      <Localized>
        <section aria-label="Settings sections">
          <h1>All Models</h1>
          <p>2 models total</p>
          <button title="New collection">Storage configuration</button>
        </section>
      </Localized>
    </I18nProvider>,
  );

  expect(screen.getByRole("heading", { name: "Todos los modelos" })).toBeInTheDocument();
  expect(screen.getByText("2 modelos en total")).toBeInTheDocument();
  expect(screen.getByRole("region", { name: "Secciones de ajustes" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Configuración de almacenamiento" })).toHaveAttribute(
    "title",
    "Nueva colección",
  );
});

it("only translates complete UI messages and preserves user content", () => {
  expect(translateUiText("es", "All Models")).toBe("Todos los modelos");
  expect(translateUiText("es", "My Models collection")).toBe("My Models collection");
  expect(translateUiText("es", "Model name: Dragon")).toBe("Model name: Dragon");
  expect(translateUiText("es", "2 models total")).toBe("2 modelos en total");
});

it("translates nested legacy component text without rewriting user content", async () => {
  localStorage.setItem("printstash.locale", "es");
  const container = document.createElement("div");
  container.id = "root";
  document.body.append(container);

  function NestedLegacySurface() {
    return <section title="No backups available."><p>No backups available.</p><p>My Models collection</p></section>;
  }

  render(
    <I18nProvider>
      <NestedLegacySurface />
      <DomLocalization />
    </I18nProvider>,
    { container },
  );

  await waitFor(() => expect(screen.getByText("No hay copias disponibles.")).toBeInTheDocument());
  expect(screen.getByText("My Models collection")).toBeInTheDocument();
  expect(screen.getByTitle("No hay copias disponibles.")).toBeInTheDocument();
  container.remove();
});
