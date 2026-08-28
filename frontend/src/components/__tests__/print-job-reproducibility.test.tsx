/*
 * Whether the exact file that produced a print can still be downloaded — and
 * saying so honestly when it cannot.
 *
 * This panel makes a claim about the past, so every level of evidence is
 * distinct: an archived artifact we hold the bytes for, a path the printer
 * reported, a project preview URL, and nothing at all. Only the first is
 * downloadable. Blurring those is the failure — offering a download for a file we
 * do not have gives the user a broken link where they expected the G-code that
 * made the part in their hand.
 *
 * A URL from a *failed* capture is blocked outright. It looks like the others and
 * points at bytes nobody verified.
 *
 * The error-code rows are about not showing the machinery: a legacy code has to
 * become a sentence, and a backend message that already contains the code must not
 * render it twice. The Spanish rows exist because these strings are assembled
 * rather than looked up, which is exactly where a translation gets skipped — and
 * the portaled toolpath preview renders outside the tree, so it needs the i18n
 * context threaded explicitly or it comes out English on every install.
 */

import "@testing-library/jest-dom/vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { PrintJobReproducibility } from "@/components/print-job-reproducibility";
import { DomLocalization, Localized } from "@/components/ui/localized";
import { I18nProvider } from "@/lib/i18n";
import {
  resolvePrintJobReproducibility,
  type PrintJobReproducibilityInput,
} from "@/lib/print-job-reproducibility";

type DownloadFile = (path: string) => Promise<void>;
const downloadAuthenticatedFile = vi.fn<DownloadFile>().mockResolvedValue(undefined);

function TestToolpathViewer({ url }: { url: string }) {
  return <div data-testid="toolpath-viewer">Authenticated toolpath: {url}</div>;
}

function makeJob(
  overrides: Partial<PrintJobReproducibilityInput> = {},
): PrintJobReproducibilityInput {
  return {
    source: "external",
    file_id: 7,
    remote_filename: "cache/benchy.gcode",
    artifact_evidence: "metadata_only",
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.removeItem("printstash.locale");
});

