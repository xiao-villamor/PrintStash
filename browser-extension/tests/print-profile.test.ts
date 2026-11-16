import { describe, expect, it } from "vitest";
import { validateCaptureProfile, profileSummary } from "../print-profile";
import {
  makerWorldCaptureFromMetadata,
  parseMakerWorldMetadataResponse,
  validateMakerWorldMetadataDto,
} from "../makerworld-capture";

describe("profile capture", () => {
  it("retains the explicit provider facts without importing its session or raw project settings", () => {
    const metadata = parseMakerWorldMetadataResponse(
      {
        id: 1087625,
        instances: [
          {
            id: 1080661,
            title: "Shelf",
            prediction: 30277,
            weight: 433,
            instanceCreator: { name: "Designer", privateCookie: "must not survive" },
            instanceFilaments: [{ type: "PLA", usedG: "433" }],
            extention: {
              modelInfo: {
                compatibility: { devProductName: "P1S" },
                plates: [{ index: 1 }, { index: 2 }],
                f3mf: { url: "https://cdn.test/?token=secret" },
              },
              instanceSetting: { isPrinterTested: true },
            },
          },
        ],
      },
      "1087625",
    );
    const checked = validateMakerWorldMetadataDto(metadata, "1087625");
    const profile = makerWorldCaptureFromMetadata(
      checked,
      "https://makerworld.com/en/models/1087625-shelf",
    ).candidates[0].profile;
    expect(profile).toEqual({
      id: "1080661",
      name: "Shelf",
      origin: "provider",
      author: "Designer",
      printer: "P1S",
      estimated_seconds: 30277,
      filament_grams: 433,
      plate_count: 2,
      materials: ["PLA"],
    });
    expect(JSON.stringify(profile)).not.toMatch(/token|cookie|tested/i);
  });
  it.each([
    { cookies: "secret" },
    { origin: "measured" },
    { estimated_seconds: -1 },
    { filament_grams: Infinity },
    { plate_count: 1.5 },
    { author: "<script>" },
  ])("rejects unsafe profile facts %j", (extra) => {
    expect(() =>
      validateCaptureProfile({ id: "a", name: "Profile", origin: "provider", ...extra }, "a"),
    ).toThrow();
  });
  it("keeps unavailable facts absent", () => {
    expect(validateCaptureProfile({ id: "a", name: "Profile", origin: "provider" }, "a")).toEqual({
      id: "a",
      name: "Profile",
      origin: "provider",
    });
  });
});

describe("profileSummary", () => {
  it("formats provider estimates for comparison", () => {
    expect(
      profileSummary({
        id: "one",
        name: "Fine",
        origin: "provider",
        author: "Maker",
        printer: "K1",
        estimated_seconds: 3263,
        filament_grams: 16.22783,
        plate_count: 1,
      }),
    ).toBe("Provider estimates · Maker · K1 · 54 min · 16.23 g · 1 plate");
  });
  it("distinguishes file estimates from measured results", () => {
    expect(
      profileSummary({
        id: "two",
        name: "Fine",
        origin: "file",
        estimated_seconds: 20,
        plate_count: 2,
      }),
    ).toBe("File estimates · <1 min · 2 plates");
  });
  it("keeps unavailable facts explicit", () => {
    expect(profileSummary({ id: "three", name: "Unknown", origin: "provider" })).toBe(
      "Provider estimates · Details not captured",
    );
  });
});
