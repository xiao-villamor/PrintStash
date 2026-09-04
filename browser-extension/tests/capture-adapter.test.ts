import { describe, expect, it } from "vitest";

import {
  buildBrowserCaptureMessage,
  JSON_LD_MAX_DEPTH,
  JSON_LD_MAX_NODES,
  JSON_LD_MAX_OBJECTS,
  JSON_LD_MAX_QUEUE,
  JSON_LD_MAX_SCRIPT_BYTES,
  JSON_LD_MAX_SCRIPTS,
  JSON_LD_MAX_TOTAL_BYTES,
  stableCaptureFileId,
} from "../capture-adapter.ts";

const printablesJsonLd = JSON.stringify({
  "@context": "https://schema.org",
  "@type": "Product",
  name: "  Articulated <b>Octopus</b> ",
  description: "A friendly\u0000 <em>print</em>\nwith tentacles.",
  url: "https://www.printables.com/model/123-octopus?tracking=secret#files",
  creator: {
    "@type": "Person",
    name: "  Ada Maker ",
    url: "https://www.printables.com/@ada?token=nope",
  },
  license: "CC-BY-4.0",
  keywords: [" Calibration ", "articulated", "calibration"],
  datePublished: "2026-08-20T10:20:30Z",
  dateModified: "2026-08-21",
});

const makerWorldJsonLd = JSON.stringify({
  "@context": "https://schema.org",
  "@type": "CreativeWork",
  name: "Calibrated cube",
  description: "A cube for <script>bad()</script> calibration.",
  author: { "@type": "Person", name: "Maker One", identifier: "maker-7" },
  license: "https://creativecommons.org/licenses/by/4.0/",
  contentUrl: "https://makerworld.bblmw.com/file.3mf?signature=signed-secret",
});

