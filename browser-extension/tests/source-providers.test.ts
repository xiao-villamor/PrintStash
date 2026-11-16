/** Provider identity binds capture metadata and site permissions to individual publications. */
import { describe, expect, it } from "vitest";
import { sourcePublication, SOURCE_PROVIDERS } from "../source-providers.ts";

describe("sourcePublication", () => {
  it.each([
    {
      label: "MakerWorld",
      url: "https://makerworld.com/es/models/1087625-shoe-rack",
      id: "1087625",
    },
    {
      label: "Printables",
      url: "https://www.printables.com/model/949852-latch/files",
      id: "949852",
    },
    { label: "Thingiverse", url: "https://www.thingiverse.com/thing:7433325/files", id: "7433325" },
    {
      label: "MyMiniFactory",
      url: "https://www.myminifactory.com/object/3d-print-cube-123",
      id: "3d-print-cube-123",
    },
    { label: "Cults", url: "https://cults3d.com/en/3d-model/tool/cube", id: "cube" },
    {
      label: "Thangs",
      url: "https://thangs.com/designer/Test%20Designer/3d-model/Test%20Cube-123",
      id: "123",
    },
    { label: "Thangs", url: "https://thangs.com/designer/models/3d-model/60925", id: "60925" },
    {
      label: "Creality Cloud",
      url: "https://www.crealitycloud.com/en/model-detail/test-cube",
      id: "test-cube",
    },
    {
      label: "MakerOnline",
      url: "https://www.makeronline.com/en/model/Test%20Cube/123.html",
      id: "123",
    },
  ])("identifies $label publication", ({ label, url, id }) => {
    expect(sourcePublication(url)).toMatchObject({ label, itemId: id });
  });
  it.each(SOURCE_PROVIDERS)("rejects a misleading $label host", ({ hosts }) => {
    expect(
      sourcePublication(`https://${hosts[0]}.attacker.test/en/model/cube/123.html`),
    ).toBeNull();
  });
  it.each([
    "https://thangs.com/designer/a/3d-model/cube%2Fpart-123",
    "https://www.makeronline.com/en/model/%2e%2e/123.html",
    "https://www.crealitycloud.com:8443/model-detail/cube",
    "https://user:secret@thangs.com/designer/a/3d-model/cube-123",
    "https://thangs.com/designer/a",
    "https://www.makeronline.com/en/model/cube/123.html/extra",
  ])("rejects an unsafe publication URL %s", (url) => expect(sourcePublication(url)).toBeNull());
});