describe("PrintJobReproducibility", () => {
  it("prefers the nested contract and keeps the reported display name separate from the file", () => {
    const job = makeJob({
      external_display_name: "Bambu project label",
      reproducibility: {
        level: "metadata",
        identity: {
          display_name: "Bambu project label",
          task_id: "task-42",
          subtask_id: "subtask-9",
          project_id: "project-7",
          profile_id: "profile-4",
          gcode_file: "cache/real-plate.gcode",
          plate_index: 2,
        },
        metadata: { current_layer: 14, total_layers: 80, nozzle_diameter: 0.4 },
        error: { code: "bambu_ftps_unavailable", message: "The printer cache is unavailable." },
        download_url: "/api/v1/files/7/download",
      },
      // These legacy fields deliberately disagree with the nested object.
      external_gcode_file: "cache/stale.gcode",
      download_url: "/api/v1/files/old/download",
    });

    const resolved = resolvePrintJobReproducibility(job);
    expect(resolved.level).toBe("metadata");
    expect(resolved.identity.gcode_file).toBe("cache/real-plate.gcode");
    expect(resolved.downloadUrl).toBeNull();

    render(
      <MemoryRouter>
        <PrintJobReproducibility job={job} />
      </MemoryRouter>,
    );
    expect(screen.getByTestId("reproducibility-level")).toHaveTextContent("Partially reproducible");
    expect(screen.getByText("real-plate.gcode")).toBeInTheDocument();
    expect(screen.getByText("Bambu project label")).toBeInTheDocument();
    expect(screen.getByText("task-42")).toBeInTheDocument();
    expect(screen.getByText("bambu_ftps_unavailable")).toBeInTheDocument();
    expect(screen.getByText("The printer cache is unavailable.")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /download archived artifact/i }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("stale.gcode")).not.toBeInTheDocument();
  });

  it("shows all evidence levels and only downloads an exact archived artifact", async () => {
    const user = userEvent.setup();
    const { rerender } = render(
      <MemoryRouter>
        <PrintJobReproducibility
          job={makeJob({
            artifact_evidence: "gcode_archived",
            reproducibility: {
              level: "exact",
              identity: {
                display_name: "Bambu project label",
                task_id: null,
                subtask_id: null,
                project_id: null,
                profile_id: null,
                gcode_file: "cache/plate.gcode",
                plate_index: null,
              },
              metadata: { current_layer: null, total_layers: null, nozzle_diameter: null },
              error: null,
              download_url: "/api/v1/files/7/download",
            },
          })}
          previewHref="/models/7"
          downloadFile={downloadAuthenticatedFile}
        />
      </MemoryRouter>,
    );

    expect(screen.getByTestId("reproducibility-level")).toHaveTextContent("Exactly reproducible");
    expect(screen.getByText("plate.gcode", { exact: true })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open model detail" })).toHaveAttribute(
      "href",
      "/models/7",
    );
    await user.click(screen.getByRole("button", { name: /download archived artifact/i }));
    expect(downloadAuthenticatedFile).toHaveBeenCalledWith("/api/v1/files/7/download");

    rerender(
      <MemoryRouter>
        <PrintJobReproducibility job={makeJob({ artifact_evidence: "capture_failed" })} />
      </MemoryRouter>,
    );
    expect(screen.getByTestId("reproducibility-level")).toHaveTextContent(
      "External/basic evidence",
    );
    expect(
      screen.queryByRole("button", { name: /download archived artifact/i }),
    ).not.toBeInTheDocument();
  });

  it("uses an authoritative reported path and blocks URLs from failed captures", async () => {
    const user = userEvent.setup();
    const exactArchivedJob = makeJob({
      remote_filename: "subtask-name",
      artifact_evidence: "gcode_archived",
      reproducibility: {
        level: "exact",
        identity: {
          display_name: "Bambu project label",
          task_id: "task-42",
          subtask_id: "subtask-9",
          project_id: "project-7",
          profile_id: "profile-4",
          gcode_file: null,
          plate_index: 2,
        },
        metadata: { current_layer: null, total_layers: null, nozzle_diameter: null },
        error: null,
        download_url: "/api/v1/files/7/download",
      },
    });

    const { rerender } = render(
      <MemoryRouter>
        <PrintJobReproducibility job={exactArchivedJob} downloadFile={downloadAuthenticatedFile} />
      </MemoryRouter>,
    );
    expect(screen.getByText("Archived artifact", { exact: true })).toBeInTheDocument();
    expect(screen.queryByText("subtask-name", { exact: true })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /download archived artifact/i }));
    expect(downloadAuthenticatedFile).toHaveBeenCalledWith("/api/v1/files/7/download");

    rerender(
      <MemoryRouter>
        <PrintJobReproducibility
          job={{
            ...exactArchivedJob,
            artifact_evidence: "capture_failed",
          }}
        />
      </MemoryRouter>,
    );
    expect(
      screen.queryByRole("button", { name: /download archived artifact/i }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("External print evidence", { exact: true })).toBeInTheDocument();
    expect(screen.queryByText("Archived artifact", { exact: true })).not.toBeInTheDocument();

    rerender(
      <MemoryRouter>
        <PrintJobReproducibility
          job={makeJob({
            remote_filename: "subtask-name",
            artifact_evidence: "metadata_only",
          })}
        />
      </MemoryRouter>,
    );
    expect(screen.getByText("External print evidence", { exact: true })).toBeInTheDocument();
    expect(screen.queryByText("Archived artifact", { exact: true })).not.toBeInTheDocument();
  });

  it("shows only a project preview URL and opens an accessible toolpath viewer", async () => {
    const user = userEvent.setup();
    const identity = {
      display_name: "Bambu project label",
      task_id: "task-42",
      subtask_id: "subtask-9",
      project_id: "project-7",
      profile_id: "profile-4",
      gcode_file: null,
      plate_index: 2,
    };
    const metadata = { current_layer: 80, total_layers: 80, nozzle_diameter: 0.4 };
    const noPreviewJob = makeJob({
      artifact_evidence: "gcode_archived",
      toolpath_preview_url: "/api/v1/files/7/ignored-preview",
      reproducibility: {
        level: "exact",
        identity,
        metadata,
        error: null,
        download_url: "/api/v1/files/7/download",
        toolpath_preview_url: "/api/v1/files/7/ignored-nested-preview",
      },
    });

    const { rerender } = render(
      <MemoryRouter>
        <PrintJobReproducibility job={noPreviewJob} toolpathViewer={TestToolpathViewer} />
      </MemoryRouter>,
    );
    expect(screen.queryByRole("button", { name: "Preview toolpath" })).not.toBeInTheDocument();

    expect(
      resolvePrintJobReproducibility({
        ...noPreviewJob,
        artifact_evidence: "project_archived",
        reproducibility: {
          ...noPreviewJob.reproducibility!,
          toolpath_preview_url: undefined,
        },
      }).toolpathPreviewUrl,
    ).toBe("/api/v1/files/7/ignored-preview");

    const previewJob = {
      ...noPreviewJob,
      artifact_evidence: "project_archived",
      toolpath_preview_url: "/api/v1/files/7/top-level-preview",
      reproducibility: {
        ...noPreviewJob.reproducibility!,
        toolpath_preview_url: "/api/v1/files/7/nested-preview",
      },
    };
    rerender(
      <MemoryRouter>
        <PrintJobReproducibility job={previewJob} toolpathViewer={TestToolpathViewer} />
      </MemoryRouter>,
    );
    const trigger = screen.getByRole("button", { name: "Preview toolpath" });
    await user.click(trigger);

    const dialog = await screen.findByRole("dialog", { name: "Toolpath preview" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(await screen.findByTestId("toolpath-viewer")).toHaveTextContent(
      "/api/v1/files/7/nested-preview",
    );

    await user.keyboard("{Escape}");
    await waitFor(() => expect(dialog).not.toBeInTheDocument());
    expect(trigger).toHaveFocus();
  });

  it("maps legacy error codes to a human message without duplicating the code", () => {
    const resolved = resolvePrintJobReproducibility(
      makeJob({
        artifact_capture_error: "external_artifact_capture_disabled",
      }),
    );

    expect(resolved.error).toEqual({
      code: "external_artifact_capture_disabled",
      message: "External artifact capture is disabled by configuration.",
    });
    expect(resolved.error?.message).not.toBe(resolved.error?.code);
  });

  it("normalizes a nested backend error when its message repeats a known code", () => {
    const resolved = resolvePrintJobReproducibility(
      makeJob({
        artifact_evidence: "capture_failed",
        reproducibility: {
          level: "metadata",
          identity: {
            display_name: "Bambu project label",
            task_id: "task-42",
            subtask_id: null,
            project_id: null,
            profile_id: null,
            gcode_file: null,
            plate_index: null,
          },
          metadata: { current_layer: null, total_layers: null, nozzle_diameter: null },
          error: {
            code: "external_artifact_capture_disabled",
            message: "external_artifact_capture_disabled",
          },
          download_url: "/api/v1/files/7/download",
        },
      }),
    );

    expect(resolved.error).toEqual({
      code: "external_artifact_capture_disabled",
      message: "External artifact capture is disabled by configuration.",
    });
  });

  it("renders the neutral label and known capture errors in Spanish", async () => {
    localStorage.setItem("printstash.locale", "es");
    const container = document.createElement("div");
    container.id = "root";
    document.body.append(container);

    render(
      <I18nProvider>
        <MemoryRouter>
          <Localized>
            <PrintJobReproducibility
              job={makeJob({
                remote_filename: "subtask-name",
                artifact_evidence: "capture_failed",
                artifact_capture_error: "external_artifact_capture_disabled",
              })}
            />
          </Localized>
        </MemoryRouter>
        <DomLocalization />
      </I18nProvider>,
      { container },
    );

    await waitFor(() => {
      expect(
        screen.getByText("Evidencia de impresión externa", { exact: true }),
      ).toBeInTheDocument();
      expect(
        screen.getByText(
          "La captura de artefactos externos está desactivada por la configuración.",
          { exact: true },
        ),
      ).toBeInTheDocument();
    });
    expect(screen.queryByText("subtask-name", { exact: true })).not.toBeInTheDocument();
    container.remove();
  });

  it("uses the i18n context inside the portaled toolpath preview", async () => {
    localStorage.setItem("printstash.locale", "es");
    const user = userEvent.setup();
    render(
      <I18nProvider>
        <MemoryRouter>
          <PrintJobReproducibility
            job={makeJob({
              artifact_evidence: "project_archived",
              reproducibility: {
                level: "exact",
                identity: {
                  display_name: "Bambu project label",
                  task_id: null,
                  subtask_id: null,
                  project_id: null,
                  profile_id: null,
                  gcode_file: null,
                  plate_index: null,
                },
                metadata: { current_layer: null, total_layers: null, nozzle_diameter: null },
                error: null,
                download_url: null,
                toolpath_preview_url: "/api/v1/files/7/toolpath-preview",
              },
            })}
            toolpathViewer={TestToolpathViewer}
          />
        </MemoryRouter>
      </I18nProvider>,
    );

    const trigger = screen.getByRole("button", { name: "Previsualizar trayectoria" });
    await user.click(trigger);
    expect(
      await screen.findByRole("dialog", { name: "Vista previa de la trayectoria" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cerrar" })).toBeInTheDocument();
  });
});
