/** Creality profiles stay bound to their publication; account secrets never leave MAIN. */
import { beforeEach, afterEach, describe, it, expect, vi } from "vitest";
import {
  requestCrealityCloudInMainWorld,
  crealityCloudCaptureFromPage,
} from "../crealitycloud-capture.ts";
const PAGE = "https://www.crealitycloud.com/model-detail/cube";
const ID = "aaaaaaaaaaaaaaaaaaaaaaaa";
beforeEach(() => {
  vi.stubGlobal("location", { hostname: "www.crealitycloud.com", pathname: "/model-detail/cube" });
});
afterEach(() => {
  vi.unstubAllGlobals();
  document.body.replaceChildren();
  document.cookie = "model_token=; max-age=0";
  document.cookie = "model_user_id=; max-age=0";
});
function publication() {
  const data = [
    { urlAlias: 1, groupName: 2, id: 3, model3mfList: 4, userInfo: 5, license: 6 },
    "cube",
    "Cube",
    "bbbbbbbbbbbbbbbbbbbbbbbb",
    [7],
    { nickName: 8, userId: 9 },
    "CC BY",
    {
      id: 10,
      modelGroupId: 3,
      name: 2,
      secondName: 11,
      size: 12,
      printTime: 13,
      filamentWeight: 14,
      plateCount: 15,
      printerName: 16,
      userInfo: 5,
    },
    "Maker",
    100,
    ID,
    "Fine",
    1000,
    100,
    25,
    2,
    "K1",
  ];
  const script = document.createElement("script");
  script.type = "application/json";
  script.id = "__NUXT_DATA__";
  script.textContent = JSON.stringify(data);
  document.body.append(script);
}
describe("requestCrealityCloudInMainWorld", () => {
  it("extracts only profiles belonging to the displayed publication", async () => {
    publication();
    const result = await requestCrealityCloudInMainWorld({
      publicationId: "cube",
      operation: "metadata",
    });
    const capture = crealityCloudCaptureFromPage(PAGE, result);
    expect(capture.candidates).toEqual([
      {
        id: ID,
        filename: "Cube.3mf",
        fileType: "other",
        sizeBytes: 1000,
        profile: {
          id: ID,
          name: "Fine",
          origin: "provider",
          author: "Maker",
          creator_relationship: "model_creator",
          printer: "K1",
          estimated_seconds: 100,
          filament_grams: 25,
          plate_count: 2,
        },
      },
    ]);
    expect(capture.source.fields.license_code?.value).toBe("CC BY");
  });
  it("refuses a download after the source page changes", async () => {
    publication();
    vi.stubGlobal("location", {
      hostname: "www.crealitycloud.com",
      pathname: "/model-detail/another",
    });
    const request = vi.fn();
    vi.stubGlobal("fetch", request);
    expect(
      await requestCrealityCloudInMainWorld({
        publicationId: "cube",
        operation: "download",
        profileId: ID,
      }),
    ).toEqual({ ok: false, code: "contract_changed" });
    expect(request).not.toHaveBeenCalled();
  });
  it("keeps authentication inside the provider request", async () => {
    publication();
    document.cookie = "model_token=explicit-test-token";
    document.cookie = "model_user_id=100";
    const url =
      "https://internal-creality-usa.oss-us-east-1.aliyuncs.com/file3mf%2Ftest.3mf?signature=test";
    vi.stubGlobal("fetch", async (_url: string, init: RequestInit) => {
      expect(init.headers).toMatchObject({
        __cxy_token_: "explicit-test-token",
        __cxy_uid_: "100",
      });
      return new Response(JSON.stringify({ code: 0, result: { downloadUrl: url } }));
    });
    expect(
      await requestCrealityCloudInMainWorld({
        publicationId: "cube",
        operation: "download",
        profileId: ID,
      }),
    ).toEqual({ ok: true, kind: "download", url });
  });
  it("requires sign in without an account cookie", async () => {
    publication();
    expect(
      await requestCrealityCloudInMainWorld({
        publicationId: "cube",
        operation: "download",
        profileId: ID,
      }),
    ).toEqual({ ok: false, code: "sign_in_required" });
  });
  it("refuses an unrelated profile", async () => {
    publication();
    expect(
      await requestCrealityCloudInMainWorld({
        publicationId: "cube",
        operation: "download",
        profileId: "cccccccccccccccccccccccc",
      }),
    ).toEqual({ ok: false, code: "contract_changed" });
  });
});