describe("browser-visible provider capture adapters", () => {
  it("does not create URL-bearing Printables candidates from browser-visible JSON-LD", () => {
    const capture = buildBrowserCaptureMessage({
      provider: "Printables",
      pageUrl: "https://www.printables.com/model/9-safe",
      pageTitle: "Safe",
      jsonLd: [
        JSON.stringify({
          distribution: [
            {
              contentUrl: "https://media.printables.com/files/safe.3mf?signature=temporary",
              encodingFormat: "model/3mf",
              contentSize: "12",
            },
            { contentUrl: "https://evilprintables.com/files/leak.stl" },
          ],
        }),
      ],
    });
    expect(capture.candidates).toEqual([]);
    expect(JSON.stringify(capture)).not.toContain("signature");
  });

  it("does not retain duplicate Printables distribution identities from JSON-LD", () => {
    const capture = buildBrowserCaptureMessage({
      provider: "Printables",
      pageUrl: "https://www.printables.com/model/9-safe",
      pageTitle: "Safe",
      jsonLd: [
        JSON.stringify({
          distribution: [
            {
              identifier: "file-first",
              contentUrl: "https://media.printables.com/files/first/cube.3mf?signature=one",
            },
            {
              identifier: "file-second",
              contentUrl: "https://media.printables.com/files/second/cube.3mf?signature=two",
            },
          ],
        }),
      ],
    });

    expect(capture.candidates).toEqual([]);
    expect(JSON.stringify(capture)).not.toContain("signature");
  });

  it("ignores reordered Printables distributions", () => {
    const first = {
      contentUrl: "https://media.printables.com/files/first/cube.3mf?signature=one",
    };
    const second = {
      contentUrl: "https://media.printables.com/files/second/cube.3mf?signature=two",
    };
    const capture = (distribution: unknown[]) =>
      buildBrowserCaptureMessage({
        provider: "Printables",
        pageUrl: "https://www.printables.com/model/9-safe",
        jsonLd: [JSON.stringify({ distribution })],
      });

    const forward = capture([first, { contentUrl: "https://evilprintables.com/nope.stl" }, second]);
    const reversed = capture([second, first]);

    expect(forward.candidates).toEqual([]);
    expect(reversed.candidates).toEqual([]);
  });

  it("requires manual attachment when Printables exposes an unsupported distribution contract", () => {
    const capture = buildBrowserCaptureMessage({
      provider: "Printables",
      pageUrl: "https://www.printables.com/model/9-safe",
      pageTitle: "Safe",
      jsonLd: [
        JSON.stringify({
          distribution: [{ download: "https://media.printables.com/files/changed.3mf" }],
        }),
      ],
    });

    expect(capture).toMatchObject({
      state: "manual_file_required",
      manual_file: { mapping: "user_selected_file", source_item_id: "9" },
    });
    expect(capture.message).toContain("attach it in Pending Imports");
    expect(capture.candidates).toEqual([]);
  });
  it("maps a Printables individual model into the bounded V2 source allowlist", () => {
    const capture = buildBrowserCaptureMessage({
      provider: "Printables",
      pageUrl: "https://www.printables.com/model/123-octopus?session=browser-cookie",
      pageTitle: "Articulated Octopus",
      jsonLd: [printablesJsonLd],
    });

    expect(capture.state).toBe("manual_file_required");
    expect(capture.message).toContain("Choose a downloaded Printables file");
    expect(capture.source).toEqual({
      provider: "printables",
      canonical_url: "https://www.printables.com/model/123-octopus",
      source_item_id: "123",
      source_revision: null,
      adapter_version: "browser-visible-v1",
      tags: ["calibration", "articulated"],
      fields: {
        title: { value: "Articulated Octopus", origin: "confirmed" },
        description: { value: "A friendly print\nwith tentacles.", origin: "confirmed" },
        creator_name: { value: "Ada Maker", origin: "confirmed" },
        creator_url: { value: "https://www.printables.com/@ada", origin: "confirmed" },
        license_code: { value: "CC-BY-4.0", origin: "confirmed" },
        published_at: { value: "2026-08-20T10:20:30Z", origin: "confirmed" },
        updated_at: { value: "2026-08-21", origin: "confirmed" },
      },
    });
  });

  it("fails closed when JSON-LD script count exceeds the collection bound", () => {
    const capture = buildBrowserCaptureMessage({
      provider: "Printables",
      pageUrl: "https://www.printables.com/model/9-safe",
      pageTitle: "Fallback title",
      jsonLd: Array.from({ length: JSON_LD_MAX_SCRIPTS + 1 }, () =>
        JSON.stringify({ name: "hostile" }),
      ),
    });

    expect(capture.source.fields).toEqual({
      title: { value: "Fallback title", origin: "inferred" },
    });
    expect(JSON.stringify(capture)).not.toContain("hostile");
  });

  it("fails closed when aggregate JSON-LD bytes exceed the bound", () => {
    const script = JSON.stringify({ name: "hostile".repeat(5_000) });
    expect(new TextEncoder().encode(script).byteLength).toBeLessThan(JSON_LD_MAX_SCRIPT_BYTES);
    expect(JSON_LD_MAX_SCRIPTS * script.length).toBeGreaterThan(JSON_LD_MAX_TOTAL_BYTES);
    const capture = buildBrowserCaptureMessage({
      provider: "Printables",
      pageUrl: "https://www.printables.com/model/9-safe",
      pageTitle: "Fallback title",
      jsonLd: Array.from({ length: JSON_LD_MAX_SCRIPTS }, () => script),
    });

    expect(capture.source.fields).toEqual({
      title: { value: "Fallback title", origin: "inferred" },
    });
    expect(JSON.stringify(capture)).not.toContain("hostile");
  });

  it("fails closed when an individual JSON-LD script exceeds the bound", () => {
    const capture = buildBrowserCaptureMessage({
      provider: "Printables",
      pageUrl: "https://www.printables.com/model/9-safe",
      pageTitle: "Fallback title",
      jsonLd: [JSON.stringify({ name: "hostile".repeat(JSON_LD_MAX_SCRIPT_BYTES) })],
    });

    expect(capture.source.fields).toEqual({
      title: { value: "Fallback title", origin: "inferred" },
    });
    expect(JSON.stringify(capture)).not.toContain("hostile");
  });

  it("fails closed when JSON-LD traversal exceeds its depth bound", () => {
    const root: Record<string, unknown> = {};
    let current = root;
    for (let depth = 0; depth <= JSON_LD_MAX_DEPTH; depth += 1) {
      const child: Record<string, unknown> = {};
      current.child = child;
      current = child;
    }
    current.name = "hostile";
    const capture = buildBrowserCaptureMessage({
      provider: "Printables",
      pageUrl: "https://www.printables.com/model/9-safe",
      pageTitle: "Fallback title",
      jsonLd: [JSON.stringify(root)],
    });

    expect(capture.source.fields).toEqual({
      title: { value: "Fallback title", origin: "inferred" },
    });
    expect(JSON.stringify(capture)).not.toContain("hostile");
  });

  it("fails closed when JSON-LD traversal exceeds its node bound", () => {
    const nodesPerScript = Math.ceil((JSON_LD_MAX_NODES + 1) / JSON_LD_MAX_SCRIPTS);
    const capture = buildBrowserCaptureMessage({
      provider: "Printables",
      pageUrl: "https://www.printables.com/model/9-safe",
      pageTitle: "Fallback title",
      jsonLd: Array.from({ length: JSON_LD_MAX_SCRIPTS }, () =>
        JSON.stringify(Array.from({ length: nodesPerScript }, () => "hostile")),
      ),
    });

    expect(capture.source.fields).toEqual({
      title: { value: "Fallback title", origin: "inferred" },
    });
    expect(JSON.stringify(capture)).not.toContain("hostile");
  });

  it("fails closed when JSON-LD object count exceeds its bound", () => {
    const objectsPerScript = Math.ceil((JSON_LD_MAX_OBJECTS + 1) / JSON_LD_MAX_SCRIPTS);
    const jsonLd = Array.from({ length: JSON_LD_MAX_SCRIPTS }, () =>
      JSON.stringify(Array.from({ length: objectsPerScript }, () => ({}))),
    );
    const capture = buildBrowserCaptureMessage({
      provider: "Printables",
      pageUrl: "https://www.printables.com/model/9-safe",
      pageTitle: "Fallback title",
      jsonLd,
    });

    expect(capture.source.fields).toEqual({
      title: { value: "Fallback title", origin: "inferred" },
    });
    expect(JSON.stringify(capture)).not.toContain("hostile");
  });

  it("fails closed when JSON-LD traversal queue exceeds its bound", () => {
    const capture = buildBrowserCaptureMessage({
      provider: "Printables",
      pageUrl: "https://www.printables.com/model/9-safe",
      pageTitle: "Fallback title",
      jsonLd: [JSON.stringify(Array.from({ length: JSON_LD_MAX_QUEUE + 1 }, () => "hostile"))],
    });

    expect(capture.source.fields).toEqual({
      title: { value: "Fallback title", origin: "inferred" },
    });
    expect(JSON.stringify(capture)).not.toContain("hostile");
  });

  it("maps MakerWorld visible metadata without retaining its signed download URL", () => {
    const capture = buildBrowserCaptureMessage({
      provider: "MakerWorld",
      pageUrl: "https://makerworld.com/en/models/77-calibration-cube",
      pageTitle: "Cube",
      jsonLd: [makerWorldJsonLd],
    });

    expect(capture.state).toBe("ready");
    expect(capture.source.fields).toMatchObject({
      title: { value: "Calibrated cube", origin: "confirmed" },
      description: { value: "A cube for calibration.", origin: "confirmed" },
      creator_name: { value: "Maker One", origin: "confirmed" },
      creator_id: { value: "maker-7", origin: "confirmed" },
      license_url: { value: "https://creativecommons.org/licenses/by/4.0/", origin: "confirmed" },
    });
    expect(capture.source.provider).toBe("makerworld");
    expect(JSON.stringify(capture)).not.toContain("signed-secret");
  });

  it("creates a Thingiverse metadata draft and requires a user-selected file", () => {
    const capture = buildBrowserCaptureMessage({
      provider: "Thingiverse",
      pageUrl: "https://www.thingiverse.com/thing:763622/files",
      pageTitle: "Whistle",
      jsonLd: [JSON.stringify({ "@type": "CreativeWork", name: "Whistle", author: "Ada" })],
    });

    expect(capture).toMatchObject({
      state: "manual_file_required",
      message: "Choose a downloaded Thingiverse file to attach it to this metadata draft.",
      manual_file: { mapping: "user_selected_file", source_item_id: "763622" },
      source: { source_item_id: "763622", fields: { title: { value: "Whistle" } } },
    });
    expect(capture.source.provider).toBe("thingiverse");
  });

  it("creates a Cults metadata draft and requires a user-selected file", () => {
    const capture = buildBrowserCaptureMessage({
      provider: "Cults",
      pageUrl: "https://cults3d.com/en/3d-model/art/cult-cube",
      pageTitle: "Cult cube",
      jsonLd: [JSON.stringify({ name: "Cult cube", author: { name: "Ada" } })],
    });

    expect(capture).toMatchObject({
      state: "manual_file_required",
      manual_file: { mapping: "user_selected_file", source_item_id: "cult-cube" },
      source: {
        provider: "cults",
        canonical_url: "https://cults3d.com/en/3d-model/art/cult-cube",
        fields: { title: { value: "Cult cube", origin: "confirmed" } },
      },
    });
    expect(capture.candidates).toEqual([]);
  });

  it("rejects duplicate manual names only when their stable source identities collide", () => {
    expect(stableCaptureFileId("9", "cube.3mf", "provider-a")).not.toBe(
      stableCaptureFileId("9", "cube.3mf", "provider-b"),
    );
  });

  it("omits unavailable or unsafe values instead of carrying raw page data", () => {
    const capture = buildBrowserCaptureMessage({
      provider: "Printables",
      pageUrl: "https://www.printables.com/model/9-safe",
      pageTitle: "Fallback title",
      jsonLd: [
        "{not json}",
        JSON.stringify({ name: "x".repeat(600), image: "data:image/png;base64,secret" }),
      ],
    });

    expect(capture.source.fields).toEqual({
      title: { value: "Fallback title", origin: "inferred" },
    });
    expect(JSON.stringify(capture)).not.toContain("base64");
    expect(JSON.stringify(capture)).not.toContain("{not json}");
  });
});
