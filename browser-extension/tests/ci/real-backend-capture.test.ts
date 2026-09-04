import { randomBytes } from "node:crypto";
import { describe, expect, it } from "vitest";

import { buildBrowserCaptureMessage } from "../../capture-adapter.ts";
import { claimBrowserPairing } from "../../core.ts";
import { captureRichFiles } from "../../capture-transport.ts";

interface SetupResponse {
  access_token: string;
}

interface PairingResponse {
  code: string;
  expires_at: string;
}

interface CaptureResult {
  id: number;
  state: string;
}

function isCaptureResult(value: unknown): value is CaptureResult {
  return (
    typeof value === "object" &&
    value !== null &&
    "id" in value &&
    typeof value.id === "number" &&
    "state" in value &&
    typeof value.state === "string"
  );
}

function requiredEnvironment(name: string): string {
  const value = process.env[name]?.trim();
  if (!value) throw new Error(`Missing required real-backend capture environment: ${name}`);
  return value;
}

async function readJson<T>(response: Response, action: string): Promise<T> {
  if (!response.ok) {
    throw new Error(`${action} failed with HTTP ${response.status}: ${await response.text()}`);
  }
  return response.json();
}

describe("production extension capture against a real backend", () => {
  it("pairs a browser device, uploads a slot, and finalizes a Pending Import", async () => {
    const vault = requiredEnvironment("PRINTSTASH_EXTENSION_CAPTURE_BASE_URL").replace(/\/$/, "");
    const setupToken = requiredEnvironment("PRINTSTASH_EXTENSION_CAPTURE_SETUP_TOKEN");
    const suffix = randomBytes(8).toString("hex");
    const username = `ci-owner-${suffix}`;
    const password = `PrintStash-ci-${randomBytes(16).toString("hex")}`;

    const setup = await readJson<SetupResponse>(
      await fetch(`${vault}/api/v1/setup`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          setup_token: setupToken,
          username,
          password,
          storage_backend: "local",
        }),
      }),
      "first-run setup",
    );
    expect(setup.access_token).toBeTruthy();

    const pairing = await readJson<PairingResponse>(
      await fetch(`${vault}/api/v1/browser-pairings`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${setup.access_token}`,
          Accept: "application/json",
        },
      }),
      "pairing code creation",
    );
    expect(pairing.code).toHaveLength(32);
    expect(pairing.expires_at).toBeTruthy();

    const claimed = await claimBrowserPairing({
      vault,
      code: pairing.code,
      name: `CI browser ${suffix}`,
    });
    expect(claimed.device?.name).toBe(`CI browser ${suffix}`);

    const capture = buildBrowserCaptureMessage({
      provider: "Printables",
      pageUrl: "https://www.printables.com/model/321-benchy",
      pageTitle: "3D Benchy",
      jsonLd: [
        JSON.stringify({
          name: "3D Benchy",
          distribution: [
            {
              contentUrl: "https://media.printables.com/files/benchy.3mf?signature=ci",
              encodingFormat: "model/3mf",
            },
          ],
        }),
      ],
    });
    // Printables is a *manual file* provider: its page does not expose a
    // downloadable URL, so the adapter deliberately yields no candidates and
    // reports `manual_file_required` — the browser supplies the bytes and names
    // them. This test previously read `candidates[0]` and threw, which made it a
    // test of a code path Printables no longer has.
    expect(capture.candidates).toHaveLength(0);
    expect(capture.state).toBe("manual_file_required");

    const captureResult = await captureRichFiles({
      vault,
      authorization: claimed.deviceCredential,
      sourceUrl: capture.source.canonical_url,
      title: capture.source.fields.title?.value,
      captureSource: capture.source,
      files: [
        {
          id: "benchy.3mf",
          file: new Blob(["ci-capture-fixture"], { type: "model/3mf" }),
          filename: "benchy.3mf",
          mediaType: "model/3mf",
        },
      ],
    });
    if (!isCaptureResult(captureResult)) throw new Error("Finalize returned an invalid capture");
    const finalized = captureResult;
    expect(finalized.state).toBe("review");
    expect(finalized.id).toBeGreaterThan(0);

    const devices = await readJson<Array<{ name: string }>>(
      await fetch(`${vault}/api/v1/browser-pairings`, {
        headers: { Authorization: `Bearer ${setup.access_token}` },
      }),
      "paired-device readback",
    );
    expect(devices.some((device) => device.name === `CI browser ${suffix}`)).toBe(true);

    const inbox = await readJson<Array<{ id: number; state: string }>>(
      await fetch(`${vault}/api/v1/inbox?include_completed=true`, {
        headers: { Authorization: `Bearer ${setup.access_token}` },
      }),
      "Pending Import readback",
    );
    expect(inbox.some((item) => item.id === finalized.id && item.state === "review")).toBe(true);
  });
});
