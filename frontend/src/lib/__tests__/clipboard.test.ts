/** Copying must work on HTTP without leaking temporary secrets or losing selection. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { copyText } from "@/lib/clipboard";

describe("copyText", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    document.body.replaceChildren();
  });

  it("copies through the modern clipboard", async () => {
    let copied = "";
    vi.stubGlobal("navigator", {
      clipboard: {
        writeText: async (text: string) => {
          copied = text;
        },
      },
    });
    await copyText("test key");
    expect(copied).toBe("test key");
    expect(document.querySelector("textarea")).toBeNull();
  });

  it.each(["absent", "denied"])("copies when the modern clipboard is %s", async (mode) => {
    vi.stubGlobal(
      "navigator",
      mode === "absent"
        ? {}
        : {
            clipboard: {
              writeText: async () => {
                throw new DOMException("Denied", "NotAllowedError");
              },
            },
          },
    );
    let copied = "";
    const original = document.execCommand;
    document.execCommand = (command) => {
      const field = document.activeElement;
      if (command === "copy" && field instanceof HTMLTextAreaElement) {
        copied = field.value.slice(field.selectionStart, field.selectionEnd);
        return true;
      }
      return false;
    };
    try {
      await copyText("a multi\nline command");
      expect(copied).toBe("a multi\nline command");
      expect(document.querySelector("textarea")).toBeNull();
    } finally {
      document.execCommand = original;
    }
  });

  it.each(["refused", "throws"])("cleans up after fallback %s", async (mode) => {
    vi.stubGlobal("navigator", {});
    const original = document.execCommand;
    document.execCommand = () => {
      if (mode === "throws") throw new Error("copy denied");
      return false;
    };
    try {
      await expect(copyText("secret")).rejects.toThrow(/clipboard_copy_failed|copy denied/);
      expect(document.querySelector("textarea")).toBeNull();
    } finally {
      document.execCommand = original;
    }
  });

  it("restores the focused input selection", async () => {
    vi.stubGlobal("navigator", {});
    const input = document.createElement("input");
    input.value = "abcdef";
    document.body.append(input);
    input.focus();
    input.setSelectionRange(1, 4, "backward");
    const original = document.execCommand;
    document.execCommand = () => true;
    try {
      await copyText("secret");
      expect(document.activeElement).toBe(input);
      expect([input.selectionStart, input.selectionEnd, input.selectionDirection]).toEqual([
        1,
        4,
        "backward",
      ]);
    } finally {
      document.execCommand = original;
    }
  });

  it("restores a document text selection", async () => {
    vi.stubGlobal("navigator", {});
    const paragraph = document.createElement("p");
    paragraph.textContent = "selected text";
    document.body.append(paragraph);
    const range = document.createRange();
    range.selectNodeContents(paragraph);
    window.getSelection()?.removeAllRanges();
    window.getSelection()?.addRange(range);
    const original = document.execCommand;
    document.execCommand = () => true;
    try {
      await copyText("secret");
      expect(window.getSelection()?.toString()).toBe("selected text");
    } finally {
      document.execCommand = original;
    }
  });
});
