/**
 * Explicit translation must update React surfaces without touching user content.
 * Locale selection is persisted, while inaccessible storage remains non-fatal.
 */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LocaleToggle } from "@/components/locale-toggle";
import { I18nProvider, useI18n, useUiLocale } from "@/lib/i18n";
import { setLocale, uiText } from "@/lib/locale";
import { Modal } from "@/components/ui/modal";

function Probe({ name = "Files" }: { name?: string }) {
  useUiLocale();
  const { t } = useI18n();
  return (
    <section aria-label={uiText("Settings sections")}>
      <h1>{t("auth.welcome")}</h1>
      <p>{name}</p>
      <button title={uiText("New collection")}>{uiText("Storage configuration")}</button>
    </section>
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  setLocale("en");
});

describe("I18nProvider", () => {
  it("defaults a new browser to English regardless of browser language", () => {
    localStorage.clear();
    vi.spyOn(window.navigator, "language", "get").mockReturnValue("es-ES");

    render(
      <I18nProvider>
        <Probe />
      </I18nProvider>,
    );

    expect(screen.getByRole("heading", { name: "Welcome back" })).toBeVisible();
    expect(document.documentElement.lang).toBe("en");
  });

  it("updates explicit messages after selecting a language", async () => {
    localStorage.setItem("printstash.locale", "en");
    render(
      <I18nProvider>
        <LocaleToggle />
        <Probe />
      </I18nProvider>,
    );

    await userEvent.click(screen.getByRole("button", { name: /Language:/ }));
    await userEvent.click(screen.getByRole("menuitemradio", { name: "Español" }));

    expect(screen.getByRole("heading", { name: "Te damos la bienvenida" })).toBeVisible();
    expect(localStorage.getItem("printstash.locale")).toBe("es");
    expect(document.documentElement.lang).toBe("es");
  });

  it("localizes page content and accessible labels", () => {
    localStorage.setItem("printstash.locale", "es");

    render(
      <I18nProvider>
        <Probe />
      </I18nProvider>,
    );

    expect(screen.getByRole("region", { name: "Secciones de ajustes" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Configuración de almacenamiento" })).toHaveAttribute(
      "title",
      "Nueva colección",
    );
  });

  it("preserves user text matching a message identifier", () => {
    localStorage.setItem("printstash.locale", "es");

    render(
      <I18nProvider>
        <Probe name="Files" />
      </I18nProvider>,
    );

    expect(screen.getByText("Files", { exact: true })).toBeVisible();
    expect(screen.queryByText("Archivos", { exact: true })).toBeNull();
  });

  it("localizes a dialog outside the application root", () => {
    localStorage.setItem("printstash.locale", "es");
    const close = vi.fn<() => void>();

    render(
      <I18nProvider>
        <Modal open onClose={close} title={uiText("Settings")}>
          <Probe />
        </Modal>
      </I18nProvider>,
    );

    expect(screen.getByRole("dialog", { name: "Ajustes" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Cerrar" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Te damos la bienvenida" })).toBeVisible();
  });

  it("preserves language when persistence is unavailable", async () => {
    localStorage.setItem("printstash.locale", "en");
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("Storage unavailable");
    });
    render(
      <I18nProvider>
        <LocaleToggle />
        <Probe />
      </I18nProvider>,
    );

    await userEvent.click(screen.getByRole("button", { name: /Language:/ }));
    await userEvent.click(screen.getByRole("menuitemradio", { name: "Español" }));

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Te damos la bienvenida" })).toBeVisible(),
    );
    expect(document.documentElement.lang).toBe("es");
  });
});
