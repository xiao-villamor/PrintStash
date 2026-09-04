/*
 * The dialog standing between a user and a destructive action.
 *
 * Two things have to hold and both are invisible when they break. Every string is
 * localized — an English "Delete permanently" on a Spanish install is the one
 * place a missing translation costs data rather than polish. And the dialog
 * carries its accessible name and role, because a confirmation a screen reader
 * announces as an unlabelled group is a confirmation its user cannot read before
 * agreeing to it.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ConfirmModal, type ConfirmModalProps } from "@/components/ui/confirm-modal";
import { I18nProvider } from "@/lib/i18n";

describe("ConfirmModal", () => {
  it("fully localizes and labels a destructive confirmation dialog", () => {
    localStorage.setItem("printstash.locale", "es");

    render(
      <I18nProvider>
        <ConfirmModal
          open
          onClose={vi.fn<ConfirmModalProps["onClose"]>()}
          onConfirm={vi.fn<ConfirmModalProps["onConfirm"]>()}
          title="Permanently delete?"
          description="This will delete the model and all its files immediately. This cannot be undone."
          confirmLabel="Delete forever"
        />
      </I18nProvider>,
    );

    expect(screen.getByRole("dialog", { name: "¿Eliminar permanentemente?" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancelar" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Eliminar definitivamente" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cerrar" })).toBeInTheDocument();
  });
});
