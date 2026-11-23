/** The config gutter counts the textarea's logical lines and follows the selected file's scroll offset. */
import "@testing-library/jest-dom/vitest";
import { fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PrinterConfigEditor } from "../config-editor";
import { json, renderApp } from "@/test-support/render";

const longFile = "includes/disposable.cfg";
const shortFile = "printer.cfg";
const contents = Array.from({ length: 80 }, (_, index) => `# Source line ${index + 1}\r\n`).join(
  "",
);

function renderConfigEditor() {
  renderApp(<PrinterConfigEditor printerId={4} />, {
    routePath: "/printers/:id",
    at: `/printers/4?tab=config&${new URLSearchParams({ file: longFile })}`,
    routes: {
      "GET /api/v1/printers/4/config/files": json({
        files: [longFile, shortFile].map((path) => ({
          path,
          writable: true,
          size_bytes: 0,
          modified: 0,
        })),
      }),
      "GET /api/v1/printers/4/config/file?": (url) => {
        const path = new URL(url, "http://localhost").searchParams.get("path");
        return json({
          path,
          content: path === longFile ? contents : "[printer]",
          revision: "test-revision",
          writable: true,
        });
      },
    },
  });
}

function lineNumbers(editor: HTMLElement) {
  const gutter = editor.parentElement?.querySelector("pre");
  if (!gutter) throw new Error("Missing line number gutter");
  return gutter;
}

describe("PrinterConfigEditor", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("counts the trailing blank line in a long CRLF file", async () => {
    renderConfigEditor();
    const editor = await screen.findByRole("textbox", { name: /includes\/disposable\.cfg/ });
    expect(editor).toHaveValue(contents.replaceAll("\r", ""));
    expect(lineNumbers(editor).textContent?.split("\n")).toEqual(
      Array.from({ length: 81 }, (_, index) => String(index + 1)),
    );
  });

  it("moves the gutter by the full source scroll offset", async () => {
    renderConfigEditor();
    const editor = await screen.findByRole("textbox", { name: /includes\/disposable\.cfg/ });
    editor.scrollTop = 933;
    fireEvent.scroll(editor);
    expect(lineNumbers(editor).style.transform).toBe("translateY(-933px)");
  });

  it("opens folders by keyboard without changing the current file", async () => {
    renderConfigEditor();
    const folder = await screen.findByRole("button", { name: "includes" });
    const selectedFile = screen.getByRole("button", { name: "disposable.cfg" });
    folder.focus();
    await userEvent.keyboard("{Enter}");
    expect(folder).toHaveAttribute("aria-expanded", "false");
    expect(selectedFile).not.toBeVisible();
    await userEvent.keyboard("{Enter}");
    expect(selectedFile).toBeVisible();
    expect(selectedFile).toHaveAttribute("aria-current", "true");
    expect(await screen.findByRole("textbox", { name: /includes\/disposable\.cfg/ })).toBeVisible();
  });

  it("resets the editor scroll position on file switch", async () => {
    renderConfigEditor();
    const editor = await screen.findByRole("textbox", { name: /includes\/disposable\.cfg/ });
    editor.scrollTop = 933;
    fireEvent.scroll(editor);

    await userEvent.click(await screen.findByRole("button", { name: shortFile }));
    const next = await screen.findByRole("textbox", { name: /printer\.cfg/ });
    expect(next.scrollTop).toBe(0);
    expect(lineNumbers(next).style.transform).toBe("");
    expect(lineNumbers(next).textContent).toBe("1");
  });
});
