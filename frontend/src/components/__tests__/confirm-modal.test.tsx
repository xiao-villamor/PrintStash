import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { ConfirmModal } from "@/components/ui/confirm-modal";
import { I18nProvider } from "@/lib/i18n";

it("fully localizes and labels a destructive confirmation dialog", () => {
  localStorage.setItem("printstash.locale", "es");

  render(
    <I18nProvider>
      <ConfirmModal
        open
        onClose={vi.fn()}
        onConfirm={vi.fn()}
        title="Permanently delete?"
        description="This will delete the model and all its files immediately. This cannot be undone."
        confirmLabel="Delete forever"
      />
    </I18nProvider>,
  );

  expect(
    screen.getByRole("dialog", { name: "¿Eliminar permanentemente?" }),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Cancelar" })).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Eliminar definitivamente" }),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Cerrar" })).toBeInTheDocument();
});
