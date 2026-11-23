/** MakerOnline packages retain explicit estimates while site verification stays inside MAIN. */
import { beforeEach, afterEach, describe, it, expect, vi } from "vitest";
import {
  requestMakerOnlineInMainWorld,
  makerOnlineCaptureFromPage,
} from "../makeronline-capture.ts";
const PAGE = "https://www.makeronline.com/en/model/Clip/100.html";
function site() {
  const model = {
    id: 100,
    title: "Clip",
    user_id: "creator",
    user_display_name: "Maker",
    license: 8,
    print_profiles: [
      {
        id: 200,
        title: "Fine",
        user_id: "creator",
        user_display_name: "Maker",
        plate_count: 2,
        print_time_label: "1h2m",
        total_size: 1000,
        printer_label: "Printer A, Printer B",
        materials_label: [{ material_type: "PLA", filament_used_g: 10 }],
        files: [{ id: 1 }, { id: 2 }],
        download: {
          mold_id: 100,
          print_profile_id: 200,
          key: "public-file-key",
          file_name: "clip.3mf",
        },
      },
    ],
  };
  const headers = vi.fn<(risk: boolean, level: number) => Promise<unknown>>(async () => ({
    "provider-authorization": "explicit-test-secret",
  }));
  const download = vi.fn<(input: object, headers: unknown) => Promise<unknown>>(async () => ({
    code: 0,
    data: "https://acop-prod-private.s3-accelerate.amazonaws.com/asop/clip.3mf?signature=test",
  }));
  const root = {
    $store: { state: { model: { detail: model } } },
    $api: { model: { downloadFile: download } },
    $children: [{ modelID: 100, getRequestHeaders: headers }],
  };
  Object.defineProperty(window, "$nuxt", { value: root, writable: true, configurable: true });
  return { model, headers, download, root };
}
beforeEach(() =>
  vi.stubGlobal("location", {
    hostname: "www.makeronline.com",
    pathname: "/en/model/Clip/100.html",
  }),
);
afterEach(() => {
  vi.unstubAllGlobals();
  delete (window as unknown as { $nuxt?: unknown }).$nuxt;
  vi.useRealTimers();
});
describe("requestMakerOnlineInMainWorld", () => {
  it("selects each package once despite multiple plates", async () => {
    site();
    const result = await requestMakerOnlineInMainWorld({
      publicationId: "100",
      operation: "metadata",
    });
    const capture = makerOnlineCaptureFromPage(PAGE, result);
    expect(capture.candidates.map((c) => ({ id: c.id, name: c.filename }))).toEqual([
      { id: "200", name: "clip.3mf" },
    ]);
  });
  it("preserves explicit provider estimates without choosing a compatible printer", async () => {
    site();
    const result = await requestMakerOnlineInMainWorld({
      publicationId: "100",
      operation: "metadata",
    });
    const capture = makerOnlineCaptureFromPage(PAGE, result);
    expect(capture.candidates[0].profile).toEqual({
      id: "200",
      name: "Fine",
      origin: "provider",
      author: "Maker",
      creator_relationship: "model_creator",
      estimated_seconds: 3720,
      filament_grams: 10,
      plate_count: 2,
      materials: ["PLA"],
    });
    expect(capture.source.fields.license_code).toBeUndefined();
  });
  it("keeps provider credentials inside its own download client", async () => {
    const { download } = site();
    download.mockImplementation(async (input, headers) => {
      expect(input).toEqual({
        key: "public-file-key",
        file_type: 2,
        is_batch_down: 0,
        print_profile_id: 200,
      });
      expect(headers).toEqual({ "provider-authorization": "explicit-test-secret" });
      return {
        code: 0,
        data: "https://acop-prod-private.s3-accelerate.amazonaws.com/asop/clip.3mf?signature=test",
      };
    });
    expect(
      await requestMakerOnlineInMainWorld({
        publicationId: "100",
        operation: "download",
        profileId: "200",
      }),
    ).toEqual({
      ok: true,
      kind: "download",
      url: "https://acop-prod-private.s3-accelerate.amazonaws.com/asop/clip.3mf?signature=test",
    });
  });
  it("requires native manual verification before retrying once", async () => {
    const { download, headers } = site();
    download.mockResolvedValueOnce({ code: 1003, data: { captchaLevel: 1 } });
    expect(
      await requestMakerOnlineInMainWorld({
        publicationId: "100",
        operation: "download",
        profileId: "200",
      }),
    ).toMatchObject({ ok: true, kind: "download" });
    expect(headers.mock.calls).toEqual([
      [true, 0],
      [true, 1],
    ]);
  });
  it("stops after a repeated verification requirement", async () => {
    const { download } = site();
    download.mockResolvedValue({ code: 1003, data: { captchaLevel: 1 } });
    expect(
      await requestMakerOnlineInMainWorld({
        publicationId: "100",
        operation: "download",
        profileId: "200",
      }),
    ).toEqual({ ok: false, code: "verification_required" });
    expect(download).toHaveBeenCalledTimes(2);
  });
  it.each([
    { label: "stale page", path: "/en/model/Other/101.html", id: 100 },
    { label: "stale store", path: "/en/model/Clip/100.html", id: 101 },
  ])("rejects $label before authentication", async ({ path, id }) => {
    const { model, download } = site();
    model.id = id;
    vi.stubGlobal("location", { hostname: "www.makeronline.com", pathname: path });
    expect(
      await requestMakerOnlineInMainWorld({
        publicationId: "100",
        operation: "download",
        profileId: "200",
      }),
    ).toEqual({ ok: false, code: "contract_changed" });
    expect(download).not.toHaveBeenCalled();
  });
  it("rejects a profile from another publication", async () => {
    const { model } = site();
    model.print_profiles[0].download.mold_id = 101;
    expect(
      await requestMakerOnlineInMainWorld({ publicationId: "100", operation: "metadata" }),
    ).toEqual({ ok: false, code: "contract_changed" });
  });
  it("rejects an unselected profile", async () => {
    site();
    expect(
      await requestMakerOnlineInMainWorld({
        publicationId: "100",
        operation: "download",
        profileId: "201",
      }),
    ).toEqual({ ok: false, code: "contract_changed" });
  });
  it.each([
    "http://acop-prod-private.s3-accelerate.amazonaws.com/clip.3mf",
    "https://acop-prod-private.s3-accelerate.amazonaws.com.evil.test/clip.3mf",
    "https://acop-prod-private.s3-accelerate.amazonaws.com/clip.html",
  ])("rejects unsafe download %s", async (url) => {
    const { download } = site();
    download.mockResolvedValue({ code: 0, data: url });
    expect(
      await requestMakerOnlineInMainWorld({
        publicationId: "100",
        operation: "download",
        profileId: "200",
      }),
    ).toEqual({ ok: false, code: "contract_changed" });
  });
  it("does not download after verification times out", async () => {
    vi.useFakeTimers();
    const { download, headers } = site();
    let verify!: (v: object) => void;
    headers.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          verify = resolve;
        }),
    );
    const pending = requestMakerOnlineInMainWorld({
      publicationId: "100",
      operation: "download",
      profileId: "200",
    });
    await vi.advanceTimersByTimeAsync(120000);
    expect(await pending).toEqual({ ok: false, code: "verification_required" });
    verify({});
    await Promise.resolve();
    await Promise.resolve();
    expect(download).not.toHaveBeenCalled();
  });
});
