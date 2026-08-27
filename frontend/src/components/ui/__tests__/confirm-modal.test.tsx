import fs from "node:fs";
import path from "node:path";

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ConfirmModal, type ConfirmModalProps } from "@/components/ui/confirm-modal";
import { I18nProvider } from "@/lib/i18n";

function sourceFiles(root: string): string[] {
  return fs.readdirSync(root, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(root, entry.name);
    return entry.isDirectory() ? sourceFiles(full) : /\.(ts|tsx)$/.test(entry.name) ? [full] : [];
  });
}

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

describe("dialog design rules", () => {
  it("never uses browser-native prompt, alert, or confirm dialogs", () => {
    const root = path.resolve(__dirname, "../../..");
    const findings = sourceFiles(root).flatMap((file) => {
      const source = fs.readFileSync(file, "utf8");
      return /window\.(prompt|alert|confirm)\s*\(/.test(source) ? [path.relative(root, file)] : [];
    });
    expect(findings).toEqual([]);
  });
});
