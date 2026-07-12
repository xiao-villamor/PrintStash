const ORCA_REPOSITORY = "https://github.com/SoftFever/OrcaSlicer";
const ORCA_RAW_ROOT = "https://raw.githubusercontent.com/SoftFever/OrcaSlicer/main/";

const ORCA_COVER_PATHS: Record<string, string> = {
  "Bambu Lab A1 mini": "resources/profiles/BBL/Bambu Lab A1 mini_cover.png",
  "Bambu Lab A1": "resources/profiles/BBL/Bambu Lab A1_cover.png",
  "Bambu Lab P1P": "resources/profiles/BBL/Bambu Lab P1P_cover.png",
  "Bambu Lab P1S": "resources/profiles/BBL/Bambu Lab P1S_cover.png",
  "Bambu Lab X1": "resources/profiles/BBL/Bambu Lab X1_cover.png",
  "Bambu Lab X1 Carbon": "resources/profiles/BBL/Bambu Lab X1 Carbon_cover.png",
  "Bambu Lab X1E": "resources/profiles/BBL/Bambu Lab X1E_cover.png",
  "Bambu Lab H2D": "resources/profiles/BBL/Bambu Lab H2D_cover.png",
  "Bambu Lab H2S": "resources/profiles/BBL/Bambu Lab H2S_cover.png",
  "Prusa MINI+": "resources/profiles/Prusa/Prusa MINI_cover.png",
  "Prusa MK3S+": "resources/profiles/Prusa/Prusa MK3S_cover.png",
  "Prusa MK4": "resources/profiles/Prusa/Prusa MK4_cover.png",
  "Prusa MK4S": "resources/profiles/Prusa/Prusa MK4S_cover.png",
  "Prusa CORE One": "resources/profiles/Prusa/Prusa CORE One_cover.png",
  "Prusa XL": "resources/profiles/Prusa/Prusa XL_cover.png",
  "Elegoo Neptune 3": "resources/profiles/Elegoo/Elegoo Neptune 3_cover.png",
  "Elegoo Neptune 3 Pro": "resources/profiles/Elegoo/Elegoo Neptune 3 Pro_cover.png",
  "Elegoo Neptune 3 Plus": "resources/profiles/Elegoo/Elegoo Neptune 3 Plus_cover.png",
  "Elegoo Neptune 3 Max": "resources/profiles/Elegoo/Elegoo Neptune 3 Max_cover.png",
  "Elegoo Neptune 4": "resources/profiles/Elegoo/Elegoo Neptune 4_cover.png",
  "Elegoo Neptune 4 Pro": "resources/profiles/Elegoo/Elegoo Neptune 4 Pro_cover.png",
  "Elegoo Neptune 4 Plus": "resources/profiles/Elegoo/Elegoo Neptune 4 Plus_cover.png",
  "Elegoo Neptune 4 Max": "resources/profiles/Elegoo/Elegoo Neptune 4 Max_cover.png",
  "Elegoo Centauri Carbon": "resources/profiles/Elegoo/Elegoo Centauri Carbon_cover.png",
  "Elegoo Centauri Carbon 2": "resources/profiles/Elegoo/Elegoo Centauri Carbon 2_cover.png",
  "Creality Ender 3": "resources/profiles/Creality/Creality Ender-3_cover.png",
  "Creality Ender 3 Pro": "resources/profiles/Creality/Creality Ender-3 Pro_cover.png",
  "Creality Ender 3 V2": "resources/profiles/Creality/Creality Ender-3 V2_cover.png",
  "Creality Ender 3 V2 Neo": "resources/profiles/Creality/Creality Ender-3 V2 Neo_cover.png",
  "Creality Ender 3 V3": "resources/profiles/Creality/Creality Ender-3 V3_cover.png",
  "Creality Ender 3 V3 SE": "resources/profiles/Creality/Creality Ender-3 V3 SE_cover.png",
  "Creality Ender 3 V3 KE": "resources/profiles/Creality/Creality Ender-3 V3 KE_cover.png",
  "Creality Ender 3 V3 Plus": "resources/profiles/Creality/Creality Ender-3 V3 Plus_cover.png",
  "Creality Ender 3 V4": "resources/profiles/Creality/Creality Ender-3 V4_cover.png",
  "Creality Ender 3 S1": "resources/profiles/Creality/Creality Ender-3 S1_cover.png",
  "Creality Ender 3 S1 Pro": "resources/profiles/Creality/Creality Ender-3 S1 Pro_cover.png",
  "Creality Ender 3 S1 Plus": "resources/profiles/Creality/Creality Ender-3 S1 Plus_cover.png",
  "Creality Ender 5": "resources/profiles/Creality/Creality Ender-5_cover.png",
  "Creality Ender 5 Pro": "resources/profiles/Creality/Creality Ender-5 Pro (2019)_cover.png",
  "Creality Ender 5 Plus": "resources/profiles/Creality/Creality Ender-5 Plus_cover.png",
  "Creality Ender 5 S1": "resources/profiles/Creality/Creality Ender-5 S1_cover.png",
  "Creality Ender 5 Max": "resources/profiles/Creality/Creality Ender-5 Max_cover.png",
  "Creality Ender 6": "resources/profiles/Creality/Creality Ender-6_cover.png",
  "Creality Hi": "resources/profiles/Creality/Creality Hi_cover.png",
  "Creality K1": "resources/profiles/Creality/Creality K1_cover.png",
  "Creality K1 Max": "resources/profiles/Creality/Creality K1 Max_cover.png",
  "Creality K1C": "resources/profiles/Creality/Creality K1C_cover.png",
  "Creality K1 SE": "resources/profiles/Creality/Creality K1 SE_cover.png",
  "Creality K2": "resources/profiles/Creality/Creality K2_cover.png",
  "Creality K2 Plus": "resources/profiles/Creality/Creality K2 Plus_cover.png",
  "Creality K2 Pro": "resources/profiles/Creality/Creality K2 Pro_cover.png",
  "Creality K2 SE": "resources/profiles/Creality/Creality K2 SE_cover.png",
  "Creality CR-10": "resources/profiles/Creality/Creality CR-10 V3_cover.png",
  "Creality CR-10 V2": "resources/profiles/Creality/Creality CR-10 V2_cover.png",
  "Creality CR-10 V3": "resources/profiles/Creality/Creality CR-10 V3_cover.png",
  "Creality CR-10 SE": "resources/profiles/Creality/Creality CR-10 SE_cover.png",
  "Creality CR-10 Max": "resources/profiles/Creality/Creality CR-10 Max_cover.png",
  "Creality CR-6 SE": "resources/profiles/Creality/Creality CR-6 SE_cover.png",
  "Creality CR-6 Max": "resources/profiles/Creality/Creality CR-6 Max_cover.png",
  "Creality CR-M4": "resources/profiles/Creality/Creality CR-M4_cover.png",
  "Creality Sermoon V1": "resources/profiles/Creality/Creality Sermoon V1_cover.png",
  "Voron 0": "resources/profiles/Voron/Voron 0.1_cover.png",
  "Voron 2.4": "resources/profiles/Voron/Voron 2.4 300_cover.png",
  "Voron Trident": "resources/profiles/Voron/Voron Trident 300_cover.png",
  "Voron Switchwire": "resources/profiles/Voron/Voron Switchwire 250_cover.png",
  "RatRig V-Core 3": "resources/profiles/Ratrig/RatRig V-Core 3 300_cover.png",
  "Sovol SV06": "resources/profiles/Sovol/Sovol SV06_cover.png",
  "Sovol SV06 Plus": "resources/profiles/Sovol/Sovol SV06 Plus_cover.png",
  "Sovol SV07": "resources/profiles/Sovol/Sovol SV07_cover.png",
  "Sovol SV08": "resources/profiles/Sovol/Sovol SV08_cover.png",
  "Anycubic Vyper": "resources/profiles/Anycubic/Anycubic Vyper_cover.png",
  "Anycubic Kobra 2": "resources/profiles/Anycubic/Anycubic Kobra 2_cover.png",
  "Anycubic Kobra 3": "resources/profiles/Anycubic/Anycubic Kobra 3_cover.png",
  "Anycubic Kobra 3 Max": "resources/profiles/Anycubic/Anycubic Kobra 3 Max_cover.png",
  "Anycubic Kobra S1": "resources/profiles/Anycubic/Anycubic Kobra S1_cover.png",
  "Flashforge Adventurer 5M": "resources/profiles/Flashforge/Flashforge Adventurer 5M_cover.png",
  "Flashforge Adventurer 5M Pro":
    "resources/profiles/Flashforge/Flashforge Adventurer 5M Pro_cover.png",
  "Qidi Q1 Pro": "resources/profiles/Qidi/Qidi Q1 Pro_cover.png",
  "Qidi X-Plus 3": "resources/profiles/Qidi/Qidi X-Plus 3_cover.png",
  "Qidi X-Max 3": "resources/profiles/Qidi/Qidi X-Max 3_cover.png",
};

export interface PrinterArtwork {
  imageUrl: string;
  sourceUrl: string;
  source: "orca" | "fallback";
}

export function printerArtwork(modelName: string | null | undefined): PrinterArtwork {
  const path = modelName ? ORCA_COVER_PATHS[modelName] : undefined;
  if (!path) {
    return {
      imageUrl: "/images/printers/generic-fdm.png",
      sourceUrl: ORCA_REPOSITORY,
      source: "fallback",
    };
  }
  const encodedPath = path.split("/").map(encodeURIComponent).join("/");
  return {
    imageUrl: `${ORCA_RAW_ROOT}${encodedPath}`,
    sourceUrl: `${ORCA_REPOSITORY}/blob/main/${encodedPath}`,
    source: "orca",
  };
}
