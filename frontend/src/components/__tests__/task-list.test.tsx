/**
 * TaskList keeps long-running work understandable after a locale change.
 * It translates controlled task state, recovery copy, and message descriptors while
 * preserving filenames and other user-owned values exactly as they were supplied.
 */
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TaskList } from "@/components/task-list";
import { setLocale, uiMessage } from "@/lib/locale";
import type { TaskItem } from "@/lib/task-center";

function task(overrides: Partial<TaskItem> = {}): TaskItem {
  return {
    id: "task-1",
    title: "Custom task",
    status: "pending",
    progress: 0,
    createdAt: 1,
    updatedAt: 1,
    ...overrides,
  };
}

function renderTaskList(tasks: TaskItem[]) {
  render(
    <MemoryRouter>
      <TaskList tasks={tasks} onClear={vi.fn<() => void>()} />
    </MemoryRouter>,
  );
}

afterEach(() => act(() => setLocale("en")));

describe("TaskList", () => {
  it("updates empty-state copy after a language change", () => {
    renderTaskList([]);
    expect(screen.getByText("No active tasks")).toBeVisible();

    act(() => setLocale("es"));

    expect(screen.getByText("No hay tareas activas")).toBeVisible();
  });

  it("localizes completed task summaries", () => {
    setLocale("es");
    renderTaskList([
      task({
        titleMessage: uiMessage("Upload {value1}", { value1: "Cube {count}" }),
        detail: "Queued",
        detailMessage: uiMessage("Queued"),
        status: "completed",
        progress: 100,
      }),
    ]);

    expect(screen.getByText("Cargar Cube {count}")).toBeVisible();
    expect(screen.getByText("En cola")).toBeVisible();
    expect(screen.getByText("Completado")).toBeVisible();
    expect(screen.getByRole("button", { name: "Borrar completadas" })).toBeVisible();
  });

  it("localizes failed task recovery controls", async () => {
    setLocale("es");
    renderTaskList([
      task({
        status: "failed",
        progress: 100,
        retryable: true,
        thumbnailStatus: "failed",
        failedItems: [
          { name: "Dragon {count}.stl", reason: "backup_blob_missing", retryable: true },
        ],
      }),
    ]);

    expect(screen.getByText("Fallido")).toBeVisible();
    await userEvent.click(screen.getByText("Detalles del elemento fallido"));
    expect(
      screen.getByText(/Falta un archivo necesario para la copia de seguridad\./),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Revisar y reintentar" })).toBeVisible();
    expect(screen.getByRole("link", { name: "Reparar miniatura" })).toBeVisible();
  });

  it("retains failed item names literally", async () => {
    setLocale("es");
    renderTaskList([
      task({
        status: "failed",
        progress: 100,
        failedItems: [
          { name: "Files {count} $&.stl", reason: "backup_blob_missing", retryable: false },
        ],
      }),
    ]);

    await userEvent.click(screen.getByText("Detalles del elemento fallido"));
    expect(screen.getByText("Files {count} $&.stl")).toBeVisible();
  });

  it("localizes indefinite active progress copy", () => {
    setLocale("es");
    renderTaskList([task({ status: "running", progress: 25, total: null })]);

    expect(screen.getByText("En curso")).toBeVisible();
    expect(
      screen.getByText("Calculando el total… Puedes cerrar esta vista sin problema."),
    ).toBeVisible();
  });

  it("offers resume and cancel for a paused durable upload", () => {
    renderTaskList([
      task({
        status: "running",
        progress: 40,
        retryable: true,
        uploadSessionId: "opaque-session-id",
        uploadPaused: true,
      }),
    ]);

    expect(screen.getByText("Paused")).toBeVisible();
    expect(screen.getByText("Resume upload")).toBeVisible();
    expect(screen.getByRole("button", { name: "Cancel upload" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Review and retry" })).toBeNull();
  });
});
