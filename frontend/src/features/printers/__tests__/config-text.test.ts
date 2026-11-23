/** Editing through a browser textarea preserves the printer file’s original line endings and trailing content. */
import { describe, expect, it } from "vitest";

import { applyConfigTextEdit, textareaConfigText } from "../config-text";

describe("configuration textarea", () => {
  it("renders mixed line endings as browser text", () => {
    expect(textareaConfigText("a\rG\r\nB\n")).toBe("a\nG\nB\n");
  });

  it("keeps untouched file text byte-for-byte across an edit", () => {
    expect(applyConfigTextEdit("# a\r\nkey: value  \n\n", "# a\nkey: café  \n\n")).toBe(
      "# a\r\nkey: café  \n\n",
    );
  });

  it("uses the existing style for a newly inserted newline", () => {
    expect(applyConfigTextEdit("a\r\nb", "a\nnew\nb")).toBe("a\r\nnew\r\nb");
  });
});
