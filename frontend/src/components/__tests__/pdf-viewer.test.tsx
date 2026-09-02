/*
 * The PDF viewer reports the renderer's underlying failure instead of hiding
 * the only actionable diagnostic behind a generic preview error.
 */

import "@testing-library/jest-dom/vitest";
import { act, render, screen } from "@testing-library/react";
import type { DocumentProps } from "react-pdf";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.hoisted(() => {
  vi.stubGlobal("DOMMatrix", class DOMMatrixStub {});
});

import { PdfViewer } from "@/components/pdf-viewer";

const documentProps: DocumentProps[] = [];

function renderDocument(props: DocumentProps) {
  documentProps.push(props);
  return <div data-testid="pdf-document" />;
}

class ResizeObserverStub {
  observe() {}
  disconnect() {}
}

vi.stubGlobal("ResizeObserver", ResizeObserverStub);

beforeEach(() => {
  documentProps.length = 0;
});

describe("PdfViewer", () => {
  it("shows the underlying PDF.js error when rendering fails", () => {
    render(<PdfViewer file="/api/v1/documents/19/file" renderDocument={renderDocument} />);

    act(() => documentProps.at(-1)?.onLoadError?.(new Error("worker crashed")));

    expect(screen.getByText("Could not render this PDF.")).toBeInTheDocument();
    expect(screen.getByText("worker crashed")).toBeInTheDocument();
  });
});
