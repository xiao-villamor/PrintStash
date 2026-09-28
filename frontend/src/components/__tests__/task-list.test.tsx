/**
 * TaskList keeps long-running work understandable after a locale change.
 * It translates controlled task state, recovery copy, and message descriptors while
 * preserving filenames and other user-owned values exactly as they were supplied.
 */
import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TaskList } from "@/components/task-list";
import { AuthContext, type AuthState } from "@/lib/auth-context";
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

function renderTaskList(tasks: TaskItem[], user: AuthState["user"] = null) {
  render(
    <MemoryRouter>
      <AuthContext.Provider
        value={{
          user,
          loading: false,
          login: async () => {},
          logout: async () => {},
          refresh: async () => {},
        }}
      >
        <TaskList tasks={tasks} onClear={vi.fn<() => void>()} />
      </AuthContext.Provider>
    </MemoryRouter>,
  );
}

afterEach(() => act(() => setLocale("en")));

describe("TaskList", () => {
  it("shows ZIP upload metrics", () => {
    renderTaskList([
      task({
        title: "Prepare large.zip",
        status: "running",
        progress: 45,
        archiveUploading: true,
        archiveSizeBytes: 1024,
        archiveTransferredBytes: 512,
        archiveSpeedBytesPerSecond: 256,
        archiveEtaSeconds: 2,
      }),
    ]);

    expect(screen.getByText("Uploaded 512 B of 1 KB (50%)")).toBeVisible();
    expect(screen.getByText("256 B/s · about 2s remaining")).toBeVisible();
    expect(screen.getByText("Keep this browser tab open during upload.")).toBeVisible();
  });

  it("does not invent a speed or ETA before transfer data arrives", () => {
    renderTaskList([
      task({
        status: "running",
        archiveUploading: true,
        archiveSizeBytes: 1024,
      }),
    ]);

    expect(screen.getByText("Uploading 1 KB. Keep this browser tab open.")).toBeVisible();
    expect(screen.queryByText(/remaining/)).toBeNull();
  });

  it("labels completed browser transfer as awaiting server receipt", () => {
    renderTaskList([
      task({
        title: "Prepare large.zip",
        status: "running",
        progress: 95,
        archiveUploading: true,
        archiveSizeBytes: 1024,
        archiveTransferredBytes: 1024,
        detail: "Finishing transfer to PrintStash. ZIP preparation starts next.",
      }),
    ]);

    expect(screen.getByText("Sent 1 KB from this browser (100%)")).toBeVisible();
    expect(
      screen.getByText("Finishing transfer to PrintStash. ZIP preparation starts next."),
    ).toBeVisible();
  });

  it("links an administrator from a completed library import to preview activity", () => {
    renderTaskList([task({ status: "completed", jobKind: "ingestion.library_import" })], {
      id: 1,
      username: "admin",
      email: null,
      is_superuser: true,
    });

    expect(screen.getByRole("link", { name: "View preview activity" })).toHaveAttribute(
      "href",
      "/settings?section=work",
    );
  });

  it("does not offer administrator job details to a member", () => {
    renderTaskList([task({ status: "completed", jobKind: "ingestion.library_import" })], {
      id: 2,
      username: "member",
      email: null,
      is_superuser: false,
    });

    expect(screen.queryByRole("link", { name: "View preview activity" })).toBeNull();
  });

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

  it("offers paused durable upload controls", () => {
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

  it("clears an empty resume selection", () => {
    renderTaskList([
      task({
        status: "running",
        progress: 40,
        retryable: true,
        uploadSessionId: "opaque-session-id",
        uploadPaused: true,
      }),
    ]);
    const input = screen.getByLabelText("Resume upload");

    fireEvent.change(input, { target: { files: [] } });

    expect(input).toHaveValue("");
  });
});
