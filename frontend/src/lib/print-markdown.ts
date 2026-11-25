const printStyles = `
  @media screen { .document-print-sheet { display: none; } }
  @media print {
    @page { margin: 18mm; }
    html, body { height: auto !important; overflow: visible !important; background: white !important; }
    body > :not(.document-print-sheet) { display: none !important; }
    .document-print-sheet {
      display: block;
      color: #111;
      color-scheme: light;
      font: 11pt/1.55 system-ui, sans-serif;
      overflow-wrap: anywhere;
    }
    .document-print-sheet :is(h1, h2, h3, h4) { line-height: 1.2; break-after: avoid; }
    .document-print-sheet h1 { font-size: 22pt; margin-bottom: 12pt; }
    .document-print-sheet h2 { font-size: 16pt; }
    .document-print-sheet img { max-width: 100%; height: auto; break-inside: avoid; }
    .document-print-sheet table { width: 100%; border-collapse: collapse; }
    .document-print-sheet :is(th, td) { border: 1px solid #bbb; padding: 5px 8px; text-align: start; }
    .document-print-sheet thead { display: table-header-group; }
    .document-print-sheet tr { break-inside: avoid; }
    .document-print-sheet pre { white-space: pre-wrap; padding: 8px; border: 1px solid #ddd; }
    .document-print-sheet blockquote { border-inline-start: 2px solid #aaa; margin-inline: 0; padding-inline: 1em; }
    .document-print-sheet a { color: #17406b; }
  }
`;

/** Print only the sanitized rendered document in the browser's PDF dialog. */
export async function printMarkdown(title: string, content: HTMLElement): Promise<void> {
  if (document.querySelector(".document-print-sheet")) return;

  const style = document.createElement("style");
  style.textContent = printStyles;
  const sheet = document.createElement("article");
  sheet.className = "document-print-sheet";
  const heading = document.createElement("h1");
  heading.textContent = title;
  // Import the Markdown renderer's nodes while leaving the screen theme behind.
  const markup = content.querySelector(".prose") ?? content;
  sheet.append(heading, ...Array.from(markup.childNodes, (node) => node.cloneNode(true)));
  const previousTitle = document.title;
  const close = () => {
    window.removeEventListener("afterprint", close);
    document.title = previousTitle;
    sheet.remove();
    style.remove();
  };

  document.head.append(style);
  document.body.append(sheet);
  try {
    document.title = title;
    await Promise.allSettled(Array.from(sheet.querySelectorAll("img"), (image) => image.decode()));
    window.addEventListener("afterprint", close, { once: true });
    window.print();
  } catch (error) {
    close();
    throw error;
  }
}
